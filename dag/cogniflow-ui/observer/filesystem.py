"""
Cogniflow Observer — filesystem reading layer.
All data comes from the filesystem. No database. No orchestrator imports.
"""
from __future__ import annotations
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import settings

try:
    import markdown as md_lib
    _HAS_MD = True
except ImportError:
    _HAS_MD = False

# ── Status display map ─────────────────────────────────────────────────────

STATUS_DISPLAY = {
    "pending":            ("Waiting to start",          "grey"),
    "running":            ("Running",                   "blue"),
    "done":               ("Complete",                  "green"),
    "failed":             ("Failed",                    "red"),
    "timeout":            ("Timed out",                 "orange"),
    "schema_invalid":     ("Output rejected",           "red"),
    "awaiting_approval":  ("Waiting for your approval", "purple"),
    "bypassed":           ("Skipped",                   "grey"),
    "rejected":           ("Rejected — will retry",     "orange"),
    "approval_timeout":   ("Approval timed out",        "orange"),
    "cancelled":          ("Cancelled",                 "orange"),
}

TERMINAL_STATUSES = {"done", "failed", "timeout", "bypassed", "schema_invalid", "cancelled"}


def status_display(raw: str) -> dict:
    label, colour = STATUS_DISPLAY.get(raw, (raw, "grey"))
    return {"raw": raw, "label": label, "colour": colour}


# ── Time helpers ───────────────────────────────────────────────────────────

def relative_time(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        diff = datetime.now(timezone.utc) - dt
        secs = int(diff.total_seconds())
        if secs < 5:   return "just now"
        if secs < 60:  return f"{secs}s ago"
        if secs < 3600: return f"{secs // 60}m ago"
        return f"{secs // 3600}h ago"
    except Exception:
        return iso[:16] if iso else "—"


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60:02d}s"


def live_duration(started_at: Optional[str]) -> str:
    if not started_at:
        return "—"
    try:
        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        return format_duration(float(secs))
    except Exception:
        return "—"


def format_bytes(n: Optional[int]) -> str:
    if n is None or n == 0:
        return "—"
    if n < 1024:
        return f"{n} B"
    return f"{n / 1024:.1f} KB"


def format_cost(usd: Optional[float]) -> str:
    if usd is None or usd <= 0:
        return "—"
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:.2f}"


# ── Pipeline discovery ─────────────────────────────────────────────────────

def is_pipeline_dir(path: Path) -> bool:
    return path.is_dir() and (path / "pipeline.json").exists()


