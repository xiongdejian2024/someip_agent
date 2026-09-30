"""本地受信 HTTPS 发行源 + 真正 FastAPI 进程 + 独立升级器的端到端验收。"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID


def app_script(version: str) -> str:
    return f'''#!{sys.executable}
import json, os
from pathlib import Path
Path("started.json").write_text(json.dumps({{"pid": os.getpid(), "version": "{version}"}}))
import someip_agent.version
someip_agent.version.__version__ = "{version}"
from someip_agent.main import run
run()
'''


def wait_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        time.sleep(0.05)
    raise TimeoutError("在线升级端到端验收等待超时")


@pytest.mark.skipif(os.name == "nt", reason="真实 POSIX 进程链路；Windows 安装包另行验收")
def test_signed_https_upgrade_api_restarts_into_new_release(tmp_path: Path):
    ui_mode = os.environ.get("SOMEIP_AGENT_UPDATE_UI_TEST") == "1"
    root = tmp_path / "installed"
    root.mkdir()
    helper = root / "updater"
    helper.write_text(f"#!{sys.executable}\nfrom someip_agent.update.worker import main\nmain()\n")
    helper.chmod(0o755)
    executable = root / "someip-agent"
    executable.write_text(app_script("0.1.0"))
    executable.chmod(0o755)
    (root / "VERSION").write_text("0.1.0")
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("VERSION", "0.2.0")
        for name, source in (
            ("someip-agent", app_script("0.2.0")),
            ("updater", helper.read_text()),
        ):
            entry = zipfile.ZipInfo(name)
            entry.external_attr = (stat.S_IFREG | 0o755) << 16
            bundle.writestr(entry, source)

    tls_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "本地升级验收")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(tls_key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "test-cert.pem", tmp_path / "test-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        tls_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    payloads: dict[str, bytes] = {}

    class ReleaseHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = payloads[self.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    source = ThreadingHTTPServer(("127.0.0.1", 0), ReleaseHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    source.socket = context.wrap_socket(source.socket, server_side=True)
    source_thread = threading.Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    base = f"https://127.0.0.1:{source.server_port}"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    signing_key = ed25519.Ed25519PrivateKey.generate()
    public_key = signing_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    signature = base64.b64encode(
        signing_key.sign(f"0.2.0\n{digest}\n{base}/release.zip".encode())
    ).decode()
    payloads["/manifest.json"] = json.dumps(
        {
            "version": "0.2.0",
            "sha256": digest,
            "download_url": base + "/release.zip",
            "signature": signature,
        }
    ).encode()
    payloads["/release.zip"] = archive.read_bytes()
    with socket.socket() as probe:
        probe.bind(
            ("127.0.0.1", int(os.environ.get("SOMEIP_AGENT_UPDATE_UI_PORT", "0")) if ui_mode else 0)
        )
        port = probe.getsockname()[1]
    env = {
        **os.environ,
        "SSL_CERT_FILE": str(cert_path),
        "SOMEIP_AGENT_PORT": str(port),
        "SOMEIP_AGENT_DATA_DIR": str(tmp_path / "data"),
        "SOMEIP_AGENT_OPEN_BROWSER": "false",
        "SOMEIP_AGENT_HOST": "0.0.0.0" if ui_mode else "127.0.0.1",
        "SOMEIP_AGENT_LLM_API_KEY": "",
        "SOMEIP_AGENT_UPDATE_INSTALL_ROOT": str(root),
        "SOMEIP_AGENT_UPDATE_HELPER_BINARY": str(helper),
        "SOMEIP_AGENT_UPDATE_MANIFEST_URL": base + "/manifest.json",
        "SOMEIP_AGENT_UPDATE_PUBLIC_KEY": base64.b64encode(public_key).decode(),
    }
    process = None
    try:
        with (tmp_path / "application.log").open("wb") as log:
            process = subprocess.Popen([str(executable)], cwd=root, env=env, stdout=log, stderr=log)
        url = f"http://127.0.0.1:{port}/api/v1"

        def health():
            with urllib.request.urlopen(url + "/health", timeout=1) as response:
                return json.load(response)

        assert wait_until(health)["version"] == "0.1.0"
        if ui_mode:
            static_dir = Path(os.environ["SOMEIP_AGENT_STATIC_DIR"])
            assert (static_dir / "index.html").is_file(), "先构建前端再执行浏览器升级验收"
            print(
                f"浏览器升级验收已就绪：http://127.0.0.1:{port}；仅修改临时安装目录 {root}",
                flush=True,
            )
        else:
            request = urllib.request.Request(url + "/updates/install", method="POST", data=b"")
            with urllib.request.urlopen(request, timeout=40) as response:
                result = json.load(response)
            assert result == {"status": "scheduled", "version": "0.2.0"}
        process.wait(timeout=180 if ui_mode else 15)  # 回收旧父进程，使升级器准确观察到退出。
        assert process.returncode == 0
        assert wait_until(lambda: health()["version"] == "0.2.0")
        statuses = list((tmp_path / "data" / "updates").rglob("*.status.json"))
        assert len(statuses) == 1
        assert wait_until(lambda: json.loads(statuses[0].read_text())["status"] == "complete")
        assert (root / "VERSION").read_text() == "0.2.0"
        assert (tmp_path / "installed.previous" / "VERSION").read_text() == "0.1.0"
        if ui_mode:
            print("真实签名升级、重启、新版本健康检查及旧版本备份断言均通过", flush=True)
            input("浏览器确认新版本页面后，回车清理本次临时进程：")
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        for marker in tmp_path.glob("installed*/started.json"):
            record = json.loads(marker.read_text())
            if process and record["pid"] == process.pid:
                continue
            try:
                os.kill(record["pid"], 15)
            except ProcessLookupError:
                pass
        source.shutdown()
        source.server_close()
        source_thread.join(timeout=5)
