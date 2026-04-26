# Analysis: `cogniflow-orchestrator_v1.0`

A Python DAG orchestrator that runs Claude CLI calls as multi-agent pipelines. File-based communication; each agent is a directory of markdown/JSON artefacts.

## Structure

```
cli.py                       6 commands: run, status, watch, validate, reset, inspect
setup.py, requirements.txt   networkx + filelock (py >= 3.10)
orchestrator/
  core.py       run_pipeline() -> validate -> compute_layers -> run layers
  dag.py        networkx topological_generations + pure-Python fallback
  agent.py      exec_agent(): resume guard -> inputs -> budget -> context -> claude subprocess -> status -> router
  context.py    collect_inputs() + assemble_context() -> 04_context.md
  budget.py     hard_fail | auto_summarise | select_top_n
  validate.py   exhaustive upfront check (collects ALL problems)
  events.py     thread-safe JSONL event log (filelock)
  config.py     env-driven (CLAUDE_BIN, AGENT_TIMEOUT, MODEL_CONTEXT_LIMIT...)
  exceptions.py typed hierarchy
tests/          test_dag, test_validate, test_context
pipelines/ai_coding_2026/  sample: 3 researchers -> synth -> writer+fact-check -> editor
```

## Execution model

Kahn's algorithm produces layers; same-layer agents run in `ThreadPoolExecutor` (<= `MAX_PARALLEL_AGENTS`). Claude is invoked via `subprocess.run([claude, "--system", sys, "-p", ctx], stdout=versioned_file, timeout=...)`. Each agent writes:

- `01_system.md` — role definition
- `02_prompt.md` — task
- `03_inputs/` — upstream outputs
- `04_context.md` — assembled prompt (what claude reads)
- `05_output.<run>.md` (+ `05_output.md` symlink) — versioned response
- `06_status.json` — lifecycle state (atomic tmp -> rename)

## Observability surface

- Append-only `.state/events.jsonl` — pipeline/layer/agent lifecycle + budget + router events
- Atomic status writes (tmp -> rename), versioned outputs, resume on re-run via checkpoint
- `watch`, `status`, `inspect` CLI commands

## Notable design choices

- IMP-01..08 map to concrete files (stdin/system/timeout/versioning/budget/networkx/router)
- Context passed via `-p` arg, not stdin — noted as a CLI limitation in `agent.py:131-143`
- Symlink fallback to `shutil.copy2` on Windows without dev-mode
- Router (`_evaluate_router`) bypasses agents by writing `STATUS_BYPASSED`; `core.py` filters them out of later layers

## Gaps vs README

- README claims stdin redirect for IMP-01, but `agent.py` passes context via `-p` argv (code comment acknowledges this)
- `requires_approval` config field is declared but unimplemented ("future feature")
- `setup.py` version is `2.0.0` despite the folder name `_v1`
