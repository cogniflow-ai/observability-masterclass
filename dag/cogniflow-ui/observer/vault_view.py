"""
Cogniflow Observer — read-only vault viewer.

The Configurator owns CRUD on `pipelines/secrets.db`. The Observer reads
metadata only — the SQL never selects the `value` column. Any column that
accidentally looks like a value is replaced with `<redacted>` before
leaving the module.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .config import settings


REDACTED = "<redacted>"
_VALUE_KEYS = {"value", "secret", "secret_value"}


def vault_path() -> Path:
    """Standard course layout: <orchestrator_root>/pipelines/secrets.db."""
    return settings.pipelines_root / "secrets.db"


def vault_exists() -> bool:
    return vault_path().exists()


def _connect() -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(
            f"file:{vault_path()}?mode=ro", uri=True, timeout=2.0,
        )
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _row_to_safe_dict(row: sqlite3.Row) -> dict:
    """Drop value-shaped columns; deserialize tags JSON."""
    d = dict(row)
    for k in list(d.keys()):
        if k in _VALUE_KEYS:
            d[k] = REDACTED
    if "tags" in d:
        try:
            d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        except (TypeError, json.JSONDecodeError):
            d["tags"] = []
    return d


def list_secrets() -> list[dict]:
    """Return every secret's metadata. Never includes the value column."""
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT name, description, tags, origin_pipeline, "
            "created_at, updated_at FROM secrets ORDER BY name"
        ).fetchall()
        out = [_row_to_safe_dict(r) for r in rows]
        # Pre-compute used_in count for the listing.
        for s in out:
            cur = conn.execute(
                "SELECT COUNT(*) FROM secret_pipeline_link WHERE secret_name = ?",
                (s["name"],),
            ).fetchone()
            s["used_in_count"] = int(cur[0]) if cur else 0
        return out
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def get_secret_meta(name: str) -> Optional[dict]:
    """One secret's metadata + the pipelines that have used it. No value."""
    conn = _connect()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT name, description, tags, origin_pipeline, "
            "created_at, updated_at FROM secrets WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        meta = _row_to_safe_dict(row)
        # Usage is best-effort. If secret_pipeline_link is missing or has a
        # column mismatch, still return the secret rather than 404-ing the
        # whole detail page — that would mask the real schema problem.
        try:
            usage = conn.execute(
                "SELECT pipeline_name, first_used_at, last_used_at "
                "FROM secret_pipeline_link WHERE secret_name = ? "
                "ORDER BY last_used_at DESC",
                (name,),
            ).fetchall()
            meta["used_in"] = [dict(r) for r in usage]
        except sqlite3.Error:
            meta["used_in"] = []
        return meta
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def audit_for_pipeline(
    pipeline_name: str,
    *,
    run_id: Optional[str] = None,
    direction: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    """Audit rows scoped to a pipeline (and optionally a single run).

    Names only — no values anywhere in the audit table. Most-recent first.
    """
    conn = _connect()
    if conn is None:
        return []
    q = ("SELECT ts, run_id, pipeline_name, agent_id, direction, "
         "secret_name, file, occurrences FROM secret_audit "
         "WHERE pipeline_name = ?")
    args: list[Any] = [pipeline_name]
    if run_id:
        q += " AND run_id = ?"; args.append(run_id)
    if direction:
        q += " AND direction = ?"; args.append(direction)
    if agent_id:
        q += " AND agent_id = ?"; args.append(agent_id)
    q += " ORDER BY id DESC LIMIT ?"; args.append(max(1, int(limit)))
    try:
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def audit_run_summary(pipeline_name: str, run_id: str) -> dict:
    """{'outbound': N, 'inbound': N, 'missing': N, 'leaked': N} for a run."""
    summary = {"outbound": 0, "inbound": 0, "missing": 0, "leaked": 0}
    conn = _connect()
    if conn is None:
        return summary
    try:
        rows = conn.execute(
            "SELECT direction, SUM(occurrences) AS total FROM secret_audit "
            "WHERE pipeline_name = ? AND run_id = ? GROUP BY direction",
            (pipeline_name, run_id),
        ).fetchall()
        for r in rows:
            if r["direction"] in summary:
                summary[r["direction"]] = int(r["total"] or 0)
        return summary
    except sqlite3.Error:
        return summary
    finally:
        conn.close()


def list_runs_with_audit(pipeline_name: str, limit: int = 30) -> list[str]:
    """Distinct run_ids that have at least one audit row, newest first."""
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT run_id, MAX(ts) AS last_ts FROM secret_audit "
            "WHERE pipeline_name = ? AND run_id != '' "
            "GROUP BY run_id ORDER BY last_ts DESC LIMIT ?",
            (pipeline_name, max(1, int(limit))),
        ).fetchall()
        return [r["run_id"] for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
