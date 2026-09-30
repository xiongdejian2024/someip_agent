"""校验仓库内所有版本源均为同一个 SemVer 版本。"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import re
import sys
from pathlib import Path

LOGGER = logging.getLogger("version-check")
SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        LOGGER.exception("读取版本文件失败: %s", path)
        raise


def read_backend_project_version(path: Path) -> str:
    """只读取 [project] 表的 version，避免为校验脚本新增 TOML 依赖。"""
    text = read_text(path)
    project_match = re.search(r"(?ms)^\[project\]\s*$\n(?P<body>.*?)(?=^\[|\Z)", text)
    if project_match is None:
        raise ValueError(f"{path} 缺少 [project] 表")
    version_match = re.search(
        r'(?m)^version\s*=\s*["\'](?P<version>[^"\']+)["\']\s*$',
        project_match.group("body"),
    )
    if version_match is None:
        raise ValueError(f"{path} 的 [project] 缺少静态 version")
    return version_match.group("version")


def read_python_version(path: Path) -> str:
    tree = ast.parse(read_text(path), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        raise ValueError(f"{path} 的 __version__ 必须是字符串常量")
    raise ValueError(f"{path} 缺少 __version__")


def read_frontend_version(path: Path) -> str:
    try:
        document = json.loads(read_text(path))
    except json.JSONDecodeError:
        LOGGER.exception("解析前端 package.json 失败: %s", path)
        raise
    version = document.get("version")
    if not isinstance(version, str):
        raise TypeError(f"{path} 缺少字符串 version")
    return version


def validate(root: Path, expected_tag: str | None = None) -> str:
    LOGGER.info("步骤 1/4：读取根版本")
    root_version = read_text(root / "VERSION").strip()
    if SEMVER_PATTERN.fullmatch(root_version) is None:
        raise ValueError(f"VERSION={root_version!r} 不符合 SemVer 2.0.0")

    LOGGER.info("步骤 2/4：读取后端、源码与前端版本")
    versions = {
        "VERSION": root_version,
        "backend/pyproject.toml": read_backend_project_version(root / "backend/pyproject.toml"),
        "backend/src/someip_agent/version.py": read_python_version(
            root / "backend/src/someip_agent/version.py"
        ),
        "frontend/package.json": read_frontend_version(root / "frontend/package.json"),
    }

    LOGGER.info("步骤 3/4：校验版本一致性")
    mismatches = {name: version for name, version in versions.items() if version != root_version}
    if mismatches:
        details = ", ".join(f"{name}={version}" for name, version in mismatches.items())
        raise ValueError(f"版本不一致，根版本为 {root_version}；{details}")

    if expected_tag:
        tag_version = expected_tag.removeprefix("v")
        if tag_version != root_version:
            raise ValueError(f"发布标签 {expected_tag!r} 与 VERSION={root_version!r} 不一致")

    LOGGER.info("步骤 4/4：版本校验通过，当前版本 %s", root_version)
    return root_version


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="项目根目录，默认由脚本位置推导",
    )
    parser.add_argument("--tag", help="可选发布标签，例如 v1.2.3")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        validate(args.root.resolve(), args.tag)
    except Exception:
        LOGGER.exception("语义版本校验失败")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
