"""
Cogniflow Observer — versioning / history layer.

Each pipeline run is snapshotted under `<pipeline>/history/<run_id>/` with the
same tree structure as the live pipeline dir. This module exposes:

  * run discovery + annotations (label/comment)
  * logical-file discovery (union of files across current + all runs)
  * per-file version listing (current + each run that has it)
  * safe read / write for the current version
  * unified HTML diff between any two versions
"""
from __future__ import annotations
import html
import json
import difflib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .filesystem import read_pipeline_json, validate_prompt_taglines

# ── Constants ──────────────────────────────────────────────────────────────

HISTORY_DIRNAME = "history"
ANNOTATION_FILE = "annotation.json"

# Canonical per-agent files we surface in the versioning tree.
# 05_output.<timestamp>.md and similar rotated files are deliberately excluded;
# the canonical 05_output.md is the one snapshotted into each run dir.
AGENT_CANONICAL = (
    "01_system.md",
    "02_prompt.md",
    "04_context.md",
    "05_output.md",
    "05_usage.json",
    "06_status.json",
)

# Pipeline-level files we surface.
PIPELINE_CANONICAL = (
    "pipeline.json",
    "summary.json",
    "env.snapshot.json",
    "events.jsonl",
)

# Logical files the user is allowed to edit on the current version.
# Any path under agents/<id>/03_inputs/ is also editable (handled separately).
EDITABLE_CURRENT = {
    "pipeline.json",
    "01_system.md",
    "02_prompt.md",
}

MAX_EDIT_BYTES = 2 * 1024 * 1024  # 2 MB — refuse to edit larger files


# ── Path safety ────────────────────────────────────────────────────────────

def _safe_join(root: Path, rel: str) -> Optional[Path]:
    """Join `root / rel`, refuse the result if it escapes `root`.
    Returns None on any traversal attempt, empty path, or absolute `rel`."""
    if not rel or rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/"):
        return None
    candidate = (root / rel)
    try:
        resolved = candidate.resolve(strict=False)
        root_r = root.resolve(strict=False)
    except OSError:
        return None
    try:
        resolved.relative_to(root_r)
    except ValueError:
        return None
    return candidate


def _history_dir(pipeline_dir: Path) -> Path:
    return pipeline_dir / HISTORY_DIRNAME


# ── Run discovery ──────────────────────────────────────────────────────────

def _run_summary(run_dir: Path) -> dict:
    p = run_dir / "summary.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run_started_from_name(name: str) -> str:
    """v1_20260421-114629 → 2026-04-21 11:46:29. Best-effort — returns '' on miss."""
    try:
        _, ts = name.split("_", 1)
        date, time_ = ts.split("-", 1)
        y, m, d = date[0:4], date[4:6], date[6:8]
        hh, mm, ss = time_[0:2], time_[2:4], time_[4:6]
        return f"{y}-{m}-{d} {hh}:{mm}:{ss}"
    except Exception:
        return ""


def read_run_annotation(run_dir: Path) -> dict:
    p = run_dir / ANNOTATION_FILE
    if not p.exists():
        return {"label": "", "comment": "", "updated_at": ""}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            "label":      data.get("label", "") or "",
            "comment":    data.get("comment", "") or "",
            "updated_at": data.get("updated_at", "") or "",
        }
    except Exception:
        return {"label": "", "comment": "", "updated_at": ""}


def write_run_annotation(run_dir: Path, label: str, comment: str) -> bool:
    if not run_dir.is_dir():
        return False
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {"label": label, "comment": comment, "updated_at": now}
    p = run_dir / ANNOTATION_FILE
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except OSError:
        return False


