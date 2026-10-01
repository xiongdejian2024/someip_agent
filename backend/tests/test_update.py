from __future__ import annotations

import base64
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
