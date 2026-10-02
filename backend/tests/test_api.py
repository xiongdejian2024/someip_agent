from pathlib import Path

from fastapi.testclient import TestClient

from someip_agent.config import Settings
from someip_agent.main import create_app
from someip_agent.version import __version__

FIXTURE = Path(__file__).parent / "fixtures" / "vehicle_service.arxml"


def test_health_and_arxml_import(tmp_path) -> None:
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, llm_api_key="test-key"))
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["version"] == __version__

        with FIXTURE.open("rb") as handle:
            response = client.post(
                "/api/v1/arxml/import",
                files={"file": (FIXTURE.name, handle, "application/xml")},
            )
        assert response.status_code == 200, response.text
        assert response.json()["services"][0]["service_id"] == 0x1234

        services = client.get("/api/v1/model/services")
        assert services.status_code == 200
        assert services.json()[0]["name"] == "VehicleStatus"

    assert (tmp_path / "models" / "current.json").is_file()

    restarted_app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, llm_api_key="test-key")
    )
    with TestClient(restarted_app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["arxml_loaded"] is True
        assert health.json()["service_count"] == 1

        services = client.get("/api/v1/model/services")
        assert services.status_code == 200
        assert services.json()[0]["name"] == "VehicleStatus"


def test_restores_legacy_arxml_import_without_current_model(tmp_path) -> None:
    legacy_import = tmp_path / "imports" / "legacy-digest" / FIXTURE.name
    legacy_import.parent.mkdir(parents=True)
    legacy_import.write_bytes(FIXTURE.read_bytes())

    app = create_app(Settings(_env_file=None, data_dir=tmp_path, llm_api_key="test-key"))
    with TestClient(app) as client:
        services = client.get("/api/v1/model/services")
        assert services.status_code == 200
        assert services.json()[0]["name"] == "VehicleStatus"

    assert (tmp_path / "models" / "current.json").is_file()


def test_arxml_import_rejects_document_without_services(tmp_path) -> None:
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, llm_api_key="test-key"))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/arxml/import",
            files={"file": ("empty.arxml", b"<AUTOSAR/>", "application/xml")},
        )

    assert response.status_code == 422
    assert "未发现可用的 SOME/IP 服务定义" in response.json()["detail"]
    assert not (tmp_path / "models" / "current.json").exists()


def test_settings_never_returns_plain_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("someip_agent.agent.service.keyring.set_password", lambda *_args: None)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, llm_api_key=""))
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/settings/llm",
            json={
                "base_url": "https://voyahgpt-gateway.voyah.cn/api/gateway/v1",
                "model": "glm-5.2",
                "api_key": "a-secret-value",
                "timeout_seconds": 30,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["api_key_configured"] is True
        assert "api_key_masked" not in body
        assert "a-secret-value" not in response.text


def test_serves_packaged_frontend(tmp_path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html><body>SOME/IP Console</body></html>", encoding="utf-8")
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", static_dir=web, llm_api_key="test")
    )
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "SOME/IP Console" in response.text
        assert client.get("/api/v1/health").status_code == 200
