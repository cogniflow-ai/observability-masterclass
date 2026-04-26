# Cogniflow UI

A single FastAPI web application that unifies the **Observer** and the **Configurator**
into one process. It frees students from running two separate servers, gives them one
URL to remember, and ships with a curated set of starter pipelines that overlay onto
the orchestrator's pipelines folder on launch.

This UI is the **DAG-flavor** UI. The Observer and Configurator subsystems
live as `observer/` and `configurator/` Python packages inside this folder
and share one process. The companion runtime that actually executes
pipelines is at [`../cogniflow-orchestrator/`](../cogniflow-orchestrator/).

For the cyclic-flavor stack, see [`../../cyclic/`](../../cyclic/) (the
cyclic UI is in development; only the cyclic orchestrator ships today).

## Layout

```
dag/cogniflow-ui/
├── app.py                  Top-level FastAPI entrypoint
├── config.json             Top-level UI config (title, version, host, port)
├── seeding.py              Overlay-on-startup logic for bundled pipelines
├── seed_pipelines/         Pipelines bundled with this UI release
│   ├── 01-writer-critic/
│   ├── 04-code-review-board/
│   ├── 07-newspaper-ai-article/
│   ├── 10-software-factory/
│   └── 15-universe-origins/
├── observer/               Observer subsystem (Python package)
│   ├── app.py              APIRouter exposing observer routes
│   ├── config.json         Observer config (orchestrator_root, port, versioning, …)
│   ├── config.py / filesystem.py / dag_svg.py / versioning.py
│   ├── templates/          Jinja templates (base.html, index.html, board.html, …)
│   └── static/             Mounted at /static/observer/
├── configurator/           Configurator subsystem (Python package)
│   ├── app.py              APIRouter exposing configurator routes
│   ├── config.json         Configurator config (pipelines_root, claude_bin, …)
│   ├── config.py / filesystem.py / dag_svg.py / versioning.py / validation.py / meta_specialize.py
│   ├── meta_prompts_v4.md
│   ├── prompt_templates/
│   ├── templates/
│   └── static/             Mounted at /static/configurator/
├── requirements.txt
└── venv/                   (Local virtualenv, gitignore in production)
```

For the full explanation of how seeding works and how every configuration field maps
to a runtime behavior, see [`instruction.md`](instruction.md).

## Quick start

```
cd dag/cogniflow-ui
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt
venv/Scripts/python -m uvicorn app:app --reload
```

Then open http://127.0.0.1:8000 in your browser. The first launch overlays the bundled
`seed_pipelines/` onto whatever folder `observer/config.json -> orchestrator_root` points
at, so you should immediately see five example pipelines on the home page.

## URL map

| URL prefix | Belongs to | Purpose |
|---|---|---|
| `/` | Observer | Pipelines list (home). Includes a navbar link to the Configurator. |
| `/pipelines/{name}/...` | Observer | Pipeline board, agent details, history, start / stop / pause / resume / reset / approve. |
| `/configurator` | Configurator | Configurator pipelines grid (was `/` in standalone configurator). |
| `/pipeline/{name}/...` | Configurator | Pipeline editor, graph topology, prompts, agents, files, validation. |
| `/templates`, `/prompt-templates` | Configurator | Pipeline templates and prompt templates. |
| `/static/observer/*` | Observer | Observer CSS + JS. |
| `/static/configurator/*` | Configurator | Configurator CSS + JS. |

The two subsystems live in the same process but use disjoint URL prefixes
(observer is plural `/pipelines/...`, configurator is singular `/pipeline/...`),
so there are no route collisions.

## Where the data actually lives

The five bundled example pipelines (and any new ones the student creates, or labs add)
live on disk **outside** this package, in the orchestrator's pipelines folder. The UI
just reads and writes to that folder.

```
dag/cogniflow-orchestrator/
└── pipelines/                          <- the live, mutable data folder
    ├── 01-writer-critic/               <- seeded by this UI on first launch
    ├── 99-my-lab-pipeline/             <- created by a student, never overwritten
    ├── .ui-seed-marker.json            <- written by the UI; tracks last seeded version
    └── ...
```

This separation is intentional: the UI bundle is read-only, the data folder is mutable,
and they evolve independently. See [`instruction.md`](instruction.md) for the full
seeding lifecycle.

## Distribution

For non-technical students the shipping form is:

* `cogniflow-ui.exe` (Windows, built with PyInstaller — see [`building.md`](building.md))
* `Cogniflow UI.app` (macOS, built via GitHub Actions on a `macos-14` runner)

Both binaries read `config.json` from the folder *next to* the executable, so users
can edit the host, port, and pointer to the orchestrator's pipelines folder without
unpacking the bundle.

A working Windows build is produced at `dist/cogniflow-ui/` after running:

```
venv/Scripts/python -m PyInstaller --noconfirm --clean cogniflow-ui.spec
```

GitHub Actions workflows at the **repo root** (`<repo>/.github/workflows/`)
produce both platforms' zipped builds automatically when a `dag-vX.Y.Z` tag
is pushed:

* `build-dag-windows.yml` → `Cogniflow-UI-DAG-Windows.zip`
* `build-dag-macos.yml` → `Cogniflow-UI-DAG-macOS.zip`

Documentation is split by operation phase:

* [`github.md`](github.md) — operational manual for everything GitHub: account
  and CLI setup, creating the repo, daily workflow, shipping releases, secrets,
  troubleshooting auth.
* [`git-actions.md`](git-actions.md) — what the CI workflows actually do once
  triggered, with diagrams.
* [`building.md`](building.md) — local PyInstaller mechanics and Gatekeeper
  notes for the macOS `.app`.
