"""独立升级进程：等待主进程退出，安装、重启并验证版本；失败恢复旧版本。"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import stat
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def extract_release(archive: Path, destination: Path, max_bytes: int = 2 * 1024**3) -> None:
    """只接受完整发行目录，拒绝路径穿越、符号链接和膨胀包。"""
    with zipfile.ZipFile(archive) as bundle:
        total = 0
        for entry in bundle.infolist():
            name = entry.filename.replace("\\", "/")
            parts = Path(name).parts
            if not parts or name.startswith("/") or ".." in parts or ":" in name:
                raise ValueError(f"升级包路径非法: {entry.filename}")
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("升级包不允许符号链接")
            total += entry.file_size
            if total > max_bytes:
                raise ValueError("升级包解压大小超限")
            target = destination.joinpath(*parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if mode & 0o111:
                    target.chmod(target.stat().st_mode | 0o111)


def _wait_parent(pid: int, timeout: float = 60) -> None:
    if not pid:
        return
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return
        try:
            if kernel.WaitForSingleObject(ctypes.c_void_p(handle), int(timeout * 1000)) != 0:
                raise TimeoutError("等待主程序退出超时")
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise TimeoutError("等待主程序退出超时")


def _health(url: str, version: str, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"升级后程序退出: {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                data = json.load(response)
            if data.get("version") == version and data.get("status") == "ok":
                return
        except Exception:
            logger.debug("升级后服务尚未就绪", exc_info=True)
        time.sleep(0.2)
    raise TimeoutError("升级后版本/健康检查失败")


def apply_update(plan: dict[str, Any]) -> dict[str, Any]:
    package = Path(plan["package"]).resolve()
    root = Path(plan["install_root"]).resolve()
    if not root.is_dir() or root.parent == root or root == Path.home():
        raise ValueError("安装目录非法")
    relative_executable = Path(plan["executable"])
    if (
        relative_executable.is_absolute()
        or ".." in relative_executable.parts
        or ":" in str(relative_executable)
    ):
        raise ValueError("升级主程序必须为安装目录内的相对路径")
    if package.is_relative_to(root):
        raise ValueError("升级包必须位于安装目录之外")
    hasher = hashlib.sha256()
    with package.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != plan["sha256"]:
        raise ValueError("安装前升级包 SHA-256 复核失败")
    previous_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not previous_version:
        raise ValueError("旧安装目录缺少可验证的版本，拒绝无法确认回滚的升级")
    backup = root.with_name(root.name + ".previous")
    prepared = root.with_name(root.name + ".updating-" + uuid4().hex)
    process = None
    replaced = False
    try:
        if package.suffix.lower() == ".zip":
            prepared.mkdir()
            extract_release(package, prepared)
            if (prepared / "VERSION").read_text().strip() != plan["version"]:
                raise ValueError("升级包 VERSION 与签名清单不一致")
            if not (prepared / plan["executable"]).is_file():
                raise ValueError("升级包缺少主程序")
        elif package.suffix.lower() == ".exe" and os.name == "nt":
            # 主程序退出前完成备份准备；拷贝失败不会让主程序先停机。
            shutil.copytree(root, prepared)
        else:
            raise ValueError("升级包必须为完整 ZIP 发行包或 Windows EXE 安装器")
        if plan.get("status_file"):
            Path(plan["status_file"]).write_text(
                json.dumps({"status": "prepared", "version": plan["version"]}), encoding="utf-8"
            )
        _wait_parent(int(plan["parent_pid"]))
        if backup.exists():
            archived = backup.with_name(backup.name + "-" + uuid4().hex)
            backup.rename(archived)
            logger.info(
                "上一轮升级备份已归档", extra={"operation": "update.archive", "path": str(archived)}
            )
        if package.suffix.lower() == ".zip":
            root.rename(backup)
            replaced = True
            prepared.rename(root)
        else:
            prepared.rename(backup)
            replaced = True
            result = subprocess.run(
                [
                    str(package),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/CLOSEAPPLICATIONS",
                    f"/DIR={root}",
                ],
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"安装器返回错误码 {result.returncode}")
        executable = root / plan["executable"]
        process = subprocess.Popen([str(executable), *plan.get("arguments", [])], cwd=root)
        _health(plan["health_url"], plan["version"], process, float(plan.get("health_timeout", 30)))
        logger.info(
            "在线升级完成", extra={"operation": "update.complete", "version": plan["version"]}
        )
        return {"status": "complete", "version": plan["version"], "backup": str(backup)}
    except Exception:
        logger.exception("在线升级失败，开始恢复旧版本", extra={"operation": "update.rollback"})
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.exception(
                    "升级后进程退出超时，将强制停止", extra={"operation": "update.rollback.stop"}
                )
                process.kill()
                process.wait(timeout=5)
        if replaced and backup.exists():
            failed = root.with_name(root.name + ".failed-update")
            if failed.exists():
                failed = root.with_name(root.name + ".failed-update-" + uuid4().hex)
            if root.exists():
                root.rename(failed)
            backup.rename(root)
            try:
                restored = subprocess.Popen(
                    [str(root / plan["executable"]), *plan.get("arguments", [])], cwd=root
                )
                _health(
                    plan["health_url"],
                    previous_version,
                    restored,
                    float(plan.get("rollback_health_timeout", plan.get("health_timeout", 30))),
                )
                logger.info(
                    "升级失败后旧版本已恢复健康",
                    extra={"operation": "update.rollback.complete", "version": previous_version},
                )
            except Exception as rollback_error:
                logger.exception(
                    "旧版本已还原但未恢复健康，需要人工处理",
                    extra={"operation": "update.rollback.failed", "version": previous_version},
                )
                raise RuntimeError(
                    "在线升级失败，且旧版本回滚启动或健康确认失败"
                ) from rollback_error
        if prepared.exists():
            failed_preparation = root.with_name(root.name + ".failed-preparation-" + uuid4().hex)
            prepared.rename(failed_preparation)
            logger.info(
                "升级准备失败现场已保留",
                extra={"operation": "update.prepare.failed", "path": str(failed_preparation)},
            )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="SOME/IP Agent 独立升级进程")
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    log_path = args.plan.with_suffix(".log")
    logging.basicConfig(
        filename=log_path, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    status_file = args.plan.with_suffix(".status.json")
    plan["status_file"] = str(status_file)
    try:
        result = apply_update(plan)
    except Exception as exc:
        logger.exception("独立升级进程失败", extra={"operation": "update.worker"})
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    status_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
