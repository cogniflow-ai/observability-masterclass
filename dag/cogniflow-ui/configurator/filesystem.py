"""Cogniflow Configurator — filesystem layer.

All reads and writes go through here. No other module touches the disk directly.
"""
from __future__ import annotations
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from filelock import FileLock
    def _lock(path: Path):
        return FileLock(str(path) + ".lock", timeout=5)
except ImportError:
    import threading
    _path_locks: dict[str, threading.Lock] = {}
    _registry_lock = threading.Lock()

    class _PathLock:
        def __init__(self, key: str):
            with _registry_lock:
                self._lock = _path_locks.setdefault(key, threading.Lock())
        def __enter__(self):
            self._lock.acquire()
            return self
        def __exit__(self, *_):
            self._lock.release()

    def _lock(path: Path):
        return _PathLock(str(path))

from .config import settings
from . import versioning as ver


# ── Atomic write ──────────────────────────────────────────────────────────────

def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _lock(path):
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)


def atomic_write_json(path: Path, data: dict):
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))


# ── Pipeline listing ──────────────────────────────────────────────────────────

def list_pipelines() -> list[dict]:
    root = settings.pipelines_root
    if not root.exists():
        return []
    result = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if d.name.startswith(".") or d.name == "templates":
            continue
        pj = d / "pipeline.json"
        if not pj.exists():
            continue
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        result.append({
            "name": d.name,
            "display_name": data.get("name", d.name),
            "graph_mode": data.get("graph_mode", "dag"),
            "description": data.get("description", ""),
            "category": data.get("category", ""),
            "labels": data.get("labels", []),
            "agent_count": len(data.get("agents", [])),
            "running": _is_running(d),
            "path": str(d),
        })
    return result


def _is_running(pipeline_dir: Path) -> bool:
    sf = pipeline_dir / ".state" / "pipeline_status.json"
    if not sf.exists():
        return False
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
        return data.get("status") in ("running", "approved")
    except Exception:
        return False


# ── Pipeline CRUD ─────────────────────────────────────────────────────────────

def get_pipeline(name: str) -> dict | None:
    d = settings.pipelines_root / name
    pj = d / "pipeline.json"
    if not pj.exists():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except Exception:
        return None
    data["_dir"] = str(d)
    data["_running"] = _is_running(d)
    return data


def create_pipeline(name: str, display_name: str, description: str,
                    graph_mode: str, category: str,
                    template_name: str | None) -> dict:
    d = settings.pipelines_root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "agents").mkdir(exist_ok=True)

    data: dict[str, Any] = {
        "name": display_name or name,
        "description": description,
        "graph_mode": graph_mode,
        "graph_orientation": "vertical",
        "category": category,
        "labels": [],
        "agents": [],
        "edges": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if template_name:
        data["template_name"] = template_name
        _apply_template(d, template_name, data)
    else:
        atomic_write_json(d / "pipeline.json", data)
        ver.save_version(d, "pipeline.json",
                         json.dumps(data, indent=2), message="Initial")

    # Seed tagline defaults so the tag-chip UI never starts empty. Only fill
    # when the template (or base data) didn't already set them — templates
    # with their own curated taglines still win.
    pj = d / "pipeline.json"
    try:
        cur = json.loads(pj.read_text(encoding="utf-8"))
    except Exception:
        cur = None
    if isinstance(cur, dict):
        changed = False
        if not cur.get("validated_taglines_system"):
            cur["validated_taglines_system"] = list(settings.default_taglines_system)
            changed = True
        if not cur.get("validated_taglines_task"):
            cur["validated_taglines_task"] = list(settings.default_taglines_task)
            changed = True
        if changed:
            atomic_write_json(pj, cur)
            ver.save_version(d, "pipeline.json",
                             json.dumps(cur, indent=2),
                             message="Seed default taglines")
            data = cur
    return data


def _apply_template(pipeline_dir: Path, template_name: str, base_data: dict):
    tdir = settings.templates_dir / template_name
    if not tdir.exists():
        atomic_write_json(pipeline_dir / "pipeline.json", base_data)
        return
    # Copy template files
    for item in tdir.rglob("*"):
        if item.name == "template.json":
            continue
        rel = item.relative_to(tdir)
        dest = pipeline_dir / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
    # Merge pipeline.json from template with user data
    tpj = tdir / "pipeline.json"
    if tpj.exists():
        try:
            tdata = json.loads(tpj.read_text())
            tdata.update({k: v for k, v in base_data.items() if v})
            tdata["updated_at"] = datetime.now(timezone.utc).isoformat()
            atomic_write_json(pipeline_dir / "pipeline.json", tdata)
            ver.save_version(pipeline_dir, "pipeline.json",
                             json.dumps(tdata, indent=2), message="From template")
        except Exception:
            atomic_write_json(pipeline_dir / "pipeline.json", base_data)
    else:
        atomic_write_json(pipeline_dir / "pipeline.json", base_data)


def delete_pipeline(name: str, trash: bool = True) -> bool:
    d = settings.pipelines_root / name
    if not d.exists():
        return False
    if trash:
        td = settings.cfg_trash_dir
        td.mkdir(parents=True, exist_ok=True)
        dest = td / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(d), str(dest))
    else:
        shutil.rmtree(d)
    return True


