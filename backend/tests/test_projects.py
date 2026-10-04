from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from someip_agent.arxml.parser import ArxmlParser
from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.workbench.projects import (
    ProjectConflict,
    ProjectDocument,
    ProjectRepository,
    ProjectSave,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vehicle_service.arxml"


def document(name="测试工程"):
    return ProjectDocument(name=name, model=ArxmlParser().parse(FIXTURE.read_bytes(), FIXTURE.name))


def test_revision_conflict_backups_and_concurrent_save(tmp_path):
    repo = ProjectRepository(tmp_path / "test.sqlite3")
    original = repo.save(ProjectSave(document=document()))
    request = ProjectSave(document=document("新名字"), expected_revision=1)

    def save():
        try:
            return repo.save(request, original.id).revision
        except ProjectConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: str(save()), range(2))) == ["2", "conflict"]
    assert [row["revision"] for row in repo.backups(original.id)] == [1]
    restored = repo.restore(original.id, 1, 2)
    assert restored.revision == 3
    assert restored.document.name == "测试工程"
    for revision in range(3, 28):
        repo.save(ProjectSave(document=document(), expected_revision=revision), original.id)
    assert len(repo.backups(original.id)) == 20
    assert repo.get(original.id).revision == 28


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_key", "secret"),
        ("native_binary", "/tmp/executable"),
        ("allowed_destinations", ["192.168.1.1"]),
        ("unknown", {}),
        ("format_version", 99),
        ("format_version", True),
    ],
)
def test_reject_sensitive_paths_and_unknown_versions(field, value):
    with pytest.raises(ValidationError):
        ProjectDocument.model_validate({"name": "拒绝", field: value})


def test_migration_and_nested_secret_rejection():
    assert (
        ProjectDocument.model_validate({"name": "老工程", "format_version": 1}).format_version == 2
    )
    with pytest.raises(ValidationError):
        ProjectDocument.model_validate(
            {
                "name": "拒绝",
                "services": {"x": {"members": {"m": {"role": "server", "password": "s"}}}},
            }
        )
    bad = document().model_dump(mode="json")
    bad["model"]["source_name"] = "/tmp/local.arxml"
    with pytest.raises(ValidationError):
        ProjectDocument.model_validate(bad)


@pytest.mark.parametrize("page", ["projects", "network"])
def test_project_management_page_round_trip(tmp_path, page):
    repo = ProjectRepository(tmp_path / "test.sqlite3")
    doc = document()
    doc.workspace.page = page
    saved = repo.save(ProjectSave(document=doc))
    assert repo.get(saved.id).document.workspace.page == page


def test_api_save_export_restore_and_restart_without_execution(tmp_path, monkeypatch):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)
    settings = Settings(_env_file=None, data_dir=tmp_path, llm_api_key="secret-not-exported")
    with TestClient(create_app(settings)) as client:
        app_state = client.app.state.container
        create = client.post(
            "/api/v1/projects", json={"document": document().model_dump(mode="json")}
        )
        assert create.status_code == 200, create.text
        identifier = create.json()["id"]
        opened = client.post(f"/api/v1/projects/{identifier}/open")
        assert opened.status_code == 200, opened.text
        assert client.get("/api/v1/model/services").json()[0]["name"] == "VehicleStatus"
        updated = {**create.json()["document"], "name": "更新工程"}
        saved = client.put(
            f"/api/v1/projects/{identifier}", json={"document": updated, "expected_revision": 1}
        )
        assert saved.status_code == 200
        assert (
            client.put(
                f"/api/v1/projects/{identifier}", json={"document": updated, "expected_revision": 1}
            ).status_code
            == 409
        )
        exported = client.get(f"/api/v1/projects/{identifier}/export")
        assert "secret-not-exported" not in exported.text
        assert "data_dir" not in exported.json()
        assert "attachment" in exported.headers["content-disposition"]
        imported = client.post("/api/v1/projects/import", json=exported.json())
        assert imported.status_code == 200
        assert imported.json()["id"] != identifier
        assert client.get(f"/api/v1/projects/{identifier}/backups").json()[0]["revision"] == 1
        restored = client.post(
            f"/api/v1/projects/{identifier}/restore", json={"revision": 1, "expected_revision": 2}
        )
        assert restored.status_code == 200
        assert restored.json()["revision"] == 3
        assert client.get("/api/v1/projects/../../private.pem").status_code == 404
        assert (
            client.post(
                "/api/v1/projects/import", json={"name": "恶意", "data_dir": "/tmp"}
            ).status_code
            == 422
        )
        assert app_state.services.statuses() == []
        assert app_state.simulator.list() == []
        assert app_state.network.list() == []
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/v1/projects/current").json()["document"]["name"] == "测试工程"
        assert restarted.get("/api/v1/model/services").json()[0]["name"] == "VehicleStatus"
        assert restarted.get("/api/v1/services/sessions").json() == []
        assert restarted.get("/api/v1/simulation").json() == []
        assert restarted.get("/api/v1/network/listeners").json() == []


