# Cogniflow Orchestrator v3.5

Multi-agent AI orchestration via Claude CLI — supports both acyclic DAG pipelines and cyclic multi-agent graphs with bidirectional feedback loops.

**No API keys. No HTTP calls. Uses your Claude subscription via `claude -p`.**

---

## What's new in v3.5

v3.5 is v3.0 with the three gap-closing capabilities from v2.1 restored, and all configuration moved from environment variables into a per-pipeline `config.json`.

| Change | Detail |
|---|---|
| **GAP-1 restored** — output schema validation | `schema.py` is back. Declare an `output_schema` in any agent's `00_config.json` and 05_output.md is validated immediately after a successful invocation. 8 modes: `json`, `regex`, `contains`, `not_contains`, `min_words`, `max_words`, `starts_with`, `ends_with`. Also runs in the cyclic path — a schema violation triggers a correction-prompt retry before failing. |
| **GAP-2 restored** — secrets hygiene | `secrets.py` is back with three protections: `.gitignore` auto-generation excluding `.state/`, regex-based credential pattern scanning on `01_system.md` / `02_prompt.md` (advisory warnings, never blocks), and `{{VAR_NAME}}` placeholder substitution in instruction files. Substitution values now come from the `substitutions` block in `config.json` (not env vars). |
| **GAP-3 restored** — human-in-the-loop approval | `approval.py` is back with the full poll-loop semantics. When an agent's `00_config.json` has `"requires_approval": true`, the orchestrator writes `07_approval_request.json`, blocks until `07_approval.json` appears, and fails the run cleanly on rejection or timeout. Restart-safe: a process death during the wait re-enters the wait loop without re-invoking claude. Works in both DAG and cyclic paths. |
| **File-based configuration** | Zero Cogniflow env vars. Every runtime knob is in `<pipeline_dir>/config.json`. Missing file → defaults. Missing keys → defaults for those values. Unknown keys → silently ignored (forward-compatible). The only OS env vars still read are Windows platform conventions (`LOCALAPPDATA`, `APPDATA`) used to locate `claude.exe` when no path is set. |

All v3.0 features preserved: cyclic graph execution, five-file per-agent memory, LLM-as-retriever, CLAUDE.md generation, hook integration, deadlock detection, cycle limits, event taxonomy.

---

## Prerequisites

