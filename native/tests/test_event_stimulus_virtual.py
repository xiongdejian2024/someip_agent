"""复用真实 ARXML/veth 初始化与清理，新增完整动态事件黄金接收专项。"""

import json
import threading
import time
from pathlib import Path

import pytest
from test_arxml_composite_virtual import (
    test_arxml_nested_structure_fixed_and_bounded_arrays_over_veth as run_composite,
)

GOLDENS = {
    "big": [
        "001b0700041234abcd00020102000a000301020300030405060002fffe",
        "001b0800041234000200020102000a0003010203000304050600020003",
    ],
    "little": [
        "1b000704003412cdab020001020a00030001020303000405060200feff",
        "1b0008040034120200020001020a000300010203030004050602000300",
    ],
}
STEP_GOLDENS = {
    "big": [
        "001b0900041234abcd00020102000a000301020300030405060002fffd",
        "001b0a00041234abcd00020102000a0003010203000304050600020004",
    ],
    "little": [
        "1b000904003412cdab020001020a00030001020303000405060200fdff",
        "1b000a04003412cdab020001020a000300010203030004050602000400",
    ],
}


def assert_dynamic_receive(server, client, value, order, bundle):
    assert bundle.catalog["EnvelopeService"]["events"]["UpdateEnvelopeChangedEvent"][
        "eventgroups"
    ] == [7]
    received, arrived = [], threading.Event()

    def receive(_key, message):
        if (
            message.get("action") == "event"
            and message.get("function") == "UpdateEnvelopeChangedEvent"
        ):
            received.append(message)
            if len(received) >= 8:
                arrived.set()

    client.register_callback("EnvelopeService_client", receive)
    try:
        server.send_event_notify_thread_start(
            "EnvelopeService_server",
            "EnvelopeChanged",
            value,
            0.02,
            sources=[
                {"path": "/tag", "generator": {"kind": "sequence", "sequence": [7, 8]}},
                {
                    "path": "/samples/1",
                    "generator": {"kind": "sequence", "sequence": [0xABCD, 2]},
                },
                {
                    "path": "/nested/temperature",
                    "generator": {"kind": "sequence", "sequence": [-2, 3]},
                },
            ],
        )
        assert arrived.wait(3), "真实 UDP/TCP 消费者未交付八个完整动态事件"
        server.send_event_notify_thread_stop("EnvelopeService_server")
        samples = received[:8]
        assert {message["payload_hex"] for message in samples} == set(GOLDENS[order])
        for message in samples:
            actual = json.loads(message["args"])
            phase = actual["tag"] - 7
            assert actual["samples"] == [0x1234, [0xABCD, 2][phase]]
            assert actual["nested"]["temperature"] == [-2, 3][phase]
            assert (
                actual["matrix"] == value["matrix"]
                and actual["bytes"] == value["bytes"]
            )
            assert message["payload_hex"] == GOLDENS[order][phase]
        native = server.sim_operator.send_request("running_service")[
            "EnvelopeService_server"
        ]
        assert not native["event_cycle_running"] and native["event_source_count"] == 0
        count = native["event_cycle_count"]
        time.sleep(0.08)
        assert (
            server.sim_operator.send_request("running_service")[
                "EnvelopeService_server"
            ]["event_cycle_count"]
            == count
        )
    finally:
        client.unregister_callback("EnvelopeService_client", receive)


@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("order", ["big", "little"])
def test_full_arxml_dynamic_event_actual_udp_tcp_and_cleanup(transport, order):
    run_composite(transport, order, 2, 2, stimulus_check=assert_dynamic_receive)


