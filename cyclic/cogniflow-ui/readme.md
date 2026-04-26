# Cyclic UI — placeholder

The cyclic flavor of the Cogniflow UI has not been built yet. When it
arrives, it will live in this folder and pair with
[`../cogniflow-orchestrator/`](../cogniflow-orchestrator/) (currently
v3.5 of the cyclic orchestrator).

The DAG flavor — already shipping — is here:
[`../../dag/cogniflow-ui/`](../../dag/cogniflow-ui/).

Until then, the cyclic orchestrator runs from CLI only:

```
cd ../cogniflow-orchestrator
python -m venv venv
venv/Scripts/python -m pip install -r requirements.txt
venv/Scripts/python -m cli run pipelines/research_dag
```

See [`../cogniflow-orchestrator/README.md`](../cogniflow-orchestrator/README.md)
for the full CLI surface.
