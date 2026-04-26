"""
Cogniflow Orchestrator v4 — Secrets vault.

Small SQLite-backed store for secret values referenced from prompts
using the ``<<secret:NAME>>`` marker. Three tables:

    secrets              — name → value, plus description, tags,
                           origin_pipeline, created_at, updated_at
    secret_pipeline_link — which pipelines have used which secrets
    secret_audit         — append-only audit log. Names only, never values.

The vault file lives at ``pipelines/secrets.db`` by default (see
``resolve_vault_path``). Added to the auto-generated ``.gitignore``.

Trust model: obfuscation and hygiene, not production secret management.
OS file permissions are the trust boundary. Values are stored in plain
SQLite (no encryption at rest).

Usage
-----
Management (Configurator / CLI)::

    v = Vault(path)
    v.put("DB_PASSWORD", "s3kret", description="staging DB")
    meta = v.list()
    v.delete("DB_PASSWORD")

Runtime (Orchestrator agent runners)::

    ctx = AuditCtx(run_id=..., pipeline_name=..., agent_id=..., file="04_context")
    hydrated = v.rehydrate(text, ctx=ctx, direction="outbound", event_log=log)
    leaked   = v.scan_leaks(response_text, ctx=ctx, event_log=log)

Direction semantics
-------------------
    outbound  — rehydrated on a string about to be sent to Claude
                (e.g. system prompt, ``04_context.md``).
    inbound   — rehydrated on a response body before writing
                ``05_output.md``.
    missing   — a ``<<secret:NAME>>`` appeared with no matching vault row;
                placeholder left intact.
    leaked    — a raw value appeared literally in a Claude response; the
                model ignored the placeholder guardrail.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .events import EventLog


# ── Markers ───────────────────────────────────────────────────────────────────

# Placeholder syntax in source files: <<secret:NAME>>
_SECRET_MARKER = re.compile(r"<<secret:([A-Za-z_][A-Za-z0-9_]*)>>")

# Identifier grammar for secret names.
_NAME_GRAMMAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ── SQLite schema (v1) ────────────────────────────────────────────────────────

_SCHEMA_V1 = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS secrets (
    name            TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',
    origin_pipeline TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secret_pipeline_link (
    secret_name   TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    first_used_at TEXT NOT NULL,
    last_used_at  TEXT NOT NULL,
    PRIMARY KEY (secret_name, pipeline_name),
    FOREIGN KEY (secret_name) REFERENCES secrets(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS secret_audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    run_id        TEXT NOT NULL DEFAULT '',
    pipeline_name TEXT NOT NULL DEFAULT '',
    agent_id      TEXT NOT NULL DEFAULT '',
    direction     TEXT NOT NULL,
    secret_name   TEXT NOT NULL,
    file          TEXT NOT NULL DEFAULT '',
    occurrences   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_audit_run      ON secret_audit(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_pipeline ON secret_audit(pipeline_name, ts);
CREATE INDEX IF NOT EXISTS idx_audit_secret   ON secret_audit(secret_name, ts);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');
"""


VALID_DIRECTIONS = {"outbound", "inbound", "missing", "leaked"}


# ── Public helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_vault_path(pipeline_dir: Path, explicit: Optional[str] = None) -> Path:
    """
    Resolve the SQLite file location.

    Order:
      1. *explicit* (from config.secrets.vault_db_path).
      2. If ``pipeline_dir.parent`` is named ``pipelines``, use
         ``<that parent>/secrets.db`` — the standard course layout.
      3. Fallback: ``<pipeline_dir>/.state/secrets.db`` — keeps tests
         and ad-hoc pipelines self-contained.
    """
    pipeline_dir = Path(pipeline_dir)
    if explicit:
        return Path(explicit)
    parent = pipeline_dir.parent
    if parent.name == "pipelines" and parent.exists():
        return parent / "secrets.db"
    return pipeline_dir / ".state" / "secrets.db"


@dataclass
class AuditCtx:
    """Context attached to a single runtime vault operation."""
    run_id:        str = ""
    pipeline_name: str = ""
    agent_id:      str = ""
    file:          str = ""   # "01_system" | "02_prompt" | "04_context"
                              # | "05_output" | "response_raw" | "03_inputs/<name>"


