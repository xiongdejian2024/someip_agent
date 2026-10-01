"""SAT 字典选择独立 application；真实 veth 身份另由被动 PCAP 黄金审计证明。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from soa_partner.src.base_partner import S2sBaseClass
from soa_partner.src.Operator import SOAOperator

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent
EVIDENCE = ROOT.parents[1] / "build" / "virtual-evidence"
CLIENTS = ("DoorService_client", "DoorService_client_1")
CASES = {
    "test_independent_concurrent_requests": 0,
    "test_stop_restart_keeps_other_identity": 1,
    "test_subscriptions_are_application_scoped": 2,
    "test_invalid_identity_does_not_mutate_members": 3,
    "test_late_response_after_member_restart_is_not_reused": 4,
}


@pytest.fixture(params=["udp", "tcp"])
def applications(request):
    transport = request.param
    port = 30740 + CASES[request.node.originalname] * 2 + (transport == "tcp")
    directory = EVIDENCE / f"applications-{port}"
    directory.mkdir(exist_ok=False)
    processes, handles, objects = [], [], []
    try:
        catalog = json.loads((ROOT / "catalog.json").read_text())
        catalog["DoorService"]["service_id"] = "0x6789"
        catalog_path = directory / "catalog.json"
        catalog_path.write_text(json.dumps(catalog))
        for node, address in (("server", "10.77.0.1"), ("client", "10.77.0.2")):
            config = json.loads((ROOT / f"{node}.json").read_text())
            if node == "client":
                config["services"] = json.loads((ROOT / "server.json").read_text())[
                    "services"
                ]
            config["applications"] = [
                {"name": node, "id": "0x7750" if node == "server" else "0x7740"},
                *(
                    [{"name": "provider", "id": "0x7751"}]
                    if node == "server"
                    else [
                        {"name": "consumer_a", "id": "0x7741"},
                        {"name": "consumer_b", "id": "0x7742"},
                    ]
                ),
            ]
            for service in config["services"]:
                if node == "client":
                    service["unicast"] = "10.77.0.1"
                service["service"] = "0x6789"
                service.pop("unreliable" if transport == "tcp" else "reliable", None)
                service["reliable" if transport == "tcp" else "unreliable"] = str(port)
                for event in service["events"]:
                    event["is_reliable"] = transport == "tcp"
            config_path = directory / f"{node}.json"
            config_path.write_text(json.dumps(config))
            handle = (directory / f"{node}.log").open("ab")
            handles.append(handle)
            process = subprocess.Popen(
                [
                    "ip",
                    "netns",
                    "exec",
                    "soa-" + node,
                    os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                    "run",
                    "--name",
                    node,
                    "--bind",
                    address,
                    "-p",
                    "16789",
                    "--catalog",
                    str(catalog_path),
                    "--config",
                    str(config_path),
                ],
                stdout=handle,
                stderr=handle,
            )
            processes.append(process)
            members = {
                "DoorService": {
                    "service": "DoorService",
                    "name": "DoorService",
                    "role": node,
                    "transport": transport,
                    "application_name": "provider"
                    if node == "server"
                    else "consumer_a",
                }
            }
            if node == "client":
                members["DoorService_1"] = {
                    "service": "DoorService",
                    "name": "DoorService",
                    "role": "client",
                    "transport": transport,
                    "application_name": "consumer_b",
                }
            objects.append(
                S2sBaseClass(
                    members, operator=SOAOperator(node, host=address), attach=True
                )
            )
        server, client = objects
        for key in CLIENTS:
            assert client.wait_for_service_reconnect(key, timeout=10)
        time.sleep(0.3)
        server.register_auto_response("DoorService_server", "SetPosition", echo=True)
        running = client.sim_operator.send_request("running_service")
        assert running["DoorService_client"]["application_id"] == 0x7741
        assert running["DoorService_1_client"]["application_id"] == 0x7742
        assert (
            server.sim_operator.send_request("running_service")["DoorService_server"][
                "application_name"
            ]
            == "provider"
        )
        yield server, client, transport
    finally:
        for obj in objects:
            obj.close()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.exception("自有多 application 进程退出超时")
                    process.kill()
                    process.wait(timeout=5)
            assert process.returncode == 0, (
                "原生 application 未正常关闭，检查保留的日志"
            )
        for handle in handles:
            handle.close()


def echo(client, key, value):
    assert client.send_request_and_return_resp(
        key, "SetPosition", {"position": value}, timeout=5
    ) == {"out": {"position": value}}


def test_independent_concurrent_requests(applications):
    server, client, _ = applications
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = [
            pool.submit(echo, client, key, base + i)
            for key, base in zip(CLIENTS, (0xA100, 0xB100), strict=True)
            for i in range(24)
        ]
        for task in tasks:
            task.result(timeout=10)
    requests = [
        server.partner_infos["DoorService_server"].req_queue.get(timeout=2)
        for _ in range(48)
    ]
    ids = [message["request_id"] for message in requests]
    assert len(set(ids)) == 48
    assert {identifier >> 16 for identifier in ids} == {0x7741, 0x7742}


def test_stop_restart_keeps_other_identity(applications):
    _, client, _ = applications
    echo(client, CLIENTS[0], 0xA200)
    echo(client, CLIENTS[1], 0xB200)
    client.stop_single_partner(CLIENTS[0])
    assert client.sim_operator.send_request("ping")["application_count"] == 3
    echo(client, CLIENTS[1], 0xB201)
    client.start_single_partner("DoorService", "client")
    assert client.wait_for_service_reconnect(CLIENTS[0], timeout=5)
    assert client.partner_infos[CLIENTS[0]].application_id == 0x7741
    echo(client, CLIENTS[0], 0xA201)


def test_subscriptions_are_application_scoped(applications):
    server, client, _ = applications
    client.unregister_event(CLIENTS[0], ["Position"])
    server.send_event_notify("DoorService_server", "Position", {"position": 0xC201})
    assert client.ck_s2s_event(CLIENTS[1], "Position", {"position": 0xC201}, timeout=5)
    assert client.ck_no_event(CLIENTS[0], "Position", timeout=0.2)
    client.register_event(CLIENTS[0], ["Position"])
    assert client.ck_s2s_event(CLIENTS[0], "Position", {"position": 0xC201}, timeout=5)
    client.stop_single_partner(CLIENTS[0])
    server.send_event_notify("DoorService_server", "Position", {"position": 0xC202})
    assert client.ck_s2s_event(CLIENTS[1], "Position", {"position": 0xC202}, timeout=5)


def test_invalid_identity_does_not_mutate_members(applications):
    server, client, transport = applications
    for members, match in [
        (
            {"DoorService_2": {"role": "client", "application_name": "undeclared"}},
            "未在",
        ),
        (
            {
                "DoorService": {
                    "role": "client",
                    "application_name": "consumer_b",
                    "transport": transport,
                }
            },
            "不能直接改变",
        ),
    ]:
        with pytest.raises(RuntimeError, match=match):
            client.sim_operator.send_request("start_config_get_args", members)
    with pytest.raises(RuntimeError, match="只能有一个 server"):
        server.sim_operator.send_request(
            "start_config_get_args",
            {"DoorService_1": {"role": "server", "transport": transport}},
        )
    assert len(client.sim_operator.send_request("running_service")) == 2
    assert len(server.sim_operator.send_request("running_service")) == 1
    echo(client, CLIENTS[0], 0xA300)
    echo(client, CLIENTS[1], 0xB300)


def test_late_response_after_member_restart_is_not_reused(applications):
    server, client, _ = applications
    client.send_method_request(CLIENTS[0], "GetPosition", {}, timeout=3)
    old = server.partner_infos["DoorService_server"].req_queue.get(timeout=2)
    assert client.sim_operator.send_request("ping")["pending_requests"] == 1
    client.stop_single_partner(CLIENTS[0])
    assert client.sim_operator.send_request("ping")["pending_requests"] == 0
    echo(client, CLIENTS[1], 0xB400)
    client.start_single_partner("DoorService", "client")
    assert client.wait_for_service_reconnect(CLIENTS[0], timeout=5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            client.send_request_and_return_resp,
            CLIENTS[0],
            "GetPosition",
            {},
            timeout=3,
        )
        new = server._take(
            server.partner_infos["DoorService_server"].req_queue,
            lambda message: message["function"] == "GetPosition",
            2,
        )
        assert new["request_id"] >> 16 == old["request_id"] >> 16 == 0x7741
        assert new["request_id"] != old["request_id"]
        server.send_method_response(
            "DoorService_server",
            "GetPosition",
            {"position": 0xD400},
            request_id=old["request_id"],
        )
        time.sleep(0.1)
        assert not pending.done(), "旧响应污染了重启后的待完成请求"
        server.send_method_response(
            "DoorService_server",
            "GetPosition",
            {"position": 0xD401},
            request_id=new["request_id"],
        )
        assert pending.result(timeout=4) == {"out": {"position": 0xD401}}
    assert client.partner_infos[CLIENTS[0]].resp_queue.empty()
    assert client.sim_operator.send_request("ping")["pending_requests"] == 0
