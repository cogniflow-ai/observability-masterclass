"""
Cogniflow Orchestrator v3.5 — Acyclic context assembly.

Used only by the DAG (acyclic) path. Cyclic pipelines assemble their
context from per-agent memory instead (see cyclic_agent.py).

  collect_inputs()   — copies upstream 05_output.md into 03_inputs/
                       (plus any static_inputs into 03_inputs/static/)
  assemble_context() — merges 02_prompt.md + 03_inputs/ → 04_context.md,
                       applying GAP-2 {{VAR}} substitutions from the
                       config.substitutions block.

v1 parity: upstream outputs are written as ``from_<dep_id>.md``, static
inputs go into ``03_inputs/static/`` with language-aware code fences,
and `MissingStaticInputError` surfaces a missing pipeline-level file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .debug import get_logger
from .exceptions import MissingDependencyOutputError, MissingStaticInputError
from .secrets import apply_substitutions

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog


# File extension → markdown fence language hint for static inputs.
_LANG_BY_EXT = {
    ".py":   "python",
    ".js":   "javascript",
    ".ts":   "typescript",
    ".tsx":  "tsx",
    ".jsx":  "jsx",
    ".sh":   "bash",
    ".bash": "bash",
    ".zsh":  "bash",
    ".sql":  "sql",
    ".rb":   "ruby",
    ".go":   "go",
    ".rs":   "rust",
    ".java": "java",
    ".cs":   "csharp",
    ".c":    "c",
    ".h":    "c",
    ".cpp":  "cpp",
    ".hpp":  "cpp",
    ".json": "json",
    ".yaml": "yaml",
    ".yml":  "yaml",
    ".toml": "toml",
    ".xml":  "xml",
    ".html": "html",
    ".css":  "css",
    ".md":   "markdown",
    ".txt":  "",
}


def _fence_lang(path: Path) -> str:
    return _LANG_BY_EXT.get(path.suffix.lower(), "")


def _load_static_inputs(agent_dir: Path) -> list[str]:
    """Read static_inputs list from 00_config.json (empty list if absent)."""
    cfg_path = agent_dir / "00_config.json"
    if not cfg_path.exists():
        return []
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    items = cfg.get("static_inputs", [])
    return list(items) if isinstance(items, list) else []


def collect_inputs(
    agent_id: str,
    agent_dir: Path,
    dependencies: list[str],
    agent_dirs: dict[str, Path],
    log: "EventLog",
    run_id: str,
) -> int:
    """
    Populate ``{agent_dir}/03_inputs/``:
      • from_<dep_id>.md    — a copy of each upstream dependency's output
      • static/<filename>   — a copy of each file listed in static_inputs

    *agent_dirs* maps agent_id → its own directory.

    Returns the number of input files collected (upstream + static).
    The pre-v3.5 ``{dep_id}_output.md`` filenames are also written as
    compatibility aliases so existing pipelines keep working.
    """
    inputs_dir = agent_dir / "03_inputs"
    inputs_dir.mkdir(exist_ok=True)

    dlog = get_logger()

    upstream_count = 0
    total_bytes = 0
    for dep_id in dependencies:
        dep_dir = agent_dirs.get(dep_id)
        if dep_dir is None:
            raise MissingDependencyOutputError(agent_id, dep_id)
        dep_output = dep_dir / "05_output.md"
        if not dep_output.exists():
            raise MissingDependencyOutputError(agent_id, dep_id, str(dep_output))
        if dep_output.is_symlink():
            dep_output = dep_output.resolve()
        content = dep_output.read_bytes()
        # v1-style name (from_<id>.md) + legacy v3 alias (<id>_output.md).
        (inputs_dir / f"from_{dep_id}.md").write_bytes(content)
        (inputs_dir / f"{dep_id}_output.md").write_bytes(content)
        total_bytes += len(content)
        upstream_count += 1
        dlog.debug(f"[context:{agent_id}] collected input from {dep_id}: "
                   f"{len(content):,} bytes")

    # Static inputs — pipeline-level shared files (e.g. fixtures, specs).
    static_spec = _load_static_inputs(agent_dir)
    static_count = 0
    if static_spec:
        # Find the pipeline directory. agent_dir is <pipeline_dir>/<agent.dir>,
        # so walking up until pipeline.json is found works for any layout.
        pipeline_dir = _find_pipeline_dir(agent_dir)
        static_dir = inputs_dir / "static"
        static_dir.mkdir(parents=True, exist_ok=True)
        for rel in static_spec:
            src = (pipeline_dir / rel).resolve()
            if not src.exists() or not src.is_file():
                raise MissingStaticInputError(agent_id, rel, str(src))
            dst = static_dir / src.name
            dst.write_bytes(src.read_bytes())
            static_count += 1

    total = upstream_count + static_count
    log.agent_inputs_collected(agent_id, total)
    return total_bytes


def find_pipeline_dir(agent_dir: Path) -> Path:
    """
    Walk up from agent_dir until a directory containing pipeline.json is
    found. Falls back to agent_dir.parent if the walk never matches
    (keeps behaviour sane in edge-case test layouts).
    """
    candidate = agent_dir.parent
    for _ in range(6):
        if (candidate / "pipeline.json").exists():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return agent_dir.parent


# Backwards-compat alias — internal callers used the underscore-prefixed name.
_find_pipeline_dir = find_pipeline_dir


def assemble_context(
    agent_id: str,
    agent_dir: Path,
    log: "EventLog",
    config: "OrchestratorConfig",
) -> int:
    """
    Build ``04_context.md`` in this order:
      1. Task              — 02_prompt.md
      2. Input files       — each file from 03_inputs/static/ in a code fence
      3. Upstream outputs  — each 03_inputs/from_*.md as its own section

    GAP-2 ``{{VAR_NAME}}`` substitution is applied to the prompt and each
    upstream input. The system prompt is intentionally excluded from
    04_context.md and is passed separately via --system-prompt (IMP-02).

    Returns the estimated token count for 04_context.md.
    """
    from .budget import estimate_tokens

    subs = config.substitutions
    parts: list[str] = []
    inputs_dir = agent_dir / "03_inputs"
    static_dir = inputs_dir / "static"

    # ── Task prompt ───────────────────────────────────────────────────────────
    prompt_path = agent_dir / "02_prompt.md"
    if prompt_path.exists():
        task = apply_substitutions(
            prompt_path.read_text(encoding="utf-8").strip(),
            subs, agent_id, log,
        )
        parts.append(f"# Task\n\n{task}")

    # ── Static input files ────────────────────────────────────────────────────
    if static_dir.exists():
        static_files = sorted(static_dir.iterdir())
        if static_files:
            sections = []
            for f in static_files:
                lang = _fence_lang(f)
                body = f.read_text(encoding="utf-8", errors="replace").rstrip()
                body = apply_substitutions(body, subs, agent_id, log)
                sections.append(f"## {f.name}\n\n```{lang}\n{body}\n```")
            parts.append("# Input files\n\n" + "\n\n---\n\n".join(sections))

    # ── Upstream agent outputs ────────────────────────────────────────────────
    if inputs_dir.exists():
        upstream_files = sorted(inputs_dir.glob("from_*.md"))
        if upstream_files:
            sections = []
            for f in upstream_files:
                dep_id = f.stem.removeprefix("from_")
                content = f.read_text(encoding="utf-8").strip()
                content = apply_substitutions(content, subs, agent_id, log)
                sections.append(f"## Output from: {dep_id}\n\n{content}")
            parts.append("# Context from upstream agents\n\n"
                         + "\n\n---\n\n".join(sections))

    full_context = "\n\n---\n\n".join(parts)
    ctx_path = agent_dir / "04_context.md"
    ctx_path.write_text(full_context, encoding="utf-8")

    ctx_bytes = len(full_context.encode("utf-8"))
    tokens = estimate_tokens(ctx_path)
    log.agent_context_ready(agent_id, ctx_bytes, tokens)
    get_logger().debug(f"[context:{agent_id}] assembled 04_context.md: "
                       f"{ctx_bytes:,} bytes, ~{tokens:,} tokens "
                       f"({len(parts)} section(s))")
    return tokens