# ── pipeline.json read/write ──────────────────────────────────────────────────

def read_pipeline_json(name: str) -> str | None:
    pj = settings.pipelines_root / name / "pipeline.json"
    if not pj.exists():
        return None
    return pj.read_text(encoding="utf-8")


def write_pipeline_json(name: str, content: str, message: str = "") -> bool:
    d = settings.pipelines_root / name
    pj = d / "pipeline.json"
    data = json.loads(content)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    new_content = json.dumps(data, indent=2, ensure_ascii=False)
    if pj.exists():
        ver.save_version(d, "pipeline.json",
                         pj.read_text(encoding="utf-8"), message or "Updated")
    atomic_write(pj, new_content)
    return True


# ── Agent CRUD ────────────────────────────────────────────────────────────────

def add_agent(pipeline_name: str, agent: dict) -> tuple[bool, str]:
    """Add an agent to the pipeline and provision its workspace with verbatim
    copies of the per-type templates. Prompt specialization is an explicit,
    post-creation action — see `specialize_agent`."""
    d = settings.pipelines_root / pipeline_name
    pj = d / "pipeline.json"
    if not pj.exists():
        return False, "pipeline.json not found"
    data = json.loads(pj.read_text(encoding="utf-8"))
    ids = {a["id"] for a in data.get("agents", []) if isinstance(a, dict)}
    if agent["id"] in ids:
        return False, f"Agent id '{agent['id']}' already exists"
    data.setdefault("agents", []).append(agent)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    old = pj.read_text(encoding="utf-8")
    ver.save_version(d, "pipeline.json", old, f"Add agent {agent['id']}")
    atomic_write_json(pj, data)

    agent_dir = d / "agents" / agent["id"]
    agent_dir.mkdir(parents=True, exist_ok=True)
    atype = agent.get("type") or "worker"
    for prompt in ("01_system.md", "02_prompt.md"):
        pf = agent_dir / prompt
        if pf.exists():
            continue
        seed = _read_prompt_template(atype, prompt)
        pf.write_text(seed, encoding="utf-8")
    return True, "ok"


