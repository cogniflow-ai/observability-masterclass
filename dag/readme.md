# DAG flavor

Reference implementation of the Cogniflow stack with a **DAG (directed
acyclic graph)** execution model. Pipelines are evaluated as a fixed
topology of agent stages, start to finish, no back-edges.

## Components

| Folder | Role |
|---|---|
| [`cogniflow-ui/`](cogniflow-ui/) | FastAPI web UI (Observer + Configurator unified). Ships as `.exe` / `.app`. |
| [`cogniflow-orchestrator/`](cogniflow-orchestrator/) | Launcher binary that watches `pipelines/` for `.command.json` files and runs each pipeline as a subprocess. Ships as `.exe` / `.app`. |

## Quick start

```
cd cogniflow-ui
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt
venv/Scripts/python -m uvicorn app:app --reload
```

Open http://127.0.0.1:8000. The UI seeds bundled pipelines into
`../cogniflow-orchestrator/pipelines/` on first launch.

For the full operational manual (building, releasing, troubleshooting),
see the docs inside `cogniflow-ui/`:

* [`cogniflow-ui/readme.md`](cogniflow-ui/readme.md) — UI orientation
* [`cogniflow-ui/instruction.md`](cogniflow-ui/instruction.md) — runtime behavior, seeding
* [`cogniflow-ui/building.md`](cogniflow-ui/building.md) — local PyInstaller builds
* [`cogniflow-ui/github.md`](cogniflow-ui/github.md) — GitHub operations and releases
* [`cogniflow-ui/git-actions.md`](cogniflow-ui/git-actions.md) — what the CI workflows do