def list_runs(pipeline_dir: Path) -> list[dict]:
    """Return runs sorted newest-first by folder name (names are v<n>_<ts>)."""
    hdir = _history_dir(pipeline_dir)
    if not hdir.is_dir():
        return []
    runs = []
    for entry in hdir.iterdir():
        if not entry.is_dir():
            continue
        summary = _run_summary(entry)
        annot   = read_run_annotation(entry)
        runs.append({
            "id":         entry.name,
            "started":    _run_started_from_name(entry.name),
            "status":     summary.get("status", ""),
            "duration_s": summary.get("duration_s"),
            "agents_run": summary.get("agents_run"),
            "tokens":     summary.get("tokens", {}),
            "label":      annot["label"],
            "comment":    annot["comment"],
            "annot_updated_at": annot["updated_at"],
        })
    runs.sort(key=lambda r: r["id"], reverse=True)
    return runs


def find_run(pipeline_dir: Path, run_id: str) -> Optional[Path]:
    """Return the run dir if `run_id` is a direct child of history/."""
    hdir = _history_dir(pipeline_dir)
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    cand = hdir / run_id
    if not cand.is_dir():
        return None
    try:
        cand.resolve().relative_to(hdir.resolve())
    except ValueError:
        return None
    return cand


# ── Logical-file discovery ─────────────────────────────────────────────────

def _agent_ids(pipeline_dir: Path) -> list[str]:
    """Union of agents listed in pipeline.json + dirs found under agents/ and
    history/*/agents/. Keeps pipeline.json order first, then any extras."""
    ids: list[str] = []
    seen: set[str] = set()

    for a in read_pipeline_json(pipeline_dir).get("agents", []):
        aid = a.get("id")
        if aid and aid not in seen:
            ids.append(aid); seen.add(aid)

    # Scan live agents dir
    live = pipeline_dir / "agents"
    if live.is_dir():
        for d in sorted(live.iterdir()):
            if d.is_dir() and d.name not in seen:
                ids.append(d.name); seen.add(d.name)

    # Scan each history run
    hdir = _history_dir(pipeline_dir)
    if hdir.is_dir():
        for run in hdir.iterdir():
            rad = run / "agents"
            if rad.is_dir():
                for d in sorted(rad.iterdir()):
                    if d.is_dir() and d.name not in seen:
                        ids.append(d.name); seen.add(d.name)
    return ids


def _inputs_logical_paths(pipeline_dir: Path, agent_id: str) -> list[str]:
    """All files found under any 03_inputs/ for this agent, across current +
    history. Returns sorted unique logical paths."""
    rel_paths: set[str] = set()
    prefix = f"agents/{agent_id}/03_inputs"

    def _scan(root: Path):
        base = root / "agents" / agent_id / "03_inputs"
        if not base.is_dir():
            return
        for f in base.rglob("*"):
            if f.is_file():
                try:
                    rel = f.relative_to(base).as_posix()
                except ValueError:
                    continue
                rel_paths.add(f"{prefix}/{rel}")

    _scan(pipeline_dir)
    hdir = _history_dir(pipeline_dir)
    if hdir.is_dir():
        for run in hdir.iterdir():
            if run.is_dir():
                _scan(run)
    return sorted(rel_paths)


def _path_exists_anywhere(pipeline_dir: Path, logical: str) -> bool:
    if (pipeline_dir / logical).exists():
        return True
    hdir = _history_dir(pipeline_dir)
    if hdir.is_dir():
        for run in hdir.iterdir():
            if run.is_dir() and (run / logical).exists():
                return True
    return False


FILE_ICONS = {
    "pipeline.json":     "🧭",
    "summary.json":      "📊",
    "env.snapshot.json": "🔐",
    "events.jsonl":      "📜",
    "01_system.md":      "⚙",
    "02_prompt.md":      "💬",
    "04_context.md":     "🧠",
    "05_output.md":      "📝",
    "05_usage.json":     "💰",
    "06_status.json":    "✓",
}


def _file_meta(logical: str, editable: bool) -> dict:
    basename = logical.rsplit("/", 1)[-1]
    if "/03_inputs/" in logical:
        icon = "📥"
        kind = "input"
    else:
        icon = FILE_ICONS.get(basename, "📄")
        kind = "config" if editable else "generated"
    return {"icon": icon, "kind": kind}


