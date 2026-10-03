import asyncio

import pytest

from someip_agent.config import Settings
from someip_agent.domain.models import SignalDataType, SignalGeneratorConfig, SimulationConfig
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.simulator import SimulationManager, SimulationPermissionError
from someip_agent.soa.operator import NativeRuntimeError


@pytest.mark.asyncio
async def test_internal_simulation_publishes_samples(tmp_path, native_runtime) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    monitor = MonitorStore()
    manager = SimulationManager(monitor, settings)
    status = await manager.start(
        SimulationConfig(
            service_id=0x1234,
            method_id=0x8001,
            interval_ms=10,
            enable_sd=False,
            generator=SignalGeneratorConfig(data_type=SignalDataType.FLOAT32),
        )
    )
    await asyncio.sleep(0.04)
    await manager.stop(status.id)
    messages = await monitor.list()
    assert len(messages) >= 2
    assert messages[0].service_id == 0x1234
    assert "value" in messages[0].signal_values
    assert messages[0].source == "vsomeip"
    assert messages[0].metadata["runtime"] == "vsomeip"
    assert messages[0].metadata["wire_verified"] is False


@pytest.mark.asyncio
async def test_udp_is_disabled_by_default(tmp_path) -> None:
    manager = SimulationManager(MonitorStore(), Settings(_env_file=None, data_dir=tmp_path))
    with pytest.raises(SimulationPermissionError):
        await manager.start(
            SimulationConfig(
                service_id=1,
                method_id=2,
                transport="udp",
                destination_host="192.0.2.1",
            )
        )


@pytest.mark.asyncio
async def test_invalid_sequence_rejected_before_scheduling(tmp_path, native_runtime) -> None:
    manager = SimulationManager(MonitorStore(), Settings(_env_file=None, data_dir=tmp_path))
    with pytest.raises(NativeRuntimeError, match="超限"):
        await manager.start(
            SimulationConfig(
                service_id=0x1234,
                method_id=0x8001,
                interval_ms=10,
                generator=SignalGeneratorConfig(
                    kind="sequence", data_type="uint8", sequence=[1, 999]
                ),
            )
        )
    assert manager.list() == []
    assert manager._sessions == {}