def _specialize_agent_prompts(pipeline_dir: Path, agent: dict,
                              atype: str) -> tuple[bool, dict]:
    """Run meta-prompt specialization for a newly-created agent.

    On success: overwrites 01_system.md / 02_prompt.md with tailored content,
    versioned. Returns (True, {}).

    On failure: leaves the verbatim copies in place and returns
    (False, {'stage', 'message', 'raw_response'}).
    """
    from . import meta_specialize as ms
    sys_tpl  = _read_prompt_template(atype, "01_system.md")
    task_tpl = _read_prompt_template(atype, "02_prompt.md")
    if not sys_tpl.strip() or not task_tpl.strip():
        return False, {
            "stage": "cli",
            "message": f"Type '{atype}' has no template pair under "
                       f"prompt_templates/{atype}/.",
            "raw_response": "",
        }
    type_desc = _read_type_description(atype)
    if not type_desc.strip():
        return False, {
            "stage": "cli",
            "message": f"Type '{atype}' has no description.md — required "
                       f"for specialization. Add one on the Prompt Templates page.",
            "raw_response": "",
        }
    meta_sys  = read_meta_prompt("01_system.md")
    meta_task = read_meta_prompt("02_prompt.md")

    try:
        tailored_sys, tailored_task = ms.specialize(
            agent_name=atype,
            type_description=type_desc,
            instance_name=agent.get("name") or agent["id"],
            instance_description=agent.get("description", ""),
            system_template=sys_tpl,
            task_template=task_tpl,
            meta_system=meta_sys,
            meta_task=meta_task,
            claude_bin=settings.claude_bin,
            timeout_s=settings.meta_prompt_timeout_s,
        )
    except ms.SpecializeError as e:
        return False, {"stage": e.stage, "message": e.message,
                       "raw_response": e.raw_response}

    # Success — overwrite the verbatim seed, snapshotting the previous content.
    agent_dir = pipeline_dir / "agents" / agent["id"]
    for filename, content in (("01_system.md",  tailored_sys),
                              ("02_prompt.md", tailored_task)):
        pf = agent_dir / filename
        rel = f"agents/{agent['id']}/{filename}"
        if pf.exists():
            ver.save_version(pipeline_dir, rel,
                             pf.read_text(encoding="utf-8"),
                             "Pre-specialization seed")
        atomic_write(pf, content)
    return True, {}


def specialize_agent(pipeline_name: str, agent_id: str) -> tuple[bool, dict]:
    """Re-run specialization for an existing agent. Returns (ok, info) where
    info has the same shape as _specialize_agent_prompts' second element."""
    d = settings.pipelines_root / pipeline_name
    agent = None
    data = get_pipeline(pipeline_name)
    for a in (data or {}).get("agents", []):
        if isinstance(a, dict) and a.get("id") == agent_id:
            agent = a
            break
    if agent is None:
        return False, {"stage": "cli",
                       "message": f"Agent '{agent_id}' not found.",
                       "raw_response": ""}
    atype = agent.get("type") or "worker"
    return _specialize_agent_prompts(d, agent, atype)


def _read_prompt_template(agent_type: str, filename: str) -> str:
    path = settings.prompt_templates_dir / agent_type / filename
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


def update_agent(pipeline_name: str, agent_id: str, updates: dict) -> tuple[bool, str]:
    d = settings.pipelines_root / pipeline_name
    pj = d / "pipeline.json"
    if not pj.exists():
        return False, "pipeline.json not found"
    data = json.loads(pj.read_text(encoding="utf-8"))
    agents = data.get("agents", [])
    for i, a in enumerate(agents):
        if isinstance(a, dict) and a.get("id") == agent_id:
            agents[i].update(updates)
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            old = pj.read_text(encoding="utf-8")
            ver.save_version(d, "pipeline.json", old, f"Update agent {agent_id}")
            atomic_write_json(pj, data)
            return True, "ok"
    return False, f"Agent '{agent_id}' not found"


def remove_agent(pipeline_name: str, agent_id: str) -> tuple[bool, str]:
    d = settings.pipelines_root / pipeline_name
    pj = d / "pipeline.json"
    if not pj.exists():
        return False, "pipeline.json not found"
    data = json.loads(pj.read_text(encoding="utf-8"))
    before = len(data.get("agents", []))
    data["agents"] = [
        a for a in data.get("agents", [])
        if not (isinstance(a, dict) and a.get("id") == agent_id)
    ]
    # Remove from all depends_on
    for a in data.get("agents", []):
        if isinstance(a, dict) and agent_id in a.get("depends_on", []):
            a["depends_on"] = [x for x in a["depends_on"] if x != agent_id]
    # Remove from edges
    data["edges"] = [
        e for e in data.get("edges", [])
        if not (isinstance(e, dict) and
                (e.get("from") == agent_id or e.get("to") == agent_id))
    ]
    if len(data.get("agents", [])) == before:
        return False, f"Agent '{agent_id}' not found"
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    old = pj.read_text(encoding="utf-8")
    ver.save_version(d, "pipeline.json", old, f"Remove agent {agent_id}")
    atomic_write_json(pj, data)
    return True, "ok"


