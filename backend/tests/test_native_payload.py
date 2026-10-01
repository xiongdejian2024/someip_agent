"""真实原生Codec/socket黄金字节与生命周期，控制契约单元测试单独标识。"""

import asyncio
import io
import socket
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import dpkt
import pytest

from someip_agent.agent.evidence import EvidenceTools, MessageTarget, SignalQuery
from someip_agent.config import Settings
from someip_agent.domain.models import (
    ArxmlModel,
    EventDefinition,
    ListenerConfig,
    MonitorMessage,
    ServiceDefinition,
    SignalDefinition,
)
from someip_agent.protocol.native_payload import NativePayloadError, NativeSignalDecoder
from someip_agent.runtime.monitor import MonitorStore
from someip_agent.soa.operator import NativeRuntimeError
from someip_agent.state import ApplicationState


def signal(name="值", kind="uint16", **kwargs):
    return SignalDefinition(name=name, data_type=kind, wire_schema={"type": kind}, **kwargs)


@pytest.fixture
def decoder(tmp_path, native_runtime):
    value = NativeSignalDecoder(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    )
    yield value
    value.close()
    assert value._operator is None


@pytest.mark.parametrize(
    "kind,payload,expected",
    [
        ("uint8", "ff", 255),
        ("int8", "ff", -1),
        ("uint16", "1234", 0x1234),
        ("int16", "ffff", -1),
        ("uint32", "ffffffff", 0xFFFFFFFF),
        ("int32", "ffffffff", -1),
        ("uint64", "ffffffffffffffff", 0xFFFFFFFFFFFFFFFF),
        ("int64", "ffffffffffffffff", -1),
        ("float32", "41480000", 12.5),
        ("float64", "4029000000000000", 12.5),
        ("boolean", "01", True),
        ("string", "00000006e4b8ade69687", "中文"),
        ("bytes", "000000030001ff", "0001ff"),
    ],
)
def test_native_scalar_golden_bytes(decoder, kind, payload, expected):
    result = decoder.decode(payload, [signal(kind=kind)])
    assert result == {"值": expected}
    if kind == "uint64":
        assert type(result["值"]) is int  # 默认比例不得把64位整数舍入为浮点。


def test_explicit_endian_scaling_and_nested_layout(decoder):
    small = signal(factor=0.5, offset=1)
    small.wire_schema = {"type": "uint16", "byte_order": "little"}
    assert decoder.decode("7800", [small]) == {"值": 61}
    nested = signal(kind="struct")
    nested.wire_schema = {
        "type": "struct",
        "length_bytes": 2,
        "fields": [
            {"name": "标记", "type": "uint8"},
            {
                "name": "列表",
                "type": "array",
                "length": 2,
                "length_bytes": 2,
                "element": {"type": "uint16"},
            },
        ],
    }
    assert decoder.decode("00070700041234abcd", [nested]) == {
        "值": {"标记": 7, "列表": [0x1234, 0xABCD]}
    }


def test_batch_reuses_process_and_retains_each_error(decoder):
    expected = ["0032"] * 70
    result = decoder.decode_many(expected, [signal()])
    assert result == [{"values": {"值": 50}}] * 70
    assert decoder._operator and decoder._operator.process
    process = decoder._operator.process
    result = decoder.decode_many(["ff", "0032", "003200", "zz", "0033"], [signal()])
    assert "error" in result[0] and "error" in result[2] and "error" in result[3]
    assert result[1] == {"values": {"值": 50}} and result[4] == {"values": {"值": 51}}
    assert decoder._operator.process is process
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert (
            list(executor.map(lambda _: decoder.decode("0032", [signal()]), range(8)))
            == [{"值": 50}] * 8
        )


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("boolean", "02"),
        ("float32", "7f800000"),
        ("float32", "7fc00000"),
        ("string", "00000001ff"),
        ("bytes", "0000000500"),
    ],
)
def test_invalid_scalar_does_not_become_null_or_guessed_value(decoder, kind, payload):
    with pytest.raises(NativePayloadError):
        decoder.decode(payload, [signal(kind=kind)])


def test_native_session_exit_recovery_and_closed_gate(decoder):
    decoder.decode("0032", [signal()])
    assert decoder._operator and decoder._operator.process
    process = decoder._operator.process
    process.terminate()
    process.wait(timeout=5)
    assert decoder.decode("0033", [signal()]) == {"值": 51}
    assert decoder._operator.process is not process
    decoder.close()
    decoder.close()
    with pytest.raises(NativeRuntimeError, match="已关闭"):
        decoder.decode("0032", [signal()])


