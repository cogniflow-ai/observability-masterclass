"""Cogniflow Configurator — bridge to the orchestrator library.

Locates the sibling cogniflow-orchestrator-v3.5 package on sys.path and
re-exports the symbols the Configurator needs as a library:

  - validate_pipeline()         — full save-time validation
  - VALID_SCHEMA_MODES          — input/output schema mode whitelist
  - PipelineValidationError     — structured-errors exception
  - Vault, resolve_vault_path,
    scan_pipeline_for_markers,
    open_vault_for              — vault management
  - scan_for_secrets            — credential-pattern scan for migration banner

If the orchestrator package is not importable, the bridge exposes a
minimal in-process fallback for VALID_SCHEMA_MODES so the UI still renders;
validation falls back to the Configurator's own local validator.

Resolution order for the orchestrator location, in priority:
  1. ORCH_PATH environment variable (absolute path to the package root,
     i.e. the directory containing the `orchestrator/` folder)
  2. Sibling directory `../cogniflow-orchestrator-v3.5/` next to this repo
  3. Already on sys.path (pip-installed)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent

_ORCH_FOUND: bool = False
_ORCH_ROOT: Path | None = None


def _try_path(p: Path) -> bool:
    """Add *p* to sys.path if it contains an `orchestrator/` package."""
    global _ORCH_FOUND, _ORCH_ROOT
    if not p.exists():
        return False
    pkg = p / "orchestrator"
    if not pkg.exists() or not (pkg / "__init__.py").exists():
        return False
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
    _ORCH_FOUND = True
    _ORCH_ROOT = p
    return True


def _locate_orchestrator() -> bool:
    env = os.getenv("ORCH_PATH", "").strip()
    if env and _try_path(Path(env)):
        return True

    candidates = [
        # Public-repo layout: dag/cogniflow-ui/configurator/ → dag/cogniflow-orchestrator/
        _HERE.parent.parent / "cogniflow-orchestrator",
        # Local dev layouts (sibling under code/)
        _HERE.parent / "cogniflow-orchestrator-v3.5",
        _HERE.parent / "cogniflow-orchestrator_v3.5",
        _HERE.parent.parent / "cogniflow-orchestrator-v3.5",
        _HERE.parent.parent / "code" / "cogniflow-orchestrator-v3.5",
    ]
    for c in candidates:
        if _try_path(c):
            return True

    try:
        import orchestrator  # noqa: F401
        global _ORCH_FOUND
        _ORCH_FOUND = True
        return True
    except ImportError:
        return False


_locate_orchestrator()


# ── Re-exports (with safe fallbacks) ──────────────────────────────────────────

try:
    from orchestrator.schema import VALID_MODES as VALID_SCHEMA_MODES  # type: ignore
except ImportError:
    VALID_SCHEMA_MODES = {
        "json", "regex", "contains", "not_contains",
        "min_words", "max_words", "starts_with", "ends_with",
        "has_sections",
    }

try:
    from orchestrator.exceptions import PipelineValidationError  # type: ignore
except ImportError:
    class PipelineValidationError(Exception):  # type: ignore
        def __init__(self, errors: list[str]) -> None:
            self.errors = errors
            super().__init__("\n".join(errors))


def is_available() -> bool:
    """True iff the orchestrator library was successfully located."""
    return _ORCH_FOUND


def root() -> Path | None:
    """Return the resolved orchestrator package root, or None if unavailable."""
    return _ORCH_ROOT


def validate_pipeline(pipeline_dir: Path) -> dict[str, Any] | None:
    """Wrap orchestrator.validate.validate_pipeline; returns the parsed spec.

    Raises PipelineValidationError on failure (passes through). Returns None
    if the orchestrator library is not available — callers should fall back
    to the Configurator's local validator in that case.
    """
    if not _ORCH_FOUND:
        return None
    from orchestrator.validate import validate_pipeline as _vp  # type: ignore
    return _vp(pipeline_dir)


def open_vault(pipeline_dir: Path):
    """Open the vault associated with *pipeline_dir*. Returns a Vault, or None."""
    if not _ORCH_FOUND:
        return None
    from orchestrator.vault import Vault, resolve_vault_path  # type: ignore
    path = resolve_vault_path(Path(pipeline_dir))
    return Vault(path)


def open_repo_vault(pipelines_root: Path):
    """Open the repo-wide vault sitting at <pipelines_root>/secrets.db.

    The Vault panel is repo-wide, not per-pipeline, so it operates one level
    above any individual pipeline directory.
    """
    if not _ORCH_FOUND:
        return None
    from orchestrator.vault import Vault  # type: ignore
    return Vault(Path(pipelines_root) / "secrets.db")


def scan_pipeline_for_markers(pipeline_dir: Path) -> dict[str, list[dict]]:
    """Walk a pipeline's prompts and return name→[{agent, file}, ...]."""
    if not _ORCH_FOUND:
        return _scan_markers_local(pipeline_dir)
    from orchestrator.vault import scan_pipeline_for_markers as _spm  # type: ignore
    try:
        return _spm(pipeline_dir)
    except Exception:
        return _scan_markers_local(pipeline_dir)