def get_agent(pipeline_name: str, agent_id: str) -> dict | None:
    data = get_pipeline(pipeline_name)
    if data is None:
        return None
    for a in data.get("agents", []):
        if isinstance(a, dict) and a.get("id") == agent_id:
            return a
    return None


# ── Edge CRUD ─────────────────────────────────────────────────────────────────

def add_edge(pipeline_name: str, edge: dict) -> tuple[bool, str]:
    d = settings.pipelines_root / pipeline_name
    pj = d / "pipeline.json"
    if not pj.exists():
        return False, "pipeline.json not found"
    data = json.loads(pj.read_text(encoding="utf-8"))
    if data.get("graph_mode") != "cyclic":
        return False, "Edges are only supported in cyclic mode"
    edges = data.setdefault("edges", [])
    key = (edge.get("from"), edge.get("to"), edge.get("type"))
    for e in edges:
        if (e.get("from"), e.get("to"), e.get("type")) == key:
            return False, "Duplicate edge already exists"
    edges.append(edge)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    old = pj.read_text(encoding="utf-8")
    ver.save_version(d, "pipeline.json", old, f"Add edge {edge}")
    atomic_write_json(pj, data)
    return True, "ok"


def remove_edge(pipeline_name: str, from_id: str,
                to_id: str, etype: str) -> tuple[bool, str]:
    d = settings.pipelines_root / pipeline_name
    pj = d / "pipeline.json"
    if not pj.exists():
        return False, "pipeline.json not found"
    data = json.loads(pj.read_text(encoding="utf-8"))
    before = len(data.get("edges", []))
    data["edges"] = [
        e for e in data.get("edges", [])
        if not (isinstance(e, dict) and
                e.get("from") == from_id and
                e.get("to") == to_id and
                e.get("type") == etype)
    ]
    if len(data.get("edges", [])) == before:
        return False, "Edge not found"
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    old = pj.read_text(encoding="utf-8")
    ver.save_version(d, "pipeline.json", old, f"Remove edge {from_id}→{to_id}")
    atomic_write_json(pj, data)
    return True, "ok"


# ── Prompt files ──────────────────────────────────────────────────────────────

def read_prompt(pipeline_name: str, agent_id: str, filename: str) -> str:
    pf = settings.pipelines_root / pipeline_name / "agents" / agent_id / filename
    if pf.exists():
        return pf.read_text(encoding="utf-8")
    return ""


def write_prompt(pipeline_name: str, agent_id: str,
                 filename: str, content: str, message: str = "") -> bool:
    d = settings.pipelines_root / pipeline_name
    pf = d / "agents" / agent_id / filename
    rel = f"agents/{agent_id}/{filename}"
    if pf.exists():
        ver.save_version(d, rel, pf.read_text(encoding="utf-8"), message or "Updated")
    pf.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(pf, content)
    return True


# ── Generic file browser ──────────────────────────────────────────────────────

EXCLUDED_DIRS = {".state", ".configurator"}
TEXT_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".py",
    ".sh", ".env", ".ini", ".cfg", ".toml", ".xml", ".html",
}


def list_files(pipeline_name: str) -> list[dict]:
    d = settings.pipelines_root / pipeline_name
    return _walk_dir(d, d)


def _walk_dir(base: Path, current: Path) -> list[dict]:
    result = []
    try:
        items = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name))
    except PermissionError:
        return result
    for item in items:
        if item.name.startswith(".") and item.name in {".state", ".configurator",
                                                        ".command.json"}:
            continue
        rel = str(item.relative_to(base))
        if item.is_dir():
            result.append({
                "name": item.name, "path": rel,
                "type": "dir", "children": _walk_dir(base, item),
            })
        else:
            is_text = item.suffix.lower() in TEXT_EXTENSIONS
            result.append({
                "name": item.name, "path": rel,
                "type": "file", "is_text": is_text,
                "size": item.stat().st_size,
            })
    return result


