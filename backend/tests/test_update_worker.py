import hashlib
import json
import os
import socket
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path

import pytest

from someip_agent.update.worker import apply_update, extract_release


def release(tmp_path: Path, *, version: str = "0.2.0", filename: str = "someip-agent") -> Path:
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("VERSION", version)
        entry = zipfile.ZipInfo(filename)
        entry.external_attr = (stat.S_IFREG | 0o755) << 16
        bundle.writestr(entry, "#!/bin/sh\nexit 0\n")
    return archive


def plan(tmp_path: Path, archive: Path) -> dict:
    root = tmp_path / "installed"
    root.mkdir()
    (root / "VERSION").write_text("0.1.0")
    (root / "someip-agent").write_text("旧程序")
    return {
        "package": str(archive),
        "install_root": str(root),
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "version": "0.2.0",
        "executable": "someip-agent",
        "parent_pid": 0,
        "health_url": "http://127.0.0.1:8765/api/v1/health",
    }


def test_release_extract_rejects_traversal_and_symlink(tmp_path) -> None:
    for name, mode in (("../outside", stat.S_IFREG), ("link", stat.S_IFLNK)):
        archive = tmp_path / "bad.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            entry = zipfile.ZipInfo(name)
            entry.external_attr = mode << 16
            bundle.writestr(entry, "../outside")
        with pytest.raises(ValueError):
            extract_release(archive, tmp_path / "extracted")
    assert not (tmp_path / "outside").exists()


def test_release_hash_checked_before_changes(tmp_path) -> None:
    archive = release(tmp_path)
    update = plan(tmp_path, archive)
    update["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        apply_update(update)
    assert (tmp_path / "installed" / "VERSION").read_text() == "0.1.0"


def test_release_version_mismatch_preserves_old_install(tmp_path) -> None:
    update = plan(tmp_path, release(tmp_path, version="0.3.0"))
    with pytest.raises(ValueError, match="VERSION"):
        apply_update(update)
    assert (tmp_path / "installed" / "VERSION").read_text() == "0.1.0"


def test_atomic_install_and_backup(tmp_path, monkeypatch) -> None:
    update = plan(tmp_path, release(tmp_path))

    class Process:
        def poll(self):
            return 0

    monkeypatch.setattr("someip_agent.update.worker.subprocess.Popen", lambda *_a, **_kw: Process())
    monkeypatch.setattr("someip_agent.update.worker._health", lambda *_a: None)
    result = apply_update(update)
    assert result["status"] == "complete"
    assert (tmp_path / "installed" / "VERSION").read_text() == "0.2.0"
    assert (tmp_path / "installed.previous" / "VERSION").read_text() == "0.1.0"


def test_failed_health_rolls_back_and_restarts_old_version(tmp_path, monkeypatch) -> None:
    update = plan(tmp_path, release(tmp_path))
    launches = []
    health_versions = []

    class Process:
        def poll(self):
            return 0

    def launch(command, **_kw):
        launches.append(command)
        return Process()

    def failed(_url, version, _process, _timeout):
        health_versions.append(version)
        if version == "0.2.0":
            raise TimeoutError("健康检查失败")

    monkeypatch.setattr("someip_agent.update.worker.subprocess.Popen", launch)
    monkeypatch.setattr("someip_agent.update.worker._health", failed)
    with pytest.raises(TimeoutError):
        apply_update(update)
    assert (tmp_path / "installed" / "VERSION").read_text() == "0.1.0"
    assert (tmp_path / "installed.failed-update" / "VERSION").read_text() == "0.2.0"
    assert len(launches) == 2
    assert health_versions == ["0.2.0", "0.1.0"]


def test_failed_rollback_health_is_not_reported_as_recovered(tmp_path, monkeypatch, caplog):
    update = plan(tmp_path, release(tmp_path))

    class Process:
        def poll(self):
            return 0

    def failed(*_args):
        raise TimeoutError("新旧版本均未就绪")

    monkeypatch.setattr("someip_agent.update.worker.subprocess.Popen", lambda *_a, **_kw: Process())
    monkeypatch.setattr("someip_agent.update.worker._health", failed)
    with pytest.raises(RuntimeError, match="回滚") as error:
        apply_update(update)
    assert isinstance(error.value.__cause__, TimeoutError)
    assert (tmp_path / "installed" / "VERSION").read_text() == "0.1.0"
    assert "未恢复健康" in caplog.text
    assert all(record.exc_info for record in caplog.records if record.levelname == "ERROR")


def test_install_requires_packaged_launcher(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from someip_agent.config import Settings
    from someip_agent.main import create_app

    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        response = client.post("/api/v1/updates/install")
    assert response.status_code == 409
    assert "自动重启" in response.json()["detail"]


def executable_server(version: str) -> str:
    return f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
Path("started.json").write_text(json.dumps({{"pid": os.getpid(), "version": "{version}"}}))
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({{"status": "ok", "version": "{version}"}}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_args):
        return
HTTPServer.allow_reuse_address = True
HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''


def process_release(tmp_path: Path, version: str, *, health_version: str | None = None) -> Path:
    archive = tmp_path / (version + ".zip")
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("VERSION", version)
        entry = zipfile.ZipInfo("someip-agent")
        entry.external_attr = (stat.S_IFREG | 0o755) << 16
        bundle.writestr(entry, executable_server(health_version or version))
    return archive


def stop_test_children(tmp_path: Path) -> None:
    for started in tmp_path.rglob("started.json"):
        pid = json.loads(started.read_text())["pid"]
        try:
            os.kill(pid, 15)
            os.waitpid(pid, 0)
        except ProcessLookupError:
            pass
        except ChildProcessError:
            pass
        started.unlink()


@pytest.mark.skipif(os.name == "nt", reason="此用例验证 POSIX 实际进程，Windows 安装器另行验收")
@pytest.mark.parametrize("failure", [False, True])
def test_real_process_install_restart_health_and_rollback(tmp_path, failure):
    archive = process_release(tmp_path, "0.2.0", health_version="0.9.0" if failure else None)
    update = plan(tmp_path, archive)
    old = tmp_path / "installed" / "someip-agent"
    old.write_text(executable_server("0.1.0"))
    old.chmod(0o755)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    update.update(
        arguments=[str(port)],
        health_timeout=0.7,
        health_url=f"http://127.0.0.1:{port}/api/v1/health",
    )
    try:
        if failure:
            with pytest.raises(TimeoutError, match="健康"):
                apply_update(update)
            expected = "0.1.0"
        else:
            assert apply_update(update)["status"] == "complete"
            expected = "0.2.0"
        with urllib.request.urlopen(update["health_url"], timeout=2) as response:
            assert json.load(response)["version"] == expected
        assert (tmp_path / "installed" / "VERSION").read_text() == expected
        if not failure:
            # 连续升级不能因上一轮 .previous 目录而被永久阻断。
            stop_test_children(tmp_path)
            second = process_release(tmp_path, "0.3.0")
            update.update(
                package=str(second),
                version="0.3.0",
                sha256=hashlib.sha256(second.read_bytes()).hexdigest(),
            )
            assert apply_update(update)["status"] == "complete"
            assert (tmp_path / "installed.previous" / "VERSION").read_text() == "0.2.0"
            assert len(list(tmp_path.glob("installed.previous-*"))) == 1
    finally:
        stop_test_children(tmp_path)
