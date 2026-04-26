# Cogniflow Multi-Agent DAG Orchestrator

A file-based orchestration engine for Claude CLI multi-agent pipelines. Agents are defined in `pipeline.json`, communicate through structured files, and produce a fully observable event stream.

```
pipeline.json  →  orchestrator  →  agents run in layers  →  events.jsonl
                                         ↓
                              .state/agents/<id>/
                                  01_system.md      ← role definition
                                  02_prompt.md      ← task
                                  03_inputs/        ← upstream outputs
                                  04_context.md     ← assembled (claude reads this)
                                  05_output.md      ← response (symlink → versioned)
                                  06_status.json    ← lifecycle state
```

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Validate the sample pipeline (no Claude calls)
python cli.py validate pipelines/ai_coding_2026

# 3. Run it
python cli.py run pipelines/ai_coding_2026

# 4. Read the final article
python cli.py inspect pipelines/ai_coding_2026 --agent 007_editor --file output
```

See [INSTALL.md](INSTALL.md) for the complete installation guide, including Windows (`claude.exe`) setup.

---

## Architecture

### Execution model

The pipeline is a directed acyclic graph (DAG). The orchestrator resolves it into execution layers using Kahn's algorithm (via `networkx`). Agents in the same layer run in parallel via `ThreadPoolExecutor`. Fan-in agents wait for all dependencies before starting.

```
Layer 0 [parallel]:   001_researcher_tools  002_researcher_agentic  003_researcher_enterprise
Layer 1 [sequential]: 004_synthesizer          ← fan-in from Layer 0
Layer 2 [parallel]:   005_writer  006_fact_checker
Layer 3 [sequential]: 007_editor               ← fan-in from Layer 2
```

### Eight design improvements over the Bash prototype

This version is a ground-up Python rewrite of an earlier Bash orchestrator. Every improvement closes a concrete failure mode of the prototype.

| # | Improvement | Where applied | Problem it solves |
|---|---|---|---|
| IMP-01 | Context written to `04_context.md`, not injected via `$(cat ...)` | `context.py::assemble_context()` | Bash `$()` expansion lost newlines, choked on quotes, and silently truncated at OS arg-length limits. The Python version builds the full context as a file — Claude reads that single file and nothing is splatted through the shell. |
| IMP-02 | `--system` flag populated from `01_system.md` | `agent.py` — `["claude", "--system", system_text, "-p", ...]` | The prototype concatenated role + task into one blob, so Claude had no proper system prompt. IMP-02 routes the role to the correct API slot. |
| IMP-03 | `validate_pipeline()` runs before the first Claude call | `core.py::run_pipeline()` — first call after setup | The prototype discovered problems mid-run, after spending credits on agents whose downstream would fail. Validation now collects all problems at once and refuses to start. |
| IMP-04 | Per-agent subprocess timeout | `agent.py` — `subprocess.run(..., timeout=config.agent_timeout)` | A hung Claude call could stall the whole pipeline indefinitely. Timeout raises `AgentTimeoutError`; status file records `timeout`. |
| IMP-05 | Versioned output files with a symlink to the latest | `agent.py` — writes `05_output.<run_id>.md`, symlinks `05_output.md` | Re-running an agent used to overwrite the previous output; there was no audit trail. Now every run is preserved; the symlink (or copy, on Windows) gives downstream agents a stable name. |
| IMP-06 | Token budget check at fan-in | `budget.py::check_and_prepare_inputs()` | Fan-in agents could exceed the model's context window unnoticed. Budget check runs before context assembly; configurable per-agent strategy (`hard_fail`, `select_top_n`, `auto_summarise`). |
| IMP-07 | Python + `networkx` replace Bash graph logic | entire `orchestrator/` package; `dag.py` uses `networkx.topological_generations` | The Bash version used grep loops for cycle detection and dependency ordering — O(n²), fragile, untested. `networkx` gives O(V+E) topological layering and real cycle reporting. A pure-Python Kahn's fallback lives in `dag.py` for installs without `networkx`. |
| IMP-08 | Conditional DAG branching via routers | `agent.py::_evaluate_router()` | The prototype was strictly linear. A router agent writes `routing.json` with a decision; the orchestrator marks non-matching branches as `bypassed` and skips them. |

### Per-agent execution order

`exec_agent()` (`agent.py`) runs each agent through this sequence:

1. Resume guard — skip if `06_status.json` is `done` (IMP-05 makes this safe to re-run)
2. `collect_inputs()` — copy upstream `05_output.md` into `03_inputs/`
3. Token budget check — `hard_fail` / `auto_summarise` / `select_top_n` (IMP-06)
4. `assemble_context()` — build `04_context.md` from prompt + inputs (IMP-01)
5. Write `running` status
6. `subprocess.run(claude, "--system", ..., "-p", ...)` — IMP-02, IMP-04
7. Write versioned output + update symlink (IMP-05)
8. Write `done` status
9. `_evaluate_router()` — IMP-08 (if the agent has a `router` block)

Validation (IMP-03) and layer computation (IMP-07) happen once up-front in `run_pipeline()` before any agent runs.

---

## CLI reference

```
python cli.py run      <pipeline_dir>   [--timeout N] [--claude-bin PATH] [--quiet]
python cli.py status   <pipeline_dir>
python cli.py watch    <pipeline_dir>   [--follow]
python cli.py validate <pipeline_dir>
python cli.py reset    <pipeline_dir>   [--agent AGENT_ID]
python cli.py inspect  <pipeline_dir>   --agent AGENT_ID [--file status|output|context|prompt|system|config]
```

### Examples

```bash
# Run with a longer timeout for complex research (IMP-04)
python cli.py run pipelines/ai_coding_2026 --timeout 600