- Python 3.10 or later
- [Claude CLI](https://docs.claude.com/en/docs/claude-code/overview) installed and authenticated
- A Claude Pro or Team subscription (no API key needed)

```bash
claude -p "say hello"
```

---

## Installation

```bash
pip install -r requirements.txt

# Verify
python cli.py validate pipelines/research_dag
```

---

## Configuration — `<pipeline_dir>/config.json`

Every pipeline directory has its own `config.json`. Missing file → built-in defaults. Sample:

```json
{
  "claude": {
    "binary":          null,
    "default_model":   null,
    "summary_model":   null,
    "retrieval_model": null
  },
  "execution": {
    "agent_timeout_s":     300,
    "max_parallel_agents": 8,
    "verbose":             true
  },
  "budget": {
    "model_context_limit":   180000,
    "input_budget_fraction": 0.66
  },
  "output": {
    "keep_versions": true
  },
  "cyclic": {
    "loop_poll_s":                 0.5,
    "thread_window":               6,
    "thread_token_budget":         1500,
    "summary_max_tokens":          1000,
    "index_compression_threshold": 80,
    "artifact_max_inject_tokens":  800
  },
  "approval": {
    "approver":        "operator",
    "poll_interval_s": 10,
    "timeout_s":       3600
  },
  "substitutions": {
    "_warning":     "Non-production. Do not store real secrets here.",
    "CLIENT_NAME":  "Example Corp"
  }
}
```

### Configuration reference

| Section / key | Default | Purpose |
|---|---|---|
| `claude.binary` | auto-detect | Absolute path to the claude executable. Null → auto-detect. |
| `claude.default_model` | null (CLI default) | Passed as `--model` to every agent invocation. |
| `claude.summary_model` | = default_model | Model used for summary-update calls. |
| `claude.retrieval_model` | = default_model | Model used for retrieval calls. |
| `execution.agent_timeout_s` | 300 | Per-agent subprocess timeout. |
| `execution.max_parallel_agents` | 8 | ThreadPoolExecutor size in DAG mode. |
| `execution.verbose` | true | Per-agent progress output. |
| `budget.model_context_limit` | 180000 | Context window of your model. |
| `budget.input_budget_fraction` | 0.66 | Fraction reserved for input. |
| `output.keep_versions` | true | Keep `05_output.v{n}.md` versioned copies. |
| `cyclic.loop_poll_s` | 0.5 | Event-loop sleep interval. |
| `cyclic.thread_window` | 6 | Verbatim turns in `recent_thread.md`. |
| `cyclic.thread_token_budget` | 1500 | Token ceiling for `recent_thread.md`. |
| `cyclic.summary_max_tokens` | 1000 | Token limit on summary-update calls. |
| `cyclic.index_compression_threshold` | 80 | Index entries before archival. |
| `cyclic.artifact_max_inject_tokens` | 800 | Max artifact content injected per turn. |
| `approval.approver` | `"operator"` | Identity recorded on approve/reject. |
| `approval.poll_interval_s` | 10 | Seconds between `07_approval.json` polls. |
| `approval.timeout_s` | 3600 | Max seconds to wait for a decision. |
| `substitutions.*` | `{}` | `{{VAR}}` placeholders. Keys starting with `_` are metadata. |

### Migrating from v3.0 env vars

| Old env var | New location |
|---|---|
| `CLAUDE_BIN` | `claude.binary` |
| `COGNIFLOW_DEFAULT_MODEL` | `claude.default_model` |
| `COGNIFLOW_SUMMARY_MODEL` | `claude.summary_model` |
| `COGNIFLOW_RETRIEVAL_MODEL` | `claude.retrieval_model` |
| `AGENT_TIMEOUT` | `execution.agent_timeout_s` |
| `MAX_PARALLEL_AGENTS` | `execution.max_parallel_agents` |
| `VERBOSE` | `execution.verbose` |
| `MODEL_CONTEXT_LIMIT` | `budget.model_context_limit` |
| `INPUT_BUDGET_FRACTION` | `budget.input_budget_fraction` |
| `KEEP_OUTPUT_VERSIONS` | `output.keep_versions` |
| `COGNIFLOW_LOOP_POLL_S` | `cyclic.loop_poll_s` |
| `COGNIFLOW_THREAD_WINDOW` | `cyclic.thread_window` |
| `COGNIFLOW_THREAD_TOKEN_BUDGET` | `cyclic.thread_token_budget` |
| `COGNIFLOW_SUMMARY_MAX_TOKENS` | `cyclic.summary_max_tokens` |
| `COGNIFLOW_INDEX_COMPRESSION_THRESHOLD` | `cyclic.index_compression_threshold` |
| `COGNIFLOW_ARTIFACT_MAX_INJECT_TOKENS` | `cyclic.artifact_max_inject_tokens` |
| `COGNIFLOW_APPROVER` | `approval.approver` |

The `--timeout` and `--claude-bin` CLI flags still work and override the config.

---

## Running a DAG pipeline (acyclic, v2.1.0 compatible)

```bash
python cli.py run pipelines/research_dag
python cli.py status pipelines/research_dag
python cli.py inspect pipelines/research_dag --agent 003_editor --file output
```

---

## Running a cyclic pipeline

```bash
python cli.py hooks install pipelines/auth_module
python cli.py run pipelines/auth_module
python cli.py watch pipelines/auth_module --follow

# Inspect agent memory
python cli.py inspect pipelines/auth_module --agent architect --file summary
python cli.py inspect pipelines/auth_module --agent architect --file index
python cli.py inspect pipelines/auth_module --agent architect --file budget
python cli.py inspect pipelines/auth_module --agent architect --file history
```

---

## Pipeline definition

### Acyclic (DAG) — unchanged from v2.1.0

```json
{
  "name": "my-pipeline",
  "agents": [
    { "id": "researcher", "dir": "agents/001", "depends_on": [] },
    { "id": "writer",     "dir": "agents/002", "depends_on": ["researcher"] }
  ]
}
```

### Cyclic

```json
{
  "name": "my-cyclic-pipeline",
  "agents": [
    { "id": "pm",       "dir": "agents/00_pm" },
    { "id": "architect","dir": "agents/01_arch" },
    { "id": "developer","dir": "agents/02_dev" }
  ],
  "edges": [
    { "from": "pm",       "to": "architect", "type": "task",     "directed": true  },
    { "from": "architect","to": "developer", "type": "feedback", "directed": false }
  ],
  "termination": {
    "strategy":      "all_done",
    "max_cycles":    10,
    "timeout_s":     3600,
    "on_cycle_limit": "escalate_pm",
    "on_deadlock":    "force_unblock_oldest"
  },
  "tags": { "domain": ["auth", "api", "database"] }
}
```

**Edge types:**

| Type | directed | Meaning |
|------|----------|---------|
| `task` | `true` | One-way trigger, fires once (equivalent to `depends_on`) |
| `feedback` | `false` | Bidirectional persistent channel, multiple activations |
| `peer` | `false` | Bidirectional peer-to-peer channel |

### Per-agent `00_config.json`

```json
{
  "description":        "...",
  "budget_strategy":    "hard_fail | auto_summarise | select_top_n",
  "cyclic_token_budget": 30000,
  "max_retries":         2,
  "requires_approval":   false,
  "output_schema": {
    "mode":        ["min_words", "not_contains"],
    "min_words":   400,
    "not_contains": ["TODO", "[PLACEHOLDER]"]
  },
  "router": { "routes": { "approved": ["005_pub"] } }
}
```

---

## CLI reference

```
python cli.py run      <pipeline_dir> [--mode auto|dag|cyclic] [--timeout N] [--claude-bin PATH] [--quiet]
python cli.py validate <pipeline_dir>
python cli.py status   <pipeline_dir>
python cli.py watch    <pipeline_dir> [--follow]
python cli.py reset    <pipeline_dir> [--agent ID]
python cli.py inspect  <pipeline_dir> --agent ID --file output|context|system|status|summary|index|budget|history|thread
python cli.py approve  <pipeline_dir> --agent ID [--note "..."]
python cli.py reject   <pipeline_dir> --agent ID [--note "..."]
python cli.py hooks install <pipeline_dir>
```

---

## Agent system prompt requirements (cyclic mode)

Every cyclic agent's `01_system.md` **must** instruct Claude to end every
response with the routing JSON block. The orchestrator appends the exact
schema at runtime — your system prompt only needs to define the role,
responsibilities, and convergence criteria.

See `pipelines/auth_module/agents/*/01_system.md` for working examples.

---

## Running tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Project layout

```
cogniflow-orchestrator-v3.5/
├── cli.py                              ← entry point (9 commands)
├── requirements.txt
├── setup.py
├── README.md
├── VERSION_2.1.md   (historical — architecture notes, carried from v2.1)
├── VERSION_3.5.md   (this release — new file)
├── todo.txt
├── orchestrator/
│   ├── __init__.py                     ← re-exports GAP + core APIs
│   ├── config.py                       ← file-based loader, no env vars
│   ├── exceptions.py                   ← 15 typed exceptions (incl. GAP exceptions)
│   ├── events.py                       ← thread-safe EventLog (40 event methods)
│   ├── event_writer.py                 ← standalone writer for hook scripts
│   ├── dag.py                          ← networkx DAG, topological sort, mode detection
│   ├── validate.py                     ← validate_pipeline() with V-CYC, GAP checks
│   ├── budget.py                       ← token budget strategies
│   ├── context.py                      ← acyclic context assembly + GAP-2 substitution
│   ├── agent.py                        ← acyclic agent runner with GAP-1/2/3
│   ├── memory.py                       ← five memory files + artifact workspace
│   ├── retrieval.py                    ← two-pass LLM-as-retriever
│   ├── cyclic_agent.py                 ← cyclic agent runner with GAP-1/3
│   ├── cyclic_engine.py                ← event loop, deadlock watchdog, convergence
│   ├── hooks.py                        ← CLAUDE.md generator, hooks installer
│   ├── mailbox.py                      ← filesystem FIFO queue
│   ├── core.py                         ← mode-routing run_pipeline(), gitignore init
│   ├── schema.py                       ← GAP-1 output schema validator (restored)
│   ├── secrets.py                      ← GAP-2 gitignore + scan + substitution (restored)
│   ├── approval.py                     ← GAP-3 approval poll loop (restored)
│   └── hook_scripts/
│       ├── post_tool_event.py
│       ├── agent_stop_event.py
│       └── agent_stop_failure_event.py
├── tests/
│   ├── test_config.py       (rewritten for file-based config)
│   ├── test_schema.py       (new — GAP-1)
│   ├── test_secrets.py      (new — GAP-2)
│   ├── test_approval.py     (new — GAP-3)
│   ├── test_exceptions.py
│   ├── test_dag.py
│   ├── test_validate.py
│   ├── test_mailbox.py
│   ├── test_memory.py
│   ├── test_events.py
│   ├── test_cyclic_output_parser.py
│   └── test_hooks.py
└── pipelines/
    ├── auth_module/           ← cyclic (PM → Arch ↔ Dev → Tester)
    │   ├── pipeline.json
    │   ├── config.json        (new)
    │   └── agents/
    └── research_dag/          ← acyclic DAG (v2.1.0 compatible)
        ├── pipeline.json
        ├── config.json        (new)
        └── agents/
```
