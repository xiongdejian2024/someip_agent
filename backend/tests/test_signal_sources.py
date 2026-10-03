"""真实原生激励的整数链路与每任务随机复现，不用替身发包。"""

import asyncio
import json
import time

import pytest
from pydantic import ValidationError

from someip_agent.config import Settings
from someip_agent.domain.models import SignalGeneratorConfig, SimulationConfig
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.runtime.simulator import SimulationManager
from someip_agent.soa.operator import NativeOperationError


def test_generator_json_preserves_integer_values_and_seed():
    config = SignalGeneratorConfig(
        data_type="uint64",
        kind="sequence",
        initial=18446744073709551615,
        maximum=18446744073709551615,
        sequence=[18446744073709551615, 18446744073709551614],
        seed=18446744073709551615,
    )
    raw = json.loads(config.model_dump_json())
    assert type(raw["initial"]) is int and raw["initial"] == 18446744073709551615
    assert raw["sequence"] == [18446744073709551615, 18446744073709551614]
    assert type(raw["seed"]) is int and raw["seed"] == 18446744073709551615


def test_scalar_step_configuration_keeps_exact_time_and_values():
    config = SignalGeneratorConfig(
        kind="step",
        data_type="uint64",
        initial=18446744073709551615,
        step_at_ms=18446744073709551615,
        step_value=18446744073709551614,
    )
    encoded = json.loads(config.model_dump_json())
    assert type(encoded["step_at_ms"]) is int and encoded["step_at_ms"] == 18446744073709551615
    assert type(encoded["step_value"]) is int and encoded["step_value"] == 18446744073709551614
    for arguments in (
        {"kind": "step", "step_at_ms": 1},
        {"kind": "step", "step_at_ms": True, "step_value": 1},
        {"kind": "constant", "step_value": 1},
    ):
        with pytest.raises(ValidationError):
            SignalGeneratorConfig.model_validate(arguments)


@pytest.mark.parametrize(
    "values",
    [
        {"seed": True},
        {"seed": -1},
        {"seed": 1.5},
        {"seed": 18446744073709551616},
        {"initial": True},
        {"initial": "18446744073709551615"},
        {"maximum": float("inf")},
        {"period_seconds": float("inf")},
        {"sequence": [float("nan")]},
        {"arbitrary": "script"},
    ],
)
def test_generator_rejects_ambiguous_or_unbounded_values(values):
    with pytest.raises(ValidationError):
        SignalGeneratorConfig(**values)


async def samples(tmp_path, binary, config, count=8):
    monitor = MonitorStore()
    manager = SimulationManager(
        monitor, Settings(_env_file=None, data_dir=tmp_path, native_binary=binary)
    )
    task = await manager.start(
        SimulationConfig(
            service_id=0x1234,
            method_id=0x8001,
            interval_ms=10,
            enable_sd=False,
            generator=config,
        )
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            messages = await monitor.list()
            if len(messages) >= count:
                return messages[:count]
            await asyncio.sleep(0.01)
        pytest.fail("原生发生器未交付足够样本")
    finally:
        await manager.stop(task.id)
        assert not manager._sessions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_type,value,golden",
    [
        ("uint64", 18446744073709551615, "ffffffffffffffff"),
        ("int64", -9223372036854775808, "8000000000000000"),
        ("uint64", 18446744073709551614, "fffffffffffffffe"),
        ("uint64", 9007199254740993, "0020000000000001"),
    ],
)
async def test_native_constant_exact_integer_trace_and_payload(
    tmp_path, native_runtime, data_type, value, golden
):
    messages = await samples(
        tmp_path,
        native_runtime,
        SignalGeneratorConfig(
            data_type=data_type,
            kind="constant",
            initial=value,
        ),
    )
    assert all(
        type(item.signal_values["value"]) is int and item.signal_values["value"] == value
        for item in messages
    )
    assert all(
        item.payload_hex == golden and not item.metadata["wire_verified"] for item in messages
    )


