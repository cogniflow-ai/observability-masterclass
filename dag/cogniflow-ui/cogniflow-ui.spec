# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Cogniflow UI v1.1.0.

Build:
    venv/Scripts/python -m PyInstaller cogniflow-ui.spec

Output:
    dist/cogniflow-ui/cogniflow-ui.exe   (Windows, one-folder mode)
    dist/cogniflow-ui.app                (macOS, when run on a Mac)

We use one-folder mode rather than one-file mode because:
  * Faster cold start (no per-launch extraction).
  * Smaller cumulative disk usage on student machines.
  * Easier to inspect/debug if something goes wrong.

The user-editable `config.json` is NOT bundled — it should be copied next
to the executable as part of the release zip, so students can edit it.
"""
from pathlib import Path

# `.spec` files are executed by PyInstaller, which sets `__file__` to the
# spec path. Use that to locate project-relative data.
PROJECT_ROOT = Path(SPECPATH).resolve()


# Resources that must travel with the executable. Each tuple is
# (source_path, dest_relative_path_inside_bundle). Dest paths use forward
# slashes; PyInstaller normalises them for the host platform.
datas = [
    # Observer subpackage.
    (str(PROJECT_ROOT / "observer" / "templates"), "observer/templates"),
    (str(PROJECT_ROOT / "observer" / "static"),    "observer/static"),
    (str(PROJECT_ROOT / "observer" / "config.json"), "observer"),

    # Configurator subpackage.
    (str(PROJECT_ROOT / "configurator" / "templates"), "configurator/templates"),
    (str(PROJECT_ROOT / "configurator" / "static"),    "configurator/static"),
    (str(PROJECT_ROOT / "configurator" / "prompt_templates"), "configurator/prompt_templates"),
    (str(PROJECT_ROOT / "configurator" / "config.json"),       "configurator"),

    # Bundled seed pipelines (overlaid onto pipelines_root on first launch).
    (str(PROJECT_ROOT / "seed_pipelines"), "seed_pipelines"),
]


# Hidden imports — modules that aren't statically importable from the
# launcher entry but are needed at runtime. uvicorn loads loops/protocols
# dynamically; jinja2 loads filters via plugin discovery in some setups.
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # Submodules of our own packages — PyInstaller usually finds these,
    # but listing them makes the build deterministic.
    "observer",
    "observer.app",
    "observer.config",
    "observer.filesystem",
    "observer.dag_svg",
    "observer.versioning",
    "observer.vault_view",
    "configurator",
    "configurator.app",
    "configurator.config",
    "configurator.filesystem",
    "configurator.dag_svg",
    "configurator.versioning",
    "configurator.validation",
    "configurator.meta_specialize",
    "configurator.orchestrator_bridge",
    # markdown's extension auto-loader.
    "markdown.extensions.fenced_code",
    "markdown.extensions.tables",
]


a = Analysis(
    ["launch.py"],
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
    name="cogniflow-ui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,        # Keep console open — students can see seed banner + errors.
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
    name="cogniflow-ui",
)


# macOS .app wrapper. Ignored on Windows builds.
app = BUNDLE(
    coll,
    name="Cogniflow UI.app",
    icon=None,
    bundle_identifier="com.cogniflow.ui",
    info_plist={
        "CFBundleDisplayName": "Cogniflow UI",
        "CFBundleShortVersionString": "1.1.0",
        "NSHighResolutionCapable": "True",
    },
)
