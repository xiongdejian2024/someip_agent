"""产品场景的确定性判据、真实原生操作、归属清理和持久报告。"""

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4
from xml.etree.ElementTree import fromstring

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from someip_agent.arxml.parser import ArxmlParser
from someip_agent.config import Settings
from someip_agent.domain.models import MonitorMessage
from someip_agent.main import create_app
from someip_agent.soa.catalog import NativeCatalogRequest
from someip_agent.state import ApplicationState
from someip_agent.workbench.projects import ProjectDocument, ProjectSave
from someip_agent.workbench.recordings import RecordingRequest
from someip_agent.workbench.run_repository import RunRepository
from someip_agent.workbench.scenario_models import ScenarioDefinition, ScenarioRunRequest

FIXTURE = Path(__file__).parent / "fixtures" / "vehicle_service.arxml"


@pytest.fixture(autouse=True)
def no_host_credentials(monkeypatch):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)


def project(state):
    return state.projects.save(
        ProjectSave(
            document=ProjectDocument(
                name="场景工程",
                model=ArxmlParser().parse(FIXTURE.read_bytes(), FIXTURE.name),
                services={
                    "pair": NativeCatalogRequest.model_validate(
                        {
                            "application_name": "scenario_pair",
                            "application_id": 0x4B01,
                            "members": {
                                "Provider": {"service": "VehicleStatus", "role": "server"},
                                "Consumer": {"service": "VehicleStatus", "role": "client"},
                            },
                        }
                    )
                },
            )
        )
    )


def request(saved, steps, **kwargs):
    return ScenarioRunRequest(
        project_id=saved.id,
        definition=ScenarioDefinition.model_validate(
            {
                "name": "<script>测试场景</script>",
                "steps": steps,
                **kwargs,
            }
        ),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"format_version": True},
        {"format_version": 2},
        {"script": "print(1)"},
        {"steps": [{"kind": "delay", "url": "https://example.com"}]},
        {"steps": [{"kind": "assert", "path": []}]},
        {"steps": [{"kind": "parallel", "children": [{"kind": "start_service", "profile": "x"}]}]},
        {"steps": [{"kind": "wait_message", "session": "x", "listener": "0"}]},
    ],
)
def test_strict_declarative_schema(change):
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate({"name": "拒绝", "steps": [{"kind": "delay"}], **change})


@pytest.mark.asyncio
async def test_parameters_int64_failed_missing_and_persistent_reports(tmp_path):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    saved = project(state)
    definition = request(
        saved,
        [{"kind": "assert", "path": ["parameters", "large"], "expected": {"$param": "large"}}],
        cases=[{"large": 18446744073709551615}, {"large": -9223372036854775808}],
    )
    started = await state.scenarios.start(definition, "精确整数关联请求")
    result = await state.scenarios.wait(started.id)
    assert result.status == "passed" and len(result.steps) == 2 and result.cleanup_complete
    assert result.steps[0].result["actual"] == 18446744073709551615
    assert result.recording_id and state.recordings.get(result.recording_id).state == "stopped"
    assert state.runs.inputs(result.id)["definition"]["steps"][0]["expected"] == {"$param": "large"}
    # 未完成的替换发生在 dispatch 之前，仍必须失败，不能只依据 steps 的空列表判通过。
    bad = await state.scenarios.start(
        request(
            saved, [{"kind": "assert", "path": ["parameters"], "expected": {"$param": "missing"}}]
        ),
        "坏参数",
    )
    failed = await state.scenarios.wait(bad.id)
    assert failed.status == "failed" and "参数不存在" in failed.error
    suite = fromstring(state.runs.junit(failed.id))
    assert int(suite.attrib["failures"]) >= 1
    assert "<script>" not in state.runs.report(result.id)
    assert "&lt;script&gt;" in state.runs.report(result.id)
    assert state.monitor._subscribers == set()
    await state.shutdown()
    reopened = RunRepository(tmp_path / "someip-agent.sqlite3")
    assert reopened.get(result.id).status == "passed"
    assert reopened.get(result.id).request_id == "精确整数关联请求"
    with sqlite3.connect(tmp_path / "someip-agent.sqlite3") as db:
        db.execute("UPDATE scenario_runs SET result_json='{}' WHERE id=?", (str(result.id),))
    with pytest.raises(ValueError, match="完整性"):
        reopened.get(result.id)


