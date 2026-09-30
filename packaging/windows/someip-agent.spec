# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


project_root = Path(SPECPATH).resolve().parents[1]
backend_src = project_root / "backend" / "src"
frontend_dist = project_root / "frontend" / "dist"

if not frontend_dist.is_dir():
    raise SystemExit("缺少 frontend/dist，请先执行 npm --prefix frontend run build")

datas = [
    (str(frontend_dist), "web"),
    (str(project_root / "VERSION"), "."),
    (str(project_root / ".env.example"), "."),
]
datas += collect_data_files("certifi")

datas += copy_metadata("someip-agent-core")

hiddenimports = collect_submodules("someip_agent")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("keyring.backends")

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[str(backend_src)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="someip-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="someip-agent",
)
