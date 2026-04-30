# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone cogniflow-orchestrator .exe.

Bundles cogniflow_app.py (multicall bootstrap) together with launcher.py,
cli.py, and the orchestrator package into a single onefile executable.
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("orchestrator")
    + collect_submodules("networkx")
    + ["filelock", "cli", "launcher"]
)

a = Analysis(
    ["cogniflow_app.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="cogniflow-orchestrator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
