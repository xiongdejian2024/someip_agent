from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from someip_agent.config import Settings
from someip_agent.update.service import UpdateService, _version_tuple


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
    signature = base64.b64encode(
        private_key.sign(f"{version}\n{digest}\n{url}".encode())
    ).decode("ascii")

    assert service._verify_signature(version, digest, url, signature)
    assert not service._verify_signature(version, "b" * 64, url, signature)
