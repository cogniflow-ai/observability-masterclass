# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Cogniflow Orchestrator (DAG flavor).

Build:
    venv/Scripts/python -m PyInstaller cogniflow-orchestrator.spec

Output:
    dist/cogniflow-orchestrator/cogniflow-orchestrator.exe   (Windows, one-folder)
    dist/Cogniflow Orchestrator.app                          (macOS, when run on a Mac)

Why one-folder mode (not one-file):
    Faster cold start, smaller cumulative disk usage on student machines,
    easier to inspect when something fails.

Architecture note:
    The launcher subprocess-spawns cli.py to run individual pipelines. In a
    frozen bundle there is no Python interpreter to invoke and no cli.py
    file on disk. The launcher detects sys.frozen and instead self-invokes
    the bundle executable with `--cli-mode <args...>`; the dispatcher at the
    top of launcher.main() catches that sentinel and delegates to cli.main().
    This means we bundle cli.py as a Python module (via hiddenimports), not
    as a data file.

The user-editable `config.json` is NOT bundled — it sits next to the
executable in the release zip so students can change `pipelines_root`
without unpacking the bundle.
"""
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve()


datas = []  # No template/static assets to ship; the orchestrator is pure Python.


# Hidden imports: PyInstaller's static analysis sometimes misses package
# submodules that are only imported via string-based discovery. Listing
# them makes the build deterministic.
hiddenimports = [
    # Self-import: launcher.main() does `from cli import main as cli_main`
    # when --cli-mode is passed. Without this, cli.py wouldn't be bundled
    # as an importable module.
    "cli",
    # Orchestrator package + its modules (v3.5 surface area).
    "orchestrator",
    "orchestrator.agent",
    "orchestrator.approval",
    "orchestrator.budget",
    "orchestrator.config",
    "orchestrator.context",
    "orchestrator.core",
    "orchestrator.cyclic_agent",
    "orchestrator.cyclic_engine",
    "orchestrator.dag",
    "orchestrator.debug",
    "orchestrator.event_writer",
    "orchestrator.events",
    "orchestrator.exceptions",
    "orchestrator.hooks",
    "orchestrator.mailbox",
    "orchestrator.memory",
    "orchestrator.retrieval",
    "orchestrator.schema",
    "orchestrator.secrets",
    "orchestrator.validate",
    "orchestrator.vault",
    # Runtime deps that PyInstaller might miss in some edge cases.
    "networkx",
    "filelock",
]


a = Analysis(
    ["launcher.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
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
    [],
    exclude_binaries=True,
    name="cogniflow-orchestrator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,        # Keep console open — students see launcher logs / errors.
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
    upx=False,
    upx_exclude=[],
    name="cogniflow-orchestrator",
)


# macOS .app wrapper. Ignored on Windows builds.
app = BUNDLE(
    coll,
    name="Cogniflow Orchestrator.app",
    icon=None,
    bundle_identifier="com.cogniflow.orchestrator",
    info_plist={
        "CFBundleDisplayName": "Cogniflow Orchestrator",
        "CFBundleShortVersionString": "1.1.0",
        "NSHighResolutionCapable": "True",
    },
)