def build_file_tree(pipeline_dir: Path) -> dict:
    """Build the versioning file tree.

    Returns:
        {
          "pipeline": [ {"logical","label","editable","version_count","icon","kind"}, ...],
          "agents":   [ {"id": "001_writer", "files": [...], "config_count": n, "run_count": n }, ...]
        }

    Only files that exist in at least one version are included.
    """
    tree: dict = {"pipeline": [], "agents": []}

    for name in PIPELINE_CANONICAL:
        if _path_exists_anywhere(pipeline_dir, name):
            editable = name in EDITABLE_CURRENT
            meta = _file_meta(name, editable)
            tree["pipeline"].append({
                "logical":       name,
                "label":         name,
                "editable":      editable,
                "version_count": len(list_file_versions(pipeline_dir, name)),
                **meta,
            })

    for aid in _agent_ids(pipeline_dir):
        files = []
        config_count = 0
        run_count = 0

        for name in AGENT_CANONICAL:
            logical = f"agents/{aid}/{name}"
            if _path_exists_anywhere(pipeline_dir, logical):
                editable = name in EDITABLE_CURRENT
                meta = _file_meta(logical, editable)
                files.append({
                    "logical":       logical,
                    "label":         name,
                    "editable":      editable,
                    "version_count": len(list_file_versions(pipeline_dir, logical)),
                    **meta,
                })
                if editable:
                    config_count += 1
                else:
                    run_count += 1

        for logical in _inputs_logical_paths(pipeline_dir, aid):
            label = logical.split("03_inputs/", 1)[1]
            meta = _file_meta(logical, True)
            files.append({
                "logical":       logical,
                "label":         f"03_inputs/{label}",
                "editable":      True,
                "version_count": len(list_file_versions(pipeline_dir, logical)),
                **meta,
            })
            config_count += 1

        if files:
            tree["agents"].append({
                "id": aid, "files": files,
                "config_count": config_count, "run_count": run_count,
            })

    return tree


# ── Version listing / reading ──────────────────────────────────────────────

def _version_file_path(pipeline_dir: Path, logical: str, version: str) -> Optional[Path]:
    """Resolve the filesystem path for (logical, version). `version` is
    'current' or a run_id. Returns None on any safety failure."""
    if version == "current":
        return _safe_join(pipeline_dir, logical)
    run_dir = find_run(pipeline_dir, version)
    if run_dir is None:
        return None
    return _safe_join(run_dir, logical)


def list_file_versions(pipeline_dir: Path, logical: str) -> list[dict]:
    """List all available versions of a logical file.

    Each entry: {"version": "current"|run_id, "exists": bool, "size": int|None, "mtime": str}
    'current' is always listed (even if absent) so the user can see the gap.
    Runs are listed newest-first and only when the file exists in that run.
    """
    versions: list[dict] = []

    cur = _safe_join(pipeline_dir, logical)
    cur_exists = bool(cur and cur.exists() and cur.is_file())
    versions.append({
        "version": "current",
        "exists":  cur_exists,
        "size":    cur.stat().st_size if cur_exists else None,
        "mtime":   _iso_mtime(cur) if cur_exists else "",
    })

    hdir = _history_dir(pipeline_dir)
    if hdir.is_dir():
        runs = sorted([r for r in hdir.iterdir() if r.is_dir()],
                      key=lambda r: r.name, reverse=True)
        for run in runs:
            p = _safe_join(run, logical)
            if p and p.exists() and p.is_file():
                versions.append({
                    "version": run.name,
                    "exists":  True,
                    "size":    p.stat().st_size,
                    "mtime":   _iso_mtime(p),
                })
    return versions


def _iso_mtime(p: Path) -> str:
    try:
        ts = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return ""


