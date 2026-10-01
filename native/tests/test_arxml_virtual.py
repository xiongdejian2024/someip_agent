"""真实 ARXML 生成两端目录，通过 SAT 字典初始化在 veth 上调用，不手写 catalog。"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from soa_partner.src.base_partner import S2sBaseClass
from soa_partner.src.Operator import SOAOperator
from someip_agent.arxml.parser import ArxmlParser
from someip_agent.config import Settings
from someip_agent.soa.catalog import NativeCatalogRequest, build_native_bundle

WORKSPACE = Path(__file__).resolve().parents[2]
EVIDENCE = WORKSPACE / "build" / "virtual-evidence"


@pytest.mark.parametrize(
    "transport,port,byte_order",
    [
        ("udp", 30503, "big"),
        ("tcp", 30504, "big"),
        ("udp", 30505, "little"),
        ("tcp", 30506, "little"),
    ],
)
def test_generated_catalog_initializes_client_server_methods_events_fields(
    transport, port, byte_order
):
    model = ArxmlParser().parse(
        (WORKSPACE / "backend/tests/fixtures/vehicle_service.arxml").read_bytes(),
        "vehicle_service.arxml",
    )
    processes, handles, partners = [], [], []
    try:
        for role, address, peer, app_id in (
            ("server", "10.77.0.1", "10.77.0.2", 0x3311),
            ("client", "10.77.0.2", "10.77.0.1", 0x3322),
        ):
            name = "arxml_" + role
            settings = Settings(
                _env_file=None,
                native_unicast=address,
                network_send_enabled=True,
                allowed_destinations=[peer, "239.255.77.1"],
            )
            member = {
                # 路径只负责选择；业务调用名须保留源 ARXML 的 SHORT-NAME。
                "service": model.services[0].path,
                "role": role,
                "transport": transport,
                "peer_host": peer,
                "port": port,
                "byte_order": byte_order,
            }
            members = {"VehicleStatus": member}
            if role == "client":
                members["VehicleStatus_1"] = dict(member)
            request = NativeCatalogRequest.model_validate(
                {
                    "application_name": name,
                    "application_id": app_id,
                    "members": members,
                    "sd_multicast_group": "239.255.77.1",
                }
            )
            bundle = build_native_bundle(model, request, settings)
            assert all(
                cfg["service"] == "VehicleStatus" for cfg in bundle.members.values()
            )
            assert all(
                cfg["name"] == "VehicleStatus" for cfg in bundle.members.values()
            )
            # 目录由被测代码生成，不能复制静态 catalog 让测试看似成功。
            directory = (
                EVIDENCE / f"arxml-{transport}-{byte_order}-{role}-{time.time_ns()}"
            )
            catalog, config = bundle.write(directory)
            handle = (directory / "native.log").open("ab")
            handles.append(handle)
            process = subprocess.Popen(
                [
                    "ip",
                    "netns",
                    "exec",
                    "soa-" + role,
                    os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                    "run",
                    "--name",
                    name,
                    "--bind",
                    address,
                    "-p",
                    "16789",
                    "--catalog",
                    str(catalog),
                    "--config",
                    str(config),
                ],
                stdout=handle,
                stderr=handle,
            )
            processes.append(process)
            partner = S2sBaseClass(
                bundle.members, operator=SOAOperator(name, host=address), attach=True
            )
            partners.append(partner)
        server, client = partners
        key = "VehicleStatus_client"
        assert client.wait_for_service_reconnect(key, timeout=10)
        numbered_key = "VehicleStatus_client_1"
        assert numbered_key in client.partner_infos
        assert client.wait_for_service_reconnect(numbered_key, timeout=10)
        time.sleep(0.3)
        state = {"IgnitionState": 0}

        def callback(server_key, message):
            if message["action"] != "request":
                return
            args = json.loads(message["args"])
            if message["function"] == "SetSpeed":
                assert args == {"Speed": 384}
                out = {"Accepted": True}
            elif message["function"] == "SetIgnitionState":
                state.update(args)
                out = state["IgnitionState"]
                server.send_event_notify(server_key, "IgnitionState", out)
            else:
                assert message["function"] == "GetIgnitionState"
                out = state["IgnitionState"]
            server.send_method_response(
                server_key, message["function"], out, request_id=message["request_id"]
            )

        server.register_callback("VehicleStatus_server", callback)
        assert client.send_request_and_ck_resp(
            key, "SetSpeed", {"Speed": 384}, {"out": {"Accepted": True}}, timeout=5
        )
        assert client.send_request_and_return_resp(
            key, "SetIgnitionState", {"IgnitionState": 7}, timeout=5
        ) == {"out": 7}
        assert client.ck_s2s_event(key, "IgnitionState", 7, timeout=5) == 7
        assert client.ck_s2s_event(numbered_key, "IgnitionState", 7, timeout=5) == 7
        assert client.send_request_and_return_resp(
            key, "GetIgnitionState", {}, timeout=5
        ) == {"out": 7}
        assert client.send_request_and_return_resp(
            numbered_key, "GetIgnitionState", {}, timeout=5
        ) == {"out": 7}
        server.send_event_notify("VehicleStatus_server", "SpeedChanged", 42.5)
        assert client.ck_s2s_event(key, "SpeedChanged", 42.5, timeout=5) == 42.5
        assert server.ck_s2s_req(
            "VehicleStatus_server", "SetSpeed", {"Speed": 384}, timeout=5
        )
    finally:
        for partner in partners:
            partner.close()
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for handle in handles:
            handle.close()
