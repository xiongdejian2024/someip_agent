"""复用 PyInstaller 和已验证原生制品构建 Linux 完整 ZIP 发行包。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

logger = logging.getLogger("Linux发行包")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def build(root: Path, output: Path, native: Path, library_dir: Path) -> Path:
    if sys.platform != "linux":
        raise RuntimeError("Linux 发行包必须在目标 Linux 架构上构建，不支持跨系统打包")
    sys.path.insert(0, str(root / "scripts"))
    from check_version import validate

    version = validate(root)
    if importlib.metadata.version("someip-agent-core") != version:
        raise ValueError("构建环境安装包版本与仓库不一致，请先安装当前后端")
    if not native.is_file() or not (root / "frontend/dist/index.html").is_file():
        raise FileNotFoundError("缺少原生二进制或已构建前端")
    native_version = subprocess.check_output(
        [str(native), "--version"], text=True
    ).strip()
    if "vsomeip 3.5.10" not in native_version:
        raise ValueError("原生二进制不是当前已验证的 vsomeip 固定版本")
    plugins = [
        library_dir / f"libvsomeip3{suffix}.so.3"
        for suffix in ("", "-cfg", "-sd", "-e2e")
    ]
    if not all(path.is_file() for path in plugins):
        raise FileNotFoundError("缺少固定版本 vsomeip 动态库/插件")
    output.mkdir(parents=True, exist_ok=False)
    logger.info("步骤 1/4：构建包含网页与原生依赖的独立主程序")
    common = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--noupx",
        "--distpath",
        str(output / "dist"),
        "--workpath",
        str(output / "work"),
        "--specpath",
        str(output / "spec"),
        "--paths",
        str(root / "backend/src"),
    ]
    command = [
        *common,
        "--onedir",
        "--name",
        "someip-agent",
        "--add-data",
        f"{root / 'frontend/dist'}:web",
        "--copy-metadata",
        "someip-agent-core",
        "--collect-data",
        "certifi",
        "--collect-submodules",
        "someip_agent",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "keyring.backends",
        "--exclude-module",
        "pytest",
        "--add-binary",
        f"{native}:native",
    ]
    for plugin in plugins:
        command += ["--add-binary", f"{plugin}:."]
    subprocess.run([*command, str(root / "packaging/linux/launcher.py")], check=True)
    logger.info("步骤 2/4：构建安装目录外可独立运行的升级器")
    subprocess.run(
        [
            *common,
            "--onefile",
            "--name",
            "someip-agent-updater",
            str(root / "packaging/linux/updater.py"),
        ],
        check=True,
    )
    release = output / "release"
    # PyInstaller 的共享库别名会生成链接；ZIP 解压门禁保持严格，发行包中改为实际文件。
    shutil.copytree(output / "dist/someip-agent", release, symlinks=False)
    shutil.copy2(output / "dist/someip-agent-updater", release / "someip-agent-updater")
    shutil.copy2(root / "VERSION", release / "VERSION")
    logger.info("步骤 3/4：保存固定原生依赖来源、许可与构建摘要")
    notices = release / "third-party"
    notices.mkdir()
    shutil.copy2(
        root / "docs/native-dependencies.md", notices / "native-dependencies.md"
    )
    shutil.copy2(
        root / "native/patches/libtins-ipv4-options.patch", notices / "libtins.patch"
    )
    shutil.copy2(Path("/opt/vsomeip/LICENSE"), notices / "vsomeip-LICENSE")
    shutil.copy2(Path("/opt/libtins/LICENSE"), notices / "libtins-LICENSE")
    provenance = {
        "version": version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "pyinstaller": importlib.metadata.version("pyinstaller"),
        "native_sha256": digest(native),
        "native_version": native_version,
        "launcher_sha256": digest(release / "someip-agent"),
        "updater_sha256": digest(release / "someip-agent-updater"),
        "vsomeip": "3.5.10",
        "libtins": "4.6",
    }
    (release / "build-info.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("步骤 4/4：生成不带外层目录、保留执行权限的完整 ZIP")
    archive = output / f"someip-agent-{version}-linux-{platform.machine()}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(release.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(release))
    logger.info("发行包已生成：%s，SHA-256=%s", archive, digest(archive))
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-binary", type=Path, required=True)
    parser.add_argument("--library-dir", type=Path, default=Path("/usr/local/lib"))
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        build(
            args.root.resolve(),
            args.output.resolve(),
            args.native_binary.resolve(),
            args.library_dir,
        )
    except Exception:
        logger.exception("Linux 发行包构建失败，保留本次构建现场")
        raise


if __name__ == "__main__":
    main()
