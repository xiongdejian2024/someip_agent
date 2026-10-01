"""真实 shell 门禁与清理回归；替身命令不承担浏览器或升级器验收。"""

import os
import shlex
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def browser_sandbox(tmp_path):
    evidence = tmp_path / "build/browser-update-evidence"
    commands = tmp_path / "commands"
    commands.mkdir()
    source = Path(__file__).with_name("run_browser_update.sh").read_text()
    assert source.count("cd /workspace\n") == 1
    assert (
        source.count("browser_evidence=/workspace/build/browser-update-evidence") == 1
    )
    script = tmp_path / "runner.sh"
    script.write_text(
        source.replace("cd /workspace\n", f"cd {shlex.quote(str(tmp_path))}\n").replace(
            "browser_evidence=/workspace/build/browser-update-evidence",
            f"browser_evidence={shlex.quote(str(evidence))}",
        )
    )
    marker = tmp_path / "network-command-called"
    (commands / "iptables").write_text(
        "#!/bin/bash\n"
        f"touch {shlex.quote(str(marker))}\n"
        'if [[ "$1" == -S ]]; then printf "%s\\n" "-P OUTPUT ACCEPT"; exit 0; fi\n'
        f"mkdir -p {shlex.quote(str(evidence / 'private'))}\n"
        f"touch {shlex.quote(str(evidence / 'private/log'))}\nexit 42\n"
    )
    (commands / "chown").write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(tmp_path / 'ownership.log'))}\n"
        "exit ${TEST_CHOWN_RESULT:-0}\n"
    )
    for command in commands.iterdir():
        command.chmod(0o755)
    return tmp_path, evidence, commands, script, marker


def execute(sandbox, *, owner="12345:12345", case="linux", extra=None):
    _, _, commands, script, _ = sandbox
    environment = {**os.environ, "PATH": str(commands) + ":" + os.environ["PATH"]}
    environment.pop("PYTHONPATH", None)
    environment["SOMEIP_AGENT_EVIDENCE_OWNER"] = owner
    environment.update(extra or {})
    return subprocess.run(
        ["bash", str(script), case],
        env=environment,
        capture_output=True,
        timeout=5,
        check=False,
    )


def test_failure_preserves_exit_code_and_does_not_chown_reused_fixture(browser_sandbox):
    root, evidence, _, _, marker = browser_sandbox
    fixture = evidence / "fixture/new"
    fixture.mkdir(parents=True)
    (fixture / "release.zip").write_bytes("只读夹具门禁，不是真实发行包".encode())
    result = execute(browser_sandbox)
    assert result.returncode == 42, result.stderr.decode()
    assert marker.exists()
    assert (evidence / "private/log").exists()
    ownership = (root / "ownership.log").read_text()
    assert str(evidence / "private") in ownership
    assert str(evidence / "fixture") not in ownership


def test_cleanup_failure_does_not_mask_network_step_failure(browser_sandbox):
    result = execute(browser_sandbox, extra={"TEST_CHOWN_RESULT": "77"})
    assert result.returncode == 42
    assert "归还所有权失败" in result.stderr.decode()


def test_invalid_owner_is_rejected_before_network_and_evidence(browser_sandbox):
    _, evidence, _, _, marker = browser_sandbox
    result = execute(browser_sandbox, owner="runner;other")
    assert result.returncode == 1
    assert "UID:GID" in result.stderr.decode()
    assert not evidence.exists() and not marker.exists()


def test_symlink_evidence_root_is_rejected_before_network(browser_sandbox):
    root, evidence, _, _, marker = browser_sandbox
    outside = root / "outside"
    outside.mkdir()
    evidence.parent.mkdir()
    evidence.symlink_to(outside, target_is_directory=True)
    result = execute(browser_sandbox)
    assert result.returncode == 1
    assert "符号链接" in result.stderr.decode()
    assert not list(outside.iterdir()) and not marker.exists()
    assert not (root / "ownership.log").exists()


@pytest.mark.parametrize("existing", ["junit.xml", "browser"])
def test_existing_evidence_is_never_overwritten(browser_sandbox, existing):
    _, evidence, _, _, marker = browser_sandbox
    evidence.mkdir(parents=True)
    saved = evidence / existing
    saved.write_text("原始证据")
    result = execute(browser_sandbox)
    assert result.returncode == 1
    assert saved.read_text() == "原始证据" and not marker.exists()


@pytest.mark.parametrize(
    "options", [{"case": "script"}, {"extra": {"PYTHONPATH": "/product/source"}}]
)
def test_invalid_mode_or_source_override_is_rejected(browser_sandbox, options):
    _, evidence, _, _, marker = browser_sandbox
    result = execute(browser_sandbox, **options)
    assert result.returncode == 1
    assert not evidence.exists() and not marker.exists()
