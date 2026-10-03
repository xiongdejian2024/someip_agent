from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from someip_agent.agent.console import ConsoleControls, console_tools
from someip_agent.agent.service import AgentService, LlmConfigurationService
from someip_agent.arxml.parser import ArxmlParseError, ArxmlParser
from someip_agent.config import Settings
from someip_agent.domain.models import ArxmlModel, MessageType, MonitorMessage, SignalDefinition
from someip_agent.protocol.native_payload import NativePayloadError, NativeSignalDecoder
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.network import NetworkCaptureManager
from someip_agent.runtime.services import ServiceSessionManager
from someip_agent.runtime.simulator import SimulationManager
from someip_agent.soa.operator import NativeRuntimeError
from someip_agent.storage.arxml_repository import ArxmlModelRepository
from someip_agent.storage.repository import AuditRepository
from someip_agent.update.service import UpdateService
from someip_agent.workbench.projects import ProjectConflict, ProjectRepository, ProjectView
from someip_agent.workbench.recordings import RecordingManager

logger = logging.getLogger(__name__)


class ApplicationState:
    def __init__(self, settings: Settings) -> None:
        settings.ensure_directories()
        self.settings = settings
        self.monitor = MonitorStore(settings.monitor_capacity)
        self.recordings = RecordingManager(settings.data_dir / "recordings", self.monitor)
        self.signal_decoder = NativeSignalDecoder(settings)
        self.network = NetworkCaptureManager(self.monitor, self.enrich_message, settings=settings)
        self.simulator = SimulationManager(self.monitor, settings)
        self.services = ServiceSessionManager(settings, self.monitor)
        self.audit = AuditRepository(settings.data_dir / "someip-agent.sqlite3")
        self.arxml_models = ArxmlModelRepository(settings.data_dir / "models")
        self.projects = ProjectRepository(settings.data_dir / "someip-agent.sqlite3")
        self.llm_configuration = LlmConfigurationService(settings)
        self.update_service = UpdateService(settings)
        self._arxml_model = self._restore_project_model()
        self._model_lock = asyncio.Lock()
        self.console = ConsoleControls()
        self.agent = AgentService(
            self.llm_configuration,
            self.monitor,
            self.simulator,
            self.services_as_dicts,
            decoder=self.signal_decoder,
            settings=settings,
            audit=self.audit.add,
        )
        self.agent.register_console_tools(console_tools(self))

    async def set_arxml_model(self, model: ArxmlModel) -> None:
        async with self._model_lock:
            self.arxml_models.save(model)
            self._arxml_model = model

    async def get_arxml_model(self) -> ArxmlModel | None:
        async with self._model_lock:
            return self._arxml_model.model_copy(deep=True) if self._arxml_model else None

    def _restore_project_model(self) -> ArxmlModel | None:
        try:
            project = self.projects.current()
            if project is not None:
                logger.info(
                    "已恢复工程配置，服务与激励保持停止", extra={"operation": "project.restart"}
                )
                return project.document.model
        except Exception:
            logger.exception(
                "工程恢复失败，保留数据库并回退模型", extra={"operation": "project.restart"}
            )
        return self._restore_arxml_model()

    async def open_project(self, identifier: UUID) -> ProjectView:
        async with self._model_lock:
            if (
                any(s.active for s in self.services.statuses())
                or any(s.running for s in self.simulator.list())
                or any(s.running for s in self.network.list())
            ):
                raise ProjectConflict(
                    "请先停止服务、仿真和监听，再切换工程；打开不会自动停止或启动它们"
                )
            view = await asyncio.to_thread(self.projects.get, identifier)
            await asyncio.to_thread(self.projects.select, identifier)
            self._arxml_model = (
                view.document.model.model_copy(deep=True) if view.document.model else None
            )
            return view

    def services_as_dicts(self) -> list[dict[str, object]]:
        model = self._arxml_model
        if model is None:
            return []
        return [service.model_dump(mode="json") for service in model.services]

    def _message_signals(
        self, message: MonitorMessage, model: ArxmlModel | None
    ) -> tuple[list[SignalDefinition], str]:
        if model is None or message.is_sd:
            return [], ""
        matches = [item for item in model.services if item.service_id == message.service_id]
        if not matches:
            return [], ""
        if len(matches) != 1:
            raise ValueError("ARXML服务ID存在多个部署，不能猜测观测payload布局")
        service = matches[0]
        signals: list[SignalDefinition] = []
        schema_source = ""
        event = next((item for item in service.events if item.event_id == message.method_id), None)
        if event:
            signals = event.signals
            schema_source = f"event:{event.name}"
        if not signals:
            method = next(
                (item for item in service.methods if item.method_id == message.method_id), None
            )
            if method:
                is_response = message.message_type in {
                    MessageType.RESPONSE,
                    MessageType.ERROR,
                    MessageType.RESPONSE_ACK,
                    MessageType.ERROR_ACK,
                }
                signals = method.output_signals if is_response else method.input_signals
                schema_source = f"method:{method.name}:{'out' if is_response else 'in'}"
        if not signals:
            for field in service.fields:
                if message.method_id in {field.getter_id, field.setter_id, field.notifier_id}:
                    if message.method_id == field.getter_id and not message.message_type & 0x80:
                        return [], f"field:{field.name}:getter-request"
                    signals = [field.signal] if field.signal else []
                    schema_source = f"field:{field.name}"
                    break
        return signals, schema_source

    @staticmethod
    def _decode_failure(message: MonitorMessage, exc: Exception) -> None:
        logger.exception("观测信号原生解码失败", extra={"operation": "monitor.payload.decode"})
        message.signal_values = {}
        message.metadata.pop("signal_decoder", None)
        message.metadata.pop("signal_schema", None)
        message.metadata["signal_decode_error"] = f"{type(exc).__name__}: {exc}"

    def enrich_messages(self, messages: list[MonitorMessage]) -> list[MonitorMessage]:
        groups: dict[tuple[int, int, str], tuple[list[SignalDefinition], list[MonitorMessage]]] = {}
        model = self._arxml_model
        for message in messages:
            try:
                signals, source = self._message_signals(message, model)
                if signals:
                    groups.setdefault(
                        (message.service_id, message.method_id, source), (signals, [])
                    )[1].append(message)
            except ValueError as exc:
                self._decode_failure(message, exc)
        for (_, _, source), (signals, candidates) in groups.items():
            try:
                records = self.signal_decoder.decode_many(
                    [m.payload_hex for m in candidates], signals
                )
                for message, record in zip(candidates, records, strict=True):
                    if "error" in record:
                        try:
                            raise NativePayloadError(record["error"])
                        except NativePayloadError as exc:
                            self._decode_failure(message, exc)
                    else:
                        message.signal_values = record["values"]
                        message.metadata["signal_schema"] = source
                        message.metadata["signal_decoder"] = "someip-agent-native-codec"
                        message.metadata.pop("signal_decode_error", None)
            except (NativeRuntimeError, ValueError) as exc:
                for message in candidates:
                    self._decode_failure(message, exc)
        return messages

    def enrich_message(self, message: MonitorMessage) -> MonitorMessage:
        return self.enrich_messages([message])[0]

    async def shutdown(self) -> None:
        await self.agent.shutdown()
        await self.services.shutdown()
        await self.network.shutdown()
        await self.simulator.shutdown()
        await self.recordings.shutdown()
        await asyncio.to_thread(self.signal_decoder.close)

    def imported_file_path(self, digest: str, source_name: str) -> Path:
        safe_name = Path(source_name).name.replace("..", "_")
        return self.settings.data_dir / "imports" / digest / safe_name

    def _restore_arxml_model(self) -> ArxmlModel | None:
        try:
            model = self.arxml_models.load()
        except Exception:
            logger.exception(
                "持久化 ARXML 模型恢复失败，将尝试从历史导入文件恢复",
                extra={"operation": "arxml.restore"},
            )
        else:
            if model is not None:
                logger.info(
                    "已恢复持久化 ARXML 模型",
                    extra={"operation": "arxml.restore"},
                )
                return model

        imports_dir = self.settings.data_dir / "imports"
        if not imports_dir.is_dir():
            return None
        try:
            imported_files = sorted(
                (path for path in imports_dir.rglob("*") if path.is_file()),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            logger.exception(
                "扫描历史 ARXML 导入文件失败",
                extra={"operation": "arxml.restore"},
            )
            return None

        for imported_file in imported_files:
            try:
                model = ArxmlParser().parse(imported_file.read_bytes(), imported_file.name)
                self.arxml_models.save(model)
            except (ArxmlParseError, OSError):
                logger.exception(
                    "历史 ARXML 导入文件恢复失败",
                    extra={"operation": "arxml.restore"},
                )
                continue
            logger.info(
                "已从历史导入文件恢复 ARXML 模型",
                extra={"operation": "arxml.restore"},
            )
            return model
        return None
