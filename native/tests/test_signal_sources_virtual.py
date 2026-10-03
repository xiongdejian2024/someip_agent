"""独立 catalog 的原生激励线上专项，不混入旧固定黄金报文审计。"""

import json
import threading

import pytest
from test_virtual import partners as source_partners

# 复用已有 veth/进程归属与清理夹具，不另写一套初始化。
partners = source_partners


@pytest.mark.parametrize(
    "partners,data_type,value,golden",
    [
        ({"transport": transport, "signal_type": data_type}, data_type, value, golden)
        for transport in ("udp", "tcp")
        for data_type, value, golden in (
            ("uint64", 18446744073709551615, "ffffffffffffffff"),
            ("int64", -9223372036854775808, "8000000000000000"),
            ("float32", 0.1, "3dcccccd"),
        )
    ],
    indirect=["partners"],
)
def test_source_exact_value_actual_veth_receive_and_stop(
    partners, data_type, value, golden
):
    server, client, transport = partners
    received, arrived = [], threading.Event()

    def receive(_key, message):
        if (
            message.get("action") == "event"
            and message.get("function") == "UpdateSampleEvent"
        ):
            received.append(message)
            arrived.set()

    client.register_callback("DoorService_client", receive)
    try:
        server.sim_operator.send_request(
            "generator_start",
            {
                "member": "DoorService_server",
                "function": "UpdateSampleEvent",
                "interval_ms": 10,
                "generator": {
                    "kind": "constant",
                    "signal_name": "value",
                    "data_type": data_type,
                    "initial": value,
                    "seed": 123,
                },
            },
        )
        assert arrived.wait(3), f"{transport} 原生消费者未收到激励报文"
        message = received[0]
        assert message["payload_hex"] == golden
        actual = json.loads(message["args"])["value"]
        if data_type == "float32":
            assert type(actual) is float and actual == 0.10000000149011612
        else:
            assert type(actual) is int and actual == value
        with pytest.raises(RuntimeError, match="旧标量发生器仍在运行"):
            server.send_event_notify_thread_start(
                "DoorService_server", "Sample", {"value": value}, 0.02
            )
        server.sim_operator.send_request(
            "generator_stop", {"member": "DoorService_server"}
        )
        view = server.sim_operator.send_request("running_service")["DoorService_server"]
        assert view["last_value"] == actual and view["emitted_count"] >= 1
        server.send_event_notify_thread_start(
            "DoorService_server", "Sample", {"value": value}, 0.02
        )
        try:
            with pytest.raises(RuntimeError, match="完整事件周期仍在运行"):
                server.sim_operator.send_request(
                    "generator_start",
                    {
                        "member": "DoorService_server",
                        "function": "UpdateSampleEvent",
                        "generator": {
                            "kind": "constant",
                            "initial": value,
                            "data_type": data_type,
                        },
                    },
                )
        finally:
            server.send_event_notify_thread_stop("DoorService_server")
    finally:
        client.unregister_callback("DoorService_client", receive)


@pytest.mark.parametrize(
    "partners",
    [
        {"transport": transport, "signal_type": "boolean"}
        for transport in ("udp", "tcp")
    ],
    indirect=["partners"],
)
def test_boolean_random_actual_udp_tcp_is_discrete_and_stops(partners):
    server, client, _transport = partners
    received, arrived = [], threading.Event()

    def receive(_key, message):
        if (
            message.get("action") == "event"
            and message.get("function") == "UpdateSampleEvent"
        ):
            received.append(message)
            if len(received) >= 32:
                arrived.set()

    client.register_callback("DoorService_client", receive)
    try:
        server.sim_operator.send_request(
            "generator_start",
            {
                "member": "DoorService_server",
                "function": "UpdateSampleEvent",
                "interval_ms": 10,
                "generator": {
                    "kind": "random",
                    "data_type": "boolean",
                    "minimum": 0,
                    "maximum": 1,
                    "seed": 42,
                },
            },
        )
        assert arrived.wait(3), "布尔随机消费者未收到足够线上样本"
        values = [json.loads(message["args"])["value"] for message in received[:32]]
        assert set(values) == {False, True} and all(
            type(value) is bool for value in values
        )
        assert [message["payload_hex"] for message in received[:32]] == [
            "01" if value else "00" for value in values
        ]
        server.sim_operator.send_request(
            "generator_stop", {"member": "DoorService_server"}
        )
        view = server.sim_operator.send_request("running_service")["DoorService_server"]
        stopped_count = view["emitted_count"]
        assert stopped_count >= 32
        threading.Event().wait(0.05)
        assert (
            server.sim_operator.send_request("running_service")["DoorService_server"][
                "emitted_count"
            ]
            == stopped_count
        )
    finally:
        client.unregister_callback("DoorService_client", receive)


@pytest.mark.parametrize(
    "partners,before,after,goldens",
    [
        ({"transport": transport, "signal_type": data_type}, before, after, goldens)
        for transport in ("udp", "tcp")
        for data_type, before, after, goldens in (
            (
                "uint64",
                18446744073709551615,
                18446744073709551614,
                ("ffffffffffffffff", "fffffffffffffffe"),
            ),
            (
                "int64",
                -9223372036854775808,
                9223372036854775807,
                ("8000000000000000", "7fffffffffffffff"),
            ),
            ("float32", 0.1, 42.5, ("3dcccccd", "422a0000")),
        )
    ],
    indirect=["partners"],
)
def test_step_source_exact_typed_values_actual_veth_and_stop(
    partners, before, after, goldens
):
    server, client, _transport = partners
    received, arrived = [], threading.Event()

    def receive(_key, message):
        if (
            message.get("action") == "event"
            and message.get("function") == "UpdateSampleEvent"
        ):
            received.append(message)
            if len(received) >= 8:
                arrived.set()

    client.register_callback("DoorService_client", receive)
    try:
        server.send_event_notify_thread_start(
            "DoorService_server",
            "Sample",
            {"value": before},
            0.01,
            sources=[
                {
                    "path": "/value",
                    "generator": {
                        "kind": "step",
                        "initial": before,
                        "step_at_ms": 29,
                        "step_value": after,
                    },
                }
            ],
        )
        assert arrived.wait(3), "阶跃源消费者未收到八个线上事件"
        server.send_event_notify_thread_stop("DoorService_server")
        for index, message in enumerate(received[:8]):
            phase = int(index * 10 >= 29)
            assert message["payload_hex"] == goldens[phase]
            actual = json.loads(message["args"])["value"]
            if goldens[0] == "3dcccccd":
                assert actual == [0.10000000149011612, after][phase]
            else:
                assert type(actual) is int and actual == [before, after][phase]
        status = server.sim_operator.send_request("running_service")[
            "DoorService_server"
        ]
        assert not status["event_cycle_running"] and status["event_source_count"] == 0
    finally:
        client.unregister_callback("DoorService_client", receive)