def read_pipeline_json(pipeline_dir: Path) -> dict:
    try:
        return json.loads((pipeline_dir / "pipeline.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def pipeline_name(pipeline_dir: Path) -> str:
    data = read_pipeline_json(pipeline_dir)
    return data.get("name", pipeline_dir.name)


# ── Per-agent status reading ───────────────────────────────────────────────

def read_agent_status(agent_dir: Path) -> dict:
    status_file = agent_dir / "06_status.json"
    if status_file.exists():
        try:
            return json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"status": "pending"}


def agent_output_bytes(agent_dir: Path) -> Optional[int]:
    """Size of 05_output.md — reads the symlink target, not the symlink itself."""
    p = agent_dir / "05_output.md"
    if p.exists():
        try:
            return p.resolve().stat().st_size if p.is_symlink() else p.stat().st_size
        except OSError:
            pass
    return None


def agent_token_info(pipeline_dir: Path, agent_id: str) -> Optional[dict]:
    """
    Read tokens_estimated and context_bytes from the most recent
    agent_context_ready event in events.jsonl.
    """
    events_file = pipeline_dir / ".state" / "events.jsonl"
    if not events_file.exists():
        return None
    result = None
    try:
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                if ev.get("event") == "agent_context_ready" and ev.get("agent") == agent_id:
                    result = {
                        "tokens": ev.get("tokens_estimated", 0),
                        "bytes":  ev.get("context_bytes", 0),
                    }
            except Exception:
                pass
    except OSError:
        pass
    return result


# ── Agent card data ────────────────────────────────────────────────────────

def agent_dir_for(pipeline_dir: Path, agent_id: str) -> Path:
    """Per-agent directory — all agent files live here (prompt, context, output, status)."""
    return pipeline_dir / "agents" / agent_id


def get_agent_card(pipeline_dir: Path, agent_def: dict,
                   context_limit: int = 180000) -> dict:
    aid       = agent_def["id"]
    agent_dir = agent_dir_for(pipeline_dir, aid)
    raw_status = read_agent_status(agent_dir)
    status     = raw_status.get("status", "pending")
    disp       = status_display(status)

    # Duration
    duration_s  = raw_status.get("duration_s")
    started_at  = raw_status.get("started_at")
    if status == "running":
        duration_str = live_duration(started_at)
    elif duration_s is not None:
        duration_str = format_duration(float(duration_s))
    else:
        duration_str = "—"

    # Output KB
    out_bytes = agent_output_bytes(agent_dir)
    out_str   = format_bytes(out_bytes)

    # Token fill
    tok_info    = agent_token_info(pipeline_dir, aid)
    token_count = tok_info["tokens"] if tok_info else None
    fill_pct    = 0
    fill_colour = "green"
    if token_count and context_limit:
        fill_pct = min(100, int(token_count / context_limit * 100))
        fill_colour = "green" if fill_pct < 60 else ("amber" if fill_pct < 85 else "red")

    # Approval files
    approval_requested = (agent_dir / "07_approval_request.json").exists()
    approval_done      = (agent_dir / "07_approval.json").exists()

    # Cost (from 06_status.json.usage.cost_usd)
    cost_usd = None
    try:
        cost_usd = float(raw_status.get("usage", {}).get("cost_usd"))
    except (TypeError, ValueError):
        cost_usd = None

    return {
        "id":                 aid,
        "description":        agent_def.get("description", ""),
        "status":             status,
        "status_label":       disp["label"],
        "status_colour":      disp["colour"],
        "duration":           duration_str,
        "output_bytes":       out_str,
        "cost_usd":           cost_usd,
        "cost":               format_cost(cost_usd),
        "token_count":        f"{token_count:,}" if token_count else None,
        "token_fill_pct":     fill_pct,
        "token_fill_colour":  fill_colour,
        "show_token_bar":     tok_info is not None,
        "depends_on":         agent_def.get("depends_on", []),
        "needs_approval":     status == "awaiting_approval",
        "approval_requested": approval_requested,
        "approval_done":      approval_done,
        "started_at":         relative_time(started_at),
        "raw_status":         raw_status,
    }


def get_all_agent_cards(pipeline_dir: Path, context_limit: int = 180000) -> list[dict]:
    data   = read_pipeline_json(pipeline_dir)
    agents = data.get("agents", [])
    return [get_agent_card(pipeline_dir, a, context_limit) for a in agents]


# ── Pipeline summary ───────────────────────────────────────────────────────

def pipeline_summary(cards: list[dict], pause_state_str: str = "") -> dict:
    total    = len(cards)
    done     = sum(1 for c in cards if c["status"] in TERMINAL_STATUSES)
    running  = sum(1 for c in cards if c["status"] == "running")
    failed   = sum(1 for c in cards if c["status"] in {"failed", "timeout", "schema_invalid"})
    waiting  = sum(1 for c in cards if c["status"] == "awaiting_approval")
    all_done = total > 0 and done == total
    any_approval = waiting > 0

    # Pipeline-level state drives the Start/Pause/Stop buttons.
    if total == 0:
        state = "idle"
    elif all_done:
        state = "complete"
    elif running > 0 or waiting > 0 or done > 0 or failed > 0:
        state = "running"
    else:
        state = "idle"

    # Pause overrides running — but never masks complete/idle, since those
    # mean nothing is actually executing.
    if pause_state_str in ("pausing", "paused") and state == "running":
        state = pause_state_str

    return {
        "total": total, "done": done, "running": running,
        "failed": failed, "waiting_approval": waiting,
        "all_done": all_done, "any_approval": any_approval,
        "pct": int(done / total * 100) if total else 0,
        "state": state,
        "pause_state": pause_state_str,
    }


# ── Event stream ───────────────────────────────────────────────────────────

def get_events(pipeline_dir: Path, n: int = 50) -> list[dict]:
    events_file = pipeline_dir / ".state" / "events.jsonl"
    if not events_file.exists():
        return []
    lines = []
    try:
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    ev = json.loads(line)
                    ev["_ts_rel"] = relative_time(ev.get("ts"))
                    ev["_ts_abs"] = ev.get("ts", "")
                    lines.append(ev)
                except Exception:
                    pass
    except OSError:
        pass
    return lines[-n:]


# ── Agent file reading ─────────────────────────────────────────────────────

def read_agent_file(pipeline_dir: Path, agent_id: str, slot: str) -> str:
    """
    slot: output | context | prompt | system | status
    """
    slot_map = {
        "output":  "05_output.md",
        "context": "04_context.md",
        "prompt":  "02_prompt.md",
        "system":  "01_system.md",
        "status":  "06_status.json",
    }
    fname    = slot_map.get(slot, slot)
    base_dir = agent_dir_for(pipeline_dir, agent_id)
    p        = base_dir / fname
    if not p.exists():
        return f"(file not found: {fname})"
    try:
        if p.is_symlink():
            p = p.resolve()
        return p.read_text(encoding="utf-8")
    except OSError as e:
        return f"(error reading file: {e})"


# ── Tagline validation (system / task prompts) ────────────────────────────

_TAGLINE_RE = re.compile(r"<(/?)([A-Za-z_][\w-]*)>")


def validate_prompt_taglines(text: str, required: list[str]) -> Optional[str]:
    """Check XML-like taglines in a prompt file.

    Rules enforced:
      1. Every name listed in `required` must appear as both <name> and </name>.
      2. Every tagline found in the text must have a matching partner — an
         open <x> with no </x>, or a </x> with no <x>, is rejected.
      3. Taglines cannot be nested: pairs must be strictly sequential.

    Only the no-space form <name> / </name> is considered — anything with
    attributes or whitespace is ignored, matching the editor's highlighter.

    Returns None on success, or a human-readable error message.
    """
    matches = list(_TAGLINE_RE.finditer(text))

    # (1) Required taglines must be present as both open and close.
    open_names  = {m.group(2) for m in matches if not m.group(1)}
    close_names = {m.group(2) for m in matches if     m.group(1)}
    missing: list[str] = []
    for name in required:
        if name not in open_names:
            missing.append(f"<{name}>")
        if name not in close_names:
            missing.append(f"</{name}>")
    if missing:
        return "Missing required tagline(s): " + ", ".join(missing)

    # (2) + (3) Walk the tag stream; enforce pairing and no nesting.
    stack: list[tuple[str, int]] = []
    for m in matches:
        slash, name = m.group(1), m.group(2)
        line = text.count("\n", 0, m.start()) + 1
        if not slash:
            if stack:
                prev_name, prev_line = stack[-1]
                return (f"Nested tagline on line {line}: <{name}> opened while "
                        f"<{prev_name}> (line {prev_line}) is still open. "
                        f"Taglines cannot be nested.")
            stack.append((name, line))
        else:
            if not stack:
                return (f"Closing tagline </{name}> on line {line} has no "
                        f"matching opening <{name}>.")
            top_name, top_line = stack[-1]
            if top_name != name:
                return (f"Mismatched tagline on line {line}: found </{name}> "
                        f"but <{top_name}> (line {top_line}) is still open.")
            stack.pop()

    if stack:
        name, line = stack[-1]
        return f"Unclosed tagline: <{name}> opened on line {line} is never closed."

    return None


def render_markdown(text: str) -> str:
    if _HAS_MD:
        return md_lib.markdown(text, extensions=["extra", "nl2br"])
    # Minimal fallback: escape HTML, preserve line breaks
    import html as html_mod
    escaped = html_mod.escape(text)
    return f"<pre>{escaped}</pre>"


# ── Pipeline list ──────────────────────────────────────────────────────────

def _run_history(pipeline_dir: Path) -> list[str]:
    """Return up to 3 most recent run outcomes as '✓' or '✗'."""
    events_file = pipeline_dir / ".state" / "events.jsonl"
    if not events_file.exists():
        return []
    outcomes = []
    try:
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                if ev.get("event") == "pipeline_done":
                    outcomes.append("✓")
                elif ev.get("event") == "pipeline_error":
                    outcomes.append("✗")
            except Exception:
                pass
    except OSError:
        pass
    return outcomes[-3:]


def _pipeline_aggregate_status(pipeline_dir: Path) -> tuple[str, str]:
    """Return (status_label, css_class) for the pipeline selector."""
    agents_dir = pipeline_dir / "agents"
    if not agents_dir.exists():
        return "Idle", "idle"
    statuses = set()
    for agent_dir in agents_dir.iterdir():
        if agent_dir.is_dir():
            s = read_agent_status(agent_dir).get("status", "pending")
            statuses.add(s)
    if not statuses:
        return "Idle", "idle"
    if "running" in statuses:
        return "Running", "running"
    if "awaiting_approval" in statuses:
        return "Waiting for approval", "approval"
    if "failed" in statuses or "timeout" in statuses:
        return "Failed", "failed"
    if all(s in TERMINAL_STATUSES for s in statuses):
        return "Complete", "done"
    return "Idle", "idle"


def _last_run_time(pipeline_dir: Path) -> str:
    events_file = pipeline_dir / ".state" / "events.jsonl"
    if not events_file.exists():
        return "Never"
    last_ts = None
    try:
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if "pipeline_done" in line or "pipeline_error" in line or "pipeline_start" in line:
                try:
                    ev = json.loads(line)
                    if ev.get("event") in ("pipeline_done", "pipeline_error", "pipeline_start"):
                        last_ts = ev.get("ts")
                except Exception:
                    pass
    except OSError:
        pass
    return relative_time(last_ts) if last_ts else "Never"


def list_pipelines(root: Path) -> list[dict]:
    pipelines = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not is_pipeline_dir(entry):
            continue
        status_label, status_class = _pipeline_aggregate_status(entry)
        pipelines.append({
            "dir":          entry.name,
            "name":         pipeline_name(entry),
            "status_label": status_label,
            "status_class": status_class,
            "last_run":     _last_run_time(entry),
            "history":      _run_history(entry),
        })
    return pipelines


# ── Approval writing ───────────────────────────────────────────────────────

def write_approval(pipeline_dir: Path, agent_id: str,
                   approve: bool, note: str = "") -> bool:
    agent_dir   = agent_dir_for(pipeline_dir, agent_id)
    approval_f  = agent_dir / "07_approval.json"
    status_f    = agent_dir / "06_status.json"
    approver    = settings.approver
    now         = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    decision    = "approved" if approve else "rejected"
    data        = {
        "agent_id":    agent_id,
        "status":      decision,
        "approved_by": approver,
        "note":        note,
        "decided_at":  now,
    }
    try:
        tmp = approval_f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(approval_f)
        # Also update 06_status.json
        if status_f.exists():
            try:
                st = json.loads(status_f.read_text(encoding="utf-8"))
                st["status"]      = "done" if approve else "pending"
                st["approved_by"] = approver
                st["decided_at"]  = now
                if note:
                    st["approval_note"] = note
                tmp2 = status_f.with_suffix(".tmp")
                tmp2.write_text(json.dumps(st, indent=2), encoding="utf-8")
                tmp2.replace(status_f)
            except Exception:
                pass
        return True
    except OSError:
        return False


# ── Reset ─────────────────────────────────────────────────────────────────

_RESET_PER_AGENT = (
    "06_status.json",
    "07_approval.json",
    "07_approval_request.json",
    "05_output.md",
    "04_context.md",
)


def _force_remove(p: Path) -> Optional[str]:
    """Try hard to remove a file/symlink. Returns an error string on failure, else None.
    On Windows a file held open by another process (e.g. the launcher tailing
    events.jsonl) refuses unlink; we fall back to truncating it to empty."""
    try:
        if p.is_symlink() or p.exists():
            p.unlink()
        return None
    except OSError as e:
        # Fallback: truncate regular files we couldn't unlink
        try:
            if p.exists() and not p.is_symlink() and p.is_file():
                p.write_text("", encoding="utf-8")
                return None
        except OSError:
            pass
        return f"{p.name}: {e.__class__.__name__}: {e}"


def reset_pipeline(pipeline_dir: Path) -> dict:
    """Clear runtime state so the pipeline can be started fresh.
    Removes events, stale command, and per-agent status/approval/output/context.
    Leaves prompts, inputs, and historical usage/output files intact.

    Returns {"ok": bool, "errors": [str, ...]}.
    """
    errors: list[str] = []
    targets: list[Path] = [
        pipeline_dir / ".state" / "events.jsonl",
        pipeline_dir / ".command.json",
        pause_file_path(pipeline_dir),
        resume_file_path(pipeline_dir),
    ]
    agents_dir = pipeline_dir / "agents"
    if agents_dir.exists():
        for ad in agents_dir.iterdir():
            if ad.is_dir():
                targets.extend(ad / name for name in _RESET_PER_AGENT)
    for p in targets:
        err = _force_remove(p)
        if err:
            errors.append(err)
    return {"ok": not errors, "errors": errors}


# ── Pause / resume sentinel files ─────────────────────────────────────────

_PAUSE_FILENAME  = "pause"
_RESUME_FILENAME = "resume"


def _state_dir(pipeline_dir: Path) -> Path:
    return pipeline_dir / ".state"


def pause_file_path(pipeline_dir: Path) -> Path:
    return _state_dir(pipeline_dir) / _PAUSE_FILENAME


def resume_file_path(pipeline_dir: Path) -> Path:
    return _state_dir(pipeline_dir) / _RESUME_FILENAME


def write_pause_file(pipeline_dir: Path) -> bool:
    """Create the empty sentinel at .state/pause. Idempotent."""
    p = pause_file_path(pipeline_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
        return True
    except OSError:
        return False


def write_resume_file(pipeline_dir: Path) -> bool:
    """Create .state/resume and best-effort remove .state/pause.

    The orchestrator reads `resume` and emits `pipeline_resumed`; after that
    it is expected to clean both sentinels. We also remove `pause` here so
    that the observer's UI does not linger in a paused state if the
    orchestrator is slow to acknowledge.
    """
    r = resume_file_path(pipeline_dir)
    p = pause_file_path(pipeline_dir)
    try:
        r.parent.mkdir(parents=True, exist_ok=True)
        r.touch(exist_ok=True)
    except OSError:
        return False
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass
    return True


def get_pause_state(pipeline_dir: Path) -> str:
    """Return "", "pausing" or "paused" based on events + sentinel files.

    Precedence (latest wins):
      pipeline_resumed / pipeline_done / pipeline_error / pipeline_start
        → not paused (returns "")
      pipeline_paused  → "paused"
      pipeline_pausing → "pausing"

    If the pause sentinel exists but no pausing event has been emitted yet
    (brief race between the observer writing the file and the orchestrator
    noticing it), we return "pausing" for optimistic UI feedback.
    """
    latest_ev: Optional[str] = None
    events_file = _state_dir(pipeline_dir) / "events.jsonl"
    if events_file.exists():
        try:
            for line in events_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                e = ev.get("event")
                if e in (
                    "pipeline_start", "pipeline_done", "pipeline_error",
                    "pipeline_pausing", "pipeline_paused", "pipeline_resumed",
                ):
                    latest_ev = e
        except OSError:
            pass

    if latest_ev == "pipeline_paused":
        return "paused"
    if latest_ev == "pipeline_pausing":
        return "pausing"

    # Optimistic path — pause file written but orchestrator hasn't reacted yet.
    if pause_file_path(pipeline_dir).exists() and latest_ev not in (
        "pipeline_resumed", "pipeline_done", "pipeline_error"
    ):
        return "pausing"

    return ""


# ── Command file writing (Start / Stop) ───────────────────────────────────

def write_command(pipeline_dir: Path, action: str) -> bool:
    cmd_file = pipeline_dir / ".command.json"
    now      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data     = {"action": action, "issued_at": now}
    try:
        tmp = cmd_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(cmd_file)
        return True
    except OSError:
        return False
