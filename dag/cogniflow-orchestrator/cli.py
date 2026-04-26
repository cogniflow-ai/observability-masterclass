#!/usr/bin/env python3
"""
Cogniflow Multi-Agent Orchestrator — CLI entry point.

Usage:
  python cli.py run      <pipeline_dir>   [--timeout N] [--verbose]
  python cli.py status   <pipeline_dir>
  python cli.py watch    <pipeline_dir>   [--follow]
  python cli.py validate <pipeline_dir>
  python cli.py reset    <pipeline_dir>   [--agent AGENT_ID]
  python cli.py inspect  <pipeline_dir>   --agent AGENT_ID [--file FILE]
  python cli.py tokens   <pipeline_dir>
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't encode the box-drawing and
# check-mark characters used in the run banner. Reconfigure stdio to utf-8
# so the CLI works without requiring the user to set PYTHONIOENCODING.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def cmd_run(args: argparse.Namespace) -> int:
    from orchestrator import run_pipeline, OrchestratorConfig
    from orchestrator.exceptions import PipelineError

    config = OrchestratorConfig()
    if args.timeout:
        config.agent_timeout = args.timeout
    if args.claude_bin:
        config.claude_bin = args.claude_bin
    if not args.verbose:
        config.verbose = False

    pipeline_dir = Path(args.pipeline_dir).resolve()
    if not pipeline_dir.exists():
        print(f"Error: pipeline directory not found: {pipeline_dir}", file=sys.stderr)
        return 1

    try:
        summary = run_pipeline(pipeline_dir, config)
        print(json.dumps(summary, indent=2))
        return 0 if summary["status"] == "done" else 1
    except PipelineError as e:
        print(f"\nPipeline error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user. State preserved — re-run to resume.", file=sys.stderr)
        return 130


def cmd_status(args: argparse.Namespace) -> int:
    from orchestrator.core import pipeline_status
    pipeline_status(Path(args.pipeline_dir).resolve())
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from orchestrator.core import watch_events
    try:
        watch_events(Path(args.pipeline_dir).resolve(), follow=args.follow)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from orchestrator.validate import validate_pipeline
    from orchestrator.exceptions import PipelineValidationError

    pipeline_dir  = Path(args.pipeline_dir).resolve()
    pipeline_json = pipeline_dir / "pipeline.json"
    agents_base   = pipeline_dir / "agents"

    try:
        data = validate_pipeline(pipeline_json, agents_base)
        print(f"✓ pipeline.json is valid")
        print(f"  Name   : {data.get('name')}")
        print(f"  Agents : {len(data.get('agents', []))}")
        return 0
    except PipelineValidationError as e:
        print(f"✗ Validation failed:\n{e}", file=sys.stderr)
        return 1


def _reset_agent(agent_dir: Path) -> None:
    """Clear one agent's checkpoint and versioned outputs so it re-runs."""
    status_f = agent_dir / "06_status.json"
    if status_f.exists():
        status_f.unlink()
        print(f"Cleared 06_status.json for '{agent_dir.name}'")
    for f in agent_dir.glob("05_output*.md"):
        if f.name != "05_output.md":  # keep symlink, remove versioned
            f.unlink()
            print(f"Removed {agent_dir.name}/{f.name}")


