"""真实原生公共时钟、同服务多事件及 API 控制，不用前端冻结冒充暂停。"""

import time
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from lxml import etree
from pydantic import ValidationError
from test_service_cycles import FIXTURES, create_session

from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.runtime.service_models import ServiceSyncCommand, ServiceSyncControl
from someip_agent.soa.operator import NativeOperationError


def event(member, function, initial, final, interval):
    return {
        "member": member,
        "function": function,
        "args": initial,
        "interval_ms": interval,
        "sources": [
            {
                "path": "",
                "generator": {
                    "kind": "step",
                    "initial": initial,
                    "step_at_ms": 29,
                    "step_value": final,
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "change",
    [
        {"events": []},
        {"speed": True},
        {"speed": 3},
        {"speed": "1"},
        {"speed": float("nan")},
        {"paused": 1},
        {"extra": 1},
        {"events": [{"member": "P", "function": "E", "interval_ms": True}]},
    ],
)
def test_sync_contract_rejects_invalid(change):
    command = {"events": [event("Provider_server", "SpeedChanged", 42.5, 24.5, 20)]}
    with pytest.raises(ValidationError):
        ServiceSyncCommand.model_validate({**command, **change})


def test_duplicate_and_control_shape():
    item = event("P", "E", 1, 2, 20)
    with pytest.raises(ValidationError):
        ServiceSyncCommand(events=[item, item])
    for command in [
        {"action": "speed"},
        {"action": "speed", "speed": True},
        {"action": "pause", "speed": 1},
        {"action": "execute"},
    ]:
        with pytest.raises(ValidationError):
            ServiceSyncControl.model_validate(command)
    for speed in (0.25, 0.5, 1, 2, 4):
        assert ServiceSyncControl(action="speed", speed=speed).speed == speed


def test_sync_budget_uses_actual_ascii_control_size():
    item = {"member": "P", "function": "E", "args": "值" * 700000}
    with pytest.raises(ValidationError, match="控制消息预算"):
        ServiceSyncCommand(events=[item])


def test_unknown_sync_session_and_no_unrequested_audit(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        base = "/api/v1/services/sessions/missing/sync"
        assert client.get(base).status_code == 404
        command = {"events": [event("P", "E", 1, 2, 20)]}
        assert client.post(base + "/start", json=command).status_code == 404
        assert client.post(base + "/control", json={"action": "stop"}).status_code == 404


def test_actual_multi_event_shared_clock_pause_step_speed_cleanup(tmp_path, native_runtime):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        session, keys = create_session(
            client, "VehicleStatus", (FIXTURES / "vehicle_service.arxml").read_bytes()
        )
        root = f"/api/v1/services/sessions/{session['id']}"
        base = root + "/sync"
        command = {
            "events": [
                event(keys["server"], "UpdateSpeedChangedEvent", 42.5, 24.5, 20),
                event(keys["server"], "UpdateIgnitionStateEvent", 9, 10, 30),
            ]
        }
        assert not client.get(base).json()["active"]
        started = client.post(base + "/start", json=command)
        assert started.status_code == 200, started.text
        state = started.json()
        assert state["active"] and state["paused"] and state["interval_ms"] == 10
        assert state["logical_ms"] == state["frame_index"] == 0
        assert not state["wire_verified"]
        assert client.post(base + "/start", json=command).status_code == 422
        assert (
            client.post(root + "/cycles/stop", json={"member": keys["server"]}).status_code == 409
        )
        assert client.post(root + "/cycles/start", json=command["events"][0]).status_code == 409
        time.sleep(0.06)
        assert client.get(base).json()["frame_index"] == 0
        for frame in range(5):
            step = client.post(base + "/control", json={"action": "step"})
            assert step.status_code == 200, step.text
            state = step.json()
            assert state["paused"] and state["frame_index"] == frame + 1
            assert state["logical_ms"] == (frame + 1) * 10
        assert [entry["emitted_count"] for entry in state["events"]] == [3, 2]
        assert [entry["last_logical_ms"] for entry in state["events"]] == [40, 30]
        partner = app.state.container.services._sessions[session["id"]].partner
        assert (
            partner.ck_s2s_event(keys["client"], "SpeedChanged", 24.5, timeout=2, fuzz_match=False)
            == 24.5
        )
        assert (
            partner.ck_s2s_event(keys["client"], "IgnitionState", 10, timeout=2, fuzz_match=False)
            == 10
        )
        history = client.get("/api/v1/monitor/messages").json()
        emitted = [
            item for item in history if item["metadata"].get("sync_group_id") == state["group_id"]
        ]
        assert sorted(item["metadata"]["sync_logical_ms"] for item in emitted) == [0, 0, 20, 30, 40]
        assert {item["payload_hex"] for item in emitted} == {"422a0000", "41c40000", "09", "0a"}
        assert all(item["metadata"]["member"] == keys["server"] for item in emitted)
        assert (
            client.post(base + "/control", json={"action": "speed", "speed": 3}).status_code == 422
        )
        assert (
            client.post(base + "/control", json={"action": "speed", "speed": 2}).json()[
                "logical_ms"
            ]
            == 50
        )
        assert client.post(base + "/control", json={"action": "resume"}).status_code == 200
        assert client.post(base + "/control", json={"action": "step"}).status_code == 422
        time.sleep(0.07)
        assert (
            client.post(base + "/control", json={"action": "speed", "speed": 0.25}).status_code
            == 200
        )
        paused = client.post(base + "/control", json={"action": "pause"}).json()
        assert paused["active"] and paused["paused"] and paused["frame_index"] > 5
        time.sleep(0.1)
        assert client.get(base).json() == paused, "暂停回执后公共时钟和计数不得继续增长"
        stopped = client.post(base + "/control", json={"action": "stop"}).json()
        assert not stopped["active"] and not stopped["paused"]
        assert all(entry["source_count"] == 0 for entry in stopped["events"])
        assert client.post(root + "/cycles/start", json=command["events"][0]).status_code == 200
        assert (
            client.post(root + "/cycles/stop", json={"member": keys["server"]}).status_code == 200
        )
        operator = partner.sim_operator
        assert client.post(root + "/stop").status_code == 200
        assert operator.process is None or operator.process.poll() is not None
        assert client.get(base).status_code == 404
        actions = [entry["action"] for entry in client.get("/api/v1/audit/events").json()]
        assert all(
            "services.sync." + action in actions
            for action in ("start", "step", "speed", "resume", "pause", "stop")
        )


def test_native_group_preflight_is_all_or_nothing(tmp_path, native_runtime):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        session, keys = create_session(
            client, "VehicleStatus", (FIXTURES / "vehicle_service.arxml").read_bytes()
        )
        root = f"/api/v1/services/sessions/{session['id']}"
        events = [
            event(keys["server"], "UpdateSpeedChangedEvent", 42.5, 24.5, 20),
            event(keys["server"], "UpdateIgnitionStateEvent", 9, 10, 30),
        ]
        invalid = deepcopy(events)
        invalid[1]["sources"][0]["generator"]["step_value"] = 256
        rejected = client.post(root + "/sync/start", json={"events": invalid, "paused": False})
        assert rejected.status_code == 422 and "超限" in rejected.text
        assert not client.get(root + "/sync").json()["active"]
        assert not client.get(root + "/cycles").json()[0]["synchronized"]
        partner = app.state.container.services._sessions[session["id"]].partner
        native_events = [
            {
                **item,
                "member": partner._native_member(item["member"]),
                "function": partner._event_name(item["function"]),
            }
            for item in events
        ]
        for bad in [
            {"paused": 1},
            {"speed": True},
            {"speed": 3},
            {"events": [native_events[0], native_events[0]]},
            {"events": [{**native_events[0], "interval_ms": 1.5}]},
        ]:
            with pytest.raises(NativeOperationError):
                partner.sim_operator.send_request(
                    "event_sync_start",
                    {
                        "group_id": "strict-native",
                        "events": native_events,
                        "speed": 1,
                        "paused": True,
                        **bad,
                    },
                )
        regular = {**events[0], "function": "UpdateSpeedChangedEvent"}
        assert client.post(root + "/cycles/start", json=regular).status_code == 200
        before = client.get(root + "/cycles").json()[0]
        assert client.post(root + "/sync/start", json={"events": events}).status_code == 422
        time.sleep(0.06)
        after = client.get(root + "/cycles").json()[0]
        assert after["running"] and after["emitted_count"] > before["emitted_count"]


def test_releasing_one_sync_owner_stops_whole_group_and_discards_recovery_config(
    tmp_path, native_runtime
):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        session, keys = create_session(
            client, "VehicleStatus", (FIXTURES / "vehicle_service.arxml").read_bytes()
        )
        root = f"/api/v1/services/sessions/{session['id']}"
        command = {"events": [event(keys["server"], "UpdateSpeedChangedEvent", 42.5, 24.5, 20)]}
        assert client.post(root + "/sync/start", json=command).status_code == 200
        partner = app.state.container.services._sessions[session["id"]].partner
        partner.stop_single_partner(keys["server"])
        status = partner.event_sync_status()
        assert not status["active"] and partner._sync_config is None
        assert all(entry["source_count"] == 0 for entry in status["events"])
        time.sleep(0.04)
        assert partner.event_sync_status() == status


def test_two_service_instances_share_clock_and_release_all_owners(tmp_path, native_runtime):
    root = etree.fromstring((FIXTURES / "vehicle_service.arxml").read_bytes())
    provided = root.xpath("//*[local-name()='PROVIDED-SOMEIP-SERVICE-INSTANCE']")[0]
    second = deepcopy(provided)
    second.xpath("./*[local-name()='SHORT-NAME']")[0].text = "SecondInstance"
    second.xpath("./*[local-name()='SERVICE-INSTANCE-ID']")[0].text = "2"
    provided.getparent().append(second)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        imported = client.post(
            "/api/v1/arxml/import", files={"file": ("two-instances.arxml", etree.tostring(root))}
        )
        assert imported.status_code == 200, imported.text
        request = {
            "application_name": "two_instance_clock",
            "application_id": 0x4901,
            "members": {
                f"{role}{instance}": {
                    "service": "VehicleStatus",
                    "role": role,
                    "instance_id": instance,
                }
                for instance in (1, 2)
                for role in ("server", "client")
            },
        }
        started = client.post("/api/v1/services/sessions", json=request)
        assert started.status_code == 200, started.text
        identifier = started.json()["id"]
        base = f"/api/v1/services/sessions/{identifier}/sync"
        partner = app.state.container.services._sessions[identifier].partner
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if all(info.service_status == "START" for info in partner.partner_infos.values()):
                break
            time.sleep(0.01)
        else:
            pytest.fail("两个原生服务实例未就绪")
        time.sleep(0.08)
        command = {
            "events": [
                event("server1_server", "UpdateSpeedChangedEvent", 42.5, 24.5, 20),
                event("server2_server", "UpdateSpeedChangedEvent", 1.25, 2.5, 30),
            ]
        }
        prepared = client.post(base + "/start", json=command)
        assert prepared.status_code == 200, prepared.text
        for _ in range(5):
            assert client.post(base + "/control", json={"action": "step"}).status_code == 200
        assert (
            partner.ck_s2s_event(
                "client1_client", "SpeedChanged", 24.5, timeout=2, fuzz_match=False
            )
            == 24.5
        )
        assert (
            partner.ck_s2s_event("client2_client", "SpeedChanged", 2.5, timeout=2, fuzz_match=False)
            == 2.5
        )
        state = client.get(base).json()
        assert [entry["emitted_count"] for entry in state["events"]] == [3, 2]
        assert [entry["last_logical_ms"] for entry in state["events"]] == [40, 30]
        partner.stop_single_partner("server1_server")
        stopped = partner.event_sync_status()
        assert not stopped["active"] and partner._sync_config is None
        assert not partner.event_cycle_status("server2_server")["synchronized"]
        time.sleep(0.06)
        assert partner.event_sync_status() == stopped