@pytest.mark.parametrize("kind", ["constant", "random", "sequence"])
def test_exact_integer_simulation_drafts_restart_without_execution(tmp_path, monkeypatch, kind):
    monkeypatch.setattr("someip_agent.agent.service.keyring.get_password", lambda *_: None)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    doc = document("精确整数工程").model_dump(mode="json")
    doc["simulations"] = [
        {
            "service_id": 0x1234,
            "method_id": 0x8001,
            "generator": {
                "kind": kind,
                "data_type": data_type,
                "minimum": low,
                "maximum": high,
                "initial": initial,
                "seed": 18446744073709551615,
                "sequence": [initial, high, low] if kind == "sequence" else [],
            },
        }
        for data_type, low, high, initial in [
            ("uint64", 0, 18446744073709551615, 9007199254740993),
            ("int64", -9223372036854775808, 9223372036854775807, -9223372036854775808),
        ]
    ]
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/projects", json={"document": doc})
        assert created.status_code == 200, created.text
        identifier = created.json()["id"]
        assert client.post(f"/api/v1/projects/{identifier}/open").status_code == 200
        exported = client.get(f"/api/v1/projects/{identifier}/export").json()["simulations"]
        assert exported[0]["generator"]["initial"] == 9007199254740993
        assert exported[1]["generator"]["initial"] == -9223372036854775808
        assert all(type(item["generator"]["initial"]) is int for item in exported)
        assert all(item["generator"]["seed"] == 18446744073709551615 for item in exported)
        assert all(item["generator"]["kind"] == kind for item in exported)
        if kind == "sequence":
            assert exported[0]["generator"]["sequence"] == [
                9007199254740993,
                18446744073709551615,
                0,
            ]
            assert exported[1]["generator"]["sequence"] == [
                -9223372036854775808,
                9223372036854775807,
                -9223372036854775808,
            ]
    with TestClient(create_app(settings)) as restarted:
        restored = restarted.get("/api/v1/projects/current").json()["document"]["simulations"]
        assert restored == exported
        assert restarted.get("/api/v1/simulation").json() == []
        assert restarted.get("/api/v1/services/sessions").json() == []
        assert restarted.get("/api/v1/network/listeners").json() == []


def test_native_active_session_blocks_open_and_drafts_do_not_start(tmp_path, native_runtime):
    settings = Settings(_env_file=None, data_dir=tmp_path, native_binary=native_runtime)
    with TestClient(create_app(settings)) as client:
        client.post("/api/v1/arxml/import", files={"file": (FIXTURE.name, FIXTURE.read_bytes())})
        request = {
            "application_name": "project_guard",
            "application_id": 0x4901,
            "members": {"Provider": {"service": "VehicleStatus", "role": "server"}},
        }
        created = client.post("/api/v1/services/sessions", json=request)
        assert created.status_code == 200, created.text
        session = created.json()
        doc = document().model_dump(mode="json")
        doc["services"] = {"saved": request}
        doc["cycles"] = [
            {
                "service_profile": "saved",
                "command": {
                    "member": "Provider_server",
                    "function": "UpdateSpeedChangedEvent",
                    "args": 24.5,
                    "interval_ms": 29,
                },
            }
        ]
        saved = client.post("/api/v1/projects", json={"document": doc})
        assert saved.status_code == 200, saved.text
        identifier = saved.json()["id"]
        assert client.post(f"/api/v1/projects/{identifier}/open").status_code == 409
        assert client.get("/api/v1/projects/current").json() is None
        assert (
            client.get(f"/api/v1/services/sessions/{session['id']}/cycles").json()[0]["running"]
            is False
        )
        assert client.post(f"/api/v1/services/sessions/{session['id']}/stop").status_code == 200
        assert client.post(f"/api/v1/projects/{identifier}/open").status_code == 200
        assert not any(item["active"] for item in client.get("/api/v1/services/sessions").json())
        assert (
            client.get("/api/v1/projects/current").json()["document"]["cycles"][0]["command"][
                "args"
            ]
            == 24.5
        )
