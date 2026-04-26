# Observability Masterclass

Code, slides, and exercises for the Observability Masterclass. The course
walks students through building, observing, and reasoning about
multi-agent pipelines, using two complementary execution models so the
contrast between them is visible at the file system level.

## The two flavors

This repo ships **two parallel reference implementations**. Each is
self-contained: clone once, pick a flavor, run.

| Folder | Execution model | Status |
|---|---|---|
| [`dag/`](dag/) | DAG (directed acyclic graph) — pipelines run as a fixed topology of agent stages, evaluated once start to finish. | Active. UI + orchestrator both present. |
| [`cyclic/`](cyclic/) | Cyclic — pipelines support loops and back-edges, agents can revisit earlier stages based on conditions. | Orchestrator present (v3.5). UI in development. |

Each flavor folder contains:

* `cogniflow-ui/` — the FastAPI web UI students interact with (Observer + Configurator unified)
* `cogniflow-orchestrator/` — the Python runtime that executes pipelines

The UI ships as a packaged `.exe` (Windows) and `.app` (macOS). The
orchestrator runs from source. See each flavor's own `readme.md` for
details.

## Quick start (DAG flavor)

```
cd dag/cogniflow-ui
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt
venv/Scripts/python -m uvicorn app:app --reload
```

Then open http://127.0.0.1:8000 in your browser. The bundled seed
pipelines overlay onto `dag/cogniflow-orchestrator/pipelines/` on first
launch.

## Distribution

Pre-built binaries are published on the
[Releases page](../../releases/latest). Each DAG release ships **four**
zips — UI and orchestrator for both platforms:

* `Cogniflow-UI-DAG-Windows.zip`
* `Cogniflow-UI-DAG-macOS.zip`
* `Cogniflow-Orchestrator-DAG-Windows.zip`
* `Cogniflow-Orchestrator-DAG-macOS.zip`

(Cyclic builds coming once `cyclic/cogniflow-ui/` exists.)

CI builds are triggered by tag pushes:

* `dag-vX.Y.Z` → builds DAG UI **and** DAG orchestrator for both platforms (4 artifacts)
* `cyclic-vX.Y.Z` → builds the cyclic flavor for both platforms (once those workflows exist)

See [`dag/cogniflow-ui/github.md`](dag/cogniflow-ui/github.md) for the
full release operations manual, and
[`dag/cogniflow-orchestrator/building.md`](dag/cogniflow-orchestrator/building.md)
for orchestrator-specific packaging notes.

## Repo layout

```
observability-masterclass/
├── readme.md                             <- this file
├── .github/workflows/
│   ├── build-dag-windows.yml             <- builds dag/cogniflow-ui for Windows
│   ├── build-dag-macos.yml               <- builds dag/cogniflow-ui for macOS
│   ├── build-dag-orch-windows.yml        <- builds dag/cogniflow-orchestrator for Windows
│   └── build-dag-orch-macos.yml          <- builds dag/cogniflow-orchestrator for macOS
├── dag/
│   ├── readme.md
│   ├── cogniflow-ui/
│   └── cogniflow-orchestrator/
└── cyclic/
    ├── readme.md
    ├── cogniflow-ui/                     <- in development
    └── cogniflow-orchestrator/
```
