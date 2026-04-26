# Cyclic flavor

Reference implementation of the Cogniflow stack with a **cyclic**
execution model. Pipelines support loops and back-edges — agents can
revisit earlier stages based on conditions, exchange messages, and
converge iteratively.

## Components

| Folder | Role | Status |
|---|---|---|
| [`cogniflow-ui/`](cogniflow-ui/) | FastAPI web UI for the cyclic flavor. | **Placeholder** — UI not yet built. |
| [`cogniflow-orchestrator/`](cogniflow-orchestrator/) | Cyclic orchestrator runtime (v3.5). Run from CLI. | Active. |

## Quick start (CLI only, until the UI exists)

```
cd cogniflow-orchestrator
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt
venv/Scripts/python cli.py run pipelines/research_dag
```

See [`cogniflow-orchestrator/README.md`](cogniflow-orchestrator/README.md)
for the full CLI surface and pipeline format.

## Comparison with DAG flavor

| Aspect | DAG ([`../dag/`](../dag/)) | Cyclic (this folder) |
|---|---|---|
| Topology | Directed acyclic — fixed stage order | Cyclic — agents can loop, send messages, converge |
| Execution | One pass start-to-finish | Iterative until convergence or max-cycles |
| UI | Shipping (.exe / .app) | Pending |
| Best for teaching | First-pass observability, clear lineage | Multi-agent coordination, deadlock detection, message routing |