# Check agent status while running (second terminal)
python cli.py status pipelines/ai_coding_2026

# Stream the event log
python cli.py watch pipelines/ai_coding_2026 --follow

# Inspect what Claude received for one agent (IMP-01)
python cli.py inspect pipelines/ai_coding_2026 --agent 004_synthesizer --file context

# Re-run just one agent (IMP-05 keeps prior outputs for comparison)
python cli.py reset pipelines/ai_coding_2026 --agent 006_fact_checker
python cli.py run   pipelines/ai_coding_2026

# Full reset and re-run
python cli.py reset pipelines/ai_coding_2026
python cli.py run   pipelines/ai_coding_2026
```

---

## Creating a pipeline

### 1. Define the DAG in `pipeline.json`

```json
{
  "name": "my-pipeline",
  "agents": [
    { "id": "001_analyst",   "depends_on": [] },
    { "id": "002_analyst",   "depends_on": [] },
    { "id": "003_synth",     "depends_on": ["001_analyst", "002_analyst"] },
    { "id": "004_writer",    "depends_on": ["003_synth"] }
  ]
}
```

### 2. Create agent instruction files

For each agent ID declared in `pipeline.json`:

```
agents/
  001_analyst/
    01_system.md    ← who this agent is (role, constraints, style)
    02_prompt.md    ← what it should do this run
    00_config.json  ← optional: budget_strategy, router
  003_synth/
    01_system.md
    02_prompt.md
    00_config.json  ← set budget_strategy: "auto_summarise" for fan-in agents
```

### 3. Validate, then run

```bash
python cli.py validate my-pipeline/    # IMP-03: exhaustive check, no Claude calls
python cli.py run      my-pipeline/
```

### Agent configuration (`00_config.json`)

```json
{
  "budget_strategy": "auto_summarise",
  "router": { "routes": { "approved": ["005_pub"], "rework": ["005_fix"] } }
}
```

| Key | Values | Default | Effect | Improvement |
|---|---|---|---|---|
| `budget_strategy` | `hard_fail` \| `auto_summarise` \| `select_top_n` | `hard_fail` | Action when fan-in context exceeds token budget | IMP-06 |
| `router` | object (see below) | `null` | Conditional DAG branching | IMP-08 |
| `requires_approval` | `true` \| `false` | `false` | Declared but **not yet implemented** — added in a later version |  |

### Conditional routing (IMP-08)

```json
{
  "router": {
    "condition_file": "routing.json",
    "routes": {
      "approved":     ["005_publisher"],
      "needs_rework": ["005_corrector"]
    }
  }
}
```

The agent writes `routing.json` to its workspace:
```json
{ "decision": "approved", "reason": "All claims verified." }
```

On completion, the orchestrator reads the decision, marks `["005_corrector"]` as `bypassed`, and skips it in subsequent layers.

---

## Observability

All outputs persist in `.state/` and survive interruption:

```
.state/
  events.jsonl              ← append-only structured event stream
  agents/
    <id>/
      04_context.md         ← exact prompt claude received (IMP-01)
      05_output.md          ← symlink → latest versioned output (IMP-05)
      05_output.<run>.md    ← versioned output, never overwritten (IMP-05)
      06_status.json        ← status · timing · exit_code · bytes
