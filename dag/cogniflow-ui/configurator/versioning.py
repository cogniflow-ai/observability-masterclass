"""Cogniflow Configurator — versioning system.

Every save to a managed file creates a numbered snapshot in
.configurator/history/<relative_path>.vN
and updates .configurator/versions.json.
"""
from __future__ import annotations
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import settings


def _cfg_dir(pipeline_dir: Path) -> Path:
    d = pipeline_dir / ".configurator"
    d.mkdir(exist_ok=True)
    return d


def _history_dir(pipeline_dir: Path) -> Path:
    d = _cfg_dir(pipeline_dir) / "history"
    d.mkdir(exist_ok=True)
    return d


def _versions_path(pipeline_dir: Path) -> Path:
    return _cfg_dir(pipeline_dir) / "versions.json"


def _load_manifest(pipeline_dir: Path) -> dict:
    vp = _versions_path(pipeline_dir)
    if vp.exists():
        try:
            return json.loads(vp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"files": {}}


def _save_manifest(pipeline_dir: Path, manifest: dict):
    vp = _versions_path(pipeline_dir)
    tmp = vp.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tmp.replace(vp)


def save_version(pipeline_dir: Path, rel_path: str,
                 content: str, message: str = "") -> int:
    """
    Create a snapshot of rel_path before overwriting.
    Returns the new version number.
    """
    manifest = _load_manifest(pipeline_dir)
    files = manifest.setdefault("files", {})
    history = files.setdefault(rel_path, [])

    next_version = (history[-1]["version"] + 1) if history else 1
    if next_version > settings.cfg_max_versions:
        # prune oldest non-tagged
        for i, entry in enumerate(history):
            if not entry.get("tag"):
                # delete snapshot file
                snap = _snapshot_path(pipeline_dir, rel_path, entry["version"])
                snap.unlink(missing_ok=True)
                history.pop(i)
                break

    # Write snapshot
    snap = _snapshot_path(pipeline_dir, rel_path, next_version)
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(content, encoding="utf-8")

    history.append({
        "version": next_version,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "message": message or "",
        "tag": None,
    })
    _save_manifest(pipeline_dir, manifest)
    return next_version


def list_versions(pipeline_dir: Path, rel_path: str) -> list[dict]:
    manifest = _load_manifest(pipeline_dir)
    return list(reversed(manifest.get("files", {}).get(rel_path, [])))


def get_version_content(pipeline_dir: Path, rel_path: str, version: int) -> str | None:
    snap = _snapshot_path(pipeline_dir, rel_path, version)
    if snap.exists():
        return snap.read_text(encoding="utf-8")
    return None


def restore_version(pipeline_dir: Path, rel_path: str,
                    version: int, message: str = "") -> bool:
    """Restore a historical version by creating a new save."""
    content = get_version_content(pipeline_dir, rel_path, version)
    if content is None:
        return False
    target = pipeline_dir / rel_path
    # Save current as new version first
    if target.exists():
        save_version(pipeline_dir, rel_path,
                     target.read_text(encoding="utf-8"),
                     message=f"Auto-save before restore to v{version}")
    # Write restored content
    _atomic_write(target, content)
    save_version(pipeline_dir, rel_path, content,
                 message=message or f"Restored from v{version}")
    return True


def delete_version(pipeline_dir: Path, rel_path: str, version: int) -> bool:
    manifest = _load_manifest(pipeline_dir)
    files = manifest.get("files", {})
    history = files.get(rel_path, [])
    for i, entry in enumerate(history):
        if entry["version"] == version:
            snap = _snapshot_path(pipeline_dir, rel_path, version)
            snap.unlink(missing_ok=True)
            history.pop(i)
            _save_manifest(pipeline_dir, manifest)
            return True
    return False


def tag_version(pipeline_dir: Path, rel_path: str,
                version: int, tag: str) -> bool:
    manifest = _load_manifest(pipeline_dir)
    for entry in manifest.get("files", {}).get(rel_path, []):
        if entry["version"] == version:
            entry["tag"] = tag
            _save_manifest(pipeline_dir, manifest)
            return True
    return False


def _snapshot_path(pipeline_dir: Path, rel_path: str, version: int) -> Path:
    safe = rel_path.replace("/", "__").replace("\\", "__")
    return _history_dir(pipeline_dir) / f"{safe}.v{version}"


def _atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
