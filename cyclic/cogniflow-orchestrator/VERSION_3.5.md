# Analysis: `cogniflow-orchestrator_v3.5`

v3.5 is v3.0 with two major changes:

1. **GAP-1, GAP-2, GAP-3 restored** as full first-class modules (`schema.py`, `secrets.py`, `approval.py`) and wired into both DAG and cyclic execution paths.
2. **All Cogniflow-specific environment variables removed.** Every runtime knob now lives in a per-pipeline `<pipeline_dir>/config.json`. The only OS env vars still read are `LOCALAPPDATA` / `APPDATA` — Windows platform conventions used to locate `claude.exe` when no path is configured.

`__version__` → `"3.5.0"`. Test suite: 130 pass (70 v3 + 60 new).

## Module inventory changes vs v3.0

| Module | Status | Change |
|---|---|---|
| `orchestrator/config.py` | **rewritten** | Loader: `OrchestratorConfig.from_pipeline_dir(path)`. Dataclass with flat attribute names (`agent_timeout`, `loop_poll_s`, …) preserved so call sites didn't need updates. Nested JSON sections (`claude`, `execution`, `budget`, `output`, `cyclic`, `approval`, `substitutions`). Missing file → defaults. Malformed JSON → `ValueError` with clear message. Unknown keys → silently ignored for forward compat. |
| `orchestrator/schema.py` | **NEW (restored)** | GAP-1. Eight validation modes (json with optional jsonschema, regex, contains, not_contains, min/max_words, starts/ends_with). Accepts either a Path or a raw string — letting the cyclic path validate `response_body` directly without touching disk. `VALID_MODES` constant re-used by `validate.py` to reject unknown modes at pipeline-validation time. |
| `orchestrator/secrets.py` | **NEW (restored)** | GAP-2. Three entry points: `generate_gitignore()` (auto-excludes `.state/`), `scan_for_secrets()` (11 credential regex patterns — AWS, GitHub/GitLab, Anthropic, OpenAI, Bearer, Basic auth, private key headers, DB connection strings), `apply_substitutions(text, substitutions_dict, …)` (replaces `{{VAR_NAME}}` with values from `config.substitutions`). The v3 inline `_SECRET_PATTERNS` list in `agent.py` has been removed. |
| `orchestrator/approval.py` | **NEW (restored)** | GAP-3. `request_approval()` writes `07_approval_request.json` and emits `agent_approval_required`. `wait_for_approval()` polls `07_approval.json` with `approval_poll_interval_s` / `approval_timeout_s` from config; prints a reminder every 60s; raises `ApprovalRejectedError` / `ApprovalTimeoutError`. `write_approval()` is the CLI side (atomic tmp→rename). `get_approval_status()` for the status display. All parameters from config.json. |
| `orchestrator/exceptions.py` | **updated** | Added `SchemaViolationError`, `ApprovalTimeoutError`, `ApprovalRejectedError`. |
| `orchestrator/events.py` | **updated** | Added `agent_schema_valid`, `agent_schema_violation` methods. All existing v3 event methods preserved. |
| `orchestrator/context.py` | **updated** | `assemble_context()` signature adds `config` parameter. Delegates substitution to `secrets.apply_substitutions` using `config.substitutions`. No more `os.environ` lookups. |
| `orchestrator/agent.py` | **rewritten** | DAG-path runner now uses `secrets.scan_for_secrets`, `secrets.apply_substitutions` (for system prompt), `schema.validate_output_schema` (after output validation), and `approval.request_approval` / `wait_for_approval`. The v3 inline helpers `_scan_secrets` and `_wait_for_approval` are gone. Resume guard also re-enters the approval wait loop when status was `awaiting_approval` on restart. |
| `orchestrator/cyclic_agent.py` | **updated** | After `parse_routing_block()` succeeds, validates `response_body` against `output_schema` if declared; schema violation triggers a correction-prompt retry within the same retry budget as malformed-output. After memory persistence, if `requires_approval: true`, blocks in `wait_for_approval` before returning the routing dict to the engine. |
| `orchestrator/core.py` | **updated** | Calls `generate_gitignore(pipeline_dir)` on every run so `.state/` is guaranteed excluded from VCS. |
| `orchestrator/validate.py` | **updated** | Validates `config.json` JSON syntax if present. For each agent's `00_config.json`, rejects unknown `output_schema` modes and non-bool `requires_approval`. Imports `VALID_MODES` from `schema.py`. |
| `orchestrator/__init__.py` | **updated** | Re-exports all GAP APIs and the three new exceptions. `__version__ = "3.5.0"`. |
| `cli.py` | **updated** | `cmd_run` loads config via `OrchestratorConfig.from_pipeline_dir(pipeline_dir)`. `--timeout` / `--claude-bin` CLI flags override config values. `cmd_approve` / `cmd_reject` use `orchestrator.approval.write_approval` and read the approver name from `config.approver`. All `os.environ` lookups removed from the CLI. |
| `setup.py` | **updated** | `version="3.5.0"`. Added `extras_require={"schema": ["jsonschema>=4.0"]}` (optional — the JSON-Schema path has a manual required-field fallback). |
| `.env.example` | **removed** | Env-free system. |

## Module inventory — unchanged from v3.0

`dag.py`, `budget.py`, `memory.py`, `retrieval.py`, `mailbox.py`, `hooks.py`, `event_writer.py`, `cyclic_engine.py`, `hook_scripts/*.py`. No env-var reads anywhere in these modules, so nothing to migrate.