def test_missing_layout_fails_before_start_and_never_calls_python(tmp_path, monkeypatch):
    value = NativeSignalDecoder(Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(
        "someip_agent.protocol.codec.SignalCodec.decode", lambda *_: pytest.fail("不得Python回退")
    )
    with pytest.raises(ValueError, match="缺少已解析类型"):
        value.decode("0032", [SignalDefinition(name="值", data_type="uint16")])
    assert value._operator is None
    value.close()


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"schema_version": True},
        {"schema_version": 1, "decoder": "python", "records": []},
        {"schema_version": 1, "decoder": "someip-agent-native-codec", "records": []},
        {
            "schema_version": 1,
            "decoder": "someip-agent-native-codec",
            "records": [{"values": {"错名": 50}}],
        },
        {"schema_version": 1, "decoder": "someip-agent-native-codec", "records": [{"error": ""}]},
    ],
)
def test_control_contract_rejects_bad_native_returns(tmp_path, monkeypatch, response):
    value = NativeSignalDecoder(Settings(_env_file=None, data_dir=tmp_path))
    operator = Mock()
    operator.send_request.return_value = response
    monkeypatch.setattr(value, "_runtime", lambda: operator)
    with pytest.raises(NativePayloadError):
        value.decode("0032", [signal()])


@pytest.mark.asyncio
async def test_monitor_and_agent_use_same_real_native_decoder(
    tmp_path, native_runtime, monkeypatch
):
    state = ApplicationState(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    )
    definition = signal("转速", factor=0.5)
    service = ServiceDefinition(
        name="源模型服务",
        service_id=0x1234,
        events=[EventDefinition(name="源模型事件", event_id=0x8001, signals=[definition])],
    )
    await state.set_arxml_model(ArxmlModel(source_name="人工黄金模型.arxml", services=[service]))
    monkeypatch.setattr(
        "someip_agent.protocol.codec.SignalCodec.decode", lambda *_: pytest.fail("生产解码不得回退")
    )
    try:
        message = MonitorMessage(
            service_id=0x1234, method_id=0x8001, payload_hex="0064", payload_size=2
        )
        enriched = await asyncio.to_thread(state.enrich_message, message)
        assert enriched.signal_values == {"转速": 50}
        assert enriched.metadata["signal_decoder"] == "someip-agent-native-codec"
        await state.monitor.publish(enriched)
        tools = state.agent._evidence
        diagnosed = await tools.analyze_message(MessageTarget(message_id=message.id))
        assert diagnosed["decoded_signals"] == {"转速": 50}
        for index, payload in enumerate(["0032", "ff", "0064"]):
            await state.monitor.publish(
                MonitorMessage(
                    id=f"原始-{index}",
                    service_id=0x1234,
                    method_id=0x8001,
                    payload_hex=payload,
                    payload_size=len(payload) // 2,
                )
            )
        statistics = await tools.analyze_signal(
            SignalQuery(service_id=0x1234, method_id=0x8001, signal_name="转速")
        )
        assert statistics["sample_count"] == 3 and statistics["decode_error_count"] == 1
        invalid = await asyncio.to_thread(
            state.enrich_message,
            MonitorMessage(
                service_id=0x1234, method_id=0x8001, payload_hex="ff", signal_values={"转速": 999}
            ),
        )
        assert invalid.payload_hex == "ff" and invalid.signal_values == {}
        assert "signal_decode_error" in invalid.metadata
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_agent_missing_native_configuration_does_not_fall_back():
    monitor = MonitorStore()
    service = ServiceDefinition(
        name="源模型服务",
        service_id=1,
        events=[EventDefinition(name="事件", event_id=0x8001, signals=[signal()])],
    )
    tools = EvidenceTools(monitor, lambda: [service.model_dump(mode="json")])
    message = MonitorMessage(service_id=1, method_id=0x8001, payload_hex="0032", payload_size=2)
    await monitor.publish(message)
    result = await tools.analyze_message(MessageTarget(message_id=message.id))
    assert result["decode_status"] == "error" and "decoded_signals" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["udp", "tcp"])
