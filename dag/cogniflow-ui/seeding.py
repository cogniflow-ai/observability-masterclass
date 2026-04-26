"""
Cogniflow UI — pipeline seeding.

On startup the UI ships a curated set of "seed" pipelines under
`seed_pipelines/`. We overlay them onto the orchestrator's `pipelines_root`
so students always have the bundled examples available.

Behaviour:
  * If the `.ui-seed-marker` file in pipelines_root is missing, OR records a
    different `app_version`, we re-seed.
  * Re-seeding copies each `seed_pipelines/<name>/` into
    `pipelines_root/<name>/`, OVERWRITING files inside seed pipelines but
    leaving every other (user-created or lab-added) pipeline untouched.
  * Non-seed files inside a seeded pipeline (runtime artefacts: events,
    history, .state/, .configurator/, agent outputs) are NOT deleted —
    they persist across re-seeds.
  * After a successful pass, the marker is updated. Same-version restarts
    are a no-op so user edits to bundled pipelines persist between runs;
    edits are only refreshed when you ship a new UI version.

The marker also records the list of pipelines that were last seeded, so we
can detect (and log) a bundled pipeline that was deleted by the user.
"""
from __future__ import annotations
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

MARKER_FILENAME = ".ui-seed-marker.json"


def _read_marker(pipelines_root: Path) -> dict:
    p = pipelines_root / MARKER_FILENAME
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_marker(pipelines_root: Path, payload: dict) -> None:
    p = pipelines_root / MARKER_FILENAME
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)


def _seed_one(seed_dir: Path, dest_dir: Path) -> int:
    """Copy `seed_dir` into `dest_dir`, overwriting same-named files.
    Files already present in `dest_dir` that are NOT in `seed_dir` are kept
    (they may be runtime artefacts produced by the orchestrator)."""
    files_written = 0
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in seed_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(seed_dir)
        dst = dest_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        files_written += 1
    return files_written


def seed_pipelines(
    seed_root: Path,
    pipelines_root: Path,
    app_version: str,
    *,
    force: bool = False,
) -> dict:
    """Overlay bundled seeds onto pipelines_root.

    Returns a summary dict suitable for logging:
        {
            "skipped": bool,        # True if version matched and force=False
            "version_before": str,
            "version_after": str,
            "seeded": [pipeline_name, ...],
            "files_written": int,
        }
    """
    pipelines_root.mkdir(parents=True, exist_ok=True)
    marker = _read_marker(pipelines_root)
    prev_version = marker.get("app_version", "")

    if not force and prev_version == app_version:
        return {
            "skipped": True,
            "version_before": prev_version,
            "version_after": app_version,
            "seeded": [],
            "files_written": 0,
        }

    if not seed_root.exists():
        # Nothing to seed (e.g. running from source without a seed_pipelines
        # dir, or the bundle is broken). Don't touch the marker — let the
        # next launch try again.
        return {
            "skipped": True,
            "version_before": prev_version,
            "version_after": prev_version,
            "seeded": [],
            "files_written": 0,
            "reason": f"seed_root does not exist: {seed_root}",
        }

    seeded: list[str] = []
    files_written = 0
    for sd in sorted(seed_root.iterdir()):
        if not sd.is_dir() or sd.name.startswith((".", "_")):
            continue
        if not (sd / "pipeline.json").exists():
            continue
        files_written += _seed_one(sd, pipelines_root / sd.name)
        seeded.append(sd.name)

    _write_marker(pipelines_root, {
        "app_version":    app_version,
        "last_seeded_at": datetime.now(timezone.utc).isoformat(),
        "seeded":         seeded,
    })

    return {
        "skipped": False,
        "version_before": prev_version,
        "version_after": app_version,
        "seeded": seeded,
        "files_written": files_written,
    }
