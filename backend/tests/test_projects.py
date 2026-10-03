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
