"""公共时钟工程／场景接入：真实原生、冻结证据与自有资源清理。"""

import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from copy import deepcopy
from uuid import UUID

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_scenarios import FIXTURE, project, request, wait_run

from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.runtime.service_models import ServiceSyncCommand
from someip_agent.soa.catalog import NativeCatalogRequest
from someip_agent.state import ApplicationState
from someip_agent.workbench.projects import ProjectDocument, ProjectSave, SyncDraft
from someip_agent.workbench.results import verify_evidence
from someip_agent.workbench.scenario_models import ScenarioDefinition


@pytest.fixture(autouse=True)
def no_host_credentials(monkeypatch):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)


def sync_command():
    return {
        "paused": True,
        "speed": 0.5,
        "events": [
            {
                "member": "Provider_server",
                "function": "UpdateSpeedChangedEvent",
                "args": 0,
                "interval_ms": 20,
                "csv_text": "time_ms,\n0,42.5\n29,24.5\n",
            },
            {
                "member": "Provider_server",
                "function": "UpdateIgnitionStateEvent",
                "args": 0,
                "interval_ms": 30,
                "sources": [
                    {
                        "path": "",
                        "generator": {
                            "kind": "state_machine",
                            "seed": 18446744073709551615,
                            "initial_state": "cold",
                            "states": [
                                {"name": "cold", "value": 9, "duration_ms": 29, "next": "warm"},
                                {"name": "warm", "value": 10},
                            ],
                        },
                    }
                ],
            },
        ],
    }


def save_sync(state):
    saved = project(state)
    doc = saved.document.model_copy(deep=True)
    doc.sync_groups = [
        SyncDraft(service_profile="pair", command=ServiceSyncCommand.model_validate(sync_command()))
    ]
    return state.projects.save(ProjectSave(document=doc, expected_revision=1), saved.id)


def sync_steps(inline=False):
    start = {"kind": "sync_start", "session": "pair", "save_as": "prepared"}
    if inline:
        start["command"] = sync_command()
    else:
        start["profile"] = "pair"
    steps = [
        {"kind": "start_service", "profile": "pair"},
        {"kind": "wait_ready", "session": "pair"},
        start,
        {"kind": "assert", "path": ["prepared", "paused"], "expected": True},
        {"kind": "assert", "path": ["prepared", "frame_index"], "expected": 0},
        {"kind": "assert", "path": ["prepared", "interval_ms"], "expected": 10},
    ]
    steps.extend(
        {"kind": "sync_control", "session": "pair", "command": {"action": "step"}} for _ in range(7)
    )
    steps.extend(
        [
            {"kind": "sync_status", "session": "pair", "save_as": "stepped"},
            {"kind": "assert", "path": ["stepped", "frame_index"], "expected": 7},
            {"kind": "assert", "path": ["stepped", "logical_ms"], "expected": 70},
            {"kind": "assert", "path": ["stepped", "events", 0, "emitted_count"], "expected": 4},
            {"kind": "assert", "path": ["stepped", "events", 1, "emitted_count"], "expected": 3},
            {
                "kind": "assert",
                "path": ["stepped", "events", 1, "active_states", ""],
                "expected": "warm",
            },
            {
                "kind": "wait_message",
                "session": "pair",
                "match": {"member": "Consumer_client", "payload_hex": "41c40000"},
                "save_as": "speed",
            },
            {
                "kind": "wait_message",
                "session": "pair",
                "match": {"member": "Consumer_client", "payload_hex": "0a"},
                "save_as": "ignition",
            },
            {"kind": "assert", "path": ["speed", "signal_values", ""], "expected": 24.5},
            {"kind": "assert", "path": ["ignition", "signal_values", ""], "expected": 10},
            {
                "kind": "sync_control",
                "session": "pair",
                "command": {"action": "speed", "speed": {"$param": "speed"}},
                "save_as": "rated",
            },
            {"kind": "assert", "path": ["rated", "speed"], "expected": {"$param": "speed"}},
            {"kind": "assert", "path": ["rated", "logical_ms"], "expected": 70},
            {"kind": "sync_control", "session": "pair", "command": {"action": "resume"}},
            {"kind": "delay", "seconds": 0.05},
            {
                "kind": "sync_control",
                "session": "pair",
                "command": {"action": "pause"},
                "save_as": "paused",
            },
            {"kind": "delay", "seconds": 0.05},
            {"kind": "sync_status", "session": "pair", "save_as": "stable"},
            # 二者整体状态在 Python 断言中精确比较；场景 eq 无动态表达式。
            {"kind": "assert", "path": ["stable", "paused"], "expected": True},
            {"kind": "sync_stop", "session": "pair", "save_as": "stopped"},
            {"kind": "assert", "path": ["stopped", "active"], "expected": False},
        ]
    )
    return steps


