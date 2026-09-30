from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from someip_agent.agent.service import AgentService, LlmConfigurationService
from someip_agent.arxml.parser import ArxmlParseError, ArxmlParser
from someip_agent.config import Settings
from someip_agent.domain.models import ArxmlModel, MessageType, MonitorMessage, SignalDefinition
from someip_agent.protocol.codec import SignalCodec, SignalCodecError
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.network import NetworkCaptureManager
from someip_agent.runtime.simulator import SimulationManager
from someip_agent.storage.arxml_repository import ArxmlModelRepository
from someip_agent.storage.repository import AuditRepository
from someip_agent.update.service import UpdateService

logger = logging.getLogger(__name__)


class ApplicationState:
    def __init__(self, settings: Settings) -> None:
        settings.ensure_directories()
        self.settings = settings
        self.monitor = MonitorStore(settings.monitor_capacity)
        self.network = NetworkCaptureManager(self.monitor, self.enrich_message)
        self.simulator = SimulationManager(self.monitor, settings)
        self.audit = AuditRepository(settings.data_dir / "someip-agent.sqlite3")
        self.arxml_models = ArxmlModelRepository(settings.data_dir / "models")
        self.llm_configuration = LlmConfigurationService(settings)
        self.update_service = UpdateService(settings)
        self._arxml_model = self._restore_arxml_model()
        self._model_lock = asyncio.Lock()
        self.agent = AgentService(
            self.llm_configuration,
            self.monitor,
            self.simulator,
            self.services_as_dicts,
        )

    async def set_arxml_model(self, model: ArxmlModel) -> None:
        async with self._model_lock:
            self.arxml_models.save(model)
            self._arxml_model = model

    async def get_arxml_model(self) -> ArxmlModel | None:
        async with self._model_lock:
            return self._arxml_model.model_copy(deep=True) if self._arxml_model else None

    def services_as_dicts(self) -> list[dict[str, object]]:
        model = self._arxml_model
        if model is None:
            return []
        return [service.model_dump(mode="json") for service in model.services]

    def enrich_message(self, message: MonitorMessage) -> MonitorMessage:
        model = self._arxml_model
        if model is None or message.is_sd:
            return message
        service = next(
            (item for item in model.services if item.service_id == message.service_id), None
        )
        if service is None:
            return message
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
                    signals = [field.signal] if field.signal else []
                    schema_source = f"field:{field.name}"
                    break
        if not signals:
            return message
        try:
            message.signal_values = SignalCodec.decode(bytes.fromhex(message.payload_hex), signals)
            message.metadata["signal_schema"] = schema_source
        except (SignalCodecError, ValueError) as exc:
            message.metadata["signal_decode_error"] = f"{type(exc).__name__}: {exc}"
        return message

    async def shutdown(self) -> None:
        await self.network.shutdown()
        await self.simulator.shutdown()

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