def assert_step_receive(server, client, value, order, bundle):
    assert bundle.catalog["EnvelopeService"]["events"]["UpdateEnvelopeChangedEvent"][
        "eventgroups"
    ] == [7]
    received, arrived = [], threading.Event()

    def receive(_key, message):
        if (
            message.get("action") == "event"
            and message.get("function") == "UpdateEnvelopeChangedEvent"
        ):
            actual = json.loads(message["args"])
            # 与初始化/固定周期报文分开，只收本次阶跃的两种 tag。
            if actual["tag"] in (9, 10):
                received.append(message)
                if len(received) >= 8:
                    arrived.set()

    client.register_callback("EnvelopeService_client", receive)
    try:
        server.send_event_notify_thread_start(
            "EnvelopeService_server",
            "EnvelopeChanged",
            value,
            0.02,
            sources=[
                {
                    "path": "/tag",
                    "generator": {
                        "kind": "step",
                        "initial": 9,
                        "step_at_ms": 29,
                        "step_value": 10,
                    },
                },
                {
                    "path": "/nested/temperature",
                    "generator": {
                        "kind": "step",
                        "initial": -3,
                        "step_at_ms": 29,
                        "step_value": 4,
                    },
                },
            ],
        )
        assert arrived.wait(3), "真实 UDP/TCP 未交付完整阶跃事件"
        server.send_event_notify_thread_stop("EnvelopeService_server")
        for index, message in enumerate(received[:8]):
            phase = int(index * 20 >= 29)
            actual = json.loads(message["args"])
            assert actual["tag"] == [9, 10][phase]
            assert actual["nested"]["temperature"] == [-3, 4][phase]
            assert (
                actual["samples"] == value["samples"]
                and actual["matrix"] == value["matrix"]
            )
            assert message["payload_hex"] == STEP_GOLDENS[order][phase]
        native = server.sim_operator.send_request("running_service")[
            "EnvelopeService_server"
        ]
        assert not native["event_cycle_running"] and native["event_source_count"] == 0
        count = native["event_cycle_count"]
        time.sleep(0.06)
        assert (
            server.sim_operator.send_request("running_service")[
                "EnvelopeService_server"
            ]["event_cycle_count"]
            == count
        )
    finally:
        client.unregister_callback("EnvelopeService_client", receive)


@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("order", ["big", "little"])
def test_full_arxml_step_at_29ms_actual_udp_tcp_and_cleanup(transport, order):
    run_composite(transport, order, 2, 2, stimulus_check=assert_step_receive)


CSV_GOLDENS = {
    "big": [
        STEP_GOLDENS["big"][0],
        "001b0a00041234000200020102000a0003010203000304050600020004",
    ],
    "little": [
        STEP_GOLDENS["little"][0],
        "1b000a040034120200020001020a000300010203030004050602000400",
    ],
}


def assert_csv_receive(server, client, value, order, bundle):
    assert bundle.catalog["EnvelopeService"]["events"]["UpdateEnvelopeChangedEvent"][
        "eventgroups"
    ] == [7]
    received, arrived = [], threading.Event()

    def receive(_key, message):
        if (
            message.get("action") == "event"
            and message.get("function") == "UpdateEnvelopeChangedEvent"
        ):
            actual = json.loads(message["args"])
            if actual["tag"] in (9, 10):
                received.append(message)
                if len(received) >= 8:
                    arrived.set()

    client.register_callback("EnvelopeService_client", receive)
    try:
        text = (
            Path(__file__).resolve().parents[2]
            / "backend/tests/fixtures/stimulus_timeline.csv"
        ).read_text()
        server.send_event_notify_thread_start(
            "EnvelopeService_server", "EnvelopeChanged", value, 0.02, csv_text=text
        )
        assert arrived.wait(3), "真实 UDP/TCP 消费者未交付八个 CSV 完整事件"
        server.send_event_notify_thread_stop("EnvelopeService_server")
        for index, message in enumerate(received[:8]):
            phase = int(index * 20 >= 29)
            actual = json.loads(message["args"])
            assert actual["tag"] == [9, 10][phase]
            assert actual["samples"] == [0x1234, [0xABCD, 2][phase]]
            assert actual["nested"]["temperature"] == [-3, 4][phase]
            assert (
                actual["matrix"] == value["matrix"]
                and actual["bytes"] == value["bytes"]
            )
            assert message["payload_hex"] == CSV_GOLDENS[order][phase]
        native = server.sim_operator.send_request("running_service")[
            "EnvelopeService_server"
        ]
        assert not native["event_cycle_running"] and native["event_source_count"] == 0
        count = native["event_cycle_count"]
        time.sleep(0.06)
        assert (
            server.sim_operator.send_request("running_service")[
                "EnvelopeService_server"
            ]["event_cycle_count"]
            == count
        )
    finally:
        client.unregister_callback("EnvelopeService_client", receive)


@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("order", ["big", "little"])
def test_full_arxml_csv_actual_udp_tcp_and_cleanup(transport, order):
    run_composite(transport, order, 2, 2, stimulus_check=assert_csv_receive)
