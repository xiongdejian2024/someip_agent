"""为 Windows 安装包生成与后端兼容的 Ed25519 签名升级清单。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

from check_version import validate
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

LOGGER = logging.getLogger("update-manifest")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        LOGGER.exception("计算安装包哈希失败: %s", path)
        raise
    return digest.hexdigest()


def load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        LOGGER.exception("读取 Ed25519 私钥失败: %s", path)
        raise
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("升级签名私钥必须是无密码 PEM 格式的 Ed25519 私钥")
    return key


def public_key_base64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def write_manifest(
    *,
    artifact: Path,
    download_url: str,
    private_key_path: Path,
    output: Path,
    version: str,
    release_notes: str | None,
    force: bool,
) -> dict[str, object]:
    parsed_url = urlparse(download_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("下载地址必须是有效的 HTTPS URL")
    if not artifact.is_file():
        raise FileNotFoundError(f"安装包不存在: {artifact}")
    if output.exists() and not force:
        raise FileExistsError(f"输出已存在: {output}；确认覆盖时请使用 --force")

    LOGGER.info("步骤 1/4：计算安装包 SHA-256")
    digest = sha256_file(artifact)
    LOGGER.info("步骤 2/4：读取 Ed25519 发布私钥")
    private_key = load_private_key(private_key_path)
    signed_data = f"{version}\n{digest}\n{download_url}".encode()
    signature = base64.b64encode(private_key.sign(signed_data)).decode("ascii")

    manifest: dict[str, object] = {
        "version": version,
        "download_url": download_url,
        "sha256": digest,
        "signature": signature,
    }
    if release_notes:
        manifest["release_notes"] = release_notes

    LOGGER.info("步骤 3/4：写入升级清单")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
    except OSError:
        LOGGER.exception("写入升级清单失败: %s", output)
        temporary.unlink(missing_ok=True)
        raise

    LOGGER.info("步骤 4/4：升级清单生成完成: %s", output)
    LOGGER.info("部署到客户端的公钥（Base64）: %s", public_key_base64(private_key))
    return manifest


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path, help="待发布安装包")
    parser.add_argument("--download-url", required=True, help="安装包最终 HTTPS 地址")
    parser.add_argument("--private-key", required=True, type=Path, help="Ed25519 PEM 私钥文件")
    parser.add_argument("--output", required=True, type=Path, help="升级清单输出路径")
    parser.add_argument("--version", help="版本；默认读取并校验仓库 VERSION")
    parser.add_argument("--release-notes", help="可选发布说明")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的输出清单")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    root = Path(__file__).resolve().parents[1]
    try:
        repository_version = validate(root)
        version = args.version or repository_version
        if version != repository_version:
            raise ValueError(
                f"指定版本 {version!r} 与已校验仓库版本 {repository_version!r} 不一致"
            )
        write_manifest(
            artifact=args.artifact.resolve(),
            download_url=args.download_url,
            private_key_path=args.private_key.resolve(),
            output=args.output.resolve(),
            version=version,
            release_notes=args.release_notes,
            force=args.force,
        )
    except Exception:
        LOGGER.exception("生成签名升级清单失败")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