@pytest.mark.parametrize("version", [1, 2])
def test_old_projects_get_empty_sync_drafts_without_running(version):
    doc = ProjectDocument.model_validate({"name": "旧格式", "format_version": version})
    assert doc.format_version == 2 and doc.sync_groups == []


@pytest.mark.parametrize(
    "change",
    [
        {"service_profile": "missing"},
        {"command": {"events": []}},
        {"command": {"events": [{"member": "P", "function": "E"}], "group_id": "runtime"}},
        {
            "command": {
                "events": [{"member": "P", "function": "E", "args": {"private_key": "secret"}}]
            }
        },
    ],
)
def test_sync_project_rejects_unbound_runtime_and_secret_fields(tmp_path, change):
    state = ApplicationState(Settings(_env_file=None, data_dir=tmp_path))
    doc = project(state).document.model_dump(mode="json")
    doc["sync_groups"] = [{"service_profile": "pair", "command": sync_command(), **change}]
    with pytest.raises(ValidationError):
        ProjectDocument.model_validate(doc)
    doc["sync_groups"] = [{"service_profile": "pair", "command": sync_command()}] * 2
    with pytest.raises(ValidationError, match="最多保存一个"):
        ProjectDocument.model_validate(doc)


@pytest.mark.parametrize(
    "step",
    [
        {"kind": "sync_start", "session": "pair"},
        {"kind": "sync_start", "session": "pair", "profile": "pair", "command": {}},
        {"kind": "sync_status"},
        {"kind": "sync_control", "session": "pair"},
        {"kind": "sync_stop", "session": "pair", "command": {"action": "stop"}},
        {
            "kind": "parallel",
            "children": [
                {"kind": "sync_control", "session": "pair", "command": {"action": "step"}}
            ],
        },
    ],
)
def test_scenario_sync_steps_are_explicit_and_serial(step):
    with pytest.raises(ValidationError):
        ScenarioDefinition(name="严格同步", steps=[step])
    with pytest.raises(ValidationError, match="清理步骤"):
        ScenarioDefinition(
            name="禁止清理重启",
            steps=[{"kind": "delay"}],
            cleanup=[{"kind": "sync_control", "session": "pair", "command": {"action": "resume"}}],
        )


