"""完整 ARXML 事件周期 API；真实原生编码/接收，非 Python 定时发送。"""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.soa.partner import S2sBaseClass

FIXTURES = Path(__file__).parent / "fixtures"


def create_session(client, service, content):
    imported = client.post("/api/v1/arxml/import", files={"file": ("cycle.arxml", content)})
    assert imported.status_code == 200, imported.text
    request = {
        "application_name": "cycle_api",
        "application_id": 0x4801,
        "members": {
            "Provider": {"service": service, "role": "server"},
            "Consumer": {"service": service, "role": "client"},
        },
    }
    created = client.post("/api/v1/services/sessions", json=request)
    assert created.status_code == 200, created.text
    session = created.json()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        view = client.get("/api/v1/services/sessions").json()[0]
        if all(member["state"] == "START" for member in view["members"]):
            break
        time.sleep(0.01)
    else:
        pytest.fail("内部 provider/consumer 未就绪")
    time.sleep(0.1)  # 等待原生订阅建立，不是替代状态验证。
    keys = {member["role"]: member["key"] for member in session["members"]}
    return session, keys


@pytest.mark.parametrize("seconds,expected", [(0.029, 29), (0.0011, 2), (0.001, 1), (60, 60000)])
def test_decimal_cycle_interval(seconds, expected):
    assert S2sBaseClass._cycle_interval(seconds) == expected


@pytest.mark.parametrize("seconds", [True, float("inf"), float("nan"), 0, 61])
def test_invalid_interval(seconds):
    with pytest.raises(ValueError):
        S2sBaseClass._cycle_interval(seconds)


def test_cycle_api_unknown_session_and_strict_schema(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        base = "/api/v1/services/sessions/missing/cycles"
        assert client.get(base).status_code == 404
        command = {"member": "Provider_server", "function": "Event", "args": {}, "interval_ms": 29}
        assert client.post(base + "/start", json=command).status_code == 404
        assert (
            client.post(base + "/start", json={**command, "interval_ms": True}).status_code == 422
        )
        assert client.post(base + "/start", json={**command, "extra": 1}).status_code == 422
        assert client.post(base + "/stop", json={"member": "Provider_server"}).status_code == 404
        result = client.post(
            "/api/v1/console/actions/service_cycle_start",
            json={
                "arguments": {"identifier": "missing", "command": command},
            },
        )
        assert result.status_code == 403, "控制台新增周期写工具必须默认拒绝"


def test_actual_cycle_start_update_reject_stop_and_cleanup(tmp_path, native_runtime):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        session, keys = create_session(
            client, "VehicleStatus", (FIXTURES / "vehicle_service.arxml").read_bytes()
        )
        base = f"/api/v1/services/sessions/{session['id']}/cycles"
        command = {
            "member": keys["server"],
            "function": "UpdateSpeedChangedEvent",
            "args": 42.5,
            "interval_ms": 29,
        }
        started = client.post(base + "/start", json=command)
        assert started.status_code == 200, started.text
        assert started.json()["running"] and started.json()["interval_ms"] == 29
        assert not started.json()["wire_verified"]
        time.sleep(0.12)
        before = client.get(base).json()[0]
        assert before["emitted_count"] >= 3
        invalid = client.post(base + "/update", json={**command, "args": "not-a-float"})
        assert invalid.status_code == 422
        assert client.get(base).json()[0]["running"], "编码拒绝不得破坏正在运行的旧任务"
        assert (
            client.post(base + "/start", json={**command, "member": keys["client"]}).status_code
            == 422
        )
        updated = client.post(base + "/update", json={**command, "args": 24.5, "interval_ms": 50})
        assert updated.status_code == 200 and updated.json()["interval_ms"] == 50
        time.sleep(0.12)
        messages = client.get("/api/v1/monitor/messages").json()
        assert any(item["payload_hex"] == "422a0000" for item in messages)
        assert any(item["payload_hex"] == "41c40000" for item in messages)
        assert all(not item["metadata"]["wire_verified"] for item in messages)
        stopped = client.post(base + "/stop", json={"member": keys["server"]})
        assert stopped.status_code == 200 and not stopped.json()["running"]
        count = stopped.json()["emitted_count"]
        time.sleep(0.12)
        assert client.get(base).json()[0]["emitted_count"] == count
        assert client.post(base + "/update", json=command).status_code == 409
        assert client.post(base + "/start", json=command).status_code == 200
        operator = app.state.container.services._sessions[session["id"]].partner.sim_operator
        assert client.post(f"/api/v1/services/sessions/{session['id']}/stop").status_code == 200
        assert operator.process is None or operator.process.poll() is not None
        assert client.get(base).status_code == 404
        actions = [row["action"] for row in client.get("/api/v1/audit/events").json()]
        assert "services.cycle.start" in actions and "services.cycle.stop" in actions


def test_composite_event_preserves_complete_schema_and_source_eventgroup(tmp_path, native_runtime):
    content = (
        (FIXTURES / "composite_service.arxml")
        .read_bytes()
        .replace(b"<EVENT-GROUP-ID>1</EVENT-GROUP-ID>", b"<EVENT-GROUP-ID>7</EVENT-GROUP-ID>")
    )
    value = {
        "tag": 7,
        "samples": [0x1234, 0xABCD],
        "bytes": [1, 2],
        "matrix": [[1, 2, 3], [4, 5, 6]],
        "nested": {"temperature": -2},
    }
    golden = "001b0700041234abcd00020102000a000301020300030405060002fffe"
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        session, keys = create_session(client, "EnvelopeService", content)
        base = f"/api/v1/services/sessions/{session['id']}/cycles"
        command = {
            "member": keys["server"],
            "function": "UpdateEnvelopeChangedEvent",
            "args": value,
            "interval_ms": 50,
        }
        result = client.post(base + "/start", json=command)
        assert result.status_code == 200, result.text
        native_session = app.state.container.services._sessions[session["id"]]
        event = native_session.bundle.catalog["Provider"]["events"][command["function"]]
        assert event["eventgroups"] == [7], "不能把真实源事件组替换成 1"
        assert (
            native_session.partner.ck_s2s_event(
                keys["client"], "EnvelopeChanged", value, timeout=2, fuzz_match=False
            )
            == value
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            messages = client.get("/api/v1/monitor/messages").json()
            if any(item["payload_hex"] == golden for item in messages):
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"未观察到完整结构/数组黄金 payload：{messages}")
        # 错误形状必须原生拒绝；不因单信号名字相同而退回简化结构。
        bad = client.post(base + "/update", json={**command, "args": {"tag": 8}})
        assert bad.status_code == 422
        stopped = client.post(base + "/stop", json={"member": keys["server"]})
        assert stopped.status_code == 200 and not stopped.json()["running"]
