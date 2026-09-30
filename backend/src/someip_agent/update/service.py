from __future__ import annotations

import base64
import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from someip_agent.config import Settings
from someip_agent.domain.models import UpdateInfo
from someip_agent.version import __version__

logger = logging.getLogger(__name__)


class UpdateError(RuntimeError):
    pass


def _version_tuple(
    version: str,
) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    match = re.fullmatch(
        r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?",
        version,
    )
    if not match:
        raise UpdateError(f"无效的语义版本: {version}")
    prerelease = tuple(
        (0, int(identifier)) if identifier.isdigit() else (1, identifier.lower())
        for identifier in (match.group(4) or "").split(".")
        if identifier
    )
    # SemVer 中正式版本的优先级高于任何先行版本；构建元数据不参与比较。
    release_rank = 0 if prerelease else 1
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        release_rank,
        prerelease,
    )


class UpdateService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._last_manifest: dict[str, object] | None = None

    async def check(self) -> UpdateInfo:
        if not self._settings.update_manifest_url:
            return UpdateInfo(current_version=__version__)
        self._validate_remote_url(self._settings.update_manifest_url)
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.get(self._settings.update_manifest_url)
                response.raise_for_status()
                manifest = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.exception("升级清单获取失败", extra={"operation": "update.check"})
            raise UpdateError(f"升级清单获取失败: {type(exc).__name__}: {exc}") from exc
        if not isinstance(manifest, dict):
            raise UpdateError("升级清单必须是 JSON 对象")
        latest = str(manifest.get("version") or "")
        url = str(manifest.get("download_url") or "")
        digest = str(manifest.get("sha256") or "").lower()
        signature = str(manifest.get("signature") or "")
        if not latest or not url or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise UpdateError("升级清单缺少 version/download_url/sha256")
        self._validate_remote_url(url)
        verified = self._verify_signature(latest, digest, url, signature)
        self._last_manifest = manifest
        return UpdateInfo(
            current_version=__version__,
            latest_version=latest,
            available=_version_tuple(latest) > _version_tuple(__version__),
            release_notes=str(manifest.get("release_notes") or "") or None,
            download_url=url,
            sha256=digest,
            signature_verified=verified,
        )

    async def stage(self) -> Path:
        info = await self.check()
        if not info.available:
            raise UpdateError("当前已是最新版本")
        if not info.signature_verified:
            raise UpdateError("升级清单签名未通过，拒绝下载")
        assert info.download_url and info.sha256 and info.latest_version
        filename = Path(urlparse(info.download_url).path).name or "someip-agent-update.bin"
        target_dir = self._settings.data_dir / "updates" / info.latest_version
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        temporary = target.with_suffix(target.suffix + ".part")
        hasher = hashlib.sha256()
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                async with client.stream("GET", info.download_url) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            hasher.update(chunk)
                            output.write(chunk)
        except (httpx.HTTPError, OSError) as exc:
            logger.exception("升级包下载失败", extra={"operation": "update.stage"})
            temporary.unlink(missing_ok=True)
            raise UpdateError(f"升级包下载失败: {type(exc).__name__}: {exc}") from exc
        if hasher.hexdigest() != info.sha256:
            temporary.unlink(missing_ok=True)
            raise UpdateError("升级包 SHA-256 校验失败")
        temporary.replace(target)
        return target

    def _verify_signature(self, version: str, digest: str, url: str, signature: str) -> bool:
        if not self._settings.update_public_key or not signature:
            return False
        signed_data = f"{version}\n{digest}\n{url}".encode()
        try:
            public_key_data = self._settings.update_public_key.encode()
            if b"BEGIN PUBLIC KEY" in public_key_data:
                loaded = serialization.load_pem_public_key(public_key_data)
                if not isinstance(loaded, Ed25519PublicKey):
                    raise UpdateError("升级公钥不是 Ed25519")
                public_key = loaded
            else:
                public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_data))
            public_key.verify(base64.b64decode(signature), signed_data)
            return True
        except (ValueError, TypeError, InvalidSignature):
            logger.exception("升级清单签名校验失败", extra={"operation": "update.verify"})
            return False

    @staticmethod
    def _validate_remote_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise UpdateError("在线升级地址必须使用有效的 HTTPS URL")
