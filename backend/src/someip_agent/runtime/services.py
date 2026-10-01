"""ARXML 服务会话生命周期；复用 SAT 字典初始化、成员 socket 与原生提交回执。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from someip_agent.config import Settings
from someip_agent.domain.models import ArxmlModel, MonitorMessage
from someip_agent.soa.catalog import NativeCatalogRequest, NativeServiceBundle, build_native_bundle
from someip_agent.soa.ipc import read_frame
from someip_agent.soa.naming import member_key
from someip_agent.soa.operator import NativeRuntimeError, SOAOperator
from someip_agent.soa.partner import S2sBaseClass

from .monitor import MonitorStore
from .service_models import (
    NativeMemberView,
    ServiceCommand,
    ServiceCommandResult,
    ServiceIncomingRequest,
    ServiceResponse,
    ServiceSessionView,
)

logger = logging.getLogger(__name__)


class ServiceSessionConflict(ValueError):
    pass


class ServiceSessionNotFound(LookupError):
    pass


@dataclass
class ServiceSession:
    id: str
    request: NativeCatalogRequest
    bundle: NativeServiceBundle
    partner: S2sBaseClass
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    started_at: datetime
    task: asyncio.Task[None] | None = None
    closing: bool = False
    last_error: str | None = None


class ServiceSessionManager:
    def __init__(self, settings: Settings, monitor: MonitorStore) -> None:
        self.settings = settings
        self.monitor = monitor
        self._sessions: dict[str, ServiceSession] = {}
        self._history: deque[ServiceSessionView] = deque(maxlen=64)
        self._lifecycle = asyncio.Lock()

    async def start(self, model: ArxmlModel, request: NativeCatalogRequest) -> ServiceSessionView:
        bundle = build_native_bundle(model, request, self.settings)
        async with self._lifecycle:
            if len(self._sessions) >= 16:
                raise ServiceSessionConflict("活动服务会话超过 16，请先停止已有会话")
            requested_names = {app["name"] for app in bundle.config["applications"]}
            requested_ids = {int(app["id"], 16) for app in bundle.config["applications"]}
            if 0x1101 in requested_ids:
                raise ServiceSessionConflict(
                    "0x1101 保留给默认信号仿真，请指定独立的 application_id"
                )
            if any(
                any(
                    app["name"] in requested_names or int(app["id"], 16) in requested_ids
                    for app in s.bundle.config["applications"]
                )
                for s in self._sessions.values()
            ):
                raise ServiceSessionConflict("应用名称或 application_id 已被活动会话占用")
            identifier = str(uuid4())
            directory = self.settings.data_dir / "native-services" / identifier
            operator: SOAOperator | None = None
            partner: S2sBaseClass | None = None
            writer: asyncio.StreamWriter | None = None
            try:
                catalog, config = bundle.write(directory)
                operator = SOAOperator(
                    request.application_name,
                    operator_port=0,
                    binary=self.settings.native_binary,
                    catalog=catalog,
                    config=config,
                    log_path=directory / "native.log",
                )
                # 不同于脚本实例的可选自动恢复：页面会话明确报告故障，停止后由用户重启。
                setup = asyncio.create_task(
                    asyncio.to_thread(
                        S2sBaseClass, bundle.members, operator=operator, auto_restart=False
                    )
                )
                try:
                    partner = await asyncio.shield(setup)
                except asyncio.CancelledError:
                    # 线程不能被协程取消；等待其明确结束后才清理，避免取消后再启动孤儿进程。
                    try:
                        partner = await setup
                    except Exception:
                        logger.exception(
                            "取消服务初始化时后台启动失败", extra={"operation": "services.cancel"}
                        )
                    raise
                reader, writer = await asyncio.open_connection(
                    operator.host, operator.operator_port
                )
                body = json.dumps({"function": "monitor"}).encode()
                writer.write(f"{len(body):08x}".encode() + body)
                await writer.drain()
                response = await asyncio.wait_for(read_frame(reader), 10)
                if response.get("failtype") != "FAILTYPE_SUCCESS":
                    raise NativeRuntimeError("原生服务监控订阅失败")
                session = ServiceSession(
                    identifier,
                    request.model_copy(deep=True),
                    bundle,
                    partner,
                    reader,
                    writer,
                    datetime.now(timezone.utc),
                )
                result = self._view(session)
                self._sessions[identifier] = session
                session.task = asyncio.create_task(self._read_monitor(session))
                logger.info(
                    "原生服务会话已初始化",
                    extra={"operation": "services.start", "session_id": identifier},
                )
                return result
            except BaseException:
                logger.exception("原生服务会话初始化失败", extra={"operation": "services.start"})
                if writer:
                    await self._close_monitor(writer)
                if partner:
                    await asyncio.to_thread(partner.close)
                elif operator:
                    await asyncio.to_thread(operator.stop_operator)
                raise

    def _view(self, session: ServiceSession) -> ServiceSessionView:
        process = session.partner.sim_operator.process
        alive = process is not None and process.poll() is None and not session.closing
        members = []
        for alias, config in session.bundle.members.items():
            key = member_key(alias, config)
            spec = session.bundle.catalog[alias]
            info = session.partner.partner_infos.get(key)
            members.append(
                NativeMemberView(
                    key=key,
                    application_name=info.application_name if info else None,
                    application_id=info.application_id if info else None,
                    role=config["role"],
                    service_path=spec["source"]["service_path"],
                    deployment_path=spec["source"]["deployment_path"],
                    service_id=spec["service_id"],
                    instance_id=spec["instance_id"],
                    transport=session.request.members[alias].transport,
                    state=info.service_status if alive and info else "OFFLINE",
                    connected=bool(alive and info and info.running),
                    methods=list(spec["methods"]),
                    no_return_methods=[
                        name
                        for name, api in spec["methods"].items()
                        if api.get("fire_and_forget", False)
                    ],
                    events=list(spec["events"]),
                    last_error=str(info.error) if info and info.error else None,
                )
            )
        return ServiceSessionView(
            id=session.id,
            model_id=session.bundle.model_id,
            source_sha256=session.bundle.source_sha256,
            application_name=session.request.application_name,
            application_id=session.request.application_id,
            started_at=session.started_at,
            active=not session.closing,
            running=bool(alive and all(m.connected for m in members)),
            pid=process.pid if process and alive else None,
            members=members,
            last_error=session.last_error,
        )

    def statuses(self) -> list[ServiceSessionView]:
        return [self._view(session) for session in self._sessions.values()] + list(self._history)

    def _session(self, identifier: str) -> ServiceSession:
        session = self._sessions.get(identifier)
        if session is None:
            raise ServiceSessionNotFound("服务会话不存在或已停止")
        if session.closing or not self._view(session).running:
            raise NativeRuntimeError("原生服务进程或成员通道已不可用，请停止后重新初始化")
        return session

    @staticmethod
    def _api(
        session: ServiceSession, command: ServiceCommand, *, role: str, event: bool
    ) -> dict[str, Any]:
        for alias, config in session.bundle.members.items():
            if member_key(alias, config) == command.member:
                if config["role"] != role:
                    raise ValueError("成员角色与操作不匹配")
                apis = session.bundle.catalog[alias]["events" if event else "methods"]
                if command.function not in apis:
                    raise ValueError("接口不在该会话的原生目录中")
                return dict(apis[command.function])
        raise ValueError("成员不属于该服务会话")

    async def call(self, identifier: str, command: ServiceCommand) -> ServiceCommandResult:
        session = self._session(identifier)
        api = self._api(session, command, role="client", event=False)
        if not isinstance(command.args, dict):
            raise ValueError("方法请求参数必须为 JSON 对象")
        if api.get("fire_and_forget", False):
            await asyncio.to_thread(
                session.partner.wait_for_service_reconnect, command.member, command.timeout
            )
            await asyncio.to_thread(
                session.partner.submit_member_command,
                command.member,
                {
                    "action": "request",
                    "function": command.function,
                    "args": json.dumps(command.args),
                },
                command.timeout,
            )
            return ServiceCommandResult(status="submitted", observation="native_submission")
        result = await asyncio.to_thread(
            session.partner.send_request_and_return_resp,
            command.member,
            command.function,
            command.args,
            command.timeout,
        )
        return ServiceCommandResult(
            status="responded", result=result, observation="vsomeip_response"
        )

    async def notify(self, identifier: str, command: ServiceCommand) -> ServiceCommandResult:
        session = self._session(identifier)
        self._api(session, command, role="server", event=True)
        await asyncio.to_thread(
            session.partner.submit_member_command,
            command.member,
            {"action": "event", "function": command.function, "args": json.dumps(command.args)},
            command.timeout,
        )
        return ServiceCommandResult(status="submitted", observation="native_submission")

    def requests(self, identifier: str) -> list[ServiceIncomingRequest]:
        session = self._session(identifier)
        result = []
        for alias, config in session.bundle.members.items():
            if config["role"] != "server":
                continue
            key = member_key(alias, config)
            target = session.partner.partner_infos[key].req_queue
            with target.mutex:
                pending = list(target.queue)
            methods = session.bundle.catalog[alias]["methods"]
            for message in pending:
                result.append(
                    ServiceIncomingRequest(
                        member=key,
                        function=message["function"],
                        request_id=message["request_id"],
                        args=json.loads(message["args"]),
                        payload_hex=message["payload_hex"],
                        received_at=message["timestamp"],
                        reply_allowed=not methods[message["function"]].get(
                            "fire_and_forget", False
                        ),
                    )
                )
        return sorted(result, key=lambda item: item.received_at, reverse=True)[:1000]

    async def respond(self, identifier: str, command: ServiceResponse) -> ServiceCommandResult:
        session = self._session(identifier)
        api = self._api(session, command, role="server", event=False)
        if api.get("fire_and_forget", False):
            raise ValueError("无响应方法不能发送响应")
        target = session.partner.partner_infos[command.member].req_queue
        with target.mutex:
            original = next(
                (
                    item
                    for item in target.queue
                    if item["request_id"] == command.request_id
                    and item["function"] == command.function
                ),
                None,
            )
        if original is None:
            raise ServiceSessionConflict("没有匹配的待响应请求，不能响应历史或其他接口")
        message = {
            "action": "response",
            "function": command.function,
            "request_id": command.request_id,
            "result": json.dumps({"out": command.args}),
            "return_code": command.return_code,
            "message_type": 0x81 if command.is_error else 0x80,
        }
        if command.args is None and (command.is_error or command.return_code):
            message["payload_hex"] = ""
        await asyncio.to_thread(
            session.partner.submit_member_command, command.member, message, command.timeout
        )
        with target.mutex:
            if original in target.queue:
                target.queue.remove(original)
        return ServiceCommandResult(status="submitted", observation="native_submission")

    async def _read_monitor(self, session: ServiceSession) -> None:
        try:
            while True:
                trace = await read_frame(session.reader)
                if trace.get("action") != "trace":
                    continue
                alias = next(
                    name
                    for name, config in session.bundle.members.items()
                    if member_key(name, config) == trace["member"]
                )
                transport = session.request.members[alias].transport
                await self.monitor.publish(
                    MonitorMessage(
                        direction="sim" if transport == "internal" else trace["direction"],
                        transport=transport,
                        source="vsomeip",
                        destination="原生服务协议栈观测",
                        service_id=trace["service_id"],
                        method_id=trace["method_id"],
                        client_id=trace["client_id"],
                        session_id=trace["session_id"],
                        interface_version=trace["interface_version"],
                        message_type=trace["message_type"],
                        return_code=trace["return_code"],
                        payload_hex=trace["payload_hex"],
                        payload_size=len(trace["payload_hex"]) // 2,
                        metadata={
                            "runtime": "vsomeip",
                            "observation": "vsomeip_api",
                            "wire_verified": False,
                            "timestamp_source": "backend_receive",
                            "service_session_id": session.id,
                            "model_id": session.bundle.model_id,
                            "source_sha256": session.bundle.source_sha256,
                            "native_monotonic_ns": trace["native_monotonic_ns"],
                            "instance_id": trace["instance_id"],
                            "member": trace["member"],
                            "application_name": trace.get("application_name"),
                            "application_id": trace.get("application_id"),
                        },
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            session.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "原生服务监控断开",
                extra={"operation": "services.monitor", "session_id": session.id},
            )
            await asyncio.to_thread(session.partner.close)

    @staticmethod
    async def _close_monitor(writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            logger.exception(
                "关闭服务监控通道时连接已经中断", extra={"operation": "services.monitor.close"}
            )

    async def stop(self, identifier: str) -> ServiceSessionView:
        async with self._lifecycle:
            session = self._sessions.get(identifier)
            if session is None:
                previous = next((item for item in self._history if item.id == identifier), None)
                if previous:
                    return previous.model_copy(deep=True)
                raise ServiceSessionNotFound("服务会话不存在")
            session.closing = True
            if session.task:
                session.task.cancel()
                try:
                    await session.task
                except asyncio.CancelledError:
                    pass
            await asyncio.to_thread(session.partner.close)
            await self._close_monitor(session.writer)
            result = self._view(session)
            del self._sessions[identifier]
            self._history.appendleft(result)
            logger.info(
                "原生服务会话已停止", extra={"operation": "services.stop", "session_id": identifier}
            )
            return result

    async def shutdown(self) -> None:
        for identifier in list(self._sessions):
            await self.stop(identifier)
