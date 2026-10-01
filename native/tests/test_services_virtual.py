"""页面同一原生服务 API 跨 veth 与 SAT 伙伴互通，独立验证两角色及明确字节序。"""

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from someip_agent.arxml.parser import ArxmlParser
from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.soa.catalog import NativeCatalogRequest, build_native_bundle
from someip_agent.soa.operator import SOAOperator
from someip_agent.soa.partner import S2sBaseClass

FIXTURE = Path("/workspace/backend/tests/fixtures/vehicle_service.arxml")


@pytest.mark.parametrize("role", ["client", "server"])
@pytest.mark.parametrize(
    "transport,port,byte_order",
    [
        ("udp", 30520, "big"),
        ("tcp", 30521, "big"),
        ("udp", 30522, "little"),
        ("tcp", 30523, "little"),
    ],
)
def test_page_service_api_over_virtual_nics(
    tmp_path, role, transport, port, byte_order
):
    peer_role = "server" if role == "client" else "client"
    model = ArxmlParser().parse(FIXTURE.read_bytes(), FIXTURE.name)
    peer_settings = Settings(
        _env_file=None,
        native_unicast="10.77.0.2",
        network_send_enabled=True,
        allowed_destinations=["10.77.0.1", "239.255.77.1"],
    )
    member = {
        "service": "VehicleStatus",
        "role": peer_role,
        "transport": transport,
        "peer_host": "10.77.0.1",
        "port": port,
        "byte_order": byte_order,
    }
    request = NativeCatalogRequest.model_validate(
        {
            "application_name": "page_peer",
            "application_id": 0x6632,
            "sd_multicast_group": "239.255.77.1",
            "members": {"VehicleStatus": member},
        }
    )
    bundle = build_native_bundle(model, request, peer_settings)
    catalog, config = bundle.write(tmp_path / "peer")
    process = None
    peer = None
    with (tmp_path / "peer.log").open("ab") as output:
        try:
            process = subprocess.Popen(
                [
                    "ip",
                    "netns",
                    "exec",
                    "soa-client",
                    os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                    "run",
                    "--name",
                    "page_peer",
                    "--bind",
                    "10.77.0.2",
                    "-p",
                    "16789",
                    "--catalog",
                    str(catalog),
                    "--config",
                    str(config),
                ],
                stdout=output,
                stderr=output,
            )
            peer = S2sBaseClass(
                bundle.members,
                operator=SOAOperator("page_peer", host="10.77.0.2"),
                attach=True,
            )
            key = "VehicleStatus_" + peer_role
            app = create_app(
                Settings(
                    _env_file=None,
                    data_dir=tmp_path / "backend",
                    native_binary=os.environ["SOMEIP_AGENT_NATIVE_BINARY"],
                    native_unicast="10.77.0.1",
                    network_send_enabled=True,
                    allowed_destinations=["10.77.0.2", "239.255.77.1"],
                )
            )
            with TestClient(app) as client:
                assert (
                    client.post(
                        "/api/v1/arxml/import",
                        files={"file": (FIXTURE.name, FIXTURE.read_bytes())},
                    ).status_code
                    == 200
                )
                started = client.post(
                    "/api/v1/services/sessions",
                    json={
                        "application_name": "page_local",
                        "application_id": 0x6631,
                        "sd_multicast_group": "239.255.77.1",
                        "members": {
                            "VehicleStatus": {
                                **member,
                                "role": role,
                                "peer_host": "10.77.0.2",
                            }
                        },
                    },
                )
                assert started.status_code == 200, started.text
                session = started.json()
                local_key = session["members"][0]["key"]
                base = "/api/v1/services/sessions/" + session["id"]
                state = {"IgnitionState": 0}

                def callback(server_key, message):
                    if message["action"] != "request":
                        return
                    arguments = json.loads(message["args"])
                    if message["function"] == "SetSpeed":
                        assert arguments == {"Speed": 384}
                        result = {"Accepted": True}
                    elif message["function"] == "SetIgnitionState":
                        state.update(arguments)
                        result = state["IgnitionState"]
                        peer.send_event_notify(server_key, "IgnitionState", result)
                    else:
                        assert message["function"] == "GetIgnitionState"
                        result = state["IgnitionState"]
                    peer.send_method_response(
                        server_key,
                        message["function"],
                        result,
                        request_id=message["request_id"],
                    )

                if role == "client":
                    peer.register_callback(key, callback)
                else:
                    assert peer.wait_for_service_reconnect(key, timeout=10)
                time.sleep(0.3)
                for function, arguments, expected in (
                    ("SetSpeed", {"Speed": 384}, {"out": {"Accepted": True}}),
                    ("SetIgnitionState", {"IgnitionState": 7}, {"out": 7}),
                    ("GetIgnitionState", {}, {"out": 7}),
                ):
                    if role == "client":
                        response = client.post(
                            base + "/call",
                            json={
                                "member": local_key,
                                "function": function,
                                "args": arguments,
                            },
                        )
                        assert response.status_code == 200, response.text
                        assert response.json()["result"] == expected
                    else:
                        with ThreadPoolExecutor(max_workers=1) as executor:
                            pending_call = executor.submit(
                                peer.send_request_and_return_resp,
                                key,
                                function,
                                arguments,
                                5,
                            )
                            deadline = time.monotonic() + 3
                            pending = []
                            while time.monotonic() < deadline and not pending:
                                snapshot = client.get(base + "/requests")
                                assert snapshot.status_code == 200
                                pending = snapshot.json()
                                if not pending:
                                    time.sleep(0.01)
                            assert len(pending) == 1 and pending[0]["args"] == arguments
                            response = client.post(
                                base + "/respond",
                                json={
                                    "member": local_key,
                                    "function": function,
                                    "args": expected["out"],
                                    "request_id": pending[0]["request_id"],
                                },
                            )
                            assert response.status_code == 200, response.text
                            assert (
                                response.json()["status"] == "submitted"
                                and not response.json()["wire_verified"]
                            )
                            assert pending_call.result(timeout=6) == expected
                if role == "server":
                    response = client.post(
                        base + "/notify",
                        json={
                            "member": local_key,
                            "function": "UpdateSpeedChangedEvent",
                            "args": 24.5,
                        },
                    )
                    assert response.status_code == 200
                    assert (
                        peer.ck_s2s_event(key, "SpeedChanged", 24.5, timeout=5) == 24.5
                    )
                else:
                    peer.send_event_notify(key, "SpeedChanged", 24.5)
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        messages = client.get("/api/v1/monitor/messages").json()
                        if any(
                            message["method_id"] == 0x8001
                            and message["payload_hex"]
                            == ("41c40000" if byte_order == "big" else "0000c441")
                            for message in messages
                        ):
                            break
                        time.sleep(0.01)
                    else:
                        pytest.fail("页面原生监控未收到对端黄金事件")
                assert client.post(base + "/stop").status_code == 200
                assert not client.get("/api/v1/services/sessions").json()[0]["running"]
        finally:
            if peer:
                peer.close()
            if process:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
