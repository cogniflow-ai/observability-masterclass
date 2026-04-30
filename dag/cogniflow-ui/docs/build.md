# Building the Cogniflow UI executables

Companion to [`instruction.md`](instruction.md). This document covers only
the packaging pipeline — how to turn the source tree into a `cogniflow-ui.exe`
on Windows or a `Cogniflow UI.app` on macOS.

If you are looking for how the running app behaves at startup (seeding,
configs, URL map), read `instruction.md` instead.

---

## 1. What the bundle looks like

Both platforms use **PyInstaller one-folder mode** driven by a single
`cogniflow-ui.spec` file. The output is a folder, not a single file —
faster cold start and easier to debug than one-file mode.

Distributed contents on Windows:

```
Cogniflow-UI-Windows.zip
└── cogniflow-ui/
    ├── cogniflow-ui.exe          <- launcher; users double-click this
    ├── config.json               <- user-editable; see "User config" below
    └── _internal/                <- bundled Python runtime + libs + resources
        ├── observer/
        │   ├── templates/
        │   ├── static/
        │   └── config.json
        ├── configurator/
        │   ├── templates/
        │   ├── static/
        │   ├── prompt_templates/
        │   ├── meta_prompts_v4.md
        │   └── config.json
        └── seed_pipelines/
            ├── 01-writer-critic/
            └── ...
```

macOS distribution is `Cogniflow UI.app` (a directory disguised as a file
that macOS Finder treats as an application). Internally it has the same
bundled tree under `Contents/Frameworks/` and `Contents/Resources/`.

## 2. User config — what students edit

The single field a student typically changes is `orchestrator_root` in the
`config.json` next to the executable:

```json
{
  "app_title": "Cogniflow",
  "app_version": "1.0.0",
  "host": "127.0.0.1",
  "port": 8000,
  "orchestrator_root": "../dag/cogniflow-orchestrator"
}
```

`orchestrator_root` is resolved against the directory holding `config.json`
when relative, used as-is when absolute. Whatever it resolves to, the UI
expects a `pipelines/` subfolder underneath.

The two subpackage configs (`_internal/observer/config.json` and
`_internal/configurator/config.json`) ship inside the bundle as defaults.
Students should not edit them — values from the top-level `config.json`
override them at startup. See `instruction.md` §2 for the full field map.

## 3. Building locally on Windows

Prerequisites: a Python 3.10+ on PATH, this project's `venv` populated
from `requirements.txt`, plus PyInstaller.

```
cd dag/cogniflow-ui
venv/Scripts/python -m pip install pyinstaller
venv/Scripts/python -m PyInstaller --noconfirm --clean cogniflow-ui.spec
```

Output: `dist/cogniflow-ui/cogniflow-ui.exe` (~35 MB folder).

Before testing or shipping, copy `config.json` next to the exe:

```
copy config.json dist\cogniflow-ui\config.json
```

Then double-click `dist\cogniflow-ui\cogniflow-ui.exe`. A console window
opens, prints the seed banner and uvicorn logs, and your default browser
opens at `http://127.0.0.1:8000`. Closing the console stops the server.

To produce a distributable zip:

```
powershell Compress-Archive -Path dist/cogniflow-ui/* -DestinationPath dist/Cogniflow-UI-Windows.zip
```

## 4. Building locally on macOS

Same `cogniflow-ui.spec`, run on a Mac:

```
cd dag/cogniflow-ui
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean cogniflow-ui.spec
```

Output: `dist/Cogniflow UI.app/`. PyInstaller's `BUNDLE` step in the spec
wraps the one-folder build into an `.app` automatically.

Zip it for upload (use `ditto`, NOT `zip`, so the executable bit on the
launcher is preserved):

```
cd dist
ditto -c -k --sequesterRsrc --keepParent "Cogniflow UI.app" "Cogniflow-UI-macOS.zip"
```

## 5. Building remotely via GitHub Actions

Two workflows are provided under `.github/workflows/`:

* `build-windows.yml` — runs on `windows-latest`, produces a Windows zip.
* `build-macos.yml` — runs on `macos-14` (Apple-silicon), produces a `.app` zip.

Both trigger on:

* **Push of a `vX.Y.Z` tag** — the build is uploaded as a workflow artifact
  AND attached to a GitHub Release.
* **Manual dispatch** from the Actions tab — the build is uploaded as a
  workflow artifact only.

Tag-and-release flow:

```
git tag v1.0.0
git push origin v1.0.0
```

A few minutes later, both `cogniflow-ui-windows` and `cogniflow-ui-macos`
zips are attached to the v1.0.0 release page on GitHub. Students download
either zip, unpack it, and double-click the launcher.

## 6. macOS Gatekeeper — first-launch UX

Unsigned `.app` bundles trigger Gatekeeper on first launch:

> "Cogniflow UI.app" cannot be opened because the developer cannot be verified.

The student must:

1. Right-click the `.app` (or Ctrl-click), choose **Open**.
2. Click **Open** in the second confirmation dialog.

After this once-per-app step, normal double-click works.

To eliminate the warning entirely, set up a paid Apple Developer ID and
add a code-signing + notarization step to `build-macos.yml`. The TODO
inside that workflow file lists the secrets you'd need to configure
(`APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD`).

## 7. Versioning a release

When you ship a new release that includes updated bundled pipelines:

1. Edit pipelines under `seed_pipelines/<name>/` as needed.
2. Bump `app_version` in `dag/cogniflow-ui/config.json` (e.g. `1.0.0` → `1.1.0`).
3. Commit, then `git tag v1.1.0 && git push origin v1.1.0`.
4. CI builds and attaches both binaries to the v1.1.0 Release.
5. On every student's next launch, the seeder sees their marker file at
   `1.0.0`, the bundle at `1.1.0`, and re-overlays the seed pipelines.
   Their custom (non-bundled) pipelines and runtime artefacts are
   preserved. See `instruction.md` §1.4 for the complete table.

## 8. Common build issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError` for an `observer.` or `configurator.` submodule at runtime | A subpackage module not picked up by PyInstaller's static analysis. | Add it to `hiddenimports` in `cogniflow-ui.spec`. |
| `TemplateNotFound` for `base.html` at runtime | `templates/` not bundled or wrong destination path in the spec. | Verify the `datas` entry exists and `BUNDLE_DIR` is being used in `app.py`. |
| Browser opens to a 500 page on first launch | `pipelines_root` from `config.json` resolves to a folder that doesn't exist. | Edit `config.json` to point at the real orchestrator install. |
| Console window flashes and disappears | Unhandled startup exception. | Run the `.exe` from a `cmd.exe` (not double-click) so the error stays visible. |
| `[seed]` line says skipped but I want a re-seed | Marker version equals current version. | Bump `app_version` in `config.json`, or delete `<pipelines_root>/.ui-seed-marker.json`. |
