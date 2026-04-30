#!/usr/bin/env python3
"""
Cogniflow Orchestrator v3.0 — CLI entry point.

Commands (all v2.1.0 commands preserved unchanged):
  run        Run a pipeline (auto/dag/cyclic mode)
  validate   Validate pipeline.json without running
  status     Show agent status table
  watch      Stream events.jsonl
  reset      Clear state to allow re-run
  inspect    Print a specific agent file
  approve    Approve a waiting agent (human-in-the-loop)
  reject     Reject a waiting agent

New in v3.0:
  hooks      Manage Claude CLI hook installation
    install  Generate .claude/settings.json and copy hook scripts

New in v4:
  vault      Manage the secrets vault
    set      Store or update a secret
    list     List every secret's metadata (never values)
    show     Show one secret's metadata (never value)
    usage    Show which pipelines reference a secret
    delete   Remove a secret
    audit    Show audit log (names only, never values)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    # Force UTF-8 on stdout/stderr so the CLI's Unicode glyphs (✓, ✗, ▶,
    # ⏸, 🤖, 🔍, etc.) print correctly on consoles that default to other
    # encodings — notably Windows cp1252. Without this, every print that
    # contains one of those characters raises UnicodeEncodeError.
    # `errors="replace"` guarantees we never crash if the terminal genuinely
    # can't render a glyph (it becomes `?` instead).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Non-TextIOWrapper streams (e.g. redirected via some tools)
            pass

    parser = argparse.ArgumentParser(
        prog="cogniflow",
        description="Cogniflow Multi-Agent Orchestrator v4",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── run ──────────────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Run a pipeline")
    p_run.add_argument("pipeline_dir")
    p_run.add_argument("--timeout", type=int, default=None,
                       help="Per-agent timeout in seconds")
    p_run.add_argument("--claude-bin", default=None, dest="claude_bin")
    p_run.add_argument("--quiet", dest="verbose", action="store_false")
    p_run.add_argument("--mode", choices=["auto", "dag", "cyclic"], default="auto",
                       help="Execution mode: auto (default), dag, or cyclic (REQ-COMPAT-003)")
    p_run.add_argument("--debug", action="store_true",
                       help="Enable verbose debug logging to stderr AND <pipeline_dir>/.state/runlog.log")
    p_run.set_defaults(verbose=True)

    # ── validate ─────────────────────────────────────────────────────────────
    p_va = sub.add_parser("validate", help="Validate pipeline.json")
    p_va.add_argument("pipeline_dir")
    p_va.add_argument("--json", dest="json_out", action="store_true",
                      help="Emit structured JSON for tooling (Configurator)")

    # ── status ───────────────────────────────────────────────────────────────
    p_st = sub.add_parser("status", help="Show agent status table")
    p_st.add_argument("pipeline_dir")

    # ── watch ─────────────────────────────────────────────────────────────────
    p_wt = sub.add_parser("watch", help="Stream events.jsonl")
    p_wt.add_argument("pipeline_dir")
    p_wt.add_argument("--follow", "-f", action="store_true")

    # ── reset ─────────────────────────────────────────────────────────────────
    p_rs = sub.add_parser("reset", help="Clear state for re-run")
    p_rs.add_argument("pipeline_dir")
    p_rs.add_argument("--agent", default=None)

    # ── inspect ───────────────────────────────────────────────────────────────
    p_in = sub.add_parser("inspect", help="Print a specific agent file")
    p_in.add_argument("pipeline_dir")
    p_in.add_argument("--agent", required=True)
    p_in.add_argument("--file", default="output",
                      help="status|output|context|system|prompt|config|summary|index|budget|history|thread (default: output)")

    # ── approve ───────────────────────────────────────────────────────────────
    p_ap = sub.add_parser("approve", help="Approve a waiting agent")
    p_ap.add_argument("pipeline_dir")
    p_ap.add_argument("--agent", required=True)
    p_ap.add_argument("--note", default="")

    # ── reject ────────────────────────────────────────────────────────────────
    p_rj = sub.add_parser("reject", help="Reject a waiting agent")
    p_rj.add_argument("pipeline_dir")
    p_rj.add_argument("--agent", required=True)
    p_rj.add_argument("--note", default="")

    # ── hooks ─────────────────────────────────────────────────────────────────
    p_hooks = sub.add_parser("hooks", help="Manage Claude CLI hook installation")
    hooks_sub = p_hooks.add_subparsers(dest="hooks_command", required=True)
    p_hi = hooks_sub.add_parser("install", help="Install hooks for a pipeline")
    p_hi.add_argument("pipeline_dir")

    # ── tokens ────────────────────────────────────────────────────────────────
    p_tk = sub.add_parser("tokens",
                          help="Show per-agent + total token usage and cost")
    p_tk.add_argument("pipeline_dir")

    # ── vault (v4) ────────────────────────────────────────────────────────────
    p_vault = sub.add_parser("vault", help="Manage the secrets vault")
    vault_sub = p_vault.add_subparsers(dest="vault_command", required=True)

    def _add_db(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default=None,
                       help="Path to the vault SQLite file (defaults to "
                            "./pipelines/secrets.db when ./pipelines exists, "
                            "else ./secrets.db)")

    p_v_set = vault_sub.add_parser("set", help="Store or update a secret")
    _add_db(p_v_set)
    p_v_set.add_argument("--name", required=True)
    p_v_set.add_argument("--description", default="")
    p_v_set.add_argument("--tags", default="",
                         help="Comma-separated tags, e.g. 'auth,prod'")
    p_v_set.add_argument("--pipeline", default=None,
                         help="Record this pipeline as origin_pipeline")
    p_v_set.add_argument("--from-stdin", action="store_true",
                         help="Read the value from stdin instead of prompting")
    p_v_set.add_argument("--value", default=None,
                         help="(UNSAFE) pass the value on the command line — "
                              "avoid in shared shells")

    p_v_list = vault_sub.add_parser("list", help="List every secret's metadata")
    _add_db(p_v_list)

    p_v_show = vault_sub.add_parser("show", help="Show one secret's metadata")
    _add_db(p_v_show)
    p_v_show.add_argument("--name", required=True)

    p_v_usage = vault_sub.add_parser("usage",
                                     help="Pipelines referencing this secret")
    _add_db(p_v_usage)
    p_v_usage.add_argument("--name", required=True)

    p_v_del = vault_sub.add_parser("delete", help="Remove a secret")
    _add_db(p_v_del)
    p_v_del.add_argument("--name", required=True)
    p_v_del.add_argument("--yes", action="store_true",
                         help="Skip confirmation prompt")

    p_v_audit = vault_sub.add_parser("audit", help="Read the audit log")
    _add_db(p_v_audit)
    p_v_audit.add_argument("--run",      dest="run_id",        default=None)
    p_v_audit.add_argument("--pipeline", dest="pipeline_name", default=None)
    p_v_audit.add_argument("--since",    default=None,
                           help="ISO-8601 timestamp lower bound")
    p_v_audit.add_argument("--limit",    type=int, default=200)

    p_v_check = vault_sub.add_parser(
        "check",
        help="Scan a pipeline's prompts for <<secret:...>> markers and "
             "report which ones are missing from the vault (pre-flight)",
    )
    _add_db(p_v_check)
    p_v_check.add_argument("pipeline_dir")
    p_v_check.add_argument("--json", dest="json_out", action="store_true",
                           help="Emit structured JSON for tooling")

    args = parser.parse_args()

    dispatch = {
        "run":      cmd_run,
        "validate": cmd_validate,
        "status":   cmd_status,
        "watch":    cmd_watch,
        "reset":    cmd_reset,
        "inspect":  cmd_inspect,
        "approve":  cmd_approve,
        "reject":   cmd_reject,
        "hooks":    cmd_hooks,
        "tokens":   cmd_tokens,
        "vault":    cmd_vault,
    }
    return dispatch[args.command](args)


# ── Command implementations ───────────────────────────────────────────────────

def cmd_run(args: argparse.Namespace) -> int:
    from orchestrator.config import OrchestratorConfig
    from orchestrator.core import run_pipeline
    from orchestrator.events import EventLog
    from orchestrator.exceptions import PipelineError, PipelineValidationError

    pipeline_dir = Path(args.pipeline_dir)
    try:
        config = OrchestratorConfig.from_pipeline_dir(pipeline_dir)
    except ValueError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1

    # CLI flags override config.json values
    if args.timeout:
        config.agent_timeout = args.timeout
    if args.claude_bin:
        config.claude_bin = args.claude_bin
    if args.debug:
        config.debug_enabled = True
    config.verbose = args.verbose

    try:
        summary = run_pipeline(pipeline_dir, config, mode=args.mode)
        return 0 if summary.get("status") == "done" else 1
    except PipelineValidationError as exc:
        # v4: emit a structured event so the Observer's pre-run banner has
        # a replayable record. Best-effort — don't mask the original error
        # if we can't write events.jsonl for any reason.
        try:
            state_dir = pipeline_dir / ".state"
            state_dir.mkdir(parents=True, exist_ok=True)
            EventLog(state_dir / "events.jsonl").pipeline_validation_error(
                run_id="", errors=list(exc.errors),
            )
        except Exception:
            pass
        print("\n✗ Pipeline validation failed:", file=sys.stderr)
        for err in exc.errors:
            print(f"  • {err}", file=sys.stderr)
        return 1
    except PipelineError as exc:
        print(f"\n✗ Pipeline failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n⚠  Interrupted. State preserved — re-run to resume.",
              file=sys.stderr)
        return 130


def cmd_validate(args: argparse.Namespace) -> int:
    from orchestrator.validate import validate_pipeline
    from orchestrator.exceptions import PipelineValidationError

    pipeline_dir = Path(args.pipeline_dir)
    json_out = getattr(args, "json_out", False)
    try:
        spec = validate_pipeline(pipeline_dir)
        edges  = spec.get("edges", [])
        cyclic = any(e.get("type") in ("feedback", "peer") for e in edges)
        if json_out:
            payload = {
                "status":   "valid",
                "name":     spec.get("name"),
                "agents":   len(spec.get("agents", [])),
                "edges":    len(edges),
                "cyclic":   cyclic,
                "warnings": [],
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f"✓ pipeline.json is valid")
            print(f"  Name  : {spec.get('name', '(unnamed)')}")
            print(f"  Agents: {len(spec.get('agents', []))}")
            if edges:
                cyclic_n = sum(1 for e in edges
                               if e.get("type") in ("feedback", "peer"))
                print(f"  Edges : {len(edges)} total ({cyclic_n} cyclic)")
        return 0
    except PipelineValidationError as exc:
        if json_out:
            payload = {"status": "invalid", "errors": list(exc.errors)}
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print("✗ Validation failed:", file=sys.stderr)
            for err in exc.errors:
                print(f"  • {err}", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    from orchestrator.core import pipeline_status

    statuses = pipeline_status(Path(args.pipeline_dir))
    if not statuses:
        print("No agent state found.  Has the pipeline been run?")
        return 0

    col_w = 24
    print(f"\n{'AGENT':<{col_w}} {'STATUS':<14} {'INV':>4}  THREAD")
    print("─" * 70)
    for s in statuses:
        status  = s.get("status", "?")
        inv     = s.get("invocation_n") or s.get("invocation_count", "")
        thread  = s.get("current_thread_id", "")
        waiting = s.get("waiting_for")
        suffix  = f"  (waiting for: {', '.join(waiting)})" if waiting else ""
        print(f"{s.get('agent_id', s.get('agent','?')):<{col_w}} "
              f"{status:<14} {str(inv):>4}  {thread}{suffix}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from orchestrator.core import watch_events
    watch_events(Path(args.pipeline_dir), follow=args.follow)
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    import shutil

    from orchestrator.core import resolve_agent_dir

    pipeline_dir = Path(args.pipeline_dir)
    state_dir    = pipeline_dir / ".state"

    def _reset_agent_dir(agent_dir: Path, agent_id: str) -> None:
        """Clear the v1-style per-agent checkpoint + versioned outputs."""
        status_f = agent_dir / "06_status.json"
        if status_f.exists():
            status_f.unlink()
            print(f"  ✓ Cleared 06_status.json for '{agent_id}'")
        for f in agent_dir.glob("05_output.v*.md"):
            f.unlink()
            print(f"  ✓ Removed {agent_id}/{f.name}")
        legacy_output = agent_dir / "05_output.md"
        if legacy_output.exists():
            legacy_output.unlink()
            print(f"  ✓ Removed {agent_id}/05_output.md")
        usage_f = agent_dir / "05_usage.json"
        if usage_f.exists():
            usage_f.unlink()

    pj_path = pipeline_dir / "pipeline.json"
    agents: list[dict] = []
    if pj_path.exists():
        try:
            agents = json.loads(pj_path.read_text(encoding="utf-8")).get("agents", [])
        except Exception:
            agents = []

    if args.agent:
        # Single-agent reset covers both layouts.
        for target in (
            state_dir / "agents"  / args.agent,
            state_dir / "mailbox" / args.agent,
        ):
            if target.exists():
                shutil.rmtree(target)
                print(f"  ✓ Reset agent state: {args.agent}")
        for a in agents:
            if a.get("id") == args.agent:
                _reset_agent_dir(resolve_agent_dir(pipeline_dir, a), args.agent)
                break
    else:
        if state_dir.exists():
            shutil.rmtree(state_dir)
            print(f"  ✓ Reset all state in {state_dir}")
        # v1-style pipelines also keep status/output next to each agent.
        for a in agents:
            aid = a.get("id")
            if not aid:
                continue
            _reset_agent_dir(resolve_agent_dir(pipeline_dir, a), aid)

    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    pipeline_dir = Path(args.pipeline_dir)
    spec = json.loads((pipeline_dir / "pipeline.json").read_text(encoding="utf-8"))
    agent_spec = next((a for a in spec["agents"] if a["id"] == args.agent), None)
    if not agent_spec:
        print(f"Agent '{args.agent}' not found in pipeline.json", file=sys.stderr)
        return 1

    from orchestrator.core import resolve_agent_dir
    agent_dir = resolve_agent_dir(pipeline_dir, agent_spec)
    state_dir = pipeline_dir / ".state" / "agents" / args.agent

    file_map = {
        "output":   agent_dir / "05_output.md",
        "context":  agent_dir / "04_context.md",
        "system":   agent_dir / "01_system.md",
        "prompt":   agent_dir / "02_prompt.md",
        "config":   agent_dir / "00_config.json",
        "status":   agent_dir / "06_status.json",
        "summary":  state_dir / "structured_summary.json",
        "index":    state_dir / "context_index.json",
        "budget":   state_dir / "08_token_budget.json",
        "history":  state_dir / "full_context.md",
        "thread":   state_dir / "recent_thread.md",
    }
    target = file_map.get(args.file)
    if not target:
        print(f"Unknown file type '{args.file}'. "
              f"Options: {', '.join(file_map)}", file=sys.stderr)
        return 1
    if not target.exists():
        print(f"File not found: {target}", file=sys.stderr)
        return 1
    print(target.read_text(encoding="utf-8"))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _write_approval(args, approved=True)


def cmd_reject(args: argparse.Namespace) -> int:
    return _write_approval(args, approved=False)


def _write_approval(args: argparse.Namespace, approved: bool) -> int:
    from orchestrator.approval import write_approval
    from orchestrator.config import OrchestratorConfig

    pipeline_dir = Path(args.pipeline_dir)
    try:
        config = OrchestratorConfig.from_pipeline_dir(pipeline_dir)
    except ValueError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1

    spec = json.loads((pipeline_dir / "pipeline.json").read_text(encoding="utf-8"))
    agent_spec = next((a for a in spec["agents"] if a["id"] == args.agent), None)
    if not agent_spec:
        print(f"Agent '{args.agent}' not found", file=sys.stderr)
        return 1

    from orchestrator.core import resolve_agent_dir
    agent_dir = resolve_agent_dir(pipeline_dir, agent_spec)
    write_approval(
        agent_dir=agent_dir,
        agent_id=args.agent,
        approver=config.approver,
        approved=approved,
        note=args.note,
    )
    verb = "approved" if approved else "rejected"
    print(f"  ✓ {verb} agent '{args.agent}' by {config.approver}")
    return 0


def cmd_hooks(args: argparse.Namespace) -> int:
    if args.hooks_command == "install":
        from orchestrator.hooks import install_hooks
        pipeline_dir = Path(args.pipeline_dir)
        if not (pipeline_dir / "pipeline.json").exists():
            print(f"No pipeline.json found in {pipeline_dir}", file=sys.stderr)
            return 1
        install_hooks(pipeline_dir)
        return 0
    return 0


def cmd_tokens(args: argparse.Namespace) -> int:
    """
    Per-agent + total token-usage table for the latest run, reading
    each agent's 06_status.json ``usage`` block (the same data emitted
    as agent_tokens events). Answers: "did this run cost $0.02 or $2.00?"
    """
    pipeline_dir  = Path(args.pipeline_dir)
    pipeline_json = pipeline_dir / "pipeline.json"
    if not pipeline_json.exists():
        print(f"No pipeline.json at {pipeline_json}", file=sys.stderr)
        return 1

    data   = json.loads(pipeline_json.read_text(encoding="utf-8"))
    agents = data.get("agents", [])

    print(f"\n{'AGENT':<28} {'IN':>10} {'OUT':>10} "
          f"{'CACHE_R':>10} {'COST $':>10} {'API ms':>9} MODEL")
    print("─" * 100)

    from orchestrator.core import resolve_agent_dir

    totals = {"in": 0, "out": 0, "cache": 0, "cost": 0.0, "counted": 0}
    for a in agents:
        aid  = a["id"]
        adir = resolve_agent_dir(pipeline_dir, a)
        sp   = adir / "06_status.json"
        if not sp.exists():
            print(f"{aid:<28} {'-':>10} {'-':>10} {'-':>10} "
                  f"{'-':>10} {'-':>9} (no status)")
            continue
        try:
            doc = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"{aid:<28} (status unreadable)")
            continue

        usage = doc.get("usage")
        if not isinstance(usage, dict):
            status = doc.get("status", "?")
            print(f"{aid:<28} {'-':>10} {'-':>10} {'-':>10} "
                  f"{'-':>10} {'-':>9} ({status}, no usage)")
            continue

        ti  = int(usage.get("input_tokens", 0))
        to  = int(usage.get("output_tokens", 0))
        cr  = int(usage.get("cache_read_tokens", 0))
        co  = float(usage.get("cost_usd", 0.0))
        ams = int(usage.get("duration_api_ms", 0))
        mdl = str(usage.get("model", ""))[:24]

        totals["in"]      += ti
        totals["out"]     += to
        totals["cache"]   += cr
        totals["cost"]    += co
        totals["counted"] += 1

        print(f"{aid:<28} {ti:>10,} {to:>10,} {cr:>10,} "
              f"{co:>10.4f} {ams:>9,} {mdl}")

    print("─" * 100)
    label = f"TOTAL ({totals['counted']} agents)"
    print(f"{label:<28} {totals['in']:>10,} {totals['out']:>10,} "
          f"{totals['cache']:>10,} {totals['cost']:>10.4f}")
    print()
    return 0


# ── v4: vault subcommand ──────────────────────────────────────────────────────

def _resolve_cli_vault_path(args_db: str | None) -> Path:
    if args_db:
        return Path(args_db)
    cwd = Path.cwd()
    if (cwd / "pipelines").exists():
        return cwd / "pipelines" / "secrets.db"
    return cwd / "secrets.db"


def _read_value_for_set(args: argparse.Namespace) -> str:
    """Collect the secret value with echo disabled where possible."""
    if args.value is not None:
        return args.value
    if args.from_stdin:
        return sys.stdin.read().rstrip("\n")
    import getpass
    value = getpass.getpass(prompt=f"Secret value for '{args.name}' (hidden): ")
    if not value:
        raise ValueError("Secret value must be non-empty")
    confirm = getpass.getpass(prompt="Confirm value: ")
    if value != confirm:
        raise ValueError("Values did not match")
    return value


def cmd_vault(args: argparse.Namespace) -> int:
    from orchestrator.vault import Vault

    db_path = _resolve_cli_vault_path(args.db)
    try:
        vault = Vault(db_path)
    except Exception as exc:
        print(f"✗ Could not open vault at {db_path}: {exc}", file=sys.stderr)
        return 1

    sub = args.vault_command

    if sub == "set":
        try:
            value = _read_value_for_set(args)
        except Exception as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        try:
            vault.put(
                name=args.name, value=value,
                description=args.description,
                tags=tags,
                pipeline=args.pipeline,
            )
        except ValueError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        print(f"  ✓ set '{args.name}' (db: {db_path})")
        return 0

    if sub == "list":
        rows = vault.list()
        if not rows:
            print("No secrets in vault.")
            return 0
        print(f"\n{'NAME':<32} {'DESCRIPTION':<36} {'TAGS':<18} "
              f"{'ORIGIN':<20} UPDATED")
        print("─" * 130)
        for r in rows:
            tags_str = ",".join(r.get("tags") or []) or "-"
            desc = (r.get("description") or "")[:34]
            origin = (r.get("origin_pipeline") or "-")[:18]
            print(f"{r['name']:<32} {desc:<36} {tags_str:<18} "
                  f"{origin:<20} {r['updated_at']}")
        print()
        return 0

    if sub == "show":
        meta = vault.get_metadata(args.name)
        if meta is None:
            print(f"✗ No secret named '{args.name}'", file=sys.stderr)
            return 1
        print(f"\n  name        : {meta['name']}")
        print(f"  description : {meta.get('description','')}")
        print(f"  tags        : {','.join(meta.get('tags') or []) or '-'}")
        print(f"  origin      : {meta.get('origin_pipeline','') or '-'}")
        print(f"  created_at  : {meta['created_at']}")
        print(f"  updated_at  : {meta['updated_at']}\n")
        return 0

    if sub == "usage":
        rows = vault.usage(args.name)
        if not rows:
            print(f"No pipeline usage recorded for '{args.name}'.")
            return 0
        print(f"\n{'PIPELINE':<32} {'FIRST_USED':<22} LAST_USED")
        print("─" * 80)
        for r in rows:
            print(f"{r['pipeline_name']:<32} {r['first_used_at']:<22} "
                  f"{r['last_used_at']}")
        print()
        return 0

    if sub == "delete":
        if not args.yes:
            try:
                confirm = input(
                    f"Delete secret '{args.name}'? [y/N]: "
                ).strip().lower()
            except EOFError:
                confirm = ""
            if confirm not in ("y", "yes"):
                print("Aborted.")
                return 1
        ok = vault.delete(args.name)
        if ok:
            print(f"  ✓ deleted '{args.name}'")
            return 0
        print(f"✗ No secret named '{args.name}'", file=sys.stderr)
        return 1

    if sub == "audit":
        rows = vault.audit(
            run_id=args.run_id, pipeline_name=args.pipeline_name,
            since=args.since, limit=args.limit,
        )
        if not rows:
            print("No audit rows for the given filters.")
            return 0
        print(f"\n{'TS':<22} {'DIR':<10} {'NAME':<24} {'FILE':<20} "
              f"{'AGENT':<20} {'PIPELINE':<20} RUN_ID")
        print("─" * 140)
        for r in rows:
            print(f"{r['ts']:<22} {r['direction']:<10} "
                  f"{r['secret_name']:<24} {r.get('file',''):<20} "
                  f"{r.get('agent_id',''):<20} "
                  f"{r.get('pipeline_name',''):<20} "
                  f"{r.get('run_id','')}")
        print()
        return 0

    if sub == "check":
        from orchestrator.vault import scan_pipeline_for_markers

        pipeline_dir = Path(args.pipeline_dir)
        try:
            refs = scan_pipeline_for_markers(pipeline_dir)
        except FileNotFoundError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        except json.JSONDecodeError as exc:
            print(f"✗ pipeline.json is not valid JSON: {exc}", file=sys.stderr)
            return 1

        present: dict[str, list[dict]] = {}
        missing: dict[str, list[dict]] = {}
        for name, where in refs.items():
            if vault.get_metadata(name) is not None:
                present[name] = where
            else:
                missing[name] = where

        json_out = getattr(args, "json_out", False)
        if json_out:
            print(json.dumps({
                "status":  "ok" if not missing else "missing",
                "total":   len(refs),
                "present": {n: present[n] for n in sorted(present)},
                "missing": {n: missing[n] for n in sorted(missing)},
            }, indent=2, ensure_ascii=False))
        else:
            print(f"\n  {len(refs)} unique secret reference(s) across "
                  f"{sum(len(v) for v in refs.values())} site(s)")
            print(f"  {len(present)} resolved · {len(missing)} missing\n")
            if missing:
                print("  MISSING from vault:")
                for name in sorted(missing):
                    sites = ", ".join(
                        f"{w['agent']}/{w['file']}" for w in missing[name]
                    )
                    print(f"    • <<secret:{name}>>  ({sites})")
                print()
                print("  Fix: python cli.py vault set --name <NAME>")
                print()
        return 0 if not missing else 1

    print(f"Unknown vault subcommand: {sub}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
