"""Linux 实际 UID 权限回归；不以这些脚本测试代替虚拟网互通。"""

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def sandbox():
    assert os.geteuid() == 0, "权限回归需在原生验收容器内以 root 执行"
    with tempfile.TemporaryDirectory(prefix="someip-evidence-permissions-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        build = root / "build"
        build.mkdir()
        os.chown(build, 12345, 12345)  # 对应宿主提前创建的 runner 输出父目录。
        commands = root / "commands"
        commands.mkdir()
        # 在首条网络设置处中断，真实 shell EXIT trap 必须保留失败并归还证据。
        command = commands / "ip"
        command.write_text(
            "#!/bin/bash\nmkdir -m 700 build/virtual-evidence/private\n"
            "printf '%s\\n' '临时测试日志' > build/virtual-evidence/private/log\n"
            "chmod 600 build/virtual-evidence/private/log\nexit 42\n"
        )
        command.chmod(0o755)
        # 副本不再次执行权限回归自身；后续真实网络步骤在首条 ip 命令处中断。
        preflight = commands / "python"
        preflight.write_text("#!/bin/bash\nexit 0\n")
        preflight.chmod(0o755)
        source = Path(__file__).with_name("run_virtual.sh").read_text()
        assert source.count("cd /workspace\n") == 1
        script = root / "run_virtual.sh"
        # 测试副本仅改工作目录；清理与权限代码原样执行，不改产品脚本。
        script.write_text(source.replace("cd /workspace\n", f"cd {shlex.quote(str(root))}\n"))
        yield root, commands, script


def run(sandbox, owner="12345:12345"):
    _root, commands, script = sandbox
    environment = {**os.environ, "PATH": str(commands) + ":" + os.environ["PATH"]}
    environment["SOMEIP_AGENT_EVIDENCE_OWNER"] = owner
    return subprocess.run(
        ["bash", str(script)], env=environment, capture_output=True, timeout=5, check=False
    )


def test_failed_run_returns_private_evidence_to_non_root_runner(sandbox):
    root, _commands, _script = sandbox
    result = run(sandbox)
    assert result.returncode == 42, result.stderr.decode()
    evidence = root / "build" / "virtual-evidence"
    assert evidence.is_dir()
    for path in [evidence, *evidence.rglob("*")]:
        assert path.stat().st_uid == path.stat().st_gid == 12345
    assert (evidence / "private").stat().st_mode & 0o777 == 0o700
    assert (evidence / "private" / "log").stat().st_mode & 0o777 == 0o600

    def as_runner():
        os.setgroups([])
        os.setgid(12345)
        os.setuid(12345)

    # 验证真实非 root 用户可读取日志并创建下个安装包输出，不仅看 stat 或 root access。
    checked = subprocess.run(
        [
            "bash",
            "-c",
            'test -r "$1/private/log" && mkdir "$2/installed-evidence"',
            "权限验证",
            str(evidence),
            str(root / "build"),
        ],
        preexec_fn=as_runner,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr.decode()


def test_invalid_owner_fails_before_creating_evidence(sandbox):
    root, _commands, _script = sandbox
    result = run(sandbox, owner="runner;other")
    assert result.returncode == 1
    assert "UID:GID" in result.stderr.decode()
    assert not (root / "build" / "virtual-evidence").exists()


def test_evidence_root_symlink_is_rejected_without_changing_target(sandbox):
    root, _commands, _script = sandbox
    target = root / "not-evidence"
    target.mkdir(mode=0o700)
    (root / "build" / "virtual-evidence").symlink_to(target, target_is_directory=True)
    result = run(sandbox)
    assert result.returncode == 1
    assert "符号链接" in result.stderr.decode()
    assert target.stat().st_uid == 0 and target.stat().st_mode & 0o777 == 0o700
    assert not list(target.iterdir())


def test_cleanup_failure_never_masks_original_exit_code(sandbox):
    _root, commands, _script = sandbox
    chown = commands / "chown"
    chown.write_text("#!/bin/bash\nprintf '%s\\n' '测试所有权归还失败' >&2\nexit 77\n")
    chown.chmod(0o755)
    result = run(sandbox)
    assert result.returncode == 42
    assert "归还所有权失败" in result.stderr.decode()


def test_cleanup_does_not_follow_nested_symlink(sandbox):
    root, commands, _script = sandbox
    outside = root / "build" / "outside-evidence"
    outside.mkdir(mode=0o700)
    command = commands / "ip"
    command.write_text(
        "#!/bin/bash\nln -s ../outside-evidence build/virtual-evidence/link\nexit 42\n"
    )
    result = run(sandbox)
    assert result.returncode == 42
    assert outside.stat().st_uid == 0 and outside.stat().st_mode & 0o777 == 0o700
    assert (root / "build" / "virtual-evidence" / "link").lstat().st_uid == 12345