## New files

- `pipelines/research_dag/config.json` — sample DAG pipeline config
- `pipelines/auth_module/config.json` — sample cyclic pipeline config (includes `COGNIFLOW_CLIENT_NAME` substitution exercising GAP-2)
- `tests/test_config.py` — rewritten around `from_pipeline_dir`, no env-based tests
- `tests/test_schema.py` — 24 tests covering all 8 modes + combinations + config discovery
- `tests/test_secrets.py` — 13 tests covering gitignore, scanning, substitution
- `tests/test_approval.py` — 12 tests covering request/wait/write, threaded approve/reject paths, incomplete-JSON handling, timeout
- `VERSION_3.5.md` — this document

## Configuration schema

`config.json` sections:

```json
{
  "claude":        { "binary", "default_model", "summary_model", "retrieval_model" },
  "execution":     { "agent_timeout_s", "max_parallel_agents", "verbose" },
  "budget":        { "model_context_limit", "input_budget_fraction" },
  "output":        { "keep_versions" },
  "cyclic":        { "loop_poll_s", "thread_window", "thread_token_budget",
                     "summary_max_tokens", "index_compression_threshold",
                     "artifact_max_inject_tokens" },
  "approval":      { "approver", "poll_interval_s", "timeout_s" },
  "substitutions": { "VAR_NAME_1": "...", "_warning": "metadata-key-dropped" }
}
```

Keys starting with `_` in `substitutions` are stripped on load (used for inline warnings/comments in the JSON).

## GAP wiring points

### GAP-1 in the DAG path (`agent.py`)
After the subprocess returns successfully and the output file is non-empty, `schema.validate_output_schema()` is called on the output path. On violation: event emitted, status written as `schema_invalid` with the violation list, and re-raised as `AgentExecutionError` so the ThreadPoolExecutor handles it.

### GAP-1 in the cyclic path (`cyclic_agent.py`)
Inside the retry loop, immediately after `parse_routing_block()` returns, `validate_output_schema()` runs on `response_body` (the text before the routing JSON). On violation, if retries remain, a correction prompt is appended to `context_text` listing the specific violations and the loop retries. Retry exhaustion raises `AgentExecutionError`.

### GAP-2 substitution
`secrets.apply_substitutions(text, config.substitutions, agent_id, log)`. Substitution is applied to `02_prompt.md` and each file in `03_inputs/` during `assemble_context()`, and to `01_system.md` before the subprocess invocation. Missing variables emit `secret_substitution_warning` and the placeholder is left intact.

### GAP-2 scanning
`secrets.scan_for_secrets(agent_id, agent_dir, log)` runs in the DAG path before context assembly. Findings become `secret_warning` events; scan is advisory and never blocks. In the cyclic path, scanning is not currently applied (cyclic agents' 01_system.md is read at invocation time but inline scanning there would be noisy per-turn — kept out of the hot path deliberately).

### GAP-3 in the DAG path (`agent.py`)
After schema validation passes, if `requires_approval: true`, the agent writes its status as `awaiting_approval`, calls `request_approval()`, and blocks in `wait_for_approval(config)`. Resume-safe: a next run that finds `awaiting_approval` in `06_status.json` re-enters the wait loop without re-invoking claude.

### GAP-3 in the cyclic path (`cyclic_agent.py`)
After memory persistence (entry body, summary, thread, artifact) but before returning the routing dict to the engine, if `requires_approval: true`, blocks in `wait_for_approval`. The engine is single-threaded so this pauses the whole pipeline cleanly; approve resumes, reject raises `AgentExecutionError` up to the engine.

## Tests

```
60 passed — new tests (test_config, test_schema, test_secrets, test_approval)
70 passed — pre-existing v3 tests (unchanged — no regressions)
━━━━━━━━━━
130 passed in ~6s
```

## Validation

```bash
python cli.py validate pipelines/research_dag
  ✓ pipeline.json is valid — Name: research-article-pipeline, Agents: 3

python cli.py validate pipelines/auth_module
  ✗ [V-CYC-008] Isolated agent with no outbound edges: 'tester'
```

The `auth_module` validation failure is **not a v3.5 regression** — V-CYC-008 was inherited from v3.0 and incorrectly flags terminal agents (agents that only receive, never send) as isolated. Tracked in `todo.txt` as a v3/v3.5 bug to fix. `research_dag` validates cleanly.

## Notable / non-breaking changes

- `context.py::assemble_context()` gained a required `config` parameter. The only caller (DAG path in `agent.py`) was updated.
- `test_context.py` was removed in v3 and not re-added in v3.5 (no context.py tests currently; coverage is via the wider tests).
- `DEFAULT_CONFIG` remains as an all-defaults instance — useful for tests that don't have a pipeline_dir.
- CLI `--timeout` and `--claude-bin` flags still override config.json values.

## Known issues carried from v3.0

Still present in v3.5 (documented in `todo.txt`):

- V-CYC-008 over-flags terminal agents (blocks the `auth_module` sample from validating).
- Hook integration has several bugs (import path failure after copy, wrong field name `tool_output` vs `tool_response`, `StopFailure` is not a real hook event, unquoted command paths).
- `routing_violation` is logged but never escalated; dead `conversation_thread_close` event method; pm agent receives no initial task seed; `on_cycle_limit: force_done` is a no-op.

These are the next items in the work plan — they were explicitly out of scope for the v3.5 refactor ("reintroduce GAPs + move to config.json").