@pytest.mark.asyncio
async def test_cancel_during_creation_registers_and_cleans_only_owned(tmp_path, monkeypatch):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    saved = project(state)
    entered, finish = asyncio.Event(), asyncio.Event()
    owned, existing = str(uuid4()), str(uuid4())
    live = {existing}

    async def creating(*_):
        entered.set()
        await finish.wait()
        live.add(owned)
        return SimpleNamespace(id=owned, model_dump=lambda **_: {"id": owned})

    async def stopping(identifier):
        live.remove(identifier)

    monkeypatch.setattr(state.services, "start", creating)
    monkeypatch.setattr(state.services, "stop", stopping)
    started = await state.scenarios.start(
        request(saved, [{"kind": "start_service", "profile": "pair"}]), "取消"
    )
    await asyncio.wait_for(entered.wait(), 2)
    cancelling = asyncio.create_task(state.scenarios.cancel(started.id))
    await asyncio.sleep(0)
    finish.set()
    result = await asyncio.wait_for(cancelling, 3)
    assert result.status == "cancelled" and result.cleanup_complete
    assert live == {existing} and not state.scenarios._runs and not state.monitor._subscribers
    assert state.recordings.get(result.recording_id).state == "stopped"
    await state.shutdown()


@pytest.mark.asyncio
async def test_immediate_cancel_and_model_precondition(tmp_path):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    saved = project(state)
    with pytest.raises(ValueError, match="前置条件"):
        await state.scenarios.start(
            request(saved, [{"kind": "delay"}], model_source_sha256="0" * 64), "前置"
        )
    assert state.runs.list() == []
    for _ in range(3):
        started = await state.scenarios.start(
            request(saved, [{"kind": "delay", "seconds": 30}]), "立即取消"
        )
        result = await state.scenarios.cancel(started.id)
        assert result.status == "cancelled" and result.cleanup_complete
    assert not state.monitor._subscribers and not state.scenarios._runs
    await state.shutdown()


@pytest.mark.asyncio
async def test_scope_filters_and_more_than_sixteen_sources(tmp_path):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    started = await state.recordings.start(
        RecordingRequest(service_session_ids=[], listener_ids=[])
    )
    identifiers = [uuid4() for _ in range(20)]
    for identifier in identifiers:
        state.recordings.permit_source(started.id, identifier)
        await state.monitor.publish(
            MonitorMessage(
                service_id=0x1234,
                method_id=0x8001,
                metadata={"service_session_id": str(identifier)},
            )
        )
    await state.monitor.publish(
        MonitorMessage(
            service_id=0x1234, method_id=0x8001, metadata={"service_session_id": str(uuid4())}
        )
    )
    stopped = await state.recordings.stop(started.id)
    assert stopped.frame_count == 20 and stopped.skipped_by_filter == 1
    frames = state.recordings.frames(started.id)["frames"]
    assert {UUID(frame["message"]["metadata"]["service_session_id"]) for frame in frames} == set(
        identifiers
    )
    await state.shutdown()