async def test_live_native_wire_to_enriched_monitor(
    tmp_path, native_runtime, monkeypatch, transport
):
    from test_network import free_port

    settings = Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    state = ApplicationState(settings)
    service = ServiceDefinition(
        name="源模型服务",
        service_id=0x1234,
        events=[EventDefinition(name="源事件", event_id=0x8001, signals=[signal()])],
    )
    await state.set_arxml_model(ArxmlModel(source_name="人工黄金模型.arxml", services=[service]))
    monkeypatch.setattr(
        "someip_agent.protocol.codec.SignalCodec.decode", lambda *_: pytest.fail("不得Python解码")
    )
    port = free_port(transport)
    try:
        await state.network.start(
            ListenerConfig(transport=transport, bind_host="127.0.0.1", port=port)
        )
        queue = await state.monitor.subscribe()
        # SOME/IP头及payload均为人工黄金字节，未调用产品编码器来生成expected。
        frames = [
            bytes.fromhex(text)
            for text in [
                "123480010000000a00000001010102000032",
                "12348001000000090000000101010200ff",
                "123480010000000a00000001010102000033",
            ]
        ]

        def send():
            with socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
            ) as peer:
                if transport == "tcp":
                    peer.connect(("127.0.0.1", port))
                for frame in frames:
                    if transport == "udp":
                        peer.sendto(frame, ("127.0.0.1", port))
                    else:
                        peer.sendall(frame)

        await asyncio.to_thread(send)
        for _ in range(3):
            await asyncio.wait_for(queue.get(), 5)
        await state.monitor.unsubscribe(queue)
        messages = await state.monitor.list()
        assert len(messages) == 3
        assert messages[0].signal_values == {"值": 50}
        assert messages[1].signal_values == {} and "signal_decode_error" in messages[1].metadata
        assert messages[2].signal_values == {"值": 51}
        assert messages[2].metadata["signal_decoder"] == "someip-agent-native-codec"
        assert state.network.list()[0].running
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_pcap_batch_uses_native_schema_and_keeps_partial_errors(tmp_path, native_runtime):
    from test_pcap import _ethernet_ipv4

    from someip_agent.pcap.importer import PcapImporter

    settings = Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    state = ApplicationState(settings)
    service = ServiceDefinition(
        name="源模型服务",
        service_id=0x1234,
        events=[EventDefinition(name="源事件", event_id=0x8001, signals=[signal()])],
    )
    await state.set_arxml_model(ArxmlModel(source_name="人工黄金模型.arxml", services=[service]))
    try:
        capture = io.BytesIO()
        writer = dpkt.pcap.Writer(capture)
        for index, raw in enumerate(
            [
                "123480010000000a00000001010102000032",
                "12348001000000090000000101010200ff",
                "123480010000000a00000001010102000033",
            ]
        ):
            packet = dpkt.udp.UDP(sport=30501, dport=30502, data=bytes.fromhex(raw))
            packet.ulen = len(packet)
            writer.writepkt(
                _ethernet_ipv4("192.168.1.10", "192.168.1.11", packet), ts=1700000000 + index
            )
        _, messages = await asyncio.to_thread(
            PcapImporter(settings).parse, capture.getvalue(), "golden.pcap"
        )
        enriched = await asyncio.to_thread(state.enrich_messages, messages)
        application = [m for m in enriched if m.service_id == 0x1234 and m.method_id == 0x8001]
        assert len(application) == 3
        assert application[0].signal_values == {"值": 50}
        assert (
            application[1].signal_values == {} and "signal_decode_error" in application[1].metadata
        )
        assert application[2].signal_values == {"值": 51}
        assert application[2].metadata["signal_decoder"] == "someip-agent-native-codec"
    finally:
        await state.shutdown()


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "uint16", "byte_order": "invalid"},
        {"type": "unknown"},
        {
            "type": "struct",
            "fields": [{"name": "重复", "type": "uint8"}, {"name": "重复", "type": "uint8"}],
        },
    ],
)
def test_native_schema_gate_rejects_unknown_or_ambiguous_layout(decoder, schema):
    definition = signal(kind="struct")
    definition.wire_schema = schema
    with pytest.raises(NativePayloadError):
        decoder.decode("0032", [definition])


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), [None]])
def test_control_return_values_must_be_finite_json(tmp_path, monkeypatch, value):
    decoder = NativeSignalDecoder(Settings(_env_file=None, data_dir=tmp_path))
    operator = Mock()
    operator.send_request.return_value = {
        "schema_version": 1,
        "decoder": "someip-agent-native-codec",
        "records": [{"values": {"值": value}}],
    }
    monkeypatch.setattr(decoder, "_runtime", lambda: operator)
    with pytest.raises(NativePayloadError, match="非有限值或null"):
        decoder.decode("0032", [signal()])


@pytest.mark.asyncio
async def test_ambiguous_service_and_missing_wire_layout_never_guess(tmp_path, monkeypatch):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    service = ServiceDefinition(
        name="源模型服务",
        service_id=1,
        events=[EventDefinition(name="事件", event_id=0x8001, signals=[signal()])],
    )
    decoder = Mock(spec=NativeSignalDecoder)
    monkeypatch.setattr(state, "signal_decoder", decoder)
    await state.set_arxml_model(
        ArxmlModel(source_name="重复部署.arxml", services=[service, service.model_copy(deep=True)])
    )
    try:
        message = state.enrich_message(
            MonitorMessage(service_id=1, method_id=0x8001, payload_hex="0032")
        )
        assert "多个部署" in message.metadata["signal_decode_error"]
        assert message.signal_values == {}
        decoder.decode_many.assert_not_called()
    finally:
        await state.shutdown()
