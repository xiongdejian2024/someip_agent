"""真实原生会话 API 验收；不以 mock 进程替代 SAT socket 初始化和调用。"""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from someip_agent.config import Settings
from someip_agent.main import create_app

FIXTURE = Path(__file__).parent / "fixtures/vehicle_service.arxml"


def request(name="service_api_test", app_id=0x4401):
    return {
        "application_name": name,
        "application_id": app_id,
        "members": {
            "Provider": {"service": "VehicleStatus", "role": "server"},
            "Consumer": {"service": "VehicleStatus", "role": "client"},
        },
    }


def import_model(client):
    response = client.post(
        "/api/v1/arxml/import", files={"file": ("vehicle.arxml", FIXTURE.read_bytes())}
    )
    assert response.status_code == 200
    return response.json()


def wait_requests(client, identifier):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/services/sessions/{identifier}/requests")
        assert response.status_code == 200
        if response.json():
            return response.json()
        time.sleep(0.01)
    pytest.fail("服务端未收到真实原生请求")


def test_service_session_model_required_and_missing_binary_without_fallback(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, native_binary="missing-service-native")
    )
    with TestClient(app) as client:
        assert client.post("/api/v1/services/sessions", json=request()).status_code == 409
        import_model(client)
        assert client.post("/api/v1/services/sessions", json=request()).status_code == 503
        assert client.get("/api/v1/services/sessions").json() == []
        assert client.get("/api/v1/services/sessions/not-found/requests").status_code == 404


def test_service_session_network_auth_checked_before_start(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        import_model(client)
        body = request()
        for member in body["members"].values():
            member.update(transport="udp", peer_host="10.77.0.2", port=30520)
        assert client.post("/api/v1/services/sessions", json=body).status_code == 403
        assert not (tmp_path / "native-services").exists()


def test_service_session_subapplications_have_real_identity_and_cross_session_conflict(
    tmp_path, native_runtime
):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        import_model(client)
        body = request("multi_api", 0x4500)
        body["members"]["Provider"].update(application_name="provider_api", application_id=0x4501)
        body["members"]["Consumer"].update(application_name="consumer_api", application_id=0x4502)
        response = client.post("/api/v1/services/sessions", json=body)
        assert response.status_code == 200, response.text
        identifier = response.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get("/api/v1/services/sessions").json()[0]
            if all(member["state"] == "START" for member in state["members"]):
                break
            time.sleep(0.01)
        assert {
            member["role"]: (member["application_name"], member["application_id"])
            for member in state["members"]
        } == {"server": ("provider_api", 0x4501), "client": ("consumer_api", 0x4502)}
        for name, identifier_conflict in (("provider_api", 0x4600), ("another_api", 0x4502)):
            assert (
                client.post(
                    "/api/v1/services/sessions", json=request(name, identifier_conflict)
                ).status_code
                == 409
            )
        sub_collision = request("unrelated_api", 0x4600)
        sub_collision["members"]["Consumer"].update(
            application_name="third_api", application_id=0x4501
        )
        assert client.post("/api/v1/services/sessions", json=sub_collision).status_code == 409
        sub_reserved = request("reserved_api", 0x4600)
        sub_reserved["members"]["Consumer"].update(
            application_name="reserved_sub", application_id=0x1101
        )
        assert client.post("/api/v1/services/sessions", json=sub_reserved).status_code == 409
        assert client.post(f"/api/v1/services/sessions/{identifier}/stop").status_code == 200
        # 会话关闭才释放上下文；新的进程可以再次申请相同身份。
        assert client.post("/api/v1/services/sessions", json=body).status_code == 200


def test_service_session_native_calls_requests_ack_stop_and_model_binding(tmp_path, native_runtime):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        model = import_model(client)
        response = client.post("/api/v1/services/sessions", json=request())
        assert response.status_code == 200, response.text
        session = response.json()
        assert (
            session["active"]
            and session["running"]
            and session["runtime"] == "vsomeip"
            and session["pid"]
        )
        assert (
            session["model_id"] == model["id"]
            and session["source_sha256"] == model["source_sha256"]
        )
        keys = {member["role"]: member["key"] for member in session["members"]}
        base = f"/api/v1/services/sessions/{session['id']}"
        assert client.get("/api/v1/health").json()["active_service_sessions"] == 1
        assert (
            client.post("/api/v1/services/sessions", json=request("different", 0x4401)).status_code
            == 409
        )
        assert (
            client.post(
                "/api/v1/services/sessions", json=request("service_api_test", 0x4402)
            ).status_code
            == 409
        )
        assert (
            client.post("/api/v1/services/sessions", json=request("reserved", 0x1101)).status_code
            == 409
        )
        # 方法调用与服务端人工响应必须可并行，不能被同一个会话锁互相阻塞。
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                client.post,
                base + "/call",
                json={"member": keys["client"], "function": "SetSpeed", "args": {"Speed": 384}},
            )
            pending = wait_requests(client, session["id"])[0]
            assert pending["payload_hex"] == "0180" and pending["args"] == {"Speed": 384}
            assert pending["member"] == keys["server"] and pending["reply_allowed"]
            reply = {
                "member": keys["server"],
                "function": "SetSpeed",
                "request_id": pending["request_id"],
                "args": {"Accepted": True},
            }
            assert client.post(base + "/respond", json=reply).json()["status"] == "submitted"
            actual = future.result(timeout=7)
            assert actual.status_code == 200, actual.text
            assert actual.json()["result"] == {"out": {"Accepted": True}}
            assert (
                actual.json()["observation"] == "vsomeip_response"
                and not actual.json()["wire_verified"]
            )
            assert client.post(base + "/respond", json=reply).status_code == 409
        # 参数非法必须得到 C++ 编码拒绝回执，不能把 socket 写入成功当事件成功。
        notification = {
            "member": keys["server"],
            "function": "UpdateSpeedChangedEvent",
            "args": "invalid",
        }
        assert client.post(base + "/notify", json=notification).status_code == 422
        notification["args"] = 42.5
        result = client.post(base + "/notify", json=notification)
        assert result.status_code == 200 and result.json()["observation"] == "native_submission"
        assert not result.json()["wire_verified"]
        notification["member"] = keys["client"]
        assert client.post(base + "/notify", json=notification).status_code == 422
        assert (
            client.post(
                base + "/call", json={"member": keys["client"], "function": "Missing"}
            ).status_code
            == 422
        )
        # 新导入的模型不替换已初始化会话的 frozen catalog。
        import_model(client)
        assert client.get("/api/v1/services/sessions").json()[0]["model_id"] == model["id"]
        stopped = client.post(base + "/stop")
        assert (
            stopped.status_code == 200
            and not stopped.json()["running"]
            and stopped.json()["pid"] is None
        )
        assert all(not member["connected"] for member in stopped.json()["members"])
        assert not stopped.json()["active"]
        assert client.post(base + "/stop").status_code == 200
        assert (
            client.post(
                base + "/call", json={"member": keys["client"], "function": "SetSpeed"}
            ).status_code
            == 404
        )
        assert client.get("/api/v1/health").json()["active_service_sessions"] == 0