def wait_run(client, identifier, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/scenarios/runs/{identifier}")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("产品场景没有在规定时间内结束")


def test_scenario_api_and_restart_interrupted(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app) as client:
        state = app.state.container
        saved = project(state)
        draft = {"name": "仅校验", "steps": [{"kind": "delay"}]}
        checked = client.post("/api/v1/scenarios/runs/validate", json=draft)
        assert checked.status_code == 200 and checked.json() == draft
        assert state.runs.list() == [] and state.recordings.list() == []
        response = client.post(
            "/api/v1/scenarios/runs",
            json=request(saved, [{"kind": "delay"}]).model_dump(mode="json", exclude_unset=True),
            headers={"X-Request-ID": "scenario-request-test"},
        )
        assert response.status_code == 202, response.text
        identifier = response.json()["id"]
        result = wait_run(client, identifier)
        assert result["status"] == "passed" and result["request_id"] == "scenario-request-test"
        assert client.get(f"/api/v1/scenarios/runs/{identifier}/junit").status_code == 200
        report = client.get(f"/api/v1/scenarios/runs/{identifier}/report")
        assert (
            report.status_code == 200
            and report.headers["content-security-policy"] == "default-src 'none'"
        )
        assert (
            client.post(f"/api/v1/scenarios/runs/{identifier}/cancel").json()["status"] == "passed"
        )
        assert client.get(f"/api/v1/scenarios/runs/{uuid4()}").status_code == 404
        assert client.get("/api/v1/scenarios/runs?limit=501").status_code == 422
        # 复用已有结果创建进程中断夹具，不启动任何后台资源。
        interrupted = state.runs.get(UUID(identifier)).model_copy(
            update={"id": uuid4(), "status": "running", "cleanup_complete": False}
        )
        state.runs.save(interrupted, inputs={"fixture": True})
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as restarted:
        result = restarted.get(f"/api/v1/scenarios/runs/{interrupted.id}").json()
        assert result["status"] == "interrupted" and not result["cleanup_complete"]
        assert restarted.get("/api/v1/services/sessions").json() == []


def test_native_parameterized_cycles_rpc_failure_and_cancel_ownership(tmp_path, native_runtime):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        state = app.state.container
        saved = project(state)
        # 用户已有会话必须在所有场景结束后仍活动，不使用 stop-all。
        assert client.post(f"/api/v1/projects/{saved.id}/open").status_code == 200
        user = client.post(
            "/api/v1/services/sessions",
            json={
                "application_name": "user_session",
                "application_id": 0x4B02,
                "members": {"User": {"service": "VehicleStatus", "role": "server"}},
            },
        )
        assert user.status_code == 200, user.text
        definition = request(
            saved,
            [
                {"kind": "start_service", "profile": "pair"},
                {"kind": "wait_ready", "session": "pair"},
                {
                    "kind": "cycle_start",
                    "session": "pair",
                    "command": {
                        "member": "Provider_server",
                        "function": "UpdateSpeedChangedEvent",
                        "args": {"$param": "speed"},
                        "interval_ms": 20,
                    },
                },
                {
                    "kind": "wait_message",
                    "session": "pair",
                    "match": {
                        "member": "Consumer_client",
                        "direction": "sim",
                        "payload_hex": "422a0000",
                    },
                    "save_as": "event",
                },
                {"kind": "assert", "path": ["event", "payload_hex"], "expected": "422a0000"},
                {
                    "kind": "parallel",
                    "children": [
                        {
                            "kind": "call",
                            "session": "pair",
                            "command": {
                                "member": "Consumer_client",
                                "function": "SetSpeed",
                                "args": {"Speed": 42},
                            },
                            "save_as": "response",
                        },
                        {
                            "kind": "respond_next",
                            "session": "pair",
                            "command": {
                                "member": "Provider_server",
                                "function": "SetSpeed",
                                "args": {"Accepted": True},
                            },
                        },
                    ],
                },
                {"kind": "assert", "path": ["response", "status"], "expected": "responded"},
            ],
            cases=[{"speed": 42.5}, {"speed": 42.5}],
            cleanup=[
                {"kind": "cycle_stop", "session": "pair", "command": {"member": "Provider_server"}}
            ],
        )
        response = client.post(
            "/api/v1/scenarios/runs", json=definition.model_dump(mode="json", exclude_unset=True)
        )
        assert response.status_code == 202, response.text
        result = wait_run(client, response.json()["id"], 30)
        assert result["status"] == "passed", json.dumps(result, ensure_ascii=False)
        assert result["cleanup_complete"]
        frames = client.get(f"/api/v1/recordings/{result['recording_id']}/frames").json()["frames"]
        assert any(
            frame["message"]["payload_hex"] == "422a0000"
            and frame["message"]["metadata"]["member"] == "Consumer_client"
            for frame in frames
        )
        assert all(
            frame["message"]["metadata"].get("service_session_id") != user.json()["id"]
            for frame in frames
        )
        bad = request(
            saved,
            [
                {"kind": "start_service", "profile": "pair"},
                {"kind": "assert", "path": ["parameters", "x"], "expected": 2},
            ],
            cases=[{"x": 1}],
        )
        failed = client.post(
            "/api/v1/scenarios/runs", json=bad.model_dump(mode="json", exclude_unset=True)
        )
        assert wait_run(client, failed.json()["id"])["status"] == "failed"
        long = request(
            saved, [{"kind": "start_service", "profile": "pair"}, {"kind": "delay", "seconds": 30}]
        )
        started = client.post(
            "/api/v1/scenarios/runs", json=long.model_dump(mode="json", exclude_unset=True)
        )
        cancelled = client.post(f"/api/v1/scenarios/runs/{started.json()['id']}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled" and cancelled.json()["cleanup_complete"]
        active = [item for item in client.get("/api/v1/services/sessions").json() if item["active"]]
        assert [item["id"] for item in active] == [user.json()["id"]]
        actions = [item["action"] for item in client.get("/api/v1/audit/events").json()]
        assert (
            "scenario.start" in actions
            and "scenario.step" in actions
            and "scenario.finish" in actions
        )