def cmd_reset(args: argparse.Namespace) -> int:
    """Reset all agents or a specific agent so they will re-run."""
    import shutil
    pipeline_dir = Path(args.pipeline_dir).resolve()
    agents_base  = pipeline_dir / "agents"

    if args.agent:
        _reset_agent(agents_base / args.agent)
    else:
        # Full reset: .state/ plus every agent's checkpoint and versioned outputs
        state_dir = pipeline_dir / ".state"
        if state_dir.exists():
            shutil.rmtree(state_dir)
            print(f"Removed .state/")

        if agents_base.exists():
            for agent_dir in sorted(agents_base.iterdir()):
                if agent_dir.is_dir():
                    _reset_agent(agent_dir)

        print("Full reset complete")

    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Print the contents of a specific agent file."""
    pipeline_dir = Path(args.pipeline_dir).resolve()
    agent_dir    = pipeline_dir / "agents" / args.agent

    file_map = {
        "status":  "06_status.json",
        "output":  "05_output.md",
        "context": "04_context.md",
        "prompt":  "02_prompt.md",
        "system":  "01_system.md",
        "config":  "00_config.json",
    }

    filename = file_map.get(args.file, args.file)
    target   = agent_dir / filename

    if not target.exists():
        # Try resolving symlink
        if target.is_symlink():
            target = target.resolve()
        else:
            print(f"File not found: {target}", file=sys.stderr)
            return 1

    content = target.read_text(encoding="utf-8")
    print(content)
    return 0


def cmd_tokens(args: argparse.Namespace) -> int:
    """
    Print a per-agent + total token-usage table for the latest run,
    reading 06_status.json files (the same data emitted as agent_tokens
    events). Answers: 'did this run cost $0.02 or $2.00?'
    """
    pipeline_dir  = Path(args.pipeline_dir).resolve()
    pipeline_json = pipeline_dir / "pipeline.json"
    agents_base   = pipeline_dir / "agents"

    if not pipeline_json.exists():
        print(f"No pipeline.json at {pipeline_json}", file=sys.stderr)
        return 1

    data   = json.loads(pipeline_json.read_text(encoding="utf-8"))
    agents = data.get("agents", [])

    print(f"\n{'AGENT':<28} {'IN':>10} {'OUT':>10} "
          f"{'CACHE_R':>10} {'COST $':>10} {'API ms':>9} MODEL")
    print("─" * 100)

    totals = {"in": 0, "out": 0, "cache": 0, "cost": 0.0, "counted": 0}
    for a in agents:
        aid = a["id"]
        sp  = agents_base / aid / "06_status.json"
        if not sp.exists():
            print(f"{aid:<28} {'-':>10} {'-':>10} {'-':>10} {'-':>10} {'-':>9} (no status)")
            continue
        try:
            doc = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"{aid:<28} (status unreadable)")
            continue

        usage = doc.get("usage")
        if not isinstance(usage, dict):
            status = doc.get("status", "?")
            print(f"{aid:<28} {'-':>10} {'-':>10} {'-':>10} {'-':>10} {'-':>9} ({status}, no usage)")
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
    print(f"{'TOTAL ({} agents)'.format(totals['counted']):<28} "
          f"{totals['in']:>10,} {totals['out']:>10,} {totals['cache']:>10,} "
          f"{totals['cost']:>10.4f}")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cogniflow",
        description="Cogniflow Multi-Agent DAG Orchestrator",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Execute a pipeline")
    p_run.add_argument("pipeline_dir", help="Path to the pipeline directory")
    p_run.add_argument("--timeout",    type=int, default=None,
                       help="Seconds per agent (default: AGENT_TIMEOUT env var or 300)")
    p_run.add_argument("--claude-bin", default=None, dest="claude_bin",
                       help="Path to claude executable (default: auto-detect)")
    p_run.add_argument("--quiet", dest="verbose", action="store_false",
                       help="Suppress per-agent progress output")
    p_run.set_defaults(verbose=True)

    # status
    p_st = sub.add_parser("status", help="Show agent status table")
    p_st.add_argument("pipeline_dir")

    # watch
    p_wt = sub.add_parser("watch", help="Stream events.jsonl")
    p_wt.add_argument("pipeline_dir")
    p_wt.add_argument("--follow", "-f", action="store_true",
                      help="Keep tailing (like tail -f)")

    # validate
    p_va = sub.add_parser("validate", help="Validate pipeline.json without running")
    p_va.add_argument("pipeline_dir")

    # reset
    p_rs = sub.add_parser("reset", help="Clear checkpoints to allow re-run")
    p_rs.add_argument("pipeline_dir")
    p_rs.add_argument("--agent", default=None,
                      help="Reset only this agent ID (default: reset all)")

    # inspect
    p_in = sub.add_parser("inspect", help="Print a specific agent file")
    p_in.add_argument("pipeline_dir")
    p_in.add_argument("--agent", required=True, help="Agent ID")
    p_in.add_argument("--file", default="output",
                      help="File to show: status|output|context|prompt|system|config  (default: output)")

    # tokens
    p_tk = sub.add_parser("tokens", help="Show per-agent + total token usage and cost")
    p_tk.add_argument("pipeline_dir")

    args = parser.parse_args()

    dispatch = {
        "run":      cmd_run,
        "status":   cmd_status,
        "watch":    cmd_watch,
        "validate": cmd_validate,
        "reset":    cmd_reset,
        "inspect":  cmd_inspect,
        "tokens":   cmd_tokens,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