# ── Vault class ───────────────────────────────────────────────────────────────

class Vault:
    """
    Thread-safe SQLite-backed secret store with audit logging.

    Every public method acquires an internal lock so multiple agent
    threads (DAG parallel layer, cyclic engine) can share one instance
    safely.
    """

    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA_V1)
            finally:
                conn.close()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def put(
        self,
        name: str,
        value: str,
        *,
        description: str = "",
        tags: Optional[list[str]] = None,
        pipeline: Optional[str] = None,
    ) -> None:
        """Insert or update one secret."""
        if not _NAME_GRAMMAR.match(name):
            raise ValueError(
                f"Invalid secret name {name!r}. "
                "Must match [A-Za-z_][A-Za-z0-9_]*."
            )
        if not isinstance(value, str) or value == "":
            raise ValueError("Secret value must be a non-empty string")

        now = _now_iso()
        tags_json = json.dumps(list(tags or []))
        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    "SELECT name FROM secrets WHERE name = ?", (name,)
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO secrets(name, value, description, tags, "
                        "origin_pipeline, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (name, value, description, tags_json,
                         pipeline or "", now, now),
                    )
                else:
                    # Preserve origin_pipeline and created_at on update.
                    conn.execute(
                        "UPDATE secrets SET value=?, description=?, tags=?, "
                        "updated_at=? WHERE name=?",
                        (value, description, tags_json, now, name),
                    )
            finally:
                conn.close()

    def get(self, name: str) -> Optional[str]:
        """Return the secret's value, or None if the name is not registered."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM secrets WHERE name = ?", (name,)
                ).fetchone()
                return row["value"] if row else None
            finally:
                conn.close()

    def get_metadata(self, name: str) -> Optional[dict]:
        """Return metadata row (no value), or None if absent."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT name, description, tags, origin_pipeline, "
                    "created_at, updated_at FROM secrets WHERE name = ?",
                    (name,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return _row_to_metadata(row)

    def delete(self, name: str) -> bool:
        """Remove a secret. Returns True if a row was deleted."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM secrets WHERE name = ?", (name,))
                return cur.rowcount > 0
            finally:
                conn.close()

    def list(self) -> list[dict]:
        """Return every secret's metadata (never the value). Ordered by name."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT name, description, tags, origin_pipeline, "
                    "created_at, updated_at FROM secrets ORDER BY name"
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_metadata(r) for r in rows]

    def usage(self, name: str) -> list[dict]:
        """Return the pipelines that have used this secret."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT pipeline_name, first_used_at, last_used_at "
                    "FROM secret_pipeline_link WHERE secret_name = ? "
                    "ORDER BY last_used_at DESC",
                    (name,),
                ).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]

    def audit(
        self,
        *,
        run_id:        Optional[str] = None,
        pipeline_name: Optional[str] = None,
        since:         Optional[str] = None,
        limit:         int           = 200,
    ) -> list[dict]:
        """Read the audit log. Never returns values."""
        q = ("SELECT ts, run_id, pipeline_name, agent_id, direction, "
             "secret_name, file, occurrences FROM secret_audit WHERE 1=1")
        args: list[Any] = []
        if run_id is not None:
            q += " AND run_id = ?";        args.append(run_id)
        if pipeline_name is not None:
            q += " AND pipeline_name = ?"; args.append(pipeline_name)
        if since is not None:
            q += " AND ts >= ?";           args.append(since)
        q += " ORDER BY id DESC LIMIT ?";  args.append(max(1, int(limit)))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(q, args).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]

    # ── Runtime operations ────────────────────────────────────────────────────

    def rehydrate(
        self,
        text: str,
        *,
        ctx: AuditCtx,
        direction: str = "outbound",
        event_log: Optional["EventLog"] = None,
    ) -> str:
        """
        Replace ``<<secret:NAME>>`` with the vault value. Audit every
        substitution by name.

        *direction* is ``"outbound"`` for prompt text going to Claude,
        ``"inbound"`` for response text being rehydrated before writing
        ``05_output.md``. Missing secrets are left intact and logged as
        ``"missing"``.
        """
        if direction not in {"outbound", "inbound"}:
            raise ValueError(
                f"rehydrate direction must be 'outbound' or 'inbound', "
                f"got {direction!r}"
            )
        if not text or "<<secret:" not in text:
            return text

        found:   dict[str, int] = {}
        missing: dict[str, int] = {}

        def replacer(match: re.Match) -> str:
            name = match.group(1)
            val = self.get(name)
            if val is None:
                missing[name] = missing.get(name, 0) + 1
                return match.group(0)
            found[name] = found.get(name, 0) + 1
            return val

        out = _SECRET_MARKER.sub(replacer, text)

        for name, n in found.items():
            self._audit_row(ctx, direction=direction,
                            secret_name=name, occurrences=n)
            self._touch_pipeline_link(name, ctx.pipeline_name)
            if event_log is not None and hasattr(event_log, "secret_substituted"):
                event_log.secret_substituted(
                    agent_id=ctx.agent_id,
                    direction=direction,
                    secret_name=name,
                    file=ctx.file,
                    occurrences=n,
                )

        for name, n in missing.items():
            self._audit_row(ctx, direction="missing",
                            secret_name=name, occurrences=n)
            if event_log is not None and hasattr(event_log, "secret_missing"):
                event_log.secret_missing(
                    agent_id=ctx.agent_id,
                    secret_name=name,
                    file=ctx.file,
                )

        return out

    def scan_leaks(
        self,
        text: str,
        *,
        ctx: AuditCtx,
        event_log: Optional["EventLog"] = None,
    ) -> list[str]:
        """
        Find raw secret values in *text* (typically a Claude response).
        Each match is logged as ``direction="leaked"`` and returned.

        Leak scanning is separate from rehydration because the point is
        to flag cases where the model ignored the ``<<secret:NAME>>``
        guardrail and echoed the literal value.
        """
        if not text:
            return []
        pairs = self._all_values_ordered_by_length_desc()
        leaked: list[str] = []
        for name, value in pairs:
            if not value:
                continue
            occurrences = text.count(value)
            if occurrences > 0:
                leaked.append(name)
                self._audit_row(
                    ctx, direction="leaked",
                    secret_name=name, occurrences=occurrences,
                )
                if event_log is not None and hasattr(event_log, "secret_leaked"):
                    event_log.secret_leaked(
                        agent_id=ctx.agent_id,
                        secret_name=name,
                        file=ctx.file or "response_raw",
                    )
        return leaked

    def redact_values(
        self,
        text: str,
        *,
        ctx: AuditCtx,
        event_log: Optional["EventLog"] = None,
    ) -> str:
        """
        Replace every known secret *value* with its placeholder
        ``<<secret:NAME>>``. Auxiliary operation — not part of the
        primary lifecycle, but useful for migration tooling and for
        producing a placeholder-only copy of a file.
        """
        if not text:
            return text
        pairs = self._all_values_ordered_by_length_desc()
        replaced: dict[str, int] = {}
        out = text
        for name, value in pairs:
            if not value:
                continue
            occurrences = out.count(value)
            if occurrences > 0:
                out = out.replace(value, f"<<secret:{name}>>")
                replaced[name] = occurrences
        for name, n in replaced.items():
            self._audit_row(ctx, direction="outbound",
                            secret_name=name, occurrences=n)
            self._touch_pipeline_link(name, ctx.pipeline_name)
            if event_log is not None and hasattr(event_log, "secret_substituted"):
                event_log.secret_substituted(
                    agent_id=ctx.agent_id, direction="outbound",
                    secret_name=name, file=ctx.file, occurrences=n,
                )
        return out

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _audit_row(
        self,
        ctx: AuditCtx,
        *,
        direction: str,
        secret_name: str,
        occurrences: int = 1,
    ) -> None:
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"Unknown audit direction: {direction!r}")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO secret_audit(ts, run_id, pipeline_name, "
                    "agent_id, direction, secret_name, file, occurrences) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (_now_iso(), ctx.run_id, ctx.pipeline_name,
                     ctx.agent_id, direction, secret_name,
                     ctx.file, int(occurrences)),
                )
            finally:
                conn.close()

    def _touch_pipeline_link(self, secret_name: str, pipeline_name: str) -> None:
        if not pipeline_name:
            return
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT first_used_at FROM secret_pipeline_link "
                    "WHERE secret_name = ? AND pipeline_name = ?",
                    (secret_name, pipeline_name),
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO secret_pipeline_link(secret_name, "
                        "pipeline_name, first_used_at, last_used_at) "
                        "VALUES (?, ?, ?, ?)",
                        (secret_name, pipeline_name, now, now),
                    )
                else:
                    conn.execute(
                        "UPDATE secret_pipeline_link SET last_used_at = ? "
                        "WHERE secret_name = ? AND pipeline_name = ?",
                        (now, secret_name, pipeline_name),
                    )
            finally:
                conn.close()

    def _all_values_ordered_by_length_desc(self) -> list[tuple[str, str]]:
        """Load (name, value) pairs, longest-value first (avoids prefix collisions)."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT name, value FROM secrets").fetchall()
            finally:
                conn.close()
        pairs = [(r["name"], r["value"]) for r in rows if r["value"]]
        pairs.sort(key=lambda p: -len(p[1]))
        return pairs


