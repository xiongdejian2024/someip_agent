"""GitHub 风格跨域 HTTPS 跳转；拒绝不安全跳转并保留升级验签/哈希门禁。"""

from __future__ import annotations

import base64
import hashlib

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from someip_agent.config import Settings
from someip_agent.update.service import UpdateError, UpdateService

MANIFEST = "https://github.com/example/agent/releases/latest/download/manifest.json"
ARTIFACT = "https://github.com/example/agent/releases/download/v99.0.0/release.zip"
CDN = "https://release-assets.githubusercontent.com/release.zip?token=temporary"


@pytest.fixture
def source(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    payload = "测试下载内容；ZIP 格式另由既有真实升级测试验收".encode()
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "version": "99.0.0",
        "download_url": ARTIFACT,
        "sha256": digest,
        "signature": base64.b64encode(
            key.sign(f"99.0.0\n{digest}\n{ARTIFACT}".encode())
        ).decode(),
    }
    public_key = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    service = UpdateService(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            update_manifest_url=MANIFEST,
            update_public_key=base64.b64encode(public_key).decode(),
        )
    )
    requests = []
    responses = {
        MANIFEST: httpx.Response(302, headers={"Location": "/version/manifest.json"}),
        "https://github.com/version/manifest.json": httpx.Response(
            307, headers={"Location": "https://release-assets.githubusercontent.com/manifest.json"}
        ),
        "https://release-assets.githubusercontent.com/manifest.json": httpx.Response(
            200, json=manifest
        ),
        ARTIFACT: httpx.Response(302, headers={"Location": CDN}),
        CDN: httpx.Response(200, content=payload),
    }

    def respond(request):
        requests.append(str(request.url))
        return responses[str(request.url)]

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_class(
            **kwargs, transport=httpx.MockTransport(respond), trust_env=False
        ),
    )
    return service, responses, requests, payload, manifest


@pytest.mark.asyncio
async def test_latest_manifest_and_cdn_artifact_preserve_signature_and_hash(source):
    service, _responses, requests, payload, _manifest = source
    info = await service.check()
    assert info.available and info.signature_verified
    assert info.download_url == ARTIFACT  # 签名绑定原始地址，不替换为临时 CDN URL。
    target = await service.stage()
    assert target.name == "release.zip"
    assert target.read_bytes() == payload
    assert MANIFEST in requests and ARTIFACT in requests and CDN in requests


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["manifest", "artifact"])
@pytest.mark.parametrize(
    "location,reason",
    [
        ("http://unsafe.test/release.zip", "HTTPS"),
        ("https://user:password@unsafe.test/release.zip", "登录凭据"),
    ],
)
async def test_unsafe_redirect_is_rejected_before_network(source, phase, location, reason):
    service, responses, requests, _payload, _manifest = source
    origin = MANIFEST if phase == "manifest" else ARTIFACT
    responses[origin] = httpx.Response(302, headers={"Location": location})
    with pytest.raises(UpdateError, match=reason):
        if phase == "manifest":
            await service.check()
        else:
            await service.stage()
    assert location not in requests
    assert not list(service._settings.data_dir.rglob("*.part"))


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["manifest", "artifact"])
async def test_redirect_loop_is_bounded_and_does_not_leave_partial_file(source, phase):
    service, responses, requests, _payload, _manifest = source
    origin = MANIFEST if phase == "manifest" else ARTIFACT
    responses[origin] = httpx.Response(302, headers={"Location": origin})
    with pytest.raises(UpdateError, match="TooManyRedirects"):
        if phase == "manifest":
            await service.check()
        else:
            await service.stage()
    assert 5 <= requests.count(origin) <= 6
    assert not list(service._settings.data_dir.rglob("*.part"))


@pytest.mark.asyncio
async def test_cdn_tampering_still_fails_hash_verification(source):
    service, responses, _requests, _payload, _manifest = source
    responses[CDN] = httpx.Response(200, content=b"tampered")
    with pytest.raises(UpdateError, match="SHA-256"):
        await service.stage()
    assert not list(service._settings.data_dir.rglob("*.part"))
    assert not list(service._settings.data_dir.rglob("release.zip"))


@pytest.mark.asyncio
async def test_invalid_signature_never_downloads_redirected_artifact(source):
    service, responses, requests, _payload, manifest = source
    responses["https://release-assets.githubusercontent.com/manifest.json"] = httpx.Response(
        200, json={**manifest, "sha256": "0" * 64}
    )
    with pytest.raises(UpdateError, match="签名未通过"):
        await service.stage()
    assert ARTIFACT not in requests and CDN not in requests


@pytest.mark.asyncio
async def test_redirects_do_not_bypass_download_size_limit(source):
    service, _responses, _requests, _payload, _manifest = source
    service._settings.update_max_download_bytes = 1
    with pytest.raises(UpdateError, match="大小超限"):
        await service.stage()
    assert not list(service._settings.data_dir.rglob("*.part"))
