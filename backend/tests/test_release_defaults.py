"""正式发行信任根的架构、显式配置与缺省禁用门禁。"""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from someip_agent.config import Settings
from someip_agent.update import release_defaults


@pytest.fixture
def release_environment(monkeypatch):
    for name in ("SOMEIP_AGENT_UPDATE_MANIFEST_URL", "SOMEIP_AGENT_UPDATE_PUBLIC_KEY"):
        # 先登记环境恢复点，随后产品代码setdefault新增的键也会被fixture撤销。
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(release_defaults.platform, "system", lambda: "Linux")
    monkeypatch.setattr(release_defaults.platform, "machine", lambda: "x86_64")


def test_release_defaults_use_public_ed25519_key(release_environment):
    release_defaults.configure_linux_release_updates()
    settings = Settings(_env_file=None)
    assert settings.update_manifest_url == release_defaults.LINUX_X86_64_MANIFEST_URL
    assert settings.update_public_key == release_defaults.RELEASE_PUBLIC_KEY
    Ed25519PublicKey.from_public_bytes(base64.b64decode(settings.update_public_key, validate=True))


@pytest.mark.parametrize("system,machine", [("Darwin", "x86_64"), ("Linux", "aarch64")])
def test_unsupported_platform_has_no_default_updates(
    release_environment, monkeypatch, system, machine
):
    monkeypatch.setattr(release_defaults.platform, "system", lambda: system)
    monkeypatch.setattr(release_defaults.platform, "machine", lambda: machine)
    release_defaults.configure_linux_release_updates()
    settings = Settings(_env_file=None)
    assert settings.update_manifest_url == ""
    assert settings.update_public_key == ""


@pytest.mark.parametrize("url,key", [("", ""), ("https://custom.example/manifest.json", "custom")])
def test_explicit_configuration_is_preserved(release_environment, monkeypatch, url, key):
    monkeypatch.setenv("SOMEIP_AGENT_UPDATE_MANIFEST_URL", url)
    monkeypatch.setenv("SOMEIP_AGENT_UPDATE_PUBLIC_KEY", key)
    release_defaults.configure_linux_release_updates()
    settings = Settings(_env_file=None)
    assert settings.update_manifest_url == url
    assert settings.update_public_key == key


def test_source_defaults_remain_disabled(release_environment):
    settings = Settings(_env_file=None)
    assert settings.update_manifest_url == ""
    assert settings.update_public_key == ""