# ── Module helpers ────────────────────────────────────────────────────────────

def _row_to_metadata(row: sqlite3.Row) -> dict:
    d = dict(row)
    raw_tags = d.get("tags") or "[]"
    try:
        d["tags"] = json.loads(raw_tags)
    except (TypeError, json.JSONDecodeError):
        d["tags"] = []
    return d


def has_any_marker(text: str) -> bool:
    """Quick check for ``<<secret:...>>`` occurrences."""
    return bool(text) and "<<secret:" in text


def extract_markers(text: str) -> list[str]:
    """Return every ``<<secret:NAME>>`` occurrence in *text* (with repeats)."""
    if not text:
        return []
    return [m.group(1) for m in _SECRET_MARKER.finditer(text)]


def scan_pipeline_for_markers(
    pipeline_dir: Path,
    *,
    files: tuple[str, ...] = ("01_system.md", "02_prompt.md"),
) -> dict[str, list[dict]]:
    """
    Walk *pipeline_dir*/pipeline.json and return a mapping
    ``name → [{agent, file}, ...]`` of every ``<<secret:NAME>>`` marker
    found in each agent's source prompts.

    Used by ``cli.py vault check`` and by tests. The vault is *not*
    consulted — this is a pure scan.
    """
    from .core import resolve_agent_dir  # local import avoids circulars

    pipeline_dir = Path(pipeline_dir)
    pj = pipeline_dir / "pipeline.json"
    if not pj.exists():
        raise FileNotFoundError(f"No pipeline.json at {pj}")
    spec = json.loads(pj.read_text(encoding="utf-8"))

    refs: dict[str, list[dict]] = {}
    for agent in spec.get("agents", []):
        aid  = agent.get("id", "?")
        adir = resolve_agent_dir(pipeline_dir, agent)
        for fname in files:
            fp = adir / fname
            if not fp.exists():
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for name in extract_markers(text):
                refs.setdefault(name, []).append({
                    "agent": aid, "file": fname,
                })
    return refs


# ── Cached-by-path factory (thread-safe, shared across agent threads) ─────────

_vault_cache: dict[str, "Vault"] = {}
_vault_cache_lock = threading.Lock()


def open_vault_for(config: Any, pipeline_dir: Path) -> "Vault":
    """
    Return a shared ``Vault`` instance for this pipeline.

    Caches by resolved DB path so every agent thread in the same process
    operates through one lock. Safe to call repeatedly.
    """
    explicit = getattr(config, "vault_db_path", None)
    path = resolve_vault_path(Path(pipeline_dir), explicit)
    key = str(path.resolve())
    with _vault_cache_lock:
        v = _vault_cache.get(key)
        if v is None:
            v = Vault(path)
            _vault_cache[key] = v
        return v
