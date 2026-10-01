from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import stat
import subprocess
import sys
import time
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
        self._install_lock = asyncio.Lock()
        self._install_scheduled = False

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
        target, _info = await self._stage_release()
        return target

    async def _stage_release(self) -> tuple[Path, UpdateInfo]:
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
        downloaded = 0
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                async with client.stream("GET", info.download_url) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            downloaded += len(chunk)
                            if downloaded > self._settings.update_max_download_bytes:
                                raise UpdateError("升级包下载大小超限")
                            hasher.update(chunk)
                            output.write(chunk)
        except (httpx.HTTPError, OSError, UpdateError) as exc:
            logger.exception("升级包下载失败", extra={"operation": "update.stage"})
            temporary.unlink(missing_ok=True)
            raise UpdateError(f"升级包下载失败: {type(exc).__name__}: {exc}") from exc
        if hasher.hexdigest() != info.sha256:
            temporary.unlink(missing_ok=True)
            raise UpdateError("升级包 SHA-256 校验失败")
        temporary.replace(target)
        return target, info

    async def install(self) -> dict[str, str]:
        """下载验证后交给外置升级器；请求返回之后主进程才允许退出。"""
        async with self._install_lock:
            if self._install_scheduled:
                raise UpdateError("升级已经启动")
            root, helper, packaged = await asyncio.to_thread(self._install_paths)
            target, info = await self._stage_release()
            manifest: dict[str, object] = {"version": info.latest_version, "sha256": info.sha256}
            formats = {".zip", ".exe"} if os.name == "nt" else {".zip"}
            if target.suffix.lower() not in formats:
                raise UpdateError("不支持的安装包格式")
            await asyncio.to_thread(
                self._schedule_install, target, manifest, root, helper, packaged
            )
            self._install_scheduled = True
            logger.info("独立升级器已启动", extra={"operation": "update.install"})
            return {"status": "scheduled", "version": str(manifest["version"])}

    def _install_paths(self) -> tuple[Path, Path, bool]:
        packaged = bool(getattr(sys, "frozen", False))
        root = self._settings.update_install_root
        if root is None and packaged:
            root = Path(sys.executable).resolve().parent
        if root is None:
            raise UpdateError("开发启动模式没有安装目录，请使用发行版执行在线升级")
        helper_name = "someip-agent-updater.exe" if os.name == "nt" else "someip-agent-updater"
        helper = self._settings.update_helper_binary or root / helper_name
        if not helper.is_file():
            raise UpdateError("发行版缺少独立升级器")
        if os.name != "nt" and (
            not stat.S_IMODE(helper.stat().st_mode) & 0o111 or not os.access(helper, os.X_OK)
        ):
            raise UpdateError("独立升级器没有执行权限")
        if (self._settings.data_dir / "updates").resolve().is_relative_to(root.resolve()):
            raise UpdateError("升级暂存目录必须位于安装目录之外")
        return root, helper, packaged

    def _schedule_install(
        self, target: Path, manifest: dict[str, object], root: Path, helper: Path, packaged: bool
    ) -> None:
        executable = Path(sys.executable).name if packaged else "someip-agent"
        plan = {
            "package": str(target.resolve()),
            "install_root": str(root.resolve()),
            "executable": executable,
            "parent_pid": os.getpid(),
            "version": manifest["version"],
            "sha256": manifest["sha256"],
            "health_url": f"http://127.0.0.1:{self._settings.port}/api/v1/health",
            "required_executables": [executable, helper.name],
        }
        if packaged and sys.platform == "linux":
            plan["required_executables"].append("_internal/native/soa_partner")
        from uuid import uuid4

        plan_path = target.parent / ("install-plan-" + uuid4().hex + ".json")
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        # 独立 onefile 升级器移到安装目录外，避免替换自己的运行文件。
        import shutil

        outside_helper = target.parent / helper.name
        shutil.copy2(helper, outside_helper)
        try:
            process = subprocess.Popen(
                [str(outside_helper), "--plan", str(plan_path)],
                env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                start_new_session=os.name != "nt",
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + 30
            status_path = plan_path.with_suffix(".status.json")
            while time.monotonic() < deadline:
                if status_path.is_file():
                    try:
                        result = json.loads(status_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        logger.debug("升级器状态文件仍在写入", exc_info=True)
                        time.sleep(0.05)
                        continue
                    if result.get("status") == "prepared":
                        return
                    raise UpdateError(f"升级器准备失败: {result.get('error', result)}")
                if process.poll() is not None:
                    raise UpdateError(f"升级器在准备阶段退出: {process.returncode}")
                time.sleep(0.05)
            process.terminate()
            process.wait(timeout=5)
            raise UpdateError("升级器准备超时，主程序保持运行")
        except Exception as exc:
            logger.exception("独立升级器启动失败", extra={"operation": "update.install"})
            raise UpdateError(f"独立升级器启动失败: {exc}") from exc

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
