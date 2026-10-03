"""采用 SAT 的前置配置、调用、断言写法；所有通信经过 veth，与控制 socket 分离。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from soa_partner.src.base_partner import S2sBaseClass
from soa_partner.src.Operator import SOAOperator

ROOT = Path(__file__).parent
EVIDENCE = ROOT.parents[1] / "build" / "virtual-evidence"


@pytest.fixture(params=["udp", "tcp"])
def partners(request):
    options = (
        request.param
        if isinstance(request.param, dict)
        else {"transport": request.param}
    )
    transport = options["transport"]
    processes = []
    handles = []
    objects = []
    try:
        catalog_path = ROOT / "catalog.json"
        if "signal_type" in options:
            catalog = json.loads(catalog_path.read_text())
            catalog["DoorService"]["events"]["UpdateSampleEvent"]["schema"]["fields"][
                0
            ]["type"] = options["signal_type"]
            catalog_path = (
                EVIDENCE / f"signal-source-{options['signal_type']}-{transport}.json"
            )
            catalog_path.write_text(json.dumps(catalog))
        for node, address in (("server", "10.77.0.1"), ("client", "10.77.0.2")):
            config = json.loads((ROOT / f"{node}.json").read_text())
            for service in config.get("services", []):
                service.pop("unreliable" if transport == "tcp" else "reliable", None)
                for event in service.get("events", []):
                    event["is_reliable"] = transport == "tcp"
            stem = f"{node}-{transport}"
            if "signal_type" in options:
                stem = f"source-{options['signal_type']}-{stem}"
            config_path = EVIDENCE / f"{stem}-config.json"
            config_path.write_text(json.dumps(config))
            log = (EVIDENCE / f"{stem}.log").open("ab")
            handles.append(log)
            process = subprocess.Popen(
                [
                    "ip",
                    "netns",
                    "exec",
                    f"soa-{node}",
                    os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                    "run",
                    "--name",
                    node,
                    "-p",
                    "16789",
                    "--bind",
                    address,
                    "--catalog",
                    str(catalog_path),
                    "--config",
                    str(config_path),
                ],
                stdout=log,
                stderr=log,
            )
            processes.append(process)
            operator = SOAOperator(node, host=address)
            cfg = {
                "DoorService": {
                    "role": node,
                    "name": "DoorService",
                    "transport": transport,
                }
            }
            partner = S2sBaseClass(cfg, operator=operator, attach=True)
            objects.append(partner)
        server, client = objects
        assert client.wait_for_service_reconnect("DoorService_client", timeout=10)
        # 订阅完成与服务可用是两个阶段，等待真实 SD 握手稳定。
        time.sleep(0.3)
        yield server, client, transport
    finally:
        for obj in objects:
            obj.close()
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for handle in handles:
            handle.close()


def test_sat_method_request_response_and_field(partners):
    server, client, _ = partners
    position = {"position": 0}

    def on_request(key, msg):
        if msg["action"] != "request":
            return
        if msg["function"] == "SetPosition":
            position.update(json.loads(msg["args"]))
            server.send_event_notify(key, "Position", position)
        server.send_method_response(
            key, msg["function"], position, request_id=msg["request_id"]
        )

    server.register_callback("DoorService_server", on_request)
    assert client.send_request_and_ck_resp(
        "DoorService_client",
        "SetPosition",
        {"position": 240},
        {"out": {"position": 240}},
        timeout=5,
    )
    assert client.ck_s2s_event("DoorService_client", "Position", {"position": 240})
    assert client.send_request_and_return_resp(
        "DoorService_client", "GetPosition", {}, timeout=5
    ) == {"out": {"position": 240}}
    assert server.ck_s2s_req("DoorService_server", "SetPosition", {"position": 240})


def test_sat_struct_array_string_little_endian_and_async(partners):
    server, client, _ = partners
    server.register_auto_response("DoorService_server", "Echo", echo=True)
    args = {"label": '中文 }{ \\"', "values": [-1, 0, 0x7FFFFFFF], "reading": 12.75}
    assert client.send_request_and_return_resp(
        "DoorService_client", "Echo", args, timeout=5, is_async=True
    ) == {"out": args}


def test_timeout_and_invalid_input(partners):
    _, client, _ = partners
    with pytest.raises(TimeoutError):
        client.send_request_and_return_resp(
            "DoorService_client", "GetPosition", {}, timeout=0.2
        )
    with pytest.raises(RuntimeError, match="超限"):
        client.send_request_and_return_resp(
            "DoorService_client", "SetPosition", {"position": 65536}, timeout=1
        )
    # 错误请求之后仍可正常调用，不能让 socket 或原生进程失效。
    assert client.sim_operator.send_request("ping")["runtime"] == "vsomeip"


def test_fire_and_forget(partners):
    server, client, _ = partners
    client.send_method_request("DoorService_client", "FireAndForget", {"position": 8})
    assert server.ck_s2s_req(
        "DoorService_server", "FireAndForget", {"position": 8}, timeout=5
    )
    assert (
        "FireAndForget" in client.partner_infos["DoorService_client"].no_return_methods
    )
    assert not client._method_timing._pending
    client.ck_method_timeout()


def test_sat_default_timeout_audits_late_response_but_not_async(partners):
    server, client, _ = partners
    client.method_default_timeout = 0.01
    tasks = []

    def respond(key, message):
        if message["action"] != "request":
            return
        timer = threading.Timer(
            0.06,
            server.send_method_response,
            args=(key, message["function"], {"position": 17}),
            kwargs={"request_id": message["request_id"]},
        )
        tasks.append(timer)
        timer.start()

    server.register_callback("DoorService_server", respond)
    try:
        correlation = client.send_method_request(
            "DoorService_client", "GetPosition", {}
        )
        response = client.partner_infos["DoorService_client"].resp_queue.get(timeout=2)
        assert (
            response["correlation_id"] == correlation
            and response["failtype"] == "FAILTYPE_SUCCESS"
        )
        with pytest.raises(AssertionError, match="GetPosition"):
            client.ck_method_timeout()
        assert len(client.method_is_timeout) == 1
        client.method_is_timeout = []
        assert client.send_request_and_return_resp(
            "DoorService_client", "GetPosition", {}, timeout=1, is_async=True
        ) == {"out": {"position": 17}}
        client.ck_method_timeout()
        info = client.partner_infos["DoorService_client"]
        assert (
            info.instance == "DoorService"
            and info.start_args["DoorService"]["role"] == "client"
        )
    finally:
        for task in tasks:
            task.join(timeout=2)


def test_sat_concurrent_same_method_timeout_keeps_request_identity(partners):
    server, client, _ = partners
    with ThreadPoolExecutor(max_workers=2) as callbacks:

        def respond(key, message):
            if message["action"] != "request":
                return
            args = json.loads(message["args"])

            def later():
                time.sleep(0.15 if args["position"] == 99 else 0.01)
                server.send_method_response(
                    key, message["function"], args, request_id=message["request_id"]
                )

            callbacks.submit(later)

        server.register_callback("DoorService_server", respond)
        with ThreadPoolExecutor(max_workers=2) as calls:
            slow = calls.submit(
                client.send_request_and_return_resp,
                "DoorService_client",
                "SetPosition",
                {"position": 99},
                0.05,
            )
            fast = calls.submit(
                client.send_request_and_return_resp,
                "DoorService_client",
                "SetPosition",
                {"position": 7},
                1,
            )
            assert fast.result(timeout=2) == {"out": {"position": 7}}
            with pytest.raises(TimeoutError):
                slow.result(timeout=2)
        with pytest.raises(AssertionError, match="SetPosition"):
            client.ck_method_timeout()
        assert len(client.method_is_timeout) == 1
    client.method_is_timeout = []
    client.ck_method_timeout()


def test_no_event_cannot_pass_after_member_socket_disconnect(partners):
    _, client, _ = partners
    info = client.partner_infos["DoorService_client"]
    timer = threading.Timer(0.03, info.socket.shutdown, args=(socket.SHUT_RDWR,))
    timer.start()
    try:
        with pytest.raises(RuntimeError, match="断开"):
            client.ck_no_event("DoorService_client", "NeverEmitted", timeout=0.5)
    finally:
        timer.join(timeout=1)


def test_native_periodic_generator_and_stop(partners):
    server, client, _ = partners
    server.sim_operator.send_request(
        "generator_start",
        {
            "member": "DoorService_server",
            "function": "UpdateSampleEvent",
            "interval_ms": 10,
            "generator": {
                "kind": "constant",
                "signal_name": "value",
                "data_type": "float32",
                "initial": 42.5,
            },
        },
    )
    assert client.ck_s2s_event("DoorService_client", "Sample", {"value": 42.5})
    time.sleep(0.15)
    server.sim_operator.send_request("generator_stop", {"member": "DoorService_server"})
    state = server.sim_operator.send_request("running_service")["DoorService_server"]
    assert state["emitted_count"] >= 10
    count = state["emitted_count"]
    time.sleep(0.05)
    assert (
        server.sim_operator.send_request("running_service")["DoorService_server"][
            "emitted_count"
        ]
        == count
    )


@pytest.mark.parametrize("sequence", [[], None])
def test_native_sequence_empty_or_absent_retains_initial_and_stops(partners, sequence):
    server, client, _ = partners
    generator = {
        "kind": "sequence",
        "signal_name": "value",
        "data_type": "float32",
        "initial": 64.5,
    }
    if sequence is not None:
        generator["sequence"] = sequence
    server.sim_operator.send_request(
        "generator_start",
        {
            "member": "DoorService_server",
            "function": "UpdateSampleEvent",
            "interval_ms": 10,
            "generator": generator,
        },
    )
    # 返回后修改调用方字典不得影响原生异步配置；空序列仍按既有 initial 语义发包。
    generator["initial"] = 99.5
    for _ in range(3):
        client.empty_event_list()
        assert client.ck_coming_event(
            "DoorService_client", "Sample", {"value": 64.5}, timeout=2
        ) == {"value": 64.5}
    server.sim_operator.send_request("generator_stop", {"member": "DoorService_server"})
    count = server.sim_operator.send_request("running_service")["DoorService_server"][
        "emitted_count"
    ]
    assert count >= 3
    time.sleep(0.1)
    client.empty_event_list()
    assert client.ck_no_event("DoorService_client", "Sample", timeout=0.1)
    assert (
        server.sim_operator.send_request("running_service")["DoorService_server"][
            "emitted_count"
        ]
        == count
    )


def test_stop_offer_reconnect_and_incremental_start(partners):
    server, client, _ = partners
    server.stop_single_partner("DoorService_server")
    deadline = time.monotonic() + 5
    while (
        time.monotonic() < deadline
        and client.partner_infos["DoorService_client"].service_status != "OFFLINE"
    ):
        time.sleep(0.01)
    assert client.partner_infos["DoorService_client"].service_status == "OFFLINE"
    server.start_single_partner("DoorService", "server")
    assert client.wait_for_service_reconnect("DoorService_client", timeout=5)


def test_second_client_and_response_routing(partners):
    server, client, _ = partners
    server.register_auto_response("DoorService_server", "SetPosition", echo=True)
    client.start_single_partner("DoorService", "client_1")
    assert client.wait_for_service_reconnect("DoorService_1_client", timeout=5)
    assert client.send_request_and_return_resp(
        "DoorService_1_client", "SetPosition", {"position": 22}, timeout=5
    ) == {"out": {"position": 22}}
    assert client.send_request_and_return_resp(
        "DoorService_client", "SetPosition", {"position": 11}, timeout=5
    ) == {"out": {"position": 11}}
    client.stop_single_partner("DoorService_1_client")
    assert client.send_request_and_return_resp(
        "DoorService_client", "SetPosition", {"position": 33}, timeout=5
    ) == {"out": {"position": 33}}


def test_concurrent_request_correlation(partners):
    server, client, _ = partners
    server.register_auto_response("DoorService_server", "SetPosition", echo=True)

    def request(value):
        return client.send_request_and_return_resp(
            "DoorService_client", "SetPosition", {"position": value}, timeout=5
        )

    with ThreadPoolExecutor(max_workers=8) as workers:
        assert list(workers.map(request, range(32))) == [
            {"out": {"position": value}} for value in range(32)
        ]


def test_unregister_one_client_keeps_other_subscription(partners):
    server, client, _ = partners
    client.start_single_partner("DoorService", "client_1")
    client.wait_for_service_reconnect("DoorService_1_client", timeout=5)
    client.unregister_event("DoorService_client", ["Position"])
    client._take(
        client.partner_infos["DoorService_client"].resp_queue,
        lambda message: message["function"] == "UnRegistEvent",
        5,
    )
    server.send_event_notify("DoorService_server", "Position", {"position": 123})
    assert client.ck_s2s_event(
        "DoorService_1_client", "Position", {"position": 123}, timeout=5
    )
    assert client.ck_no_event("DoorService_client", "Position", timeout=0.2)
    client.register_event("DoorService_client", ["Position"])
    client._take(
        client.partner_infos["DoorService_client"].resp_queue,
        lambda message: message["function"] == "RegistEvent",
        5,
    )
    server.send_event_notify("DoorService_server", "Position", {"position": 124})
    assert client.ck_s2s_event(
        "DoorService_client", "Position", {"position": 124}, timeout=5
    )


def test_sat_failtype_enum_and_string(partners):
    from soa_partner.src.base_partner import FailType

    _, client, _ = partners
    for failtype in (FailType.FAILTYPE_TIMEOUT, "FAILTYPE_TIMEOUT"):
        assert client.send_request_and_ck_failtype(
            "DoorService_client", "GetPosition", {}, failtype, timeout=0.1
        )


def test_sat_periodic_event_controls_run_in_native(partners, monkeypatch):
    from someip_agent.soa.operator import NativeRuntimeError

    server, client, _ = partners

    def forbid_python_tick(*args, **kwargs):
        raise AssertionError("周期通知不能调用 Python 逐周期发包函数")

    monkeypatch.setattr(server, "send_event_notify", forbid_python_tick)
    server.send_event_notify_thread_start(
        "DoorService_server", "Position", {"position": 321}, cycle_time=0.01
    )
    assert client.ck_s2s_event("DoorService_client", "Position", {"position": 321}) == {
        "position": 321
    }
    server.send_event_notify_thread_update(
        "DoorService_server", "Position", {"position": 322}
    )
    assert client.ck_coming_event(
        "DoorService_client", "Position", {"position": 322}, timeout=2
    ) == {"position": 322}
    assert server.partner_infos["DoorService_server"].cycle_time == 0.01
    with pytest.raises(NativeRuntimeError):
        server.send_event_notify_thread_update(
            "DoorService_server", "Position", {"position": 999999}
        )
    before = server.sim_operator.send_request("running_service")["DoorService_server"]
    assert before["event_cycle_running"]
    time.sleep(0.05)
    after = server.sim_operator.send_request("running_service")["DoorService_server"]
    assert after["event_cycle_count"] > before["event_cycle_count"]
    client.empty_event_list()
    assert client.ck_s2s_event("DoorService_client", "Position", {"position": 322}) == {
        "position": 322
    }
    # 每次清空后都必须出现新报文，不可用一次字段变更或旧缓存冒充周期发送。
    for _ in range(3):
        client.empty_event_list()
        assert client.ck_coming_event(
            "DoorService_client", "Position", {"position": 322}, timeout=1
        ) == {"position": 322}
    time.sleep(0.06)
    server.send_event_notify_thread_stop("DoorService_server")
    state = server.sim_operator.send_request("running_service")["DoorService_server"]
    assert not state["event_cycle_running"] and state["event_cycle_count"] >= 5
    count = state["event_cycle_count"]
    time.sleep(0.1)
    client.empty_event_list()
    assert client.ck_no_event("DoorService_client", "Position", timeout=0.1)
    assert (
        server.sim_operator.send_request("running_service")["DoorService_server"][
            "event_cycle_count"
        ]
        == count
    )
    with pytest.raises(AttributeError):
        server.send_event_notify_thread_update(
            "DoorService_server", "Position", {"position": 323}
        )


def test_sat_response_retry_and_custom_auto_response(partners):
    server, client, _ = partners
    calls = []

    def reply():
        calls.append(1)
        server.send_method_response(
            "DoorService_server", "GetPosition", {"position": len(calls)}
        )

    server.send_method_response_DoorService_GetPosition = reply
    server.register_auto_response("DoorService_server", "GetPosition")
    assert client.send_request_and_ck_resp(
        "DoorService_client",
        "GetPosition",
        {},
        {"out": {"position": 3}},
        timeout=3,
        cycle_time=0.01,
        is_async=True,
    )
    assert len(calls) == 3


def test_sat_field_cache_and_combined_assertions(partners):
    server, client, _ = partners
    angle = {"value": 41}

    def reply(key, message):
        if message["action"] == "request" and message["function"] == "GetAngle":
            server.send_method_response(
                key, "GetAngle", angle["value"], request_id=message["request_id"]
            )

    server.register_callback("DoorService_server", reply)
    server.send_event_notify("DoorService_server", "Angle", {"angle": 41})
    assert client.ck_field("DoorService_client", "Angle", {"angle": 41}, timeout=3)
    # 新订阅者获取 vsomeip 的 field 初始缓存，不依赖 Python 队列中旧数据。
    client.start_single_partner("DoorService", "client_1")
    client.wait_for_service_reconnect("DoorService_client_1", timeout=5)
    assert client.ck_s2s_event(
        "DoorService_client_1", "Angle", {"angle": 41}, timeout=5
    ) == {"angle": 41}
    angle["value"] = 42
    server.send_event_notify("DoorService_server", "Angle", {"angle": 42})
    assert client.chk_notify("DoorService_client", "Angle", {"angle": 42}, timeout=5)
    assert client.return_latest_event(
        "DoorService_client", "Angle", pop_event=False
    ) == {"angle": 42}
    assert client.ck_event_and_resp(
        "DoorService_client", "Angle", {"angle": 42}, timeout=5
    )
    assert client.ck_no_event_and_ck_resp(
        "DoorService_client", "Angle", {"out": 42}, timeout=0.1
    )
    server.send_event_notify_thread_start(
        "DoorService_server", "Angle", {"angle": 42}, cycle_time=0.02
    )
    assert client.ck_coming_event_and_resp(
        "DoorService_client", "Angle", {"angle": 42}, timeout=3
    )
    server.send_event_notify_thread_stop("DoorService_server")


def test_sat_named_instance_and_numbered_client_keys(partners):
    server, client, _ = partners
    server.register_auto_response("DoorService_server", "SetPosition", echo=True)
    client.start_single_partner("DoorService", "client_1")
    assert "DoorService_client_1" in list(client.partner_infos)
    assert (
        client.partner_infos["DoorService_client_1"]
        is client.partner_infos["DoorService_1_client"]
    )
    client.start_single_partner("DoorService", "client_1")
    assert len(client.partner_infos) == 2
    assert client.send_request_and_return_resp(
        "DoorService_client_1", "SetPosition", {"position": 7}, timeout=5
    ) == {"out": {"position": 7}}
    client.stop_single_partner("DoorService_client_1")
    client.stop_single_partner("DoorService_client")
    client.start_single_partner("DoorService", "client", instance="DoorService_BGM")
    assert list(client.partner_infos) == ["DoorService_client_DoorService_BGM"]
    assert client.send_request_and_return_resp(
        "DoorService_client_DoorService_BGM", "SetPosition", {"position": 8}, timeout=5
    ) == {"out": {"position": 8}}


def test_sat_v20_multiple_request_assertion(partners):
    server, client, _ = partners
    client.send_method_request("DoorService_client", "FireAndForget", {"position": 8})
    client.send_method_request("DoorService_client", "FireAndForget", {"position": 9})
    assert (
        server.ck_s2s_req_v20(
            "DoorService_server",
            ["FireAndForget", "FireAndForget"],
            [{"position": 8}, {"position": 9}],
            timeout=5,
        )["function"]
        == "FireAndForget"
    )


@pytest.mark.parametrize("malformed", [False, True])
def test_wire_error_and_malformed_response_are_not_reported_as_timeout(
    partners, malformed
):
    server, client, _ = partners

    def reply(key, message):
        if message["action"] != "request" or message["function"] != "GetAngle":
            return
        if malformed:
            # 故意发送不满足 uint16 返回 schema 的正常 RESPONSE；不能悄悄吞掉相关响应。
            server._send(
                key,
                {
                    "action": "response",
                    "function": "GetAngle",
                    "request_id": message["request_id"],
                    "payload_hex": "01",
                    "return_code": 0,
                },
            )
        else:
            server.send_method_response(
                key,
                "GetAngle",
                request_id=message["request_id"],
                return_code=6,
                is_error=True,
            )

    server.register_callback("DoorService_server", reply)
    response = client._send_request_and_return_resp_atom(
        "DoorService_client", "GetAngle", {}, timeout=2
    )
    assert response["failtype"] == (
        "FAILTYPE_DESERIALIZATION_FAILURE" if malformed else "FAILTYPE_OTHER_ERROR"
    )
    assert response["return_code"] == (0 if malformed else 6)
    assert response["message_type"] == (0x80 if malformed else 0x81)
    assert response["payload_hex"] == ("01" if malformed else "")
    server.unregister_callback("DoorService_server", reply)
    server.register_auto_response("DoorService_server", "SetPosition", echo=True)
    assert client.send_request_and_return_resp(
        "DoorService_client", "SetPosition", {"position": 19}, timeout=2
    ) == {"out": {"position": 19}}


def test_sat_missing_auto_handler_does_not_return_echo_success(partners):
    from soa_partner.src.base_partner import FailType

    server, client, _ = partners
    server.register_auto_response("DoorService_server", "SetPosition")
    assert client.send_request_and_ck_failtype(
        "DoorService_client",
        "SetPosition",
        {"position": 20},
        FailType.FAILTYPE_TIMEOUT,
        timeout=0.2,
    )


def test_field_initial_delivery_does_not_replay_regular_events_or_stale_field(partners):
    server, client, _ = partners
    server.send_event_notify("DoorService_server", "Position", {"position": 501})
    server.send_event_notify("DoorService_server", "Sample", {"value": 42.5})
    assert client.ck_s2s_event("DoorService_client", "Position", {"position": 501})
    assert client.ck_s2s_event("DoorService_client", "Sample", {"value": 42.5})
    client.start_single_partner("DoorService", "client_1")
    client.wait_for_service_reconnect("DoorService_client_1", timeout=5)
    assert client.ck_s2s_event("DoorService_client_1", "Position", {"position": 501})
    assert client.ck_no_event("DoorService_client_1", "Sample", timeout=0.1)
    assert client.ck_no_event("DoorService_client", "Position", timeout=0.1)
    server.stop_single_partner("DoorService_server")
    deadline = time.monotonic() + 5
    while client.partner_infos["DoorService_client_1"].service_status != "OFFLINE":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    server.start_single_partner("DoorService", "server")
    client.wait_for_service_reconnect("DoorService_client_1", timeout=5)
    time.sleep(0.2)
    client.start_single_partner("DoorService", "client_2")
    client.wait_for_service_reconnect("DoorService_client_2", timeout=5)
    assert client.ck_no_event("DoorService_client_2", "Position", timeout=0.1)
    server.send_event_notify("DoorService_server", "Position", {"position": 502})
    assert client.ck_s2s_event("DoorService_client_2", "Position", {"position": 502})