```

Status writes are atomic (`tmp → rename`) so a reader never sees a partial file.

### Event types

| Event | Source | Fields |
|---|---|---|
| `pipeline_start` / `pipeline_done` / `pipeline_error` | `core.py` | run_id, layers, duration_s |
| `layer_start` / `layer_done` / `layer_fail` | `core.py` | layer, agents, parallel |
| `agent_launched` / `agent_start` / `agent_done` | `agent.py` | agent, duration_s, output_bytes |
| `agent_inputs_collected` | `context.py` | input_count |
| `agent_budget_estimated` | `budget.py` (IMP-06) | context_bytes, tokens_estimated (pre-flight, incl. system prompt) |
| `agent_context_ready` | `context.py` | context_bytes, tokens_estimated (actual size of `04_context.md`) |
| `agent_skip` / `agent_fail` / `agent_timeout` | `agent.py` | reason, exit_code, timeout_s |
| `agent_budget_exceeded` | `budget.py` (IMP-06) | tokens_estimated, budget, strategy |
| `agent_bypassed` / `router_decision` | `agent.py` (IMP-08) | by_router, decision, activated, skipped |

### Event stream queries

```bash
# Filter failures
python -c "
import json
for l in open('.state/events.jsonl'):
    e = json.loads(l)
    if e['event'] == 'agent_fail': print(e)
"

# Layer timing
python -c "
import json
for l in open('.state/events.jsonl'):
    e = json.loads(l)
    if 'layer' in e: print(e['ts'], e['event'], 'layer', e['layer'])
"
```

---

## Running the tests

```bash
pip install pytest
pytest tests/ -v
```

Test modules:

| File | Covers |
|---|---|
| `test_dag.py` | graph construction, topological layers, cycle detection (IMP-07) |
| `test_validate.py` | pipeline.json schema, file presence, budget/router checks (IMP-03) |
| `test_context.py` | `collect_inputs()`, `assemble_context()` (IMP-01) |

To include coverage:
```bash
pytest tests/ -v --cov=orchestrator --cov-report=term-missing
```

---

## Environment variables

| Variable | Default | Description | Improvement |
|---|---|---|---|
| `CLAUDE_BIN` | auto-detect | Full path to claude / claude.exe |  |
| `AGENT_TIMEOUT` | `300` | Seconds before killing a claude call | IMP-04 |
| `MODEL_CONTEXT_LIMIT` | `180000` | Token window for budget checks | IMP-06 |
| `INPUT_BUDGET_FRACTION` | `0.66` | Fraction of limit reserved for input | IMP-06 |
| `KEEP_OUTPUT_VERSIONS` | `1` | Set `0` to disable versioned outputs | IMP-05 |
| `MAX_PARALLEL_AGENTS` | `8` | Thread pool size for parallel layers | IMP-07 |
| `VERBOSE` | `1` | Set `0` to suppress per-agent lines |  |

---

## Implementation note on IMP-01

The `claude` CLI requires `-p` to run in non-interactive mode, and there is no documented stdin-only variant. The orchestrator passes the assembled context as the `-p` argument (argv) rather than piping via stdin. The win that matters for IMP-01 is unchanged: the full context lives in `04_context.md` and goes to Claude as a single file-backed payload, so newlines, quotes, and large upstream outputs survive intact. This is the direct replacement for the Bash prototype's `$(cat ...)` expansion, which lost all three.

See the comment block in `agent.py` near the `subprocess.run` call for the security note on argv visibility.

---

## Project layout

```
cogniflow-orchestrator/
├── cli.py                        ← entry point (6 commands)
├── requirements.txt
├── setup.py
├── INSTALL.md
├── README.md
├── orchestrator/
│   ├── __init__.py
│   ├── config.py                 ← runtime config + claude auto-detection
│   ├── exceptions.py             ← typed exception hierarchy
│   ├── events.py                 ← thread-safe JSONL event log
│   ├── dag.py                    ← networkx DAG + fallback Kahn's (IMP-07)
│   ├── validate.py               ← exhaustive upfront validation (IMP-03)
│   ├── budget.py                 ← token budget strategies (IMP-06)
│   ├── context.py                ← context assembly (IMP-01)
│   ├── agent.py                  ← agent executor (IMP-02, IMP-04, IMP-05, IMP-08)
│   └── core.py                   ← pipeline runner + observability helpers
├── tests/
│   ├── test_dag.py
│   ├── test_validate.py
│   └── test_context.py
└── pipelines/
    └── ai_coding_2026/           ← ready-to-run sample pipeline
        ├── pipeline.json
        └── agents/
            ├── 001_researcher_tools/
            ├── 002_researcher_agentic/
            ├── 003_researcher_enterprise/
            ├── 004_synthesizer/
            ├── 005_writer/
            ├── 006_fact_checker/
            └── 007_editor/
```
