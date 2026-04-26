"""Cogniflow Configurator — FastAPI router.

Exposes `router` (APIRouter) and `templates` so the parent app
(dag/cogniflow-ui/app.py) can mount it alongside the observer.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import filesystem as fs
from . import versioning as ver
from .config import settings
from .dag_svg import build_svg
from .validation import (
    validate_pipeline, check_running,
    validate_prompt_taglines, _required_taglines, _kind_for_filename,
    global_default_taglines,
)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()


def _t(request: Request, name: str, ctx: dict) -> HTMLResponse:
    ctx.setdefault("settings", settings)
    return templates.TemplateResponse(request=request, name=name, context=ctx)


def _pipeline_dir(name: str) -> Path:
    return settings.pipelines_root / name


def _val(name: str) -> dict:
    d = _pipeline_dir(name)
    r = validate_pipeline(d)
    return r.as_dict()


_ERR_AGENT_PATH_RE = re.compile(r"^agents/([^/]+)/")
_ERR_AGENT_IDX_RE = re.compile(r"^agents\[(\d+)\]")


def _err_agent_ids(val: dict, data: dict | None) -> set[str]:
    """Extract the set of agent ids that currently have validation errors.
    Used to paint failing nodes in red on the graph."""
    out: set[str] = set()
    if not val or not data:
        return out
    agents = data.get("agents", []) if isinstance(data, dict) else []
    for e in val.get("errors", []):
        field = e.get("field", "") if isinstance(e, dict) else ""
        m = _ERR_AGENT_PATH_RE.match(field)
        if m:
            out.add(m.group(1))
            continue
        m = _ERR_AGENT_IDX_RE.match(field)
        if m:
            try:
                agent = agents[int(m.group(1))]
                if isinstance(agent, dict) and agent.get("id"):
                    out.add(agent["id"])
            except (IndexError, ValueError):
                pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# §1  Pipeline selector
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/configurator", response_class=HTMLResponse)
async def index(request: Request):
    pipelines = fs.list_pipelines()
    templates_list = fs.list_templates()
    return _t(request, "index.html", {
        "pipelines": pipelines,
        "templates": templates_list,
    })


# ─────────────────────────────────────────────────────────────────────────────
# §2  Pipeline overview
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline/{name}", response_class=HTMLResponse)
async def pipeline_view(request: Request, name: str, tab: str = "graph",
                        subtab: str = "view"):
    data = fs.get_pipeline(name)
    if data is None:
        return HTMLResponse("Pipeline not found", status_code=404)
    val = _val(name)
    svg = build_svg(_pipeline_dir(name), data,
                    error_agent_ids=_err_agent_ids(val, data))
    agent_ids = [a["id"] for a in data.get("agents", [])
                 if isinstance(a, dict) and "id" in a]
    json_content = fs.read_pipeline_json(name) or ""
    return _t(request, "pipeline.html", {
        "pipeline_name": name,
        "pipeline": data,
        "tab": tab,
        "subtab": subtab,
        "validation": val,
        "svg": svg,
        "agent_ids": agent_ids,
        "labels": fs.get_labels(name),
        "running": check_running(_pipeline_dir(name)),
        "all_pipelines": fs.list_pipelines(),
        "json_content": json_content,
        "claude_bin_found": bool(settings.claude_bin),
        "orientation": data.get("graph_orientation", "vertical"),
        "global_taglines_system": global_default_taglines("system"),
        "global_taglines_task":   global_default_taglines("task"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# §3  Create pipeline
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/pipeline/new", response_class=HTMLResponse)
async def create_pipeline(
    request: Request,
    name: Annotated[str, Form()],
    display_name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    graph_mode: Annotated[str, Form()] = "dag",
    category: Annotated[str, Form()] = "",
    template_name: Annotated[str, Form()] = "",
):
    if not re.match(r'^[a-z0-9_-]+$', name):
        pipelines = fs.list_pipelines()
        return _t(request, "index.html", {
            "pipelines": pipelines,
            "templates": fs.list_templates(),
            "error": "Pipeline name must be lowercase alphanumeric with hyphens/underscores only",
        })
    if (settings.pipelines_root / name).exists():
        pipelines = fs.list_pipelines()
        return _t(request, "index.html", {
            "pipelines": pipelines,
            "templates": fs.list_templates(),
            "error": f"Pipeline '{name}' already exists",
        })
    fs.create_pipeline(name, display_name, description, graph_mode,
                       category, template_name or None)
    return RedirectResponse(f"/pipeline/{name}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# §4  Delete pipeline
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/pipeline/{name}/delete", response_class=HTMLResponse)
async def delete_pipeline(
    request: Request, name: str,
    confirm_name: Annotated[str, Form()] = "",
    trash: Annotated[str, Form()] = "true",
):
    if confirm_name != name:
        return HTMLResponse(
            f'<div class="error-banner">Name does not match. Type <strong>{name}</strong> to confirm.</div>',
            status_code=400,
        )
    if check_running(_pipeline_dir(name)):
        return HTMLResponse(
            '<div class="error-banner">Cannot delete a running pipeline.</div>',
            status_code=400,
        )
    fs.delete_pipeline(name, trash=trash.lower() != "false")
    return RedirectResponse("/configurator", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# §5  Graph — SVG render
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline/{name}/graph", response_class=HTMLResponse)
async def graph_svg_partial(request: Request, name: str):
    data = fs.get_pipeline(name)
    val = _val(name)
    svg = (build_svg(_pipeline_dir(name), data,
                     error_agent_ids=_err_agent_ids(val, data))
           if data else "")
    return _t(request, "partials/graph_view.html", {
        "pipeline_name": name,
        "svg": svg,
        "validation": val,
        "orientation": (data or {}).get("graph_orientation", "horizontal"),
    })


@router.post("/pipeline/{name}/graph/orientation",
          response_class=HTMLResponse)
async def graph_orientation_toggle(
    request: Request, name: str,
    orient: Annotated[str, Form()] = "vertical",
):
    """Persist graph_orientation and re-render just the graph-view partial.
    Used by the segmented toggle on the View subtab — no Apply click needed."""
    new_orient = "vertical" if orient.lower() == "vertical" else "horizontal"
    data = fs.get_pipeline(name)
    if data is not None and data.get("graph_orientation") != new_orient:
        data["graph_orientation"] = new_orient
        fs.write_pipeline_json(
            name, json.dumps(data, indent=2),
            f"Orientation → {new_orient}")
        data = fs.get_pipeline(name)
    val = _val(name)
    svg = (build_svg(_pipeline_dir(name), data,
                     error_agent_ids=_err_agent_ids(val, data))
           if data else "")
    return _t(request, "partials/graph_view.html", {
        "pipeline_name": name,
        "svg": svg,
        "validation": val,
        "orientation": new_orient,
    })


# ─────────────────────────────────────────────────────────────────────────────
# §6  Graph — Topology Panel: Add agent
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/pipeline/{name}/graph/agent", response_class=HTMLResponse)
async def topology_add_agent(
    request: Request, name: str,
    agent_id: Annotated[str, Form()],
    agent_name: Annotated[str, Form()] = "",
    agent_type: Annotated[str, Form()] = "worker",
    category: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    depends_on: Annotated[str, Form()] = "",
    timeout_s: Annotated[str, Form()] = "",
    require_approval: Annotated[str, Form()] = "false",
):
    deps = [d.strip() for d in depends_on.split(",") if d.strip()]
    agent = {
        "id": agent_id.strip(),
        "name": agent_name.strip() or agent_id.strip(),
        "type": agent_type,
        "category": category,
        "description": description,
        "depends_on": deps,
        "require_approval": require_approval.lower() == "true",
    }
    if timeout_s:
        try:
            agent["timeout_s"] = int(timeout_s)
        except ValueError:
            pass
    ok, msg = fs.add_agent(name, agent)
    return await _topology_partial(request, name, error=None if ok else msg)


@router.delete("/pipeline/{name}/graph/agent/{agent_id}", response_class=HTMLResponse)
async def topology_remove_agent(request: Request, name: str, agent_id: str):
    ok, msg = fs.remove_agent(name, agent_id)
    return await _topology_partial(request, name, error=None if ok else msg)


@router.put("/pipeline/{name}/graph/agent/{agent_id}", response_class=HTMLResponse)
async def topology_update_agent(
    request: Request, name: str, agent_id: str,
    agent_name: Annotated[str, Form()] = "",
    agent_type: Annotated[str, Form()] = "worker",
    category: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    depends_on: Annotated[str, Form()] = "",
    timeout_s: Annotated[str, Form()] = "",
    require_approval: Annotated[str, Form()] = "false",
):
    deps = [d.strip() for d in depends_on.split(",") if d.strip()]
    updates = {
        "name": agent_name.strip() or agent_id,
        "type": agent_type,
        "category": category,
        "description": description,
        "depends_on": deps,
        "require_approval": require_approval.lower() == "true",
    }
    if timeout_s:
        try:
            updates["timeout_s"] = int(timeout_s)
        except ValueError:
            pass
    ok, msg = fs.update_agent(name, agent_id, updates)
    return await _topology_partial(request, name, error=None if ok else msg)


async def _topology_partial(request: Request, name: str, error: str | None):
    data = fs.get_pipeline(name)
    val = _val(name)
    svg = (build_svg(_pipeline_dir(name), data,
                     error_agent_ids=_err_agent_ids(val, data))
           if data else "")
    return _t(request, "partials/topology.html", {
        "pipeline_name": name,
        "pipeline": data,
        "validation": val,
        "svg": svg,
        "error": error,
        "claude_bin_found": bool(settings.claude_bin),
        "agent_ids": [a["id"] for a in (data or {}).get("agents", [])
                      if isinstance(a, dict)],
    })


# ─────────────────────────────────────────────────────────────────────────────
# §7  Graph — Topology Panel: Edges
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/pipeline/{name}/graph/edge", response_class=HTMLResponse)
async def topology_add_edge(
    request: Request, name: str,
    from_id: Annotated[str, Form()],
    to_id: Annotated[str, Form()],
    edge_type: Annotated[str, Form()] = "feedback",
):
    edge = {"from": from_id, "to": to_id, "type": edge_type}
    ok, msg = fs.add_edge(name, edge)
    return await _topology_partial(request, name, error=None if ok else msg)


@router.post("/pipeline/{name}/graph/edge/delete", response_class=HTMLResponse)
async def topology_remove_edge(
    request: Request, name: str,
    from_id: Annotated[str, Form()],
    to_id: Annotated[str, Form()],
    edge_type: Annotated[str, Form()] = "feedback",
):
    ok, msg = fs.remove_edge(name, from_id, to_id, edge_type)
    return await _topology_partial(request, name, error=None if ok else msg)


# ─────────────────────────────────────────────────────────────────────────────
# §8  Graph — Raw JSON editor
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline/{name}/graph/json", response_class=HTMLResponse)
async def graph_json_get(request: Request, name: str):
    content = fs.read_pipeline_json(name) or ""
    val = _val(name)
    return _t(request, "partials/json_editor.html", {
        "pipeline_name": name,
        "json_content": content,
        "validation": val,
    })


@router.post("/pipeline/{name}/graph/json", response_class=HTMLResponse)
async def graph_json_save(
    request: Request, name: str,
    json_content: Annotated[str, Form()],
    message: Annotated[str, Form()] = "",
):
    # Parse syntax
    try:
        parsed = json.loads(json_content)
    except json.JSONDecodeError as e:
        return _t(request, "partials/json_editor.html", {
            "pipeline_name": name,
            "json_content": json_content,
            "validation": {"status": "errors",
                           "errors": [{"section": "graph", "field": "pipeline.json",
                                       "message": f"JSON syntax error: {e}"}],
                           "warnings": [], "badge_color": "red"},
            "save_error": f"JSON syntax error: {e}",
        })
    # Semantic validation
    val = validate_pipeline(_pipeline_dir(name), parsed)
    # Block only on syntax / structural errors
    blocking = [e for e in val.errors if e.section == "graph" and
                "syntax" in e.message.lower()]
    # Save (semantic warnings don't block)
    fs.write_pipeline_json(name, json.dumps(parsed, indent=2), message)
    # Re-validate from disk
    val2 = _val(name)
    svg = build_svg(_pipeline_dir(name), parsed,
                    error_agent_ids=_err_agent_ids(val2, parsed))
    return _t(request, "partials/json_editor.html", {
        "pipeline_name": name,
        "json_content": json.dumps(parsed, indent=2),
        "validation": val2,
        "save_ok": True,
        "svg": svg,
    })


# ─────────────────────────────────────────────────────────────────────────────
# §9  Agent detail / metadata
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline/{name}/agent/{agent_id}", response_class=HTMLResponse)
async def agent_detail(request: Request, name: str, agent_id: str):
    agent = fs.get_agent(name, agent_id)
    pipeline = fs.get_pipeline(name)
    labels_data = fs.get_labels(name)
    all_agent_ids = [a["id"] for a in (pipeline or {}).get("agents", [])
                     if isinstance(a, dict) and a.get("id") != agent_id]
    return _t(request, "partials/agent_detail.html", {
        "pipeline_name": name,
        "agent": agent,
        "agent_id": agent_id,
        "all_agent_ids": all_agent_ids,
        "labels_data": labels_data,
    })


@router.post("/pipeline/{name}/agent/{agent_id}", response_class=HTMLResponse)
async def agent_save_meta(
    request: Request, name: str, agent_id: str,
    agent_name: Annotated[str, Form()] = "",
    agent_type: Annotated[str, Form()] = "worker",
    category: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    labels: Annotated[str, Form()] = "",
    timeout_s: Annotated[str, Form()] = "",
    require_approval: Annotated[str, Form()] = "false",
    budget_strategy: Annotated[str, Form()] = "",
):
    label_list = [l.strip() for l in labels.split(",") if l.strip()]
    for lbl in label_list:
        fs.add_label_to_catalogue(name, lbl)
    updates = {
        "name": agent_name or agent_id,
        "type": agent_type,
        "category": category,
        "description": description,
        "labels": label_list,
        "require_approval": require_approval.lower() == "true",
    }
    if timeout_s:
        try:
            updates["timeout_s"] = int(timeout_s)
        except ValueError:
            pass
    if budget_strategy:
        updates["budget_strategy"] = budget_strategy
    ok, msg = fs.update_agent(name, agent_id, updates)
    agent = fs.get_agent(name, agent_id)
    return _t(request, "partials/agent_detail.html", {
        "pipeline_name": name,
        "agent": agent,
        "agent_id": agent_id,
        "all_agent_ids": [],
        "labels_data": fs.get_labels(name),
        "save_ok": ok,
        "error": None if ok else msg,
    })


@router.post("/pipeline/{name}/agent/new", response_class=HTMLResponse)
async def agent_create(
    request: Request, name: str,
    agent_id: Annotated[str, Form()],
    agent_name: Annotated[str, Form()] = "",
    agent_type: Annotated[str, Form()] = "worker",
    category: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    depends_on: Annotated[str, Form()] = "",
    template_agent: Annotated[str, Form()] = "",
):
    deps = [d.strip() for d in depends_on.split(",") if d.strip()]
    agent = {
        "id": agent_id.strip(),
        "name": agent_name.strip() or agent_id.strip(),
        "type": agent_type,
        "category": category,
        "description": description,
        "depends_on": deps,
        "require_approval": False,
    }
    ok, msg = fs.add_agent(name, agent)
    if ok:
        return RedirectResponse(
            f"/pipeline/{name}?tab=agents", status_code=303)
    return _t(request, "partials/agent_detail.html", {
        "pipeline_name": name, "agent": agent,
        "agent_id": agent_id, "all_agent_ids": [],
        "labels_data": {}, "error": msg,
    })


@router.post("/pipeline/{name}/agent/{agent_id}/delete", response_class=HTMLResponse)
async def agent_delete(request: Request, name: str, agent_id: str):
    fs.remove_agent(name, agent_id)
    return RedirectResponse(f"/pipeline/{name}?tab=agents", status_code=303)


@router.post("/pipeline/{name}/agent/{agent_id}/specialize",
          response_class=HTMLResponse)
async def agent_specialize(request: Request, name: str, agent_id: str):
    """Re-run meta-prompt specialization for an existing agent. Overwrites
    the agent's 01_system.md / 02_prompt.md on success (snapshotted first).
    On failure, leaves current files untouched and surfaces the raw response."""
    ok, info = fs.specialize_agent(name, agent_id)
    agent = fs.get_agent(name, agent_id)
    ctx = {
        "pipeline_name": name,
        "agent": agent,
        "agent_id": agent_id,
        "all_agent_ids": [],
        "labels_data": fs.get_labels(name),
    }
    if ok:
        ctx["specialize_ok"] = True
    else:
        ctx["specialize_error"] = info
    return _t(request, "partials/agent_detail.html", ctx)


# ─────────────────────────────────────────────────────────────────────────────
# §10  Prompt editor
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline/{name}/prompt/{agent_id}/{filename}", response_class=HTMLResponse)
async def prompt_editor(request: Request, name: str,
                        agent_id: str, filename: str):
    content = fs.read_prompt(name, agent_id, filename)
    versions = ver.list_versions(_pipeline_dir(name),
                                 f"agents/{agent_id}/{filename}")
    pipeline = fs.get_pipeline(name) or {}
    return _t(request, "partials/prompt_editor.html", {
        "pipeline_name": name,
        "agent_id": agent_id,
        "filename": filename,
        "content": content,
        "versions": versions,
        "token_est": max(1, len(content) // 4),
        "context_limit": settings.model_context_limit,
        "required_taglines": _required_taglines(
            pipeline, _kind_for_filename(filename)),
    })


@router.post("/pipeline/{name}/prompt/{agent_id}/{filename}",
          response_class=HTMLResponse)
async def prompt_save(
    request: Request, name: str, agent_id: str, filename: str,
    content: Annotated[str, Form()],
    message: Annotated[str, Form()] = "",
):
    # Tagline validation (if enabled on this pipeline and this is a prompt file)
    tag_error: str | None = None
    if filename in ("01_system.md", "02_prompt.md"):
        pipeline = fs.get_pipeline(name) or {}
        required = _required_taglines(pipeline, _kind_for_filename(filename))
        tag_error = validate_prompt_taglines(content, required)

    saved = tag_error is None
    if saved:
        fs.write_prompt(name, agent_id, filename, content, message)

    versions = ver.list_versions(_pipeline_dir(name),
                                 f"agents/{agent_id}/{filename}")
    token_est = max(1, len(content) // 4)
    warn_tokens = token_est > (settings.model_context_limit * 0.20 // 1)
    pipeline = fs.get_pipeline(name) or {}
    return _t(request, "partials/prompt_editor.html", {
        "pipeline_name": name,
        "agent_id": agent_id,
        "filename": filename,
        "content": content,
        "versions": versions,
        "token_est": token_est,
        "context_limit": settings.model_context_limit,
        "save_ok": saved,
        "save_error": tag_error,
        "warn_tokens": warn_tokens,
        "required_taglines": _required_taglines(
            pipeline, _kind_for_filename(filename)),
    })


# ─────────────────────────────────────────────────────────────────────────────
# §11  Version history
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline/{name}/versions/{file_path:path}", response_class=HTMLResponse)
async def version_history(request: Request, name: str, file_path: str):
    versions = ver.list_versions(_pipeline_dir(name), file_path)
    return _t(request, "partials/version_history.html", {
        "pipeline_name": name,
        "file_path": file_path,
        "versions": versions,
    })


@router.post("/pipeline/{name}/versions/restore", response_class=HTMLResponse)
async def version_restore(
    request: Request, name: str,
    file_path: Annotated[str, Form()],
    version: Annotated[int, Form()],
    message: Annotated[str, Form()] = "",
):
    ok = ver.restore_version(_pipeline_dir(name), file_path, version, message)
    versions = ver.list_versions(_pipeline_dir(name), file_path)
    return _t(request, "partials/version_history.html", {
        "pipeline_name": name,
        "file_path": file_path,
        "versions": versions,
        "restore_ok": ok,
        "restore_version": version,
    })


@router.post("/pipeline/{name}/versions/delete", response_class=HTMLResponse)
async def version_delete(
    request: Request, name: str,
    file_path: Annotated[str, Form()],
    version: Annotated[int, Form()],
):
    ver.delete_version(_pipeline_dir(name), file_path, version)
    versions = ver.list_versions(_pipeline_dir(name), file_path)
    return _t(request, "partials/version_history.html", {
        "pipeline_name": name,
        "file_path": file_path,
        "versions": versions,
    })


# ─────────────────────────────────────────────────────────────────────────────
# §12  Generic file browser
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline/{name}/files", response_class=HTMLResponse)
async def file_browser(request: Request, name: str):
    tree = fs.list_files(name)
    return _t(request, "partials/file_browser.html", {
        "pipeline_name": name,
        "tree": tree,
    })


def _required_for_path(pipeline: dict, file_path: str) -> list[str]:
    """Return the required-tagline list appropriate for the given file path.
    Empty list for files that aren't system/task prompts."""
    basename = file_path.rsplit("/", 1)[-1]
    if basename not in ("01_system.md", "02_prompt.md"):
        return []
    return _required_taglines(pipeline, _kind_for_filename(basename))


