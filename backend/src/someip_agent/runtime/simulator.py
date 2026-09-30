from __future__ import annotations

import asyncio
import logging
import math
import random
import socket
import struct
import time
from datetime import datetime, timezone
from uuid import uuid4

from someip_agent.config import Settings
from someip_agent.domain.models import (
    GeneratorKind,
    MessageType,
    MonitorMessage,
    SignalDataType,
    SimulationConfig,
    SimulationStatus,
)
from someip_agent.protocol.sd import SD_METHOD_ID, SD_SERVICE_ID, SdEntry, SdPayload
from someip_agent.protocol.someip import SomeIpMessage

from .monitor import MonitorStore

logger = logging.getLogger(__name__)


class SimulationPermissionError(PermissionError):
    pass


class SimulationManager:
    def __init__(self, monitor: MonitorStore, settings: Settings) -> None:
        self._monitor = monitor
        self._settings = settings
        self._statuses: dict[str, SimulationStatus] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._session_id = 0

    async def start(self, config: SimulationConfig) -> SimulationStatus:
        if config.transport == "udp" and not self._is_destination_allowed(
            config.destination_host, config.destination_port
        ):
            raise SimulationPermissionError(
                "真实网络发送默认关闭；请设置 SOMEIP_AGENT_NETWORK_SEND_ENABLED 并加入目标白名单"
            )
        if (
            config.transport == "udp"
            and config.enable_sd
            and not self._is_destination_allowed(config.sd_multicast_group, config.sd_port)
        ):
            raise SimulationPermissionError(
                "SOME/IP-SD 目标未加入发送白名单；请加入组播地址与 30490 端口"
            )
        simulation_id = str(uuid4())
        status = SimulationStatus(
            id=simulation_id,
            config=config,
            running=True,
            started_at=datetime.now(timezone.utc),
        )
        self._statuses[simulation_id] = status
        self._tasks[simulation_id] = asyncio.create_task(
            self._run(simulation_id), name=f"someip-sim-{simulation_id}"
        )
        logger.info("仿真已启动", extra={"operation": "simulation.start"})
        return status.model_copy(deep=True)

    async def stop(self, simulation_id: str | None = None) -> list[SimulationStatus]:
        identifiers = [simulation_id] if simulation_id else list(self._tasks)
        stopped: list[SimulationStatus] = []
        for identifier in identifiers:
            task = self._tasks.pop(identifier, None)
            status = self._statuses.get(identifier)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if status is not None:
                status.running = False
                stopped.append(status.model_copy(deep=True))
        if stopped:
            logger.info("仿真已停止", extra={"operation": "simulation.stop"})
        return stopped

    def list(self) -> list[SimulationStatus]:
        return [status.model_copy(deep=True) for status in self._statuses.values()]

    async def shutdown(self) -> None:
        await self.stop()

    async def _run(self, simulation_id: str) -> None:
        status = self._statuses[simulation_id]
        config = status.config
        started = time.monotonic()
        sock: socket.socket | None = None
        sd_sock: socket.socket | None = None
        try:
            if config.transport == "udp":
                family = socket.AF_INET6 if ":" in config.destination_host else socket.AF_INET
                sock = socket.socket(family, socket.SOCK_DGRAM)
                sock.setblocking(False)
                sock.connect((config.destination_host, config.destination_port))
                if config.enable_sd:
                    sd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sd_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                    sd_sock.setblocking(False)
                    sd_sock.connect((config.sd_multicast_group, config.sd_port))
            next_sd_offer = 0.0
            while True:
                elapsed = time.monotonic() - started
                if config.enable_sd and elapsed >= next_sd_offer:
                    sd_message, sd_summary = self._build_offer(config, ttl=config.sd_ttl)
                    if sd_sock is not None:
                        await asyncio.get_running_loop().sock_sendall(sd_sock, sd_message.encode())
                    await self._monitor.publish(
                        MonitorMessage(
                            direction="sim" if config.transport == "internal" else "tx",
                            transport=config.transport,
                            source="simulator-sd",
                            destination=(
                                "sd-monitor"
                                if config.transport == "internal"
                                else f"{config.sd_multicast_group}:{config.sd_port}"
                            ),
                            service_id=SD_SERVICE_ID,
                            method_id=SD_METHOD_ID,
                            session_id=sd_message.header.session_id,
                            message_type=MessageType.NOTIFICATION,
                            payload_hex=sd_message.payload.hex(),
                            payload_size=len(sd_message.payload),
                            is_sd=True,
                            sd_summary=sd_summary,
                            metadata={
                                "simulation_id": simulation_id,
                                "advertised_service_id": config.service_id,
                                "advertised_instance_id": config.instance_id,
                            },
                        )
                    )
                    next_sd_offer = elapsed + config.sd_offer_cycle_ms / 1000
                value = self._generate(config, elapsed, status.emitted_count)
                payload = self._encode_value(value, config.generator.data_type)
                self._session_id = (self._session_id % 0xFFFF) + 1
                wire_message = SomeIpMessage.build(
                    service_id=config.service_id,
                    method_id=config.method_id,
                    payload=payload,
                    session_id=self._session_id,
                    interface_version=config.interface_version,
                    message_type=MessageType.NOTIFICATION,
                )
                if sock is not None:
                    await asyncio.get_running_loop().sock_sendall(sock, wire_message.encode())
                await self._monitor.publish(
                    MonitorMessage(
                        direction="sim" if config.transport == "internal" else "tx",
                        transport=config.transport,
                        source="simulator",
                        destination=(
                            "monitor"
                            if config.transport == "internal"
                            else f"{config.destination_host}:{config.destination_port}"
                        ),
                        service_id=config.service_id,
                        method_id=config.method_id,
                        session_id=self._session_id,
                        interface_version=config.interface_version,
                        message_type=MessageType.NOTIFICATION,
                        payload_hex=payload.hex(),
                        payload_size=len(payload),
                        signal_values={config.generator.signal_name: value},
                        metadata={"simulation_id": simulation_id, "simulation": config.name},
                    )
                )
                status.emitted_count += 1
                status.last_value = value
                await asyncio.sleep(config.interval_ms / 1000)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "仿真循环异常",
                extra={"operation": "simulation.loop", "simulation_id": simulation_id},
            )
        finally:
            status.running = False
            if sock is not None:
                sock.close()
            if sd_sock is not None:
                sd_sock.close()

    @staticmethod
    def _generate(config: SimulationConfig, elapsed: float, index: int) -> float:
        generator = config.generator
        if generator.kind == GeneratorKind.CONSTANT:
            return generator.initial
        if generator.kind == GeneratorKind.RANDOM:
            return random.uniform(generator.minimum, generator.maximum)
        if generator.kind == GeneratorKind.SEQUENCE:
            if not generator.sequence:
                return generator.initial
            return generator.sequence[index % len(generator.sequence)]
        phase = (elapsed % generator.period_seconds) / generator.period_seconds
        if generator.kind == GeneratorKind.RAMP:
            return generator.minimum + (generator.maximum - generator.minimum) * phase
        midpoint = (generator.minimum + generator.maximum) / 2
        amplitude = (generator.maximum - generator.minimum) / 2
        return midpoint + amplitude * math.sin(2 * math.pi * phase)

    @staticmethod
    def _encode_value(value: float, data_type: SignalDataType) -> bytes:
        formats = {
            SignalDataType.BOOLEAN: "!?",
            SignalDataType.UINT8: "!B",
            SignalDataType.UINT16: "!H",
            SignalDataType.UINT32: "!I",
            SignalDataType.UINT64: "!Q",
            SignalDataType.INT8: "!b",
            SignalDataType.INT16: "!h",
            SignalDataType.INT32: "!i",
            SignalDataType.INT64: "!q",
            SignalDataType.FLOAT32: "!f",
            SignalDataType.FLOAT64: "!d",
        }
        if data_type == SignalDataType.STRING:
            encoded = str(value).encode("utf-8")
            return struct.pack("!I", len(encoded)) + encoded
        if data_type == SignalDataType.BYTES:
            return bytes([max(0, min(255, int(value)))])
        fmt = formats[data_type]
        typed_value: bool | int | float
        if data_type == SignalDataType.BOOLEAN:
            typed_value = bool(value)
        elif data_type in {SignalDataType.FLOAT32, SignalDataType.FLOAT64}:
            typed_value = float(value)
        else:
            typed_value = int(value)
        try:
            return struct.pack(fmt, typed_value)
        except struct.error as exc:
            raise ValueError(f"信号值 {typed_value} 超出 {data_type.value} 范围") from exc

    def _is_destination_allowed(self, host: str, port: int) -> bool:
        if not self._settings.network_send_enabled:
            return False
        allowed = set(self._settings.allowed_destinations)
        return host in allowed or f"{host}:{port}" in allowed

    def _build_offer(self, config: SimulationConfig, *, ttl: int) -> tuple[SomeIpMessage, str]:
        self._session_id = (self._session_id % 0xFFFF) + 1
        payload = SdPayload(
            flags=0xC0,
            entries=(
                SdEntry(
                    entry_type=0x01,
                    index_first_option=0,
                    index_second_option=0,
                    number_first_options=0,
                    number_second_options=0,
                    service_id=config.service_id,
                    instance_id=config.instance_id,
                    major_version=config.interface_version,
                    ttl=ttl,
                    minor_version=0,
                ),
            ),
        )
        return payload.to_someip(session_id=self._session_id), payload.summary()