def _scan_markers_local(pipeline_dir: Path) -> dict[str, list[dict]]:
    """Fallback marker scan that mirrors orchestrator.vault.scan_pipeline_for_markers
    but without depending on the package — used when the bridge cannot import."""
    import json
    import re as _re
    pj = Path(pipeline_dir) / "pipeline.json"
    if not pj.exists():
        return {}
    try:
        spec = json.loads(pj.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rx = _re.compile(r"<<secret:([A-Za-z_][A-Za-z0-9_]*)>>")
    refs: dict[str, list[dict]] = {}
    for agent in spec.get("agents", []):
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", "?")
        # Try v1 layout (agents/<id>/) first, then v3.5 implicit (<id>/)
        for layout in ("agents/", ""):
            for fname in ("01_system.md", "02_prompt.md"):
                fp = Path(pipeline_dir) / f"{layout}{aid}" / fname
                if not fp.exists():
                    continue
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for m in rx.finditer(text):
                    refs.setdefault(m.group(1), []).append({
                        "agent": aid, "file": fname,
                    })
    return refs


def scan_for_secrets_in_text(text: str) -> list[dict]:
    """Run the orchestrator's credential regex set against *text*.

    Returns a list of {pattern, count} entries. Used by the migration
    banner to decide whether substitution values look like credentials.
    """
    if not _ORCH_FOUND:
        return _scan_text_local(text)
    try:
        from orchestrator.secrets import _SECRET_PATTERNS  # type: ignore
    except ImportError:
        return _scan_text_local(text)
    findings: list[dict] = []
    for label, pattern in _SECRET_PATTERNS:
        n = len(pattern.findall(text))
        if n:
            findings.append({"pattern": label, "count": n})
    return findings


_LOCAL_SECRET_PATTERNS: list[tuple[str, Any]] = []


def _scan_text_local(text: str) -> list[dict]:
    """Fallback for environments without the orchestrator package."""
    global _LOCAL_SECRET_PATTERNS
    if not _LOCAL_SECRET_PATTERNS:
        import re as _re
        _LOCAL_SECRET_PATTERNS = [
            ("AWS access key",        _re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
            ("AWS secret key",        _re.compile(r"(?i)aws.{0,20}secret.{0,20}[=:]\s*[A-Za-z0-9/+=]{40}")),
            ("GitHub token",          _re.compile(r"\bghp_[A-Za-z0-9]{36}\b|\bghc_[A-Za-z0-9]{36}\b")),
            ("GitLab token",          _re.compile(r"\bglpat-[A-Za-z0-9\-_]{20,}\b")),
            ("Anthropic API key",     _re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{40,}\b")),
            ("OpenAI API key",        _re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
            ("Generic API key",       _re.compile(r"(?i)api[_\-]?key\s*[=:]\s*[\"']?[A-Za-z0-9\-_]{16,}[\"']?")),
            ("Bearer token",          _re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_\.]{20,}")),
            ("Basic auth credential", _re.compile(r"(?i)(?:password|passwd|pwd)\s*[=:]\s*[^\s]{8,}")),
            ("Private key header",    _re.compile(r"-----BEGIN (RSA|EC|OPENSSH|DSA) PRIVATE KEY-----")),
            ("Connection string",     _re.compile(r"(?i)(?:mongodb|postgres|mysql|redis)://[^\s]+")),
        ]
    findings: list[dict] = []
    for label, pattern in _LOCAL_SECRET_PATTERNS:
        n = len(pattern.findall(text))
        if n:
            findings.append({"pattern": label, "count": n})
    return findings


def name_grammar_ok(name: str) -> bool:
    """Mirror orchestrator.vault._NAME_GRAMMAR — used by the New Secret dialog
    so we can reject bad names client-side AND server-side without bouncing
    through the (slower) Vault.put exception path."""
    import re as _re
    return bool(_re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""))
