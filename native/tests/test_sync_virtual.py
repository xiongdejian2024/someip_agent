"""独立 veth 上同一真实 ARXML 服务两事件公共时钟及黄金接收。"""

import json
import threading
import time
from pathlib import Path

import pytest
from test_arxml_composite_virtual import (
    test_arxml_nested_structure_fixed_and_bounded_arrays_over_veth as run_composite,
)
from test_event_stimulus_virtual import STEP_GOLDENS


def assert_sync_receive(server, client, value, order, bundle):
    events = ("UpdateEnvelopeChangedEvent", "UpdateEnvelopeStateEvent")
    received = {event: [] for event in events}
    arrived = threading.Event()

    def receive(_key, message):
        function = message.get("function")
        if message.get("action") == "event" and function in received:
            actual = json.loads(message["args"])
            if actual["tag"] in (9, 10):
                received[function].append(message)
                if len(received[events[0]]) >= 4 and len(received[events[1]]) >= 3:
                    arrived.set()

    client.register_callback("EnvelopeService_client", receive)
    bindings = [
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
    ]
    try:
        assert bundle.catalog["EnvelopeService"]["events"][events[0]][
            "eventgroups"
        ] == [7]
        state = server.start_event_sync(
            [
                {
                    "member": "EnvelopeService_server",
                    "function": function,
                    "args": value,
                    "interval_ms": interval,
                    "sources": bindings,
                }
                for function, interval in zip(events, (20, 30))
            ]
        )
        assert (
            state["paused"] and state["interval_ms"] == 10 and state["frame_index"] == 0
        )
        for _ in range(7):
            state = server.control_event_sync("step")
        assert arrived.wait(3), "消费者没有实际交付同步单步的两个完整事件"
        assert state["logical_ms"] == 70 and state["frame_index"] == 7
        for function, timestamps in zip(events, ([0, 20, 40, 60], [0, 30, 60])):
            assert len(received[function]) == len(timestamps)
            for message, timestamp in zip(received[function], timestamps):
                phase = int(timestamp >= 29)
                assert message["payload_hex"] == STEP_GOLDENS[order][phase]
                actual = json.loads(message["args"])
                assert actual["tag"] == [9, 10][phase]
                assert actual["nested"]["temperature"] == [-3, 4][phase]
                assert (
                    actual["samples"] == value["samples"]
                    and actual["matrix"] == value["matrix"]
                )
        time.sleep(0.08)
        assert server.event_sync_status() == state, "单步后仍暂停，公共时钟不能自己继续"
        assert server.control_event_sync("speed", speed=2)["logical_ms"] == 70
        assert server.control_event_sync("resume")["active"]
        time.sleep(0.08)
        paused = server.control_event_sync("pause")
        assert paused["frame_index"] > 7
        time.sleep(0.08)
        assert server.event_sync_status() == paused
        evidence = {
            "paused": paused,
            "received": {
                key: [
                    {
                        "payload_hex": message["payload_hex"],
                        "args": json.loads(message["args"]),
                    }
                    for message in values
                ]
                for key, values in received.items()
            },
        }
        destination = (
            Path(__file__).resolve().parents[2]
            / "build/virtual-evidence"
            / f"sync-{order}-{bundle.members['EnvelopeService']['transport']}.json"
        )
        destination.write_text(
            json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
        )
        stopped = server.control_event_sync("stop")
        assert not stopped["active"] and all(
            event["source_count"] == 0 for event in stopped["events"]
        )
        time.sleep(0.06)
        assert server.event_sync_status() == stopped
        server.send_event_notify_thread_start(
            "EnvelopeService_server", "EnvelopeChanged", value, 0.02
        )
        server.send_event_notify_thread_stop("EnvelopeService_server")
    finally:
        client.unregister_callback("EnvelopeService_client", receive)
        server.control_event_sync("stop")


@pytest.mark.parametrize("transport", ["udp", "tcp"])
@pytest.mark.parametrize("order", ["big", "little"])
def test_same_service_events_actual_sync_clock_udp_tcp(transport, order):
    run_composite(transport, order, 2, 2, stimulus_check=assert_sync_receive)
