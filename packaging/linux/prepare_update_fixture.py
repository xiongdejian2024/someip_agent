"""在独立临时源码副本编译第二版本，仅供真实发行包升级验收。"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from build import build

logger = logging.getLogger("Linux升级夹具")


def prepare(root: Path, output: Path, native: Path) -> Path:
    sys.path.insert(0, str(root / "scripts"))
    from check_version import SEMVER_PATTERN, validate

    baseline = validate(root)
    matched = SEMVER_PATTERN.fullmatch(baseline)
    assert matched is not None
    version = f"{matched['major']}.{matched['minor']}.{int(matched['patch']) + 1}"
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    for relative in (
        "backend",
        "packaging/linux",
        "scripts",
        "docs",
        "native/patches",
        "frontend/dist",
    ):
        shutil.copytree(
            root / relative,
            source / relative,
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
        )
    shutil.copy2(root / "native/CMakeLists.txt", source / "native/CMakeLists.txt")
    (source / "VERSION").write_text(version + "\n", encoding="utf-8")
    version_file = source / "backend/src/someip_agent/version.py"
    version_file.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    project = source / "backend/pyproject.toml"
    project.write_text(
        project.read_text().replace(f'version = "{baseline}"', f'version = "{version}"', 1),
        encoding="utf-8",
    )
    frontend = json.loads((root / "frontend/package.json").read_text())
    frontend["version"] = version
    (source / "frontend/package.json").write_text(json.dumps(frontend), encoding="utf-8")
    logger.info("使用独立副本构建测试版 %s，不修改仓库或发布该版本", version)
    install = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-build-isolation",
    ]
    try:
        subprocess.run([*install, str(source / "backend")], check=True)
        return build(source, output / "new", native, Path("/usr/local/lib"))
    finally:
        subprocess.run([*install, str(root / "backend")], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-binary", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        prepare(args.root.resolve(), args.output.resolve(), args.native_binary.resolve())
    except Exception:
        logger.exception("Linux 真实升级夹具构建失败")
        raise
