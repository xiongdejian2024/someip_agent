"""vsomeip 原生仿真生命周期：Python 仅控制，不再逐周期构包或发包。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from someip_agent.config import Settings
from someip_agent.domain.models import MonitorMessage, SimulationConfig, SimulationStatus
from someip_agent.soa.ipc import read_frame
from someip_agent.soa.operator import NativeRuntimeError, SOAOperator

from .monitor import MonitorStore
from .native_config import SimulationPermissionError as SimulationPermissionError
from .native_config import validate_network, write_inputs
from .network_gate import NetworkTaskGate

logger = logging.getLogger(__name__)


@dataclass
class NativeSession:
    operator: SOAOperator
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    task: asyncio.Task[None] | None = None


class SimulationManager:
    def __init__(
        self, monitor: MonitorStore, settings: Settings, gate: NetworkTaskGate | None = None
    ) -> None:
        self._monitor = monitor
        self._settings = settings
        self._network_gate = gate or NetworkTaskGate()
        self._statuses: dict[str, SimulationStatus] = {}
        self._sessions: dict[str, NativeSession] = {}
        self._lifecycle = asyncio.Lock()

    async def start(self, config: SimulationConfig) -> SimulationStatus:
        with self._network_gate.task_operation():
            return await self._start(config)

    def has_network_resources(self) -> bool:
        return bool(self._sessions) or self._lifecycle.locked()

    async def _start(self, config: SimulationConfig) -> SimulationStatus:
        validate_network(config, self._settings)
        async with self._lifecycle:
            identifier = str(uuid4())
            directory = self._settings.data_dir / "native" / identifier
            catalog, native_config, name = write_inputs(
                directory, identifier, config, self._settings
            )
            operator = SOAOperator(
                name,
                operator_port=0,
                binary=self._settings.native_binary,
                catalog=catalog,
                config=native_config,
                log_path=directory / "native.log",
            )
            status = SimulationStatus(
                id=identifier,
                config=config,
                running=False,
                started_at=datetime.now(timezone.utc),
            )
            session = None
            try:
                await asyncio.to_thread(operator.run_operator)
                await asyncio.to_thread(operator.create_socket)
                await asyncio.to_thread(
                    operator.send_request,
                    "start_config",
                    {
                        "Simulation": {"role": "server", "name": config.name},
                    },
                )
                reader, writer = await asyncio.open_connection(
                    operator.host, operator.operator_port
                )
                body = json.dumps({"function": "monitor"}).encode()
                writer.write(f"{len(body):08x}".encode() + body)
                await writer.drain()
                response = await asyncio.wait_for(read_frame(reader), 10)
                if response.get("failtype") != "FAILTYPE_SUCCESS":
                    raise NativeRuntimeError("原生监控订阅失败")
                session = NativeSession(operator, reader, writer)
                self._statuses[identifier] = status
                self._sessions[identifier] = session
                session.task = asyncio.create_task(self._read_monitor(identifier, session))
                await asyncio.to_thread(
                    operator.send_request,
                    "generator_start",
                    {
                        "member": "Simulation_server",
                        "function": "UpdateSampleEvent",
                        "interval_ms": config.interval_ms,
                        "generator": config.generator.model_dump(mode="json"),
                    },
                )
                if session.task.done():
                    raise NativeRuntimeError(status.last_error or "原生监控在启动期间断开")
                status.running = True
                logger.info(
                    "vsomeip 原生仿真已启动",
                    extra={"operation": "simulation.start", "simulation_id": identifier},
                )
                return status.model_copy(deep=True)
            except BaseException:
                logger.exception(
                    "原生仿真启动失败",
                    extra={"operation": "simulation.start", "simulation_id": identifier},
                )
                self._sessions.pop(identifier, None)
                self._statuses.pop(identifier, None)
                if session:
                    await self._close_session(session)
                else:
                    await asyncio.to_thread(operator.stop_operator)
                raise

    async def _read_monitor(self, identifier: str, session: NativeSession) -> None:
        status = self._statuses[identifier]
        try:
            while True:
                message = await read_frame(session.reader)
                if message.get("action") == "error":
                    raise NativeRuntimeError(str(message.get("error", "原生发生器失败")))
                if message.get("action") != "trace":
                    continue
                config = status.config
                status.emitted_count = int(message["emitted_count"])
                status.last_value = message["last_value"]
                internal = config.transport == "internal"
                await self._monitor.publish(
                    MonitorMessage(
                        direction="sim" if internal else message["direction"],
                        transport=config.transport,
                        source="vsomeip",
                        destination="本地原生协议栈" if internal else "授权的事件订阅者",
                        service_id=message["service_id"],
                        method_id=message["method_id"],
                        client_id=message["client_id"],
                        session_id=message["session_id"],
                        interface_version=message["interface_version"],
                        message_type=message["message_type"],
                        return_code=message["return_code"],
                        payload_hex=message["payload_hex"],
                        payload_size=len(message["payload_hex"]) // 2,
                        signal_values={config.generator.signal_name: status.last_value},
                        metadata={
                            "simulation_id": identifier,
                            "runtime": "vsomeip",
                            "native_monotonic_ns": message["native_monotonic_ns"],
                            "observation": message["observation"],
                            "active_states": message.get("active_states", {}),
                            "wire_verified": False,
                            "note": "协议栈 API 观测，不代替线上抓包；内部模式不发送 SD 组播",
                        },
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "原生仿真监控断开",
                extra={"operation": "simulation.monitor", "simulation_id": identifier},
            )
            await asyncio.to_thread(session.operator.stop_operator)
        finally:
            status.running = False

    async def _close_session(self, session: NativeSession) -> None:
        if session.task:
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        session.writer.close()
        try:
            await session.writer.wait_closed()
        finally:
            await asyncio.to_thread(session.operator.stop_operator)

    async def stop(self, simulation_id: str | None = None) -> list[SimulationStatus]:
        with self._network_gate.task_operation():
            return await self._stop(simulation_id)

    async def _stop(self, simulation_id: str | None = None) -> list[SimulationStatus]:
        async with self._lifecycle:
            identifiers = [simulation_id] if simulation_id else list(self._sessions)
            stopped = []
            for identifier in identifiers:
                session = self._sessions.pop(identifier, None)
                if session:
                    try:
                        await asyncio.to_thread(
                            session.operator.send_request,
                            "generator_stop",
                            {"member": "Simulation_server"},
                        )
                    except NativeRuntimeError as exc:
                        logger.exception(
                            "停止仿真时原生进程已不可用",
                            extra={"operation": "simulation.stop", "simulation_id": identifier},
                        )
                        if not self._statuses[identifier].last_error:
                            self._statuses[identifier].last_error = f"{type(exc).__name__}: {exc}"
                    finally:
                        await self._close_session(session)
                status = self._statuses.get(identifier)
                if status:
                    status.running = False
                    stopped.append(status.model_copy(deep=True))
            if stopped:
                logger.info("原生仿真已停止", extra={"operation": "simulation.stop"})
            return stopped

    def list(self) -> list[SimulationStatus]:
        return [status.model_copy(deep=True) for status in self._statuses.values()]

    async def shutdown(self) -> None:
        await self.stop()
