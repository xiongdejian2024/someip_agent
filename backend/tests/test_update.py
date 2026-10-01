from __future__ import annotations

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from someip_agent.config import Settings
from someip_agent.update.service import UpdateError, UpdateService, _version_tuple


def test_semantic_version_precedence() -> None:
    assert _version_tuple("1.0.0-rc.1") < _version_tuple("1.0.0")
    assert _version_tuple("1.0.0-alpha.2") < _version_tuple("1.0.0-alpha.10")
    assert _version_tuple("v1.2.3+build.8") == _version_tuple("1.2.3+build.9")


def test_update_manifest_ed25519_signature(tmp_path) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        update_public_key=base64.b64encode(public_key).decode("ascii"),
    )
    service = UpdateService(settings)
    version = "0.2.0"
    digest = "a" * 64
    url = "https://updates.example.test/someip-agent-0.2.0.exe"
    signature = base64.b64encode(private_key.sign(f"{version}\n{digest}\n{url}".encode())).decode(
        "ascii"
    )

    assert service._verify_signature(version, digest, url, signature)
    assert not service._verify_signature(version, "b" * 64, url, signature)


@pytest.mark.skipif(os.name == "nt", reason="验证 POSIX 默认升级器")
def test_default_posix_helper_and_external_data(tmp_path):
    root = tmp_path / "installed"
    root.mkdir()
    helper = root / "someip-agent-updater"
    helper.write_text("独立升级器")
    helper.chmod(0o755)
    service = UpdateService(
        Settings(_env_file=None, data_dir=tmp_path / "data", update_install_root=root)
    )
    assert service._install_paths() == (root, helper, False)
    helper.chmod(0o644)
    with pytest.raises(UpdateError, match="执行权限"):
        service._install_paths()


@pytest.mark.skipif(os.name == "nt", reason="验证 POSIX 默认升级器")
def test_install_rejects_staging_inside_installation(tmp_path):
    root = tmp_path / "installed"
    root.mkdir()
    helper = root / "someip-agent-updater"
    helper.write_text("独立升级器")
    helper.chmod(0o755)
    service = UpdateService(
        Settings(_env_file=None, data_dir=root / "data", update_install_root=root)
    )
    with pytest.raises(UpdateError, match="安装目录之外"):
        service._install_paths()


@pytest.mark.parametrize("status", ["prepared", "complete", "failed"])
def test_installation_status_survives_service_restart_and_excludes_private_paths(tmp_path, status):
    from fastapi.testclient import TestClient

    from someip_agent.main import create_app

    installation_id = "a" * 32
    directory = tmp_path / "updates"
    directory.mkdir()
    (directory / f"install-plan-{installation_id}.status.json").write_text(
        json.dumps(
            {
                "installation_id": installation_id,
                "version": "0.2.0",
                "status": status,
                "backup": "/private/installed.previous",
                "pid": 12345,
                "rollback_pid": 12346,
            }
        )
    )
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        response = client.get(f"/api/v1/updates/install/{installation_id}")
    assert response.status_code == 200
    assert response.json() == {
        "installation_id": installation_id,
        "version": "0.2.0",
        "status": status,
        "rollback_completed": False,
        "rollback_failed": False,
        "restored_version": None,
        "error": None,
    }


@pytest.mark.parametrize("bad", ["../other", "A" * 32, "a" * 31, "a" * 33])
def test_installation_status_rejects_nonidentifiers(tmp_path, bad):
    service = UpdateService(Settings(_env_file=None, data_dir=tmp_path))
    with pytest.raises(UpdateError, match="安装 ID"):
        service.installation_status(bad)


def test_installation_status_missing_is_not_reported_as_success(tmp_path):
    from fastapi.testclient import TestClient

    from someip_agent.main import create_app

    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        response = client.get("/api/v1/updates/install/" + "a" * 32)
    assert response.status_code == 404


@pytest.mark.parametrize("kind", ["symlink", "oversize", "partial", "foreign", "contradictory"])
def test_installation_status_invalid_records_fail_closed(tmp_path, kind):
    installation_id = "a" * 32
    directory = tmp_path / "updates"
    directory.mkdir()
    path = directory / f"install-plan-{installation_id}.status.json"
    record = {"installation_id": installation_id, "version": "0.2.0", "status": "failed"}
    if kind == "symlink":
        target = tmp_path / "other.json"
        target.write_text(json.dumps(record))
        path.symlink_to(target)
    elif kind == "oversize":
        path.write_text(" " * 65537)
    elif kind == "partial":
        path.write_text('{"status":')
    else:
        if kind == "foreign":
            record["installation_id"] = "b" * 32
        else:
            record.update(rollback_completed=True, rollback_failed=True, restored_version="0.1.0")
        path.write_text(json.dumps(record))
    service = UpdateService(Settings(_env_file=None, data_dir=tmp_path))
    with pytest.raises(UpdateError):
        service.installation_status(installation_id)