def read_file(pipeline_name: str, rel_path: str) -> str | None:
    fp = settings.pipelines_root / pipeline_name / rel_path
    if not fp.exists() or fp.is_dir():
        return None
    try:
        return fp.read_text(encoding="utf-8")
    except Exception:
        return None


def write_file(pipeline_name: str, rel_path: str,
               content: str, message: str = "") -> bool:
    d = settings.pipelines_root / pipeline_name
    fp = d / rel_path
    if fp.exists():
        old = read_file(pipeline_name, rel_path)
        if old is not None:
            ver.save_version(d, rel_path, old, message or "Updated")
    atomic_write(fp, content)
    return True


def create_file_or_dir(pipeline_name: str, rel_path: str,
                       is_dir: bool = False) -> bool:
    fp = settings.pipelines_root / pipeline_name / rel_path
    if is_dir:
        fp.mkdir(parents=True, exist_ok=True)
    else:
        fp.parent.mkdir(parents=True, exist_ok=True)
        if not fp.exists():
            fp.write_text("", encoding="utf-8")
    return True


def delete_file(pipeline_name: str, rel_path: str) -> bool:
    fp = settings.pipelines_root / pipeline_name / rel_path
    if not fp.exists():
        return False
    if fp.is_dir():
        shutil.rmtree(fp)
    else:
        fp.unlink()
    return True


# ── Labels ────────────────────────────────────────────────────────────────────

def _labels_path(pipeline_name: str) -> Path:
    return (settings.pipelines_root / pipeline_name /
            ".configurator" / "labels.json")


def get_labels(pipeline_name: str) -> dict:
    lp = _labels_path(pipeline_name)
    if lp.exists():
        try:
            return json.loads(lp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"catalogue": [], "assignments": {}}


def save_labels(pipeline_name: str, labels_data: dict):
    lp = _labels_path(pipeline_name)
    lp.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(lp, labels_data)


def add_label_to_catalogue(pipeline_name: str, label: str):
    ld = get_labels(pipeline_name)
    if label not in ld["catalogue"]:
        ld["catalogue"].append(label)
        save_labels(pipeline_name, ld)


# ── Templates ─────────────────────────────────────────────────────────────────