def test_service_session_timeout_and_shutdown_cleanup(tmp_path, native_runtime):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        import_model(client)
        session = client.post("/api/v1/services/sessions", json=request("cleanup", 0x4403)).json()
        manager = app.state.container.services
        operator = manager._sessions[session["id"]].partner.sim_operator
        key = next(member["key"] for member in session["members"] if member["role"] == "client")
        response = client.post(
            f"/api/v1/services/sessions/{session['id']}/call",
            json={"member": key, "function": "SetSpeed", "args": {"Speed": 1}, "timeout": 0.05},
        )
        assert response.status_code == 504
        assert operator.process is not None and operator.process.poll() is None
    assert operator.process is None or operator.process.poll() is not None


def test_service_session_crash_stays_releasable_and_internal_monitor_is_not_wire(
    tmp_path, native_runtime
):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        import_model(client)
        session = client.post("/api/v1/services/sessions", json=request("crash", 0x4404)).json()
        identifier = session["id"]
        server = next(member["key"] for member in session["members"] if member["role"] == "server")
        sent = client.post(
            f"/api/v1/services/sessions/{identifier}/notify",
            json={"member": server, "function": "UpdateSpeedChangedEvent", "args": 24.5},
        )
        assert sent.status_code == 200
        deadline = time.monotonic() + 3
        traces = []
        while time.monotonic() < deadline and not traces:
            traces = client.get("/api/v1/monitor/messages").json()
            if not traces:
                time.sleep(0.01)
        assert traces and all(
            item["transport"] == "internal" and item["direction"] == "sim" for item in traces
        )
        assert all(not item["metadata"]["wire_verified"] for item in traces)
        operator = app.state.container.services._sessions[identifier].partner.sim_operator
        assert operator.process is not None
        operator.process.kill()
        operator.process.wait(timeout=5)
        failed = client.get("/api/v1/services/sessions").json()[0]
        assert failed["active"] and not failed["running"] and failed["pid"] is None
        assert (
            client.post("/api/v1/services/sessions", json=request("crash", 0x4404)).status_code
            == 409
        )
        stopped = client.post(f"/api/v1/services/sessions/{identifier}/stop")
        assert stopped.status_code == 200 and not stopped.json()["active"]
        assert client.get("/api/v1/health").json()["active_service_sessions"] == 0
        restarted = client.post("/api/v1/services/sessions", json=request("crash", 0x4404))
        assert restarted.status_code == 200 and restarted.json()["active"]
