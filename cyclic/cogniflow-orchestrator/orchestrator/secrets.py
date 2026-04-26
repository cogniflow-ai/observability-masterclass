"""
Cogniflow Orchestrator v3.5 — Secrets and sensitive data (GAP-2, restored).

Three protections, all applied automatically by the runtime:

  1. ``generate_gitignore()`` — write ``.gitignore`` at the pipeline root
     so ``.state/`` cannot be committed. Called by core.run_pipeline() on
     every run (no-op if already correct).

  2. ``scan_for_secrets()`` — regex scan of 01_system.md and 02_prompt.md
     for credential-like patterns. Emits ``secret_warning`` events;
     advisory only, never blocks.

  3. ``apply_substitutions()`` — replace ``{{VAR_NAME}}`` placeholders
     with values from ``config.json`` ``substitutions`` block. Missing
     vars emit ``secret_substitution_warning`` and are left unchanged.

NON-PRODUCTION NOTE
-------------------
``config.json`` is a plain text file. Any substitution value you store
there is committed (unless you add config.json to .gitignore) and appears
in ``04_context.md`` / the ``-p`` arg to claude. Do not put real
production secrets here — use a proper secrets manager and a local
pre-processing step for those.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .events import EventLog


# ── Credential patterns (advisory scan) ───────────────────────────────────────

_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key",        re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS secret key",        re.compile(r"(?i)aws.{0,20}secret.{0,20}[=:]\s*[A-Za-z0-9/+=]{40}")),
    ("GitHub token",          re.compile(r"\bghp_[A-Za-z0-9]{36}\b|\bghc_[A-Za-z0-9]{36}\b")),
    ("GitLab token",          re.compile(r"\bglpat-[A-Za-z0-9\-_]{20,}\b")),
    ("Anthropic API key",     re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{40,}\b")),
    ("OpenAI API key",        re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("Generic API key",       re.compile(r"(?i)api[_\-]?key\s*[=:]\s*[\"']?[A-Za-z0-9\-_]{16,}[\"']?")),
    ("Bearer token",          re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_\.]{20,}")),
    ("Basic auth credential", re.compile(r"(?i)(?:password|passwd|pwd)\s*[=:]\s*[^\s]{8,}")),
    ("Private key header",    re.compile(r"-----BEGIN (RSA|EC|OPENSSH|DSA) PRIVATE KEY-----")),
    ("Connection string",     re.compile(r"(?i)(?:mongodb|postgres|mysql|redis)://[^\s]+")),
]


# ── .gitignore template ───────────────────────────────────────────────────────

_GITIGNORE_CONTENT = """\
# ── Cogniflow Orchestrator — auto-generated .gitignore ──────────────────────
#
# IMPORTANT: .state/ contains full prompt text, assembled contexts, upstream
# outputs, and event logs. It must never be committed to version control.
# pipelines/secrets.db holds the SQLite vault and must likewise stay local.

# Runtime state
**/.state/
.state/

# Secrets vault (v4)
**/pipelines/secrets.db
**/pipelines/secrets.db-journal
**/pipelines/secrets.db-shm
**/pipelines/secrets.db-wal
pipelines/secrets.db
pipelines/secrets.db-journal
pipelines/secrets.db-shm
pipelines/secrets.db-wal

# Lock files
**/*.lock

# Python artefacts
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/
.pytest_cache/
.coverage
htmlcov/

# IDE
.vscode/
.idea/
*.swp
*.swo
"""


_VAULT_GITIGNORE_LINES = (
    "**/pipelines/secrets.db",
    "pipelines/secrets.db",
)


_VAR_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


# ── Public API ────────────────────────────────────────────────────────────────

def generate_gitignore(pipeline_dir: Path) -> Path:
    """
    Create or update ``<pipeline_dir>/.gitignore`` so ``.state/`` is excluded.

    If the file exists and already excludes ``.state/``, nothing is written.
    Returns the path to the .gitignore file.
    """
    gitignore = pipeline_dir / ".gitignore"

    if not gitignore.exists():
        gitignore.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
        return gitignore

    existing = gitignore.read_text(encoding="utf-8")
    additions: list[str] = []
    if ".state/" not in existing:
        additions.extend(("**/.state/", ".state/"))
    for line in _VAULT_GITIGNORE_LINES:
        if line not in existing:
            additions.append(line)
    if additions:
        with open(gitignore, "a", encoding="utf-8") as fh:
            fh.write("\n# Cogniflow — added by orchestrator\n")
            for line in additions:
                fh.write(line + "\n")

    return gitignore


def scan_for_secrets(
    agent_id: str,
    agent_dir: Path,
    log: "EventLog",
) -> list[dict]:
    """
    Scan 01_system.md and 02_prompt.md for credential-like patterns.

    Emits ``secret_warning`` events; advisory only (never blocks).
    Returns the list of findings for tests / callers who want them.
    """
    findings: list[dict] = []
    for fname in ("01_system.md", "02_prompt.md"):
        fpath = agent_dir / fname
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8")

        for label, pattern in _SECRET_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                findings.append({
                    "agent_id": agent_id,
                    "file":     fname,
                    "pattern":  label,
                    "count":    len(matches),
                })
                log.secret_warning(agent_id, label)

    return findings


def apply_substitutions(
    text: str,
    substitutions: dict[str, str],
    agent_id: str,
    log: "EventLog",
) -> str:
    """
    Replace ``{{VAR_NAME}}`` in *text* with ``substitutions[VAR_NAME]``.

    Placeholders whose key is missing are left unchanged and a
    ``secret_substitution_warning`` event is emitted so the operator
    sees what is needed.
    """
    from .debug import get_logger

    replaced: list[str] = []
    missing:  list[str] = []

    def replacer(m: re.Match) -> str:
        var = m.group(1)
        if var in substitutions:
            replaced.append(var)
            return substitutions[var]
        missing.append(var)
        log.secret_substitution_warning(agent_id, var)
        return m.group(0)

    out = _VAR_RE.sub(replacer, text)

    if replaced or missing:
        dlog = get_logger()
        # Names only — never the values.
        if replaced:
            dlog.debug(f"[secret:{agent_id}] substituted: "
                       f"{sorted(set(replaced))} ({len(replaced)} occurrence(s))")
        if missing:
            dlog.debug(f"[secret:{agent_id}] MISSING substitutions: "
                       f"{sorted(set(missing))} — placeholders left intact")

    return out
