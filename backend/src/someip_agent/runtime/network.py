"""原生端口监听的控制与展示层；不绑定线上端口、不在 Python 中解码 SOME/IP 头。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from someip_agent.config import Settings
from someip_agent.domain.models import ListenerConfig, ListenerStatus, MonitorMessage
from someip_agent.protocol.sd import SdPayload
from someip_agent.protocol.someip import SomeIpDecodeError
from someip_agent.soa.ipc import read_frame
from someip_agent.soa.operator import NativeOperationError, NativeRuntimeError, SOAOperator

from .monitor import MonitorStore

logger = logging.getLogger(__name__)


class NetworkCaptureManager:
    """控制原生端口监听或被动抓包；Python 不读取网卡或重组 TCP。"""

    def __init__(
        self,
        monitor: MonitorStore,
        message_enricher: Callable[[MonitorMessage], MonitorMessage] | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._monitor = monitor
        self._message_enricher = message_enricher or (lambda message: message)
        self._settings = settings or Settings()
        self._statuses: dict[str, ListenerStatus] = {}
        self._active: set[str] = set()
        self._operator: SOAOperator | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lifecycle = asyncio.Lock()

    async def _ensure_runtime(self) -> SOAOperator:
        if (
            self._operator
            and self._operator.process
            and self._operator.process.poll() is None
            and self._operator.tcp_socket is not None
            and self._reader_task
            and not self._reader_task.done()
        ):
            return self._operator
        await self._close_runtime()
        operator = SOAOperator(
            "network",
            operator_port=0,
            binary=self._settings.native_binary,
            log_path=self._settings.data_dir / "native-network" / str(uuid4()) / "native.log",
            mode="network",
        )
        self._operator = operator
        try:
            await asyncio.to_thread(operator.run_operator)
            await asyncio.to_thread(operator.create_socket)
            reader, writer = await asyncio.open_connection(operator.host, operator.operator_port)
            self._writer = writer
            body = json.dumps({"function": "monitor"}).encode()
            writer.write(f"{len(body):08x}".encode() + body)
            await writer.drain()
            reply = await asyncio.wait_for(read_frame(reader), 10)
            if reply.get("failtype") != "FAILTYPE_SUCCESS":
                raise NativeRuntimeError("原生网络监控订阅失败")
            self._reader_task = asyncio.create_task(self._read_monitor(reader, operator))
            return operator
        except BaseException:
            logger.exception("原生网络进程启动失败", extra={"operation": "network.native.start"})
            await self._close_runtime()
            raise

    async def start(self, config: ListenerConfig) -> ListenerStatus:
        async with self._lifecycle:
            identifier = str(uuid4())
            status = ListenerStatus(id=identifier, config=config, running=False)
            self._statuses[identifier] = status
            try:
                operator = await self._ensure_runtime()
                self._active.add(identifier)
                await asyncio.to_thread(
                    operator.send_request,
                    "network_start",
                    {"id": identifier, "config": config.model_dump(mode="json")},
                )
                if self._reader_task is None or self._reader_task.done():
                    raise NativeRuntimeError("原生监听在启动期间已断开")
                status.running = identifier in self._active
            except BaseException as exc:
                self._active.discard(identifier)
                status.running = False
                status.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "原生网络监听启动失败",
                    extra={"operation": "network.listener.start", "listener_id": identifier},
                )
                if not self._active:
                    await self._close_runtime()
                if isinstance(exc, NativeOperationError):
                    raise ValueError(str(exc)) from exc
                raise
            logger.info(
                "原生网络监听已启动",
                extra={"operation": "network.listener.start", "listener_id": identifier},
            )
            return status.model_copy(deep=True)

    async def stop(self, listener_id: str | None = None) -> list[ListenerStatus]:
        async with self._lifecycle:
            identifiers = [listener_id] if listener_id else list(self._statuses)
            stopped = []
            for identifier in identifiers:
                status = self._statuses.get(identifier)
                if status is None:
                    continue
                self._active.discard(identifier)
                if status.running and self._operator:
                    try:
                        await asyncio.to_thread(
                            self._operator.send_request, "network_stop", {"id": identifier}
                        )
                    except Exception as exc:
                        logger.exception(
                            "原生网络监听停止失败，将关闭所属进程",
                            extra={"operation": "network.listener.stop", "listener_id": identifier},
                        )
                        status.last_error = f"{type(exc).__name__}: {exc}"
                        self._active.clear()
                        await self._close_runtime()
                        for item in self._statuses.values():
                            if item.running and not item.last_error:
                                item.last_error = (
                                    f"原生进程因停止失败关闭: {type(exc).__name__}: {exc}"
                                )
                            item.running = False
                            item.active_streams = 0
                            item.active_fragment_datagrams = 0
                            item.fragment_buffered_bytes = 0
                status.running = False
                status.active_streams = 0
                status.active_fragment_datagrams = 0
                status.fragment_buffered_bytes = 0
                stopped.append(status.model_copy(deep=True))
                logger.info(
                    "原生网络监听已停止",
                    extra={"operation": "network.listener.stop", "listener_id": identifier},
                )
            if not self._active:
                await self._close_runtime()
            return stopped

    async def interfaces(self) -> list[dict[str, Any]]:
        async with self._lifecycle:
            try:
                operator = await self._ensure_runtime()
                result = await asyncio.to_thread(operator.send_request, "network_interfaces", {})
                if not isinstance(result, list):
                    raise NativeRuntimeError("原生网卡列表格式非法")
                return result
            finally:
                if not self._active:
                    await self._close_runtime()

    def list(self) -> list[ListenerStatus]:
        return [status.model_copy(deep=True) for status in self._statuses.values()]

    async def shutdown(self) -> None:
        await self.stop()

    async def _close_runtime(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._writer:
            self._writer.close()
            try:
                await asyncio.wait_for(self._writer.wait_closed(), 2)
            except (OSError, TimeoutError):
                logger.exception("关闭原生监控连接失败", extra={"operation": "network.native.stop"})
            self._writer = None
        if self._operator:
            await asyncio.to_thread(self._operator.stop_operator)
            self._operator = None

    async def _read_monitor(self, reader: asyncio.StreamReader, operator: SOAOperator) -> None:
        try:
            while True:
                packet = await read_frame(reader)
                identifier = str(packet.get("listener_id", ""))
                status = self._statuses.get(identifier)
                if status is None or identifier not in self._active:
                    continue
                if packet.get("action") not in {"packet", "listener_error", "listener_stats"}:
                    continue
                status.received_count = int(packet["received_count"])
                status.parse_error_count = int(packet["parse_error_count"])
                for field in (
                    "captured_count",
                    "kernel_dropped_count",
                    "interface_dropped_count",
                    "active_streams",
                    "active_fragment_datagrams",
                    "fragment_buffered_bytes",
                    "reassembled_datagrams",
                    "fragment_error_count",
                ):
                    if field in packet:
                        setattr(status, field, packet[field])
                if packet.get("running") is False:
                    status.running = False
                    status.active_streams = 0
                    status.active_fragment_datagrams = 0
                    status.fragment_buffered_bytes = 0
                    self._active.discard(identifier)
                if packet["action"] == "listener_stats":
                    continue
                if packet["action"] == "listener_error":
                    status.last_error = str(packet["error"])
                    logger.error(
                        "原生网络监听异常，堆栈见原生日志：%s",
                        status.last_error,
                        extra={"operation": "network.listener.error", "listener_id": status.id},
                    )
                    continue
                await self._monitor.publish(
                    self._message_enricher(self._to_monitor_message(packet))
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("原生网络监控断开", extra={"operation": "network.native.monitor"})
            for identifier in self._active:
                status = self._statuses[identifier]
                status.running = False
                status.active_streams = 0
                status.active_fragment_datagrams = 0
                status.fragment_buffered_bytes = 0
                status.last_error = f"{type(exc).__name__}: {exc}"
            self._active.clear()
            await asyncio.to_thread(operator.stop_operator)

    @staticmethod
    def _to_monitor_message(packet: dict[str, Any]) -> MonitorMessage:
        passive = packet.get("observation") == "pcap_capture"
        summary = None
        if packet["is_sd"]:
            # SD 文本摘要是展示层分析；不参与线上 SD 状态机和 SOME/IP 解码。
            try:
                summary = SdPayload.decode(bytes.fromhex(packet["payload_hex"])).summary()
            except SomeIpDecodeError as exc:
                logger.exception("SD 展示摘要解析失败", extra={"operation": "network.sd.summary"})
                summary = f"SOME/IP-SD 解析失败: {exc}"
        return MonitorMessage(
            timestamp=datetime.fromtimestamp(
                packet["received_at_ns"] / 1_000_000_000, timezone.utc
            ),
            direction="rx",
            transport=packet["transport"],
            source=packet["source"],
            destination=packet["destination"],
            service_id=packet["service_id"],
            method_id=packet["method_id"],
            client_id=packet["client_id"],
            session_id=packet["session_id"],
            interface_version=packet["interface_version"],
            message_type=packet["message_type"],
            return_code=packet["return_code"],
            payload_hex=packet["payload_hex"],
            payload_size=packet["payload_size"],
            is_sd=packet["is_sd"],
            sd_summary=summary,
            metadata={
                "listener_id": packet["listener_id"],
                "runtime": "vsomeip",
                "observation": "pcap_capture" if passive else "socket_receive",
                "wire_verified": True,
                "capture_interface": packet.get("capture_interface"),
                "tcp_partial": packet.get("tcp_partial", False),
                "ip_reassembled": packet.get("ip_reassembled", False),
                "ip_fragment_count": packet.get("ip_fragment_count", 0),
                "timestamp_source": "pcap_software" if passive else "socket_software",
                "note": (
                    "原生网卡被动抓包，时间戳为完成帧的软件时间"
                    if passive
                    else "原生端口实际收包，不是网卡被动抓包"
                ),
            },
        )
