"""结果比较、原始证据完整性、旧历史不重签及导出取消清理。"""

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from someip_agent.api.artifact_files import _exports, export_file
from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.scenario_cli import verify_main
from someip_agent.workbench.results import verify_evidence
from someip_agent.workbench.run_repository import RunRepository

FIXTURE = Path(__file__).parent / "fixtures" / "vehicle_service.arxml"


@pytest.fixture(autouse=True)
def no_host_credentials(monkeypatch):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)


def create_project(client):
    imported = client.post(
        "/api/v1/arxml/import", files={"file": (FIXTURE.name, FIXTURE.read_bytes())}
    )
    assert imported.status_code == 200, imported.text
    project = client.post(
        "/api/v1/projects",
        json={
            "document": {
                "name": "证据工程",
                "model": client.get("/api/v1/model").json(),
            }
        },
    )
    assert project.status_code == 200, project.text
    return project.json()["id"]


def run(client, project_id, value=18446744073709551615):
    started = client.post(
        "/api/v1/scenarios/runs",
        json={
            "project_id": project_id,
            "definition": {
                "name": "精确结果比较",
                "cases": [{"value": value, "seed": 7}],
                "steps": [
                    {
                        "kind": "assert",
                        "path": ["parameters", "value"],
                        "expected": 18446744073709551615,
                    }
                ],
            },
        },
    )
    assert started.status_code == 202, started.text
    identifier = started.json()["id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = client.get(f"/api/v1/scenarios/runs/{identifier}").json()
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("证据运行未结束")


def test_compare_exact_values_runtime_configuration_and_persistent_inputs(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app) as client:
        project = create_project(client)
        first, second = run(client, project), run(client, project)
        base = f"/api/v1/scenarios/runs/{second['id']}"
        identical = client.get(f"{base}/compare/{first['id']}")
        assert identical.status_code == 200, identical.text
        assert identical.json()["comparable"] and not identical.json()["assertions"][0]["changed"]
        assert (
            identical.json()["assertions"][0]["current"]["result"]["actual"] == 18446744073709551615
        )
        changed = run(client, project, 18446744073709551614)
        difference = client.get(
            f"/api/v1/scenarios/runs/{changed['id']}/compare/{first['id']}"
        ).json()
        assert not difference["comparable"] and difference["status"] == {
            "baseline": "passed",
            "current": "failed",
        }
        assert any(item["field"] == "definition_sha256" for item in difference["metadata_changes"])
        assert difference["assertions"][0]["changed"]
        assert len(first["runtime_identity"]["backend_sha256"]) == 64
        inputs = client.get(base + "/inputs")
        assert inputs.status_code == 200 and inputs.json()["definition"]["cases"][0]["seed"] == 7
        active = client.post(
            "/api/v1/scenarios/runs",
            json={
                "project_id": project,
                "definition": {"name": "未结束", "steps": [{"kind": "delay", "seconds": 30}]},
            },
        ).json()["id"]
        assert (
            client.get(f"/api/v1/scenarios/runs/{active}/compare/{first['id']}").status_code == 409
        )
        assert client.post(f"/api/v1/scenarios/runs/{active}/cancel").status_code == 200
    repo = RunRepository(tmp_path / "someip-agent.sqlite3")
    assert repo.inputs(UUID(first["id"]))["project"]["id"] == project
    with sqlite3.connect(tmp_path / "someip-agent.sqlite3") as db:
        db.execute("UPDATE scenario_runs SET input_json='{}' WHERE id=?", (first["id"],))
    with pytest.raises(ValueError, match="输入完整性"):
        repo.inputs(UUID(first["id"]))


def test_complete_evidence_original_arxml_scoped_audit_and_offline_tamper(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", llm_api_key="DO_NOT_EXPORT_HOST_KEY")
    )
    with TestClient(app) as client:
        identifier = run(client, create_project(client))["id"]
        state = app.state.container
        for _ in range(205):
            state.audit.add(action="unrelated", target="not-this-run")
        response = client.get(f"/api/v1/scenarios/runs/{identifier}/evidence")
        assert response.status_code == 200, response.text
        assert b"DO_NOT_EXPORT_HOST_KEY" not in response.content
        assert "attachment" in response.headers["content-disposition"]
        saved = tmp_path / "evidence.zip"
        saved.write_bytes(response.content)
        manifest = verify_evidence(saved)
        assert (
            manifest["complete"]
            and manifest["source_arxml_included"]
            and manifest["record_complete"]
        )
        assert verify_main([str(saved)]) == 0
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            assert archive.read("source.arxml") == FIXTURE.read_bytes()
            audit = json.loads(archive.read("audit.json"))
            assert {row["target"] for row in audit} == {identifier}
            assert any(row["action"] == "scenario.start" for row in audit)
            assert any(row["action"] == "scenario.finish" for row in audit)
            assert {row["action"] for row in audit}.isdisjoint({"unrelated"})
            original = {name: archive.read(name) for name in archive.namelist()}
        # 不执行内容；替换任一附件，即使 ZIP 本身 CRC 正确仍须被 SHA 拒绝。
        tampered = tmp_path / "tampered.zip"
        with zipfile.ZipFile(tampered, "w") as archive:
            for name, content in original.items():
                archive.writestr(name, content + b" " if name == "inputs.json" else content)
        with pytest.raises(ValueError, match="大小|SHA"):
            verify_evidence(tampered)
        assert verify_main([str(tampered)]) == 2
        # 声明不是宽松真值：不能把 1 当成“完整”。
        malformed = dict(original)
        changed_manifest = json.loads(malformed["manifest.json"])
        changed_manifest["complete"] = 1
        malformed["manifest.json"] = json.dumps(changed_manifest).encode()
        with zipfile.ZipFile(tampered, "w") as archive:
            for name, content in malformed.items():
                archive.writestr(name, content)
        with pytest.raises(ValueError, match="布尔值"):
            verify_evidence(tampered)
        # 即使外层清单重新计算，嵌套录制的分段哈希不符仍必须被拒绝。
        with zipfile.ZipFile(BytesIO(original["recording.zip"])) as archive:
            record = json.loads(archive.read("manifest.json"))
        frame = b"{}\n"
        record.update(
            frame_count=1,
            bytes=len(frame),
            segments=[
                {
                    "number": 0,
                    "first_index": 0,
                    "frame_count": 1,
                    "bytes": len(frame),
                    "sha256": hashlib.sha256(frame).hexdigest(),
                }
            ],
        )
        nested = BytesIO()
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("manifest.json", json.dumps(record))
            archive.writestr("segment-00000.jsonl", b"[]\n")
        malformed = dict(original)
        malformed["recording.zip"] = nested.getvalue()
        changed_manifest = json.loads(original["manifest.json"])
        changed_manifest["files"]["recording.zip"] = {
            "bytes": len(nested.getvalue()),
            "sha256": hashlib.sha256(nested.getvalue()).hexdigest(),
        }
        malformed["manifest.json"] = json.dumps(changed_manifest).encode()
        with zipfile.ZipFile(tampered, "w") as archive:
            for name, content in malformed.items():
                archive.writestr(name, content)
        with pytest.raises(ValueError, match="分段 SHA"):
            verify_evidence(tampered)
        source = state.imported_file_path(manifest["model_source_sha256"], FIXTURE.name)
        source.unlink()
        incomplete = client.get(f"/api/v1/scenarios/runs/{identifier}/evidence")
        assert incomplete.status_code == 200
        saved.write_bytes(incomplete.content)
        assert not verify_evidence(saved)["complete"] and verify_main([str(saved)]) == 1
        assert state.services.statuses() == [] and state.network.list() == []


def test_legacy_history_migrates_without_claiming_sealed_inputs(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        result = run(client, create_project(client))
    body = json.dumps(result, ensure_ascii=False)
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE scenario_runs(id TEXT PRIMARY KEY,started_at TEXT,status TEXT,"
            "result_json TEXT,input_json TEXT,result_sha256 TEXT)"
        )
        db.execute(
            "INSERT INTO scenario_runs VALUES(?,?,?,?,?,?)",
            (
                result["id"],
                result["started_at"],
                result["status"],
                body,
                "{}",
                hashlib.sha256(body.encode()).hexdigest(),
            ),
        )
    migrated = RunRepository(path)
    assert migrated.get(UUID(result["id"])).status == "passed"
    with pytest.raises(ValueError, match="未封存"):
        migrated.inputs(UUID(result["id"]))


@pytest.mark.asyncio
async def test_cancelled_export_waits_for_generation_then_cleans(tmp_path):
    entered, finish = threading.Event(), threading.Event()
    path = tmp_path / "generated.zip"

    def generate():
        entered.set()
        assert finish.wait(3)
        path.write_bytes(b"temporary")
        return path

    task = asyncio.create_task(export_file(generate, "fixture.zip"))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not path.exists() and _exports._value == 2


@pytest.mark.parametrize(
    "field", ["api_key", "API-KEY", "private_key", "password", "network_send_enabled"]
)
def test_scenario_parameters_cannot_export_host_credentials_or_permissions(tmp_path, field):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        result = client.post(
            "/api/v1/scenarios/runs/validate",
            json={
                "name": "拒绝凭据",
                "cases": [{field: "sensitive"}],
                "steps": [{"kind": "delay"}],
            },
        )
        assert result.status_code == 422


def test_native_evidence_contains_actual_consumer_payload_and_binary_identity(
    tmp_path, native_runtime
):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime))
    with TestClient(app) as client:
        identifier = create_project(client)
        view = client.get(f"/api/v1/projects/{identifier}").json()
        view["document"]["services"] = {
            "pair": {
                "application_name": "evidence_native",
                "application_id": 0x4E01,
                "members": {
                    "Provider": {"service": "VehicleStatus", "role": "server"},
                    "Consumer": {"service": "VehicleStatus", "role": "client"},
                },
            }
        }
        saved = client.put(
            f"/api/v1/projects/{identifier}",
            json={"document": view["document"], "expected_revision": view["revision"]},
        )
        assert saved.status_code == 200, saved.text
        started = client.post(
            "/api/v1/scenarios/runs",
            json={
                "project_id": identifier,
                "definition": {
                    "name": "原生证据包",
                    "steps": [
                        {"kind": "start_service", "profile": "pair"},
                        {"kind": "wait_ready", "session": "pair"},
                        {
                            "kind": "cycle_start",
                            "session": "pair",
                            "command": {
                                "member": "Provider_server",
                                "function": "UpdateSpeedChangedEvent",
                                "args": 42.5,
                                "interval_ms": 20,
                            },
                        },
                        {
                            "kind": "wait_message",
                            "session": "pair",
                            "match": {"member": "Consumer_client", "payload_hex": "422a0000"},
                            "save_as": "received",
                        },
                        {
                            "kind": "assert",
                            "path": ["received", "payload_hex"],
                            "expected": "422a0000",
                        },
                    ],
                },
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = client.get(f"/api/v1/scenarios/runs/{run_id}").json()
            if result["status"] != "running":
                break
            time.sleep(0.01)
        assert result["status"] == "passed", json.dumps(result, ensure_ascii=False)
        package = client.get(f"/api/v1/scenarios/runs/{run_id}/evidence")
        assert package.status_code == 200, package.text
        with zipfile.ZipFile(BytesIO(package.content)) as evidence:
            manifest = json.loads(evidence.read("manifest.json"))
            assert manifest["complete"] and manifest["audit_complete"]
            assert len(manifest["runtime_identity"]["native_sha256"]) == 64
            with zipfile.ZipFile(BytesIO(evidence.read("recording.zip"))) as recording:
                messages = [
                    json.loads(line)["message"]
                    for name in recording.namelist()
                    if name.endswith("jsonl")
                    for line in recording.read(name).splitlines()
                ]
                assert any(
                    message["payload_hex"] == "422a0000"
                    and message["metadata"]["member"] == "Consumer_client"
                    for message in messages
                )
        assert all(not item["active"] for item in client.get("/api/v1/services/sessions").json())
