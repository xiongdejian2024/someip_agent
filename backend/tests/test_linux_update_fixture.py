"""升级夹具随仓库版本变化，不把将来的 CI 固定在 0.1.0。"""

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("baseline,expected", [("1.2.3", "1.2.4"), ("2.0.0-rc.1", "2.0.1")])
def test_upgrade_fixture_uses_current_version_without_changing_original(
    tmp_path, monkeypatch, baseline, expected
):
    repository = Path(__file__).resolve().parents[2]
    root = tmp_path / "repository"
    for folder in (
        "backend/src/someip_agent",
        "packaging/linux",
        "scripts",
        "docs",
        "native/patches",
        "frontend/dist",
    ):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text(baseline)
    (root / "backend/pyproject.toml").write_text(f'[project]\nversion = "{baseline}"\n')
    (root / "backend/src/someip_agent/version.py").write_text(f'__version__ = "{baseline}"\n')
    (root / "frontend/package.json").write_text(json.dumps({"version": baseline}))
    shutil.copy2(repository / "scripts/check_version.py", root / "scripts/check_version.py")
    builds, installs = [], []

    def fake_build(source, output, native, _libraries):
        assert (source / "VERSION").read_text().strip() == expected
        assert f'version = "{expected}"' in (source / "backend/pyproject.toml").read_text()
        assert json.loads((source / "frontend/package.json").read_text())["version"] == expected
        assert (
            f'__version__ = "{expected}"'
            in (source / "backend/src/someip_agent/version.py").read_text()
        )
        builds.append((source, native))
        return output / "release.zip"

    monkeypatch.setitem(sys.modules, "build", SimpleNamespace(build=fake_build))
    spec = importlib.util.spec_from_file_location(
        "linux_update_fixture", repository / "packaging/linux/prepare_update_fixture.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess, "run", lambda command, **_kw: installs.append(command))
    output = tmp_path / "fixture"
    native = tmp_path / "soa_partner"
    assert module.prepare(root, output, native) == output / "new/release.zip"
    assert builds == [(output / "source", native)]
    assert installs[0][-1] == str(output / "source/backend")
    assert installs[1][-1] == str(root / "backend")
    assert (root / "VERSION").read_text() == baseline
    assert f'version = "{baseline}"' in (root / "backend/pyproject.toml").read_text()