@pytest.mark.asyncio
async def test_native_scalar_step_uses_elapsed_clock_and_keeps_u64(tmp_path, native_runtime):
    messages = await samples(
        tmp_path,
        native_runtime,
        SignalGeneratorConfig(
            kind="step",
            data_type="uint64",
            initial=18446744073709551615,
            step_at_ms=29,
            step_value=18446744073709551614,
        ),
    )
    actual = [message.signal_values["value"] for message in messages]
    assert actual[0] == 18446744073709551615 and actual[-1] == 18446744073709551614
    after = False
    for message, value in zip(messages, actual, strict=True):
        if value == 18446744073709551614:
            after = True
            assert message.payload_hex == "fffffffffffffffe"
        else:
            assert not after and value == 18446744073709551615
            assert message.payload_hex == "ffffffffffffffff"


@pytest.mark.asyncio
async def test_native_integer_sequence_keeps_all_bits(tmp_path, native_runtime):
    values = [18446744073709551615, 18446744073709551614, 0]
    messages = await samples(
        tmp_path,
        native_runtime,
        SignalGeneratorConfig(
            data_type="uint64",
            kind="sequence",
            sequence=values,
        ),
    )
    assert [item.signal_values["value"] for item in messages] == [values[i % 3] for i in range(8)]
    assert [item.payload_hex for item in messages[:3]] == [
        "ffffffffffffffff",
        "fffffffffffffffe",
        "0000000000000000",
    ]


@pytest.mark.asyncio
async def test_float32_observation_is_actual_encoded_value(tmp_path, native_runtime):
    messages = await samples(
        tmp_path,
        native_runtime,
        SignalGeneratorConfig(
            data_type="float32",
            kind="constant",
            initial=0.1,
        ),
    )
    assert all(item.payload_hex == "3dcccccd" for item in messages)
    assert all(
        type(item.signal_values["value"]) is float
        and item.signal_values["value"] == 0.10000000149011612
        for item in messages
    )


@pytest.mark.asyncio
async def test_native_random_restarts_per_task_seed(tmp_path, native_runtime):
    async def seeded(seed):
        return [
            item.signal_values["value"]
            for item in await samples(
                tmp_path,
                native_runtime,
                SignalGeneratorConfig(
                    data_type="uint64",
                    kind="random",
                    minimum=0,
                    maximum=18446744073709551615,
                    seed=seed,
                ),
            )
        ]

    first, second, different = await seeded(123), await seeded(123), await seeded(124)
    assert first == second and first != different
    assert all(type(value) is int and 0 <= value <= 18446744073709551615 for value in first)


@pytest.mark.asyncio
async def test_native_boolean_random_is_discrete_reproducible_and_typed(tmp_path, native_runtime):
    config = SignalGeneratorConfig(
        data_type="boolean", kind="random", minimum=0, maximum=1, seed=42
    )
    first = await samples(tmp_path, native_runtime, config, count=32)
    second = await samples(tmp_path, native_runtime, config, count=32)
    values = [message.signal_values["value"] for message in first]
    assert set(values) == {False, True}
    assert all(type(value) is bool for value in values)
    assert values == [message.signal_values["value"] for message in second]
    assert [message.payload_hex for message in first] == [
        "01" if value else "00" for value in values
    ]


@pytest.mark.asyncio
async def test_native_invalid_replacement_keeps_existing_source(tmp_path, native_runtime):
    monitor = MonitorStore()
    manager = SimulationManager(
        monitor, Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    )
    task = await manager.start(
        SimulationConfig(
            service_id=0x1234,
            method_id=0x8001,
            interval_ms=10,
            generator=SignalGeneratorConfig(
                data_type="uint64", kind="constant", initial=18446744073709551615
            ),
        )
    )
    try:
        operator = manager._sessions[task.id].operator
        before = await asyncio.to_thread(operator.send_request, "running_service")
        invalid = {
            "member": "Simulation_server",
            "function": "UpdateSampleEvent",
            "interval_ms": 10,
            "generator": {
                "kind": "sequence",
                "signal_name": "value",
                "data_type": "uint64",
                "sequence": [1, -1],
            },
        }
        with pytest.raises(NativeOperationError, match="负"):
            await asyncio.to_thread(operator.send_request, "generator_start", invalid)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            after = await asyncio.to_thread(operator.send_request, "running_service")
            if (
                after["Simulation_server"]["emitted_count"]
                >= before["Simulation_server"]["emitted_count"] + 3
            ):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("非法替换破坏既有周期任务")
        assert after["Simulation_server"]["last_value"] == 18446744073709551615
        assert all(item.payload_hex == "ffffffffffffffff" for item in await monitor.list())
    finally:
        await manager.stop(task.id)
        assert not manager._sessions
