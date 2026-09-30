from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from someip_agent.domain.models import ListenerConfig, ListenerStatus, MonitorMessage
from someip_agent.protocol.sd import SdPayload, is_sd_message
from someip_agent.protocol.someip import (
    SOMEIP_HEADER_SIZE,
    SomeIpDecodeError,
    SomeIpHeader,
    SomeIpMessage,
    decode_many,
)

from .monitor import MonitorStore

logger = logging.getLogger(__name__)


class _DatagramReceiver(asyncio.DatagramProtocol):
    def __init__(self, manager: NetworkCaptureManager, listener_id: str) -> None:
        self._manager = manager
        self._listener_id = listener_id
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        if isinstance(transport, asyncio.DatagramTransport):
            self._transport = transport

    def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
        local = self._transport.get_extra_info("sockname") if self._transport else ("", 0)
        asyncio.create_task(
            self._manager.ingest(
                self._listener_id,
                data,
                source=f"{address[0]}:{address[1]}",
                destination=f"{local[0]}:{local[1]}",
                transport="udp",
            )
        )

    def error_received(self, exc: Exception) -> None:
        self._manager.record_listener_error(self._listener_id, exc)


class NetworkCaptureManager:
    def __init__(
        self,
        monitor: MonitorStore,
        message_enricher: Callable[[MonitorMessage], MonitorMessage] | None = None,
    ) -> None:
        self._monitor = monitor
        self._message_enricher = message_enricher or (lambda message: message)
        self._statuses: dict[str, ListenerStatus] = {}
        self._handles: dict[str, asyncio.DatagramTransport | asyncio.AbstractServer] = {}

    async def start(self, config: ListenerConfig) -> ListenerStatus:
        listener_id = str(uuid4())
        status = ListenerStatus(id=listener_id, config=config)
        self._statuses[listener_id] = status
        try:
            if config.transport == "udp":
                transport = await self._start_udp(listener_id, config)
                self._handles[listener_id] = transport
            else:
                server = await asyncio.start_server(
                    lambda reader, writer: self._handle_tcp(listener_id, reader, writer),
                    config.bind_host,
                    config.port,
                )
                self._handles[listener_id] = server
        except Exception as exc:
            status.running = False
            status.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "网络监听启动失败",
                extra={"operation": "network.listener.start", "listener_id": listener_id},
            )
            raise
        logger.info("网络监听已启动", extra={"operation": "network.listener.start"})
        return status.model_copy(deep=True)

    async def _start_udp(
        self, listener_id: str, config: ListenerConfig
    ) -> asyncio.DatagramTransport:
        loop = asyncio.get_running_loop()
        if config.multicast_group:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((config.bind_host, config.port))
            membership = socket.inet_aton(config.multicast_group) + socket.inet_aton(
                config.interface_ip
            )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.setblocking(False)
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _DatagramReceiver(self, listener_id),
                sock=sock,
            )
        else:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _DatagramReceiver(self, listener_id),
                local_addr=(config.bind_host, config.port),
            )
        return transport

    async def stop(self, listener_id: str | None = None) -> list[ListenerStatus]:
        identifiers = [listener_id] if listener_id else list(self._handles)
        stopped: list[ListenerStatus] = []
        for identifier in identifiers:
            handle = self._handles.pop(identifier, None)
            status = self._statuses.get(identifier)
            if handle is not None:
                handle.close()
                if isinstance(handle, asyncio.AbstractServer):
                    await handle.wait_closed()
            if status is not None:
                status.running = False
                stopped.append(status.model_copy(deep=True))
        return stopped

    def list(self) -> list[ListenerStatus]:
        return [status.model_copy(deep=True) for status in self._statuses.values()]

    async def shutdown(self) -> None:
        await self.stop()

    async def ingest(
        self,
        listener_id: str,
        data: bytes,
        *,
        source: str,
        destination: str,
        transport: str,
    ) -> None:
        status = self._statuses.get(listener_id)
        if status is None:
            return
        try:
            for message in decode_many(data):
                await self._monitor.publish(
                    self._message_enricher(
                        self._to_monitor_message(
                            message,
                            source=source,
                            destination=destination,
                            transport=transport,
                            listener_id=listener_id,
                        )
                    )
                )
                status.received_count += 1
        except SomeIpDecodeError as exc:
            status.parse_error_count += 1
            status.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "网络报文 SOME/IP 解析失败",
                extra={"operation": "network.packet.decode", "listener_id": listener_id},
            )

    async def _handle_tcp(
        self,
        listener_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername") or ("", 0)
        local = writer.get_extra_info("sockname") or ("", 0)
        try:
            while True:
                raw_header = await reader.readexactly(SOMEIP_HEADER_SIZE)
                header = SomeIpHeader.decode(raw_header)
                payload = await reader.readexactly(header.payload_length)
                await self.ingest(
                    listener_id,
                    raw_header + payload,
                    source=f"{peer[0]}:{peer[1]}",
                    destination=f"{local[0]}:{local[1]}",
                    transport="tcp",
                )
        except asyncio.IncompleteReadError:
            pass
        except Exception as exc:
            self.record_listener_error(listener_id, exc)
        finally:
            writer.close()
            await writer.wait_closed()

    def record_listener_error(self, listener_id: str, exc: Exception) -> None:
        status = self._statuses.get(listener_id)
        if status:
            status.last_error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "网络监听异常",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"operation": "network.listener.error", "listener_id": listener_id},
        )

    @staticmethod
    def _to_monitor_message(
        message: SomeIpMessage,
        *,
        source: str,
        destination: str,
        transport: str,
        listener_id: str,
    ) -> MonitorMessage:
        header = message.header
        sd = is_sd_message(message)
        summary = None
        if sd:
            try:
                summary = SdPayload.decode(message.payload).summary()
            except SomeIpDecodeError as exc:
                summary = f"SOME/IP-SD 解析失败: {exc}"
        return MonitorMessage(
            timestamp=datetime.now(timezone.utc),
            direction="rx",
            transport=transport,
            source=source,
            destination=destination,
            service_id=header.service_id,
            method_id=header.method_id,
            client_id=header.client_id,
            session_id=header.session_id,
            interface_version=header.interface_version,
            message_type=header.message_type,
            return_code=header.return_code,
            payload_hex=message.payload.hex(),
            payload_size=len(message.payload),
            is_sd=sd,
            sd_summary=summary,
            metadata={"listener_id": listener_id},
        )