def read_file_version(pipeline_dir: Path, logical: str, version: str) -> tuple[bool, str]:
    """Return (ok, text). On failure the text field holds the error message."""
    p = _version_file_path(pipeline_dir, logical, version)
    if p is None:
        return False, "Invalid path or version."
    if not p.exists() or not p.is_file():
        return False, f"(file not present in version: {version})"
    try:
        return True, p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"(error reading file: {e})"


def is_editable(logical: str) -> bool:
    """Only current-version edits on whitelisted files are allowed."""
    name = logical.rsplit("/", 1)[-1]
    if logical == "pipeline.json":
        return True
    if logical.startswith("agents/") and "/03_inputs/" in logical:
        return True
    return name in EDITABLE_CURRENT


def write_current_file(pipeline_dir: Path, logical: str, content: str) -> tuple[bool, str]:
    """Write `content` to the current version of `logical`. Returns (ok, msg)."""
    if not is_editable(logical):
        return False, "This file is not editable — it is generated by the pipeline."
    if len(content.encode("utf-8", errors="replace")) > MAX_EDIT_BYTES:
        return False, "File is too large to edit via the observer."

    p = _safe_join(pipeline_dir, logical)
    if p is None:
        return False, "Invalid path."

    if logical.endswith(".json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON: {e}"

    # Tagline validation for system / task prompts, gated by the pipeline
    # config flag `validate_taglines`. The set of required taglines comes
    # from `validated_taglines` (comma-separated) in pipeline.json.
    if logical.endswith(".md"):
        basename = logical.rsplit("/", 1)[-1]
        if basename in ("01_system.md", "02_prompt.md"):
            pj = read_pipeline_json(pipeline_dir)
            if pj.get("validate_taglines"):
                raw = pj.get("validated_taglines", "")
                if isinstance(raw, list):
                    required = [str(t).strip() for t in raw if str(t).strip()]
                else:
                    required = [t.strip() for t in str(raw).split(",") if t.strip()]
                err = validate_prompt_taglines(content, required)
                if err:
                    return False, err

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(p)
        return True, "Saved."
    except OSError as e:
        return False, f"Could not write file: {e}"


# ── Diff rendering ─────────────────────────────────────────────────────────

def render_diff(pipeline_dir: Path, logical: str,
                version_a: str, version_b: str) -> dict:
    """Render a unified diff between two versions. Returns dict with keys:
       ok, lines (list of {cls, text}), a_meta, b_meta, error."""
    ok_a, text_a = read_file_version(pipeline_dir, logical, version_a)
    ok_b, text_b = read_file_version(pipeline_dir, logical, version_b)
    if not ok_a:
        return {"ok": False, "error": f"A: {text_a}", "lines": [],
                "a_meta": version_a, "b_meta": version_b}
    if not ok_b:
        return {"ok": False, "error": f"B: {text_b}", "lines": [],
                "a_meta": version_a, "b_meta": version_b}

    a_lines = text_a.splitlines(keepends=False)
    b_lines = text_b.splitlines(keepends=False)

    udiff = list(difflib.unified_diff(
        a_lines, b_lines,
        fromfile=f"{version_a} / {logical}",
        tofile=f"{version_b} / {logical}",
        lineterm="",
        n=3,
    ))

    rendered: list[dict] = []
    for i, raw in enumerate(udiff):
        if i < 2:
            rendered.append({"cls": "diff-file", "text": raw})
            continue
        if raw.startswith("@@"):
            cls = "diff-hunk"
        elif raw.startswith("+"):
            cls = "diff-add"
        elif raw.startswith("-"):
            cls = "diff-del"
        else:
            cls = "diff-ctx"
        rendered.append({"cls": cls, "text": raw})

    if not udiff:
        rendered.append({"cls": "diff-same", "text": "(files are identical)"})

    return {
        "ok":      True,
        "error":   "",
        "lines":   rendered,
        "a_meta":  version_a,
        "b_meta":  version_b,
    }


def escape_html(s: str) -> str:
    return html.escape(s, quote=False)
