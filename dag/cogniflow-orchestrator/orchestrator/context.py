"""
Cogniflow Orchestrator — Context assembly.

collect_inputs()    — copies upstream 05_output.md files into 03_inputs/
                      and any declared static_inputs into 03_inputs/static/
assemble_context()  — merges task + static inputs + upstream outputs into
                      04_context.md

04_context.md is the ONLY file claude reads. Nothing passes on the CLI
except the --system-prompt flag (which uses 01_system.md via subprocess arg).
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .exceptions import MissingDependencyOutputError, MissingStaticInputError

if TYPE_CHECKING:
    from .events import EventLog


# File extension → markdown fence language hint. Unknown extensions fall
# back to "" (plain fence), which renders fine in any markdown viewer.
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
    agents_base: Path,
    log: "EventLog",
    run_id: str,
) -> int:
    """
    Populate agents/<agent_id>/03_inputs/:
      • from_<dep_id>.md     — a copy of each upstream dependency's output
      • static/<filename>    — a copy of each file listed in static_inputs

    Static input paths in 00_config.json are resolved relative to the
    pipeline directory (agents_base.parent), so pipelines can ship shared
    fixtures next to pipeline.json and have any number of agents reference
    them without duplicating the content in prompts.

    Returns the total number of input files collected (upstream + static).
    """
    inputs_dir = agent_dir / "03_inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    # ── Upstream outputs ──────────────────────────────────────────────────
    # Each agent writes a single live 05_output.md; per-run history is
    # captured by the pipeline-level snapshot, not by versioned filenames
    # next to the agent.
    upstream_count = 0
    for dep_id in dependencies:
        dep_dir = agents_base / dep_id
        src = dep_dir / "05_output.md"

        if not src.exists():
            raise MissingDependencyOutputError(agent_id, dep_id, str(src))

        # Follow a legacy symlink so we copy the actual content, not a
        # dangling link.
        if src.is_symlink():
            src = src.resolve()

        dest = inputs_dir / f"from_{dep_id}.md"
        dest.write_bytes(src.read_bytes())
        upstream_count += 1

    # ── Static inputs (pipeline-level shared files) ──────────────────────
    static_spec  = _load_static_inputs(agent_dir)
    pipeline_dir = agents_base.parent
    static_dir   = inputs_dir / "static"
    static_count = 0

    if static_spec:
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
    return total


def assemble_context(
    agent_id: str,
    agent_dir: Path,
    log: "EventLog",
) -> Path:
    """
    Build 04_context.md in this order:
      1. Task              — 02_prompt.md
      2. Input files       — each file from 03_inputs/static/ in a code fence
      3. Upstream outputs  — each 03_inputs/from_*.md as its own section

    The system prompt is intentionally excluded from 04_context.md and
    is passed separately to claude via --system-prompt (IMP-02).

    Returns the path to 04_context.md.
    """
    context_path = agent_dir / "04_context.md"
    inputs_dir   = agent_dir / "03_inputs"
    static_dir   = inputs_dir / "static"

    parts: list[str] = []

    # ── Task prompt ───────────────────────────────────────────────────────
    task = (agent_dir / "02_prompt.md").read_text(encoding="utf-8").strip()
    parts.append(f"# Task\n\n{task}")

    # ── Static input files ────────────────────────────────────────────────
    static_files = sorted(static_dir.iterdir()) if static_dir.exists() else []
    if static_files:
        sections = []
        for f in static_files:
            lang = _fence_lang(f)
            body = f.read_text(encoding="utf-8", errors="replace").rstrip()
            sections.append(f"## {f.name}\n\n```{lang}\n{body}\n```")
        parts.append("# Input files\n\n" + "\n\n---\n\n".join(sections))

    # ── Upstream agent outputs ────────────────────────────────────────────
    upstream_files = sorted(inputs_dir.glob("from_*.md"))
    if upstream_files:
        sections = []
        for f in upstream_files:
            dep_id = f.stem.removeprefix("from_")
            content = f.read_text(encoding="utf-8").strip()
            sections.append(f"## Output from: {dep_id}\n\n{content}")
        parts.append("# Context from upstream agents\n\n" +
                     "\n\n---\n\n".join(sections))

    full_context = "\n\n---\n\n".join(parts)
    context_path.write_text(full_context, encoding="utf-8")

    ctx_bytes  = len(full_context.encode("utf-8"))
    ctx_tokens = int(ctx_bytes / 3.5)
    log.agent_context_ready(agent_id, ctx_bytes, ctx_tokens)

    return context_path
