# Cogniflow UI v1.1.0

A single FastAPI web application that unifies the **Observer** and the **Configurator**
into one process. Students get one URL, one login, one launcher.

This release (v1.1.0) merges the v3.5 line of the Observer and Configurator and ships
against the v1.1.0 orchestrator. Compared to v1.0.x, it adds: per-agent input/output
schemas with structured violation drill-down, approval routing (on_reject / on_approve)
between agents, the secrets vault with usage scanning and pipeline-scoped audit, the
pipeline-level Settings tab (approver / poll-interval / rehydrate-outputs + secrets
migration banner), the run-history audit panel, and the pause/resume controls on the
board.

## Layout

```
cogniflow-ui/
├── app.py                  Top-level FastAPI entrypoint
├── launch.py               Bundled-binary entrypoint (PyInstaller)
├── config.json             Top-level UI config (title, version, host, port)
├── seeding.py              Overlay-on-startup logic for bundled pipelines
├── seed_pipelines/         Pipelines bundled with this UI release (empty by default)
├── observer/               Observer subsystem (Python package)
│   ├── app.py              APIRouter exposing observer routes
│   ├── config.json         Observer config (orchestrator_root, port, versioning, …)
│   ├── config.py / filesystem.py / dag_svg.py / versioning.py / vault_view.py
│   ├── templates/
│   └── static/             Mounted at /static/observer/
├── configurator/           Configurator subsystem (Python package)
│   ├── app.py              APIRouter exposing configurator routes
│   ├── config.json         Configurator config (pipelines_root, claude_bin, …)
│   ├── config.py / filesystem.py / dag_svg.py / versioning.py / validation.py
│   ├── meta_specialize.py / orchestrator_bridge.py
│   ├── prompt_templates/   Per-agent-type starter prompts (+ _meta/ for Specialize)
│   ├── templates/
│   └── static/             Mounted at /static/configurator/
├── cogniflow-ui.spec       PyInstaller spec
├── requirements.txt
└── paths.py                Path resolution helpers (frozen vs. source)
```

## Quick start

```
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt
venv/Scripts/python -m uvicorn app:app --reload
```

Then open http://127.0.0.1:8000. The merged UI lands on the Observer's home
(pipelines list); the navbar has a `⚙ Configurator` button to switch surfaces.

## URL map

| URL prefix | Owner | Purpose |
|---|---|---|
| `/` | Observer | Pipelines list (home). Navbar links to Configurator + Vault viewer. |
| `/pipelines/{name}/...` | Observer | Pipeline board, agent details, history, approval queue, audit, start/stop/pause/resume/reset/approve. |
| `/observer/vault*` | Observer | Read-only vault viewer (relocated from `/vault*` to avoid colliding with the configurator's CRUD). |
| `/configurator` | Configurator | Configurator pipelines grid (was `/` in standalone). |
| `/pipeline/{name}/...` | Configurator | Pipeline editor, graph topology, prompts, agents, schemas, approval routes, files, validation, settings. |
| `/templates`, `/prompt-templates` | Configurator | Pipeline templates and prompt templates. |
| `/vault*` | Configurator | Full vault CRUD (new/replace/metadata/delete/usage). |
| `/static/observer/*` | Observer | Observer CSS + JS. |
| `/static/configurator/*` | Configurator | Configurator CSS + JS. |

The two subsystems live in the same process but use disjoint URL prefixes
(observer is plural `/pipelines/...`, configurator is singular `/pipeline/...`),
so there are no route collisions.

## Where the data actually lives

Pipelines (and any new ones the student creates, or labs add) live on disk
**outside** this package, in the orchestrator's pipelines folder. The UI just
reads and writes to that folder.

```
cogniflow-orchestrator/
└── pipelines/                          <- the live, mutable data folder
    ├── 01-writer-critic/
    ├── 99-my-lab-pipeline/             <- created by a student, never overwritten
    ├── secrets.db                      <- the vault (managed by orchestrator + UI)
    ├── .ui-seed-marker.json            <- tracks last seeded version
    └── ...
```

This separation is intentional: the UI bundle is read-only, the data folder is
mutable, and they evolve independently.

## Distribution

For non-technical students the shipping form is:

* `cogniflow-ui.exe` (Windows, built with PyInstaller)
* `Cogniflow UI.app` (macOS, built via GitHub Actions on a `macos-14` runner)

Both binaries read `config.json` from the folder *next to* the executable, so users
can edit the host, port, and pointer to the orchestrator's pipelines folder without
unpacking the bundle.

GitHub Actions workflows under `.github/workflows/` produce both platforms' zipped
builds automatically when a `dag-vX.Y.Z` tag is pushed.
