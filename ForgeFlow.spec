# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH)
source_root = project_root / "src"
icon_path = source_root / "forgeflow" / "resources" / "forgeflow.ico"

analysis = Analysis(
    [str(project_root / "packaging" / "forgeflow_launcher.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[
        # BlenderAdapter launches this source file with the agent's external Python.
        (str(source_root / "forgeflow" / "adapters" / "blender_bridge.py"), "forgeflow/adapters"),
        (str(icon_path), "forgeflow/resources"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="ForgeFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
    version=str(project_root / "packaging" / "version_info.txt"),
    uac_admin=False,
)
