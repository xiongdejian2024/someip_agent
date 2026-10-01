"""完整 SDK 源码单独归档，不把源码文件数扩散到升级准备的解压路径。"""

import importlib.util
import zipfile
from pathlib import Path

import pytest


def builder():
    path = Path(__file__).resolve().parents[2] / "packaging/linux/build.py"
    spec = importlib.util.spec_from_file_location("linux_archive_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sdk_archive_preserves_nested_bytes_and_excludes_git(tmp_path):
    source = tmp_path / "sdk"
    (source / "implementation/sd").mkdir(parents=True)
    (source / ".git/objects").mkdir(parents=True)
    (source / "implementation/sd/message.cpp").write_bytes(b"\x00\xff\n")
    (source / "LICENSE").write_bytes(b"MPL-2.0\n")
    (source / ".git/objects/secret").write_bytes(b"not archived")
    target = tmp_path / "sdk.zip"
    builder().archive_sdk(source, target)
    with zipfile.ZipFile(target) as bundle:
        assert set(bundle.namelist()) == {"implementation/sd/message.cpp", "LICENSE"}
        assert bundle.read("implementation/sd/message.cpp") == b"\x00\xff\n"
        assert bundle.read("LICENSE") == b"MPL-2.0\n"
    with pytest.raises(FileExistsError):
        builder().archive_sdk(source, target)


@pytest.mark.parametrize("directory", [False, True])
def test_sdk_archive_rejects_links_outside_source(tmp_path, directory):
    source = tmp_path / "sdk"
    source.mkdir()
    outside = tmp_path / "outside"
    if directory:
        outside.mkdir()
    else:
        outside.write_bytes(b"not archived")
    (source / "link").symlink_to(outside, target_is_directory=directory)
    with pytest.raises(ValueError, match="符号链接"):
        builder().archive_sdk(source, tmp_path / "sdk.zip")