@router.get("/pipeline/{name}/files/{file_path:path}", response_class=HTMLResponse)
async def file_read(request: Request, name: str, file_path: str):
    content = fs.read_file(name, file_path)
    pipeline = fs.get_pipeline(name) or {}
    return _t(request, "partials/file_editor.html", {
        "pipeline_name": name,
        "file_path": file_path,
        "content": content or "",
        "not_found": content is None,
        "required_taglines": _required_for_path(pipeline, file_path),
    })


@router.post("/pipeline/{name}/files/{file_path:path}", response_class=HTMLResponse)
async def file_write(
    request: Request, name: str, file_path: str,
    content: Annotated[str, Form()],
    message: Annotated[str, Form()] = "",
):
    save_error: str | None = None

    # JSON syntax check on .json
    if file_path.endswith(".json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            save_error = f"Invalid JSON: {e}"

    # Tagline check on system/task prompt files
    if save_error is None and file_path.endswith(".md"):
        basename = file_path.rsplit("/", 1)[-1]
        if basename in ("01_system.md", "02_prompt.md"):
            pipeline = fs.get_pipeline(name) or {}
            required = _required_taglines(pipeline, _kind_for_filename(basename))
            save_error = validate_prompt_taglines(content, required)

    saved = save_error is None
    if saved:
        fs.write_file(name, file_path, content, message)

    pipeline = fs.get_pipeline(name) or {}
    return _t(request, "partials/file_editor.html", {
        "pipeline_name": name,
        "file_path": file_path,
        "content": content,
        "not_found": False,
        "save_ok": saved,
        "save_error": save_error,
        "required_taglines": _required_for_path(pipeline, file_path),
    })


@router.post("/pipeline/{name}/files-new", response_class=HTMLResponse)
async def file_create(
    request: Request, name: str,
    rel_path: Annotated[str, Form()],
    is_dir: Annotated[str, Form()] = "false",
):
    fs.create_file_or_dir(name, rel_path, is_dir.lower() == "true")
    tree = fs.list_files(name)
    return _t(request, "partials/file_browser.html", {
        "pipeline_name": name,
        "tree": tree,
    })


@router.post("/pipeline/{name}/files-delete", response_class=HTMLResponse)
async def file_delete_route(
    request: Request, name: str,
    rel_path: Annotated[str, Form()],
):
    fs.delete_file(name, rel_path)
    tree = fs.list_files(name)
    return _t(request, "partials/file_browser.html", {
        "pipeline_name": name,
        "tree": tree,
    })


# ─────────────────────────────────────────────────────────────────────────────
# §13  Validation
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/pipeline/{name}/validate", response_class=HTMLResponse)
async def validate(request: Request, name: str):
    from datetime import datetime
    val = _val(name)
    return _t(request, "partials/validate_panel.html", {
        "pipeline_name": name,
        "validation": val,
        "ran_at": datetime.now().strftime("%H:%M:%S"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# §14  Graph mode change
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/pipeline/{name}/settings/taglines", response_class=HTMLResponse)
async def settings_taglines_save(
    request: Request, name: str,
    kind: Annotated[str, Form()],
    tags: Annotated[str, Form()] = "",
):
    """Auto-save endpoint for the tag-chip inputs on the Graph tab. Called
    by the JS widget every time a chip is added or removed — no Apply click.
    Only updates the one field it owns; preserves everything else."""
    if kind not in ("system", "task"):
        return HTMLResponse("", status_code=400)
    field = f"validated_taglines_{kind}"
    new_list = [t.strip() for t in tags.split(",") if t.strip()]
    data = fs.get_pipeline(name)
    if data is None:
        return HTMLResponse("", status_code=404)
    if data.get(field) != new_list:
        data[field] = new_list
        # Drop the legacy single-list field now that we're writing the split ones.
        data.pop("validated_taglines", None)
        fs.write_pipeline_json(
            name, json.dumps(data, indent=2),
            f"Required {kind} taglines updated")
    return HTMLResponse("", status_code=204)


@router.post("/pipeline/{name}/settings/mode", response_class=HTMLResponse)
async def settings_mode_save(
    request: Request, name: str,
    mode: Annotated[str, Form()] = "dag",
):
    """Auto-save endpoint for the Mode toggle (dag / cyclic) on the Graph tab."""
    new_mode = "cyclic" if mode.lower() == "cyclic" else "dag"
    data = fs.get_pipeline(name)
    if data is None:
        return HTMLResponse("", status_code=404)
    if data.get("graph_mode") != new_mode:
        data["graph_mode"] = new_mode
        fs.write_pipeline_json(
            name, json.dumps(data, indent=2), f"Mode → {new_mode}")
    return HTMLResponse("", status_code=204)


@router.post("/pipeline/{name}/settings/validate-taglines",
          response_class=HTMLResponse)
async def settings_validate_taglines_save(
    request: Request, name: str,
    enabled: Annotated[str, Form()] = "false",
):
    """Auto-save endpoint for the 'Validate taglines' checkbox."""
    flag = enabled.lower() == "true"
    data = fs.get_pipeline(name)
    if data is None:
        return HTMLResponse("", status_code=404)
    if bool(data.get("validate_taglines")) != flag:
        data["validate_taglines"] = flag
        fs.write_pipeline_json(
            name, json.dumps(data, indent=2),
            f"Validate taglines → {flag}")
    return HTMLResponse("", status_code=204)


# ─────────────────────────────────────────────────────────────────────────────
# §15  Templates
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/templates", response_class=HTMLResponse)
async def templates_list(request: Request):
    return _t(request, "templates.html", {
        "templates": fs.list_templates(),
    })


@router.post("/templates/save", response_class=HTMLResponse)
async def template_save(
    request: Request,
    pipeline_name: Annotated[str, Form()],
    tname: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    author: Annotated[str, Form()] = "",
):
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    fs.save_as_template(pipeline_name, tname, description, tag_list, author)
    return RedirectResponse("/templates", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# §16  SVG download
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline/{name}/graph/svg")
async def download_svg(name: str):
    data = fs.get_pipeline(name)
    val = _val(name)
    svg = build_svg(_pipeline_dir(name), data,
                    error_agent_ids=_err_agent_ids(val, data))
    return Response(
        content=svg, media_type="image/svg+xml",
        headers={"Content-Disposition": f'attachment; filename="{name}-graph.svg"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# §17  Prompt templates (per agent-type starter prompts)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/prompt-templates", response_class=HTMLResponse)
async def prompt_templates_list(request: Request, selected: str = ""):
    types = fs.list_prompt_templates()
    # `selected` can be "_meta" (the meta-prompt editor) or any regular type.
    if not selected and types:
        selected = types[0]["type"]
    is_meta = selected == "_meta"

    if is_meta:
        sys_content = fs.read_meta_prompt("01_system.md")
        task_content = fs.read_meta_prompt("02_prompt.md")
        sys_versions = ver.list_versions(settings.prompt_templates_dir,
                                         "_meta/01_system.md")
        task_versions = ver.list_versions(settings.prompt_templates_dir,
                                          "_meta/02_prompt.md")
        type_description = ""
    else:
        sys_content = fs.read_prompt_template(selected, "01_system.md") if selected else ""
        task_content = fs.read_prompt_template(selected, "02_prompt.md") if selected else ""
        sys_versions = ver.list_versions(settings.prompt_templates_dir,
                                         f"{selected}/01_system.md") if selected else []
        task_versions = ver.list_versions(settings.prompt_templates_dir,
                                          f"{selected}/02_prompt.md") if selected else []
        type_description = fs.read_type_description(selected) if selected else ""

    return _t(request, "prompt_templates.html", {
        "types": types,
        "selected": selected,
        "is_meta": is_meta,
        "sys_content": sys_content,
        "task_content": task_content,
        "sys_versions": sys_versions,
        "task_versions": task_versions,
        "type_description": type_description,
        "templates_dir": str(settings.prompt_templates_dir),
        "claude_bin_found": bool(settings.claude_bin),
    })


@router.post("/prompt-templates/new", response_class=HTMLResponse)
async def prompt_template_new(
    request: Request,
    type_name: Annotated[str, Form()],
):
    ok, msg = fs.create_prompt_template_type(type_name.strip().lower())
    if not ok:
        types = fs.list_prompt_templates()
        return _t(request, "prompt_templates.html", {
            "types": types,
            "selected": types[0]["type"] if types else "",
            "is_meta": False,
            "sys_content": "", "task_content": "",
            "sys_versions": [], "task_versions": [],
            "type_description": "",
            "templates_dir": str(settings.prompt_templates_dir),
            "claude_bin_found": bool(settings.claude_bin),
            "error": msg,
        })
    return RedirectResponse(f"/prompt-templates?selected={type_name}",
                            status_code=303)


@router.post("/prompt-templates/{agent_type}/delete", response_class=HTMLResponse)
async def prompt_template_delete(request: Request, agent_type: str):
    if agent_type == "_meta":
        return HTMLResponse(
            '<div class="error-banner">The meta prompts cannot be deleted.</div>',
            status_code=400)
    fs.delete_prompt_template_type(agent_type)
    return RedirectResponse("/prompt-templates", status_code=303)


@router.post("/prompt-templates/_meta/{filename}", response_class=HTMLResponse)
async def prompt_template_meta_save(
    request: Request, filename: str,
    content: Annotated[str, Form()],
    message: Annotated[str, Form()] = "",
):
    if filename not in ("01_system.md", "02_prompt.md"):
        return HTMLResponse(
            f'<div class="error-banner">Invalid meta file: {filename}</div>',
            status_code=400)
    # Meta prompts: no required-tagline list (they follow their own structure).
    # Still enforce structural tagline rules to catch unpaired or nested tags.
    tag_error = validate_prompt_taglines(content, required=[])
    if tag_error is not None:
        return _t(request, "prompt_templates.html", {
            "types": fs.list_prompt_templates(),
            "selected": "_meta",
            "is_meta": True,
            "sys_content": content if filename == "01_system.md"
                           else fs.read_meta_prompt("01_system.md"),
            "task_content": content if filename == "02_prompt.md"
                            else fs.read_meta_prompt("02_prompt.md"),
            "sys_versions": ver.list_versions(
                settings.prompt_templates_dir, "_meta/01_system.md"),
            "task_versions": ver.list_versions(
                settings.prompt_templates_dir, "_meta/02_prompt.md"),
            "type_description": "",
            "templates_dir": str(settings.prompt_templates_dir),
            "claude_bin_found": bool(settings.claude_bin),
            "error": f"{filename}: {tag_error}",
        })
    fs.write_meta_prompt(filename, content, message)
    return RedirectResponse("/prompt-templates?selected=_meta", status_code=303)


@router.post("/prompt-templates/{agent_type}/description",
          response_class=HTMLResponse)
async def prompt_template_description_save(
    request: Request, agent_type: str,
    content: Annotated[str, Form()],
    message: Annotated[str, Form()] = "",
):
    if agent_type == "_meta":
        return HTMLResponse(
            '<div class="error-banner">The meta entry has no type description.</div>',
            status_code=400)
    ok, msg = fs.write_type_description(agent_type, content, message)
    if not ok:
        return HTMLResponse(
            f'<div class="error-banner">{msg}</div>', status_code=400)
    return RedirectResponse(
        f"/prompt-templates?selected={agent_type}", status_code=303)


@router.post("/prompt-templates/{agent_type}/{filename}", response_class=HTMLResponse)
async def prompt_template_save(
    request: Request, agent_type: str, filename: str,
    content: Annotated[str, Form()],
    message: Annotated[str, Form()] = "",
):
    # Structural tagline rules (pairing + no nesting) — no required list,
    # since templates ship with placeholders meant to be filled in.
    tag_error = validate_prompt_taglines(content, required=[])
    if tag_error is not None:
        types = fs.list_prompt_templates()
        sys_content = fs.read_prompt_template(agent_type, "01_system.md")
        task_content = fs.read_prompt_template(agent_type, "02_prompt.md")
        if filename == "01_system.md":
            sys_content = content
        elif filename == "02_prompt.md":
            task_content = content
        return _t(request, "prompt_templates.html", {
            "types": types,
            "selected": agent_type,
            "is_meta": False,
            "sys_content": sys_content,
            "task_content": task_content,
            "sys_versions": ver.list_versions(
                settings.prompt_templates_dir, f"{agent_type}/01_system.md"),
            "task_versions": ver.list_versions(
                settings.prompt_templates_dir, f"{agent_type}/02_prompt.md"),
            "type_description": fs.read_type_description(agent_type),
            "templates_dir": str(settings.prompt_templates_dir),
            "claude_bin_found": bool(settings.claude_bin),
            "error": f"{filename}: {tag_error}",
        })
    fs.write_prompt_template(agent_type, filename, content, message)
    return RedirectResponse(
        f"/prompt-templates?selected={agent_type}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# §18  Entry point
# ─────────────────────────────────────────────────────────────────────────────