def test_sync_drafts_export_import_backup_restart_without_execution(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        saved = save_sync(client.app.state.container)
        exported = client.get(f"/api/v1/projects/{saved.id}/export").json()
        groups = exported["sync_groups"]
        assert (
            groups[0]["command"]["events"][0]["csv_text"] == sync_command()["events"][0]["csv_text"]
        )
        assert (
            groups[0]["command"]["events"][1]["sources"][0]["generator"]["seed"]
            == 18446744073709551615
        )
        assert groups[0]["command"]["paused"] and groups[0]["command"]["speed"] == 0.5
        imported = client.post("/api/v1/projects/import", json=exported)
        assert imported.status_code == 200 and imported.json()["document"]["sync_groups"] == groups
        assert imported.json()["id"] != str(saved.id)
        changed = deepcopy(exported)
        changed["sync_groups"][0]["command"]["speed"] = 4
        assert (
            client.put(
                f"/api/v1/projects/{saved.id}", json={"document": changed, "expected_revision": 2}
            ).status_code
            == 200
        )
        restored = client.post(
            f"/api/v1/projects/{saved.id}/restore", json={"revision": 2, "expected_revision": 3}
        )
        assert restored.status_code == 200 and restored.json()["document"]["sync_groups"] == groups
        assert client.post(f"/api/v1/projects/{saved.id}/open").status_code == 200
        assert client.get("/api/v1/services/sessions").json() == []
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/projects/current").json()["document"]["sync_groups"] == groups
        assert client.get("/api/v1/services/sessions").json() == []
        assert client.get("/api/v1/simulation").json() == []
        assert client.get("/api/v1/network/listeners").json() == []


@pytest.mark.parametrize("inline", [False, True])
def test_actual_sync_scenario_public_clock_sources_frozen_inputs_and_cleanup(
    tmp_path, native_runtime, inline
):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        state = app.state.container
        saved = save_sync(state)
        definition = request(
            saved,
            sync_steps(inline),
            cases=[{"speed": 0.25}, {"speed": 4.0}],
            cleanup=[{"kind": "sync_stop", "session": "pair"}],
        )
        started = client.post(
            "/api/v1/scenarios/runs", json=definition.model_dump(mode="json", exclude_unset=True)
        )
        assert started.status_code == 202, started.text
        result = wait_run(client, started.json()["id"], 30)
        assert result["status"] == "passed" and result["cleanup_complete"], result
        for case in range(2):
            results = [step for step in result["steps"] if step["case"] == case]
            pauses = [
                step["result"]
                for step in results
                if step["kind"] == "sync_control"
                and step["result"]["frame_index"] > 7
                and step["result"]["paused"]
            ]
            statuses = [step["result"] for step in results if step["kind"] == "sync_status"]
            assert pauses[-1] == statuses[-1], "暂停后逻辑帧和所有源状态不得增长"
        frozen = client.get(f"/api/v1/scenarios/runs/{result['id']}/inputs").json()
        assert (
            frozen["project"]["document"]["sync_groups"]
            == saved.document.model_dump(mode="json")["sync_groups"]
        )
        assert (
            frozen["definition"]["steps"]
            == definition.definition.model_dump(mode="json", exclude_unset=True)["steps"]
        )
        frames = client.get(f"/api/v1/recordings/{result['recording_id']}/frames").json()["frames"]
        tx = [
            frame["message"]
            for frame in frames
            if frame["message"]["metadata"].get("sync_group_id")
        ]
        assert {message["payload_hex"] for message in tx} == {"422a0000", "41c40000", "09", "0a"}
        group_ids = {message["metadata"]["sync_group_id"] for message in tx}
        assert len(group_ids) == 2, "每个参数用例必须创建独立原生时钟"
        for identifier in group_ids:
            early = [
                message
                for message in tx
                if message["metadata"]["sync_group_id"] == identifier
                and message["metadata"]["sync_logical_ms"] < 70
            ]
            assert sorted(message["metadata"]["sync_logical_ms"] for message in early) == [
                0,
                0,
                20,
                30,
                40,
                60,
                60,
            ]
        assert not any(session.active for session in state.services.statuses())


@pytest.mark.parametrize("late_failure", [False, True])
@pytest.mark.asyncio
async def test_cancel_during_actual_sync_write_settles_then_closes_only_owned(
    tmp_path, native_runtime, monkeypatch, late_failure
):
    state = ApplicationState(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    )
    saved = save_sync(state)
    user = await state.services.start(
        saved.document.model,
        NativeCatalogRequest(
            application_name="user_sync",
            application_id=0x4B02,
            members={"User": {"service": "VehicleStatus", "role": "server"}},
        ),
    )
    user_group = await state.services.start_sync(
        user.id,
        ServiceSyncCommand(
            events=[{"member": "User_server", "function": "UpdateSpeedChangedEvent", "args": 42.5}]
        ),
    )
    entered, finish = asyncio.Event(), asyncio.Event()
    original = state.services.start_sync
    owned = []

    async def delayed(identifier, command):
        result = await original(identifier, command)
        owned.append(state.services._sessions[identifier].partner.sim_operator)
        entered.set()
        await finish.wait()
        if late_failure:
            raise ValueError("模拟同步写命令的迟到失败")
        return result

    monkeypatch.setattr(state.services, "start_sync", delayed)
    started = await state.scenarios.start(
        request(
            saved,
            [
                {"kind": "start_service", "profile": "pair"},
                {"kind": "sync_start", "session": "pair", "profile": "pair"},
            ],
        ),
        "同步写窗口取消",
    )
    try:
        await asyncio.wait_for(entered.wait(), 10)
        cancelling = asyncio.create_task(state.scenarios.cancel(started.id))
        await asyncio.sleep(0)
        assert not cancelling.done(), "不能在原生写命令仍可能生效时提前返回已清理"
        finish.set()
        result = await asyncio.wait_for(cancelling, 10)
        assert result.status == "cancelled" and result.cleanup_complete
        assert [item.id for item in state.services.statuses() if item.active] == [user.id]
        assert await state.services.sync_status(user.id) == user_group
        assert all(
            operator.process is None or operator.process.poll() is not None for operator in owned
        )
        assert state.recordings.get(result.recording_id).state == "stopped"
    finally:
        finish.set()
        await state.shutdown()


@pytest.mark.parametrize(
    "failure", ["missing_draft", "wrong_profile", "foreign_session", "bad_event"]
)
@pytest.mark.asyncio
async def test_sync_failure_does_not_touch_foreign_clock_or_partially_send(
    tmp_path, native_runtime, failure
):
    state = ApplicationState(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    )
    saved = save_sync(state)
    user = await state.services.start(
        saved.document.model,
        NativeCatalogRequest(
            application_name="foreign_clock",
            application_id=0x4B02,
            members={"User": {"service": "VehicleStatus", "role": "server"}},
        ),
    )
    unchanged = await state.services.start_sync(
        user.id,
        ServiceSyncCommand(
            events=[{"member": "User_server", "function": "UpdateSpeedChangedEvent", "args": 42.5}]
        ),
    )
    step = {"kind": "sync_start", "session": "pair", "profile": "pair"}
    if failure == "missing_draft":
        doc = saved.document.model_copy(deep=True)
        doc.sync_groups = []
        saved = state.projects.save(ProjectSave(document=doc, expected_revision=2), saved.id)
    elif failure == "wrong_profile":
        step["profile"] = "other"
    elif failure == "foreign_session":
        step = {"kind": "sync_control", "session": str(user.id), "command": {"action": "resume"}}
    else:
        command = sync_command()
        command["paused"] = False
        command["events"][1]["function"] = "NotAnEvent"
        step = {"kind": "sync_start", "session": "pair", "command": command}
    try:
        started = await state.scenarios.start(
            request(saved, [{"kind": "start_service", "profile": "pair"}, step]), "同步拒绝归属"
        )
        result = await state.scenarios.wait(started.id)
        assert result.status == "failed" and result.cleanup_complete
        assert await state.services.sync_status(user.id) == unchanged
        assert [item.id for item in state.services.statuses() if item.active] == [user.id]
        frames = state.recordings.frames(result.recording_id)["frames"]
        assert not any(frame["message"]["metadata"].get("sync_group_id") for frame in frames)
    finally:
        await state.shutdown()


@pytest.mark.asyncio
async def test_sync_run_uses_frozen_draft_after_project_is_edited(
    tmp_path, native_runtime, monkeypatch
):
    state = ApplicationState(
        Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    )
    saved = save_sync(state)
    original = state.services.start_sync
    observed = []

    async def edit_then_start(identifier, command):
        doc = saved.document.model_copy(deep=True)
        doc.sync_groups[0].command.events[0].csv_text = "time_ms,\n0,99\n"
        state.projects.save(ProjectSave(document=doc, expected_revision=2), saved.id)
        observed.append(command.model_dump(mode="json"))
        return await original(identifier, command)

    monkeypatch.setattr(state.services, "start_sync", edit_then_start)
    try:
        started = await state.scenarios.start(
            request(saved, sync_steps(), cases=[{"speed": 1.0}]), "冻结同步输入"
        )
        result = await state.scenarios.wait(started.id)
        assert result.status == "passed" and result.cleanup_complete
        assert observed == [saved.document.sync_groups[0].command.model_dump(mode="json")]
        frozen = state.runs.inputs(result.id)["project"]
        assert frozen["revision"] == 2
        assert (
            frozen["document"]["sync_groups"][0]["command"]["events"][0]["csv_text"]
            == sync_command()["events"][0]["csv_text"]
        )
        assert state.projects.get(saved.id).revision == 3
    finally:
        await state.shutdown()


def test_actual_headless_sync_cli_http_native_receipts_and_complete_evidence(
    tmp_path, native_runtime
):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", native_binary=native_runtime)
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline
                time.sleep(0.01)
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
                imported = client.post(
                    "/api/v1/arxml/import", files={"file": (FIXTURE.name, FIXTURE.read_bytes())}
                )
                assert imported.status_code == 200, imported.text
                saved = save_sync(app.state.container)
                definition = request(
                    saved,
                    sync_steps(),
                    cases=[{"speed": 2.0}],
                    cleanup=[{"kind": "sync_stop", "session": "pair"}],
                ).definition.model_dump(mode="json", exclude_unset=True)
                path = tmp_path / "scenario.json"
                path.write_text(json.dumps(definition, ensure_ascii=False), encoding="utf-8")
                executed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "someip_agent.main",
                        "scenario",
                        "--server",
                        f"http://127.0.0.1:{port}",
                        "--project",
                        str(saved.id),
                        "--definition",
                        str(path),
                        "--result",
                        str(tmp_path / "result.json"),
                        "--junit",
                        str(tmp_path / "junit.xml"),
                        "--html",
                        str(tmp_path / "report.html"),
                        "--evidence",
                        str(tmp_path / "evidence.zip"),
                    ],
                    timeout=45,
                    capture_output=True,
                    text=True,
                )
                assert executed.returncode == 0, executed.stdout + executed.stderr
                result = json.loads((tmp_path / "result.json").read_text())
                assert result["status"] == "passed" and result["cleanup_complete"]
                assert verify_evidence(tmp_path / "evidence.zip")["complete"]
                assert app.state.container.runs.inputs(UUID(result["id"]))["project"]["document"][
                    "sync_groups"
                ]
                assert client.get("/api/v1/health").json()["active_service_sessions"] == 0
        finally:
            server.should_exit = True
            thread.join(15)
            assert not thread.is_alive()
