# Building the Cogniflow Orchestrator executables

Companion to [`README.md`](README.md). This document covers only the
packaging pipeline — how to turn the source tree into a
`cogniflow-orchestrator.exe` on Windows or a `Cogniflow Orchestrator.app`
on macOS.

The shipping form is a launcher binary that watches a `pipelines/`
directory for `.command.json` files (written by the UI) and spawns
itself in CLI mode to run individual pipelines. There is no separate
Python interpreter or `cli.py` file inside the bundle — everything lives
inside the executable.

## 1. What the bundle looks like

Both platforms use **PyInstaller one-folder mode** driven by a single
`cogniflow-orchestrator.spec` file. Distributed contents on Windows:

```
Cogniflow-Orchestrator-DAG-Windows.zip
└── cogniflow-orchestrator/
    ├── cogniflow-orchestrator.exe   <- launcher; users double-click this
    ├── config.json                  <- user-editable; pipelines_root, poll_interval_s
    └── _internal/                   <- bundled Python runtime + libs + cli/orchestrator modules
```

macOS distribution is `Cogniflow Orchestrator.app` — internally the
same one-folder tree under `Contents/Frameworks/` and `Contents/Resources/`.

## 2. User config — what students edit

```json
{
  "app_title": "Cogniflow Orchestrator",
  "app_version": "1.0.0",
  "pipelines_root": "./pipelines",
  "poll_interval_s": 1
}
```

`pipelines_root` is resolved against the directory holding the
executable when relative, used as-is when absolute. CLI flags
(`--root`, `--poll`) and env vars (`PIPELINES_ROOT`, `LAUNCHER_POLL_S`)
still override the values from `config.json`.

## 3. Architecture: how the frozen launcher runs pipelines

In source mode, `launcher.py` spawns subprocesses with
`[python_exe, cli.py, "run", <pipeline_dir>]`. In a frozen bundle there
is no Python interpreter or `cli.py` file on disk, so the launcher
detects `sys.frozen` and instead self-invokes:

```
[<bundle_exe>, "--cli-mode", "run", <pipeline_dir>]
```

The dispatcher at the top of `launcher.main()` catches the `--cli-mode`
sentinel and delegates to `cli.main()`. The `cli` module is bundled as a
hidden import (see the `hiddenimports` list in
`cogniflow-orchestrator.spec`). This is why every pipeline run shows up
as a child `cogniflow-orchestrator.exe` process rather than a `python`
process — same binary, different argv.

## 4. Building locally on Windows

```
cd dag/cogniflow-orchestrator
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt pyinstaller
venv/Scripts/python -m PyInstaller --noconfirm --clean cogniflow-orchestrator.spec
```

Output: `dist/cogniflow-orchestrator/cogniflow-orchestrator.exe`
(~4 MB folder).

Before testing or shipping, copy `config.json` next to the exe:

```
copy config.json dist\cogniflow-orchestrator\config.json
```

Smoke tests:

```
dist\cogniflow-orchestrator\cogniflow-orchestrator.exe --version
dist\cogniflow-orchestrator\cogniflow-orchestrator.exe --help
dist\cogniflow-orchestrator\cogniflow-orchestrator.exe --cli-mode --help
```

The third command exercises the self-invocation path — it should print
the orchestrator CLI usage (with `run`, `status`, `watch`, etc.).

To produce a distributable zip:

```
powershell Compress-Archive -Path dist/cogniflow-orchestrator/* -DestinationPath dist/Cogniflow-Orchestrator-DAG-Windows.zip
```

## 5. Building locally on macOS

```
cd dag/cogniflow-orchestrator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean cogniflow-orchestrator.spec
```

Output: `dist/Cogniflow Orchestrator.app/`. Zip with `ditto`, NOT `zip`,
to preserve the executable bit:

```
cd dist
ditto -c -k --sequesterRsrc --keepParent "Cogniflow Orchestrator.app" "Cogniflow-Orchestrator-DAG-macOS.zip"
```

## 6. Building remotely via GitHub Actions

Two workflows under `<repo-root>/.github/workflows/`:

* `build-dag-orch-windows.yml` — Windows zip
* `build-dag-orch-macos.yml` — macOS `.app` zip

Both trigger on `dag-v*.*.*` tag pushes (the same prefix used by the UI
workflows), so a single tag push produces all four DAG artifacts on the
same Release page.

## 7. macOS Gatekeeper

Same as the UI: unsigned `.app` triggers Gatekeeper's "developer cannot
be verified" warning on first launch. Right-click → Open clears it
once. To eliminate entirely, add `APPLE_ID` / `APPLE_TEAM_ID` /
`APPLE_APP_PASSWORD` secrets and wire `codesign` +
`xcrun notarytool` into `build-dag-orch-macos.yml`.

## 8. Common build issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: cli` at runtime when `--cli-mode` is invoked | `cli` missing from `hiddenimports`. | Add `"cli"` to the list in `cogniflow-orchestrator.spec`. |
| `ModuleNotFoundError` for an `orchestrator.*` submodule during a pipeline run | Submodule not in `hiddenimports`. | Add it to the list. |
| Launcher starts but never spawns a pipeline subprocess despite `.command.json` appearing | `pipelines_root` resolves to a different folder than where the UI is writing. | Verify the path printed in the launcher banner matches the UI's `orchestrator_root` resolved location. |
| Double-clicked `.exe` flashes and disappears | Unhandled startup exception. | Run the `.exe` from a `cmd.exe` (not double-click) so the error stays visible. |
| `--cli-mode --help` prints launcher help instead of CLI help | The `--cli-mode` dispatcher at the top of `launcher.main()` was bypassed (e.g. someone removed it). | Restore the dispatcher; it must run before argparse. |