def list_templates() -> list[dict]:
    td = settings.templates_dir
    if not td.exists():
        return []
    result = []
    for d in sorted(td.iterdir()):
        if not d.is_dir():
            continue
        tj = d / "template.json"
        if tj.exists():
            try:
                meta = json.loads(tj.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        else:
            meta = {}
        result.append({
            "name": d.name,
            "display_name": meta.get("name", d.name),
            "description": meta.get("description", ""),
            "graph_mode": meta.get("graph_mode", "dag"),
            "tags": meta.get("tags", []),
            "author": meta.get("author", ""),
        })
    return result


def save_as_template(pipeline_name: str, tname: str,
                     description: str, tags: list[str], author: str) -> bool:
    src = settings.pipelines_root / pipeline_name
    dest = settings.templates_dir / tname
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    # Copy everything except .state, .configurator, .command.json
    for item in src.iterdir():
        if item.name in (".state", ".configurator") or \
                item.name.startswith(".command"):
            continue
        if item.is_dir():
            shutil.copytree(item, dest / item.name)
        else:
            shutil.copy2(item, dest / item.name)
    # Write template.json
    meta = {
        "name": tname,
        "description": description,
        "tags": tags,
        "author": author,
        "graph_mode": get_pipeline(pipeline_name).get("graph_mode", "dag"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(dest / "template.json", meta)
    return True


# ── Prompt templates (per agent-type starter prompts) ─────────────────────────

PROMPT_TEMPLATE_FILES = ("01_system.md", "02_prompt.md")


def list_prompt_templates() -> list[dict]:
    """Return one entry per agent-type directory under prompt_templates_dir.
    The reserved 'meta' directory is excluded — it holds the specialization
    meta-prompts, not an agent type."""
    root = settings.prompt_templates_dir
    if not root.exists():
        return []
    result = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name == META_DIR_NAME:
            continue
        files = []
        for fname in PROMPT_TEMPLATE_FILES:
            fp = d / fname
            files.append({
                "name": fname,
                "exists": fp.exists(),
                "size": fp.stat().st_size if fp.exists() else 0,
            })
        result.append({
            "type": d.name,
            "files": files,
        })
    return result


def read_prompt_template(agent_type: str, filename: str) -> str:
    if filename not in PROMPT_TEMPLATE_FILES:
        return ""
    fp = settings.prompt_templates_dir / agent_type / filename
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    return ""


def write_prompt_template(agent_type: str, filename: str,
                          content: str, message: str = "") -> tuple[bool, str]:
    if filename not in PROMPT_TEMPLATE_FILES:
        return False, f"Invalid filename: {filename}"
    root = settings.prompt_templates_dir
    rel = f"{agent_type}/{filename}"
    fp = root / rel
    if fp.exists():
        try:
            ver.save_version(root, rel, fp.read_text(encoding="utf-8"),
                             message or "Updated")
        except Exception:
            pass
    fp.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(fp, content)
    return True, "ok"


def create_prompt_template_type(agent_type: str) -> tuple[bool, str]:
    import re
    if not re.match(r"^[a-z0-9_]+$", agent_type):
        return False, "Type name must be lowercase alphanumeric/underscore"
    if agent_type == META_DIR_NAME:
        return False, f"'{META_DIR_NAME}' is reserved."
    d = settings.prompt_templates_dir / agent_type
    if d.exists():
        return False, f"Type '{agent_type}' already exists"
    d.mkdir(parents=True, exist_ok=True)
    for fname in PROMPT_TEMPLATE_FILES:
        (d / fname).write_text("", encoding="utf-8")
    (d / DESCRIPTION_FILE).write_text("", encoding="utf-8")
    return True, "ok"


def delete_prompt_template_type(agent_type: str) -> bool:
    d = settings.prompt_templates_dir / agent_type
    if not d.exists() or not d.is_dir():
        return False
    shutil.rmtree(d)
    return True


# ── Meta prompts (for instance specialization) ────────────────────────────────

META_DIR_NAME = "_meta"
META_FILES = ("01_system.md", "02_prompt.md")


def read_meta_prompt(filename: str) -> str:
    if filename not in META_FILES:
        return ""
    fp = settings.prompt_templates_dir / META_DIR_NAME / filename
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    return ""


def write_meta_prompt(filename: str, content: str,
                      message: str = "") -> tuple[bool, str]:
    if filename not in META_FILES:
        return False, f"Invalid meta prompt filename: {filename}"
    root = settings.prompt_templates_dir
    rel = f"{META_DIR_NAME}/{filename}"
    fp = root / rel
    if fp.exists():
        try:
            ver.save_version(root, rel, fp.read_text(encoding="utf-8"),
                             message or "Updated")
        except Exception:
            pass
    fp.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(fp, content)
    return True, "ok"


# ── Per-type description (TYPE_DESCRIPTION input to the meta-prompt) ─────────

DESCRIPTION_FILE = "description.md"


def _read_type_description(agent_type: str) -> str:
    fp = settings.prompt_templates_dir / agent_type / DESCRIPTION_FILE
    if fp.exists():
        try:
            return fp.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


def read_type_description(agent_type: str) -> str:
    return _read_type_description(agent_type)


def write_type_description(agent_type: str, content: str,
                           message: str = "") -> tuple[bool, str]:
    root = settings.prompt_templates_dir
    type_dir = root / agent_type
    if not type_dir.exists() or not type_dir.is_dir():
        return False, f"Type '{agent_type}' does not exist."
    rel = f"{agent_type}/{DESCRIPTION_FILE}"
    fp = root / rel
    if fp.exists():
        try:
            ver.save_version(root, rel, fp.read_text(encoding="utf-8"),
                             message or "Updated")
        except Exception:
            pass
    atomic_write(fp, content)
    return True, "ok"
