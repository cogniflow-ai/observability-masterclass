"""
Cogniflow Observer — FastAPI router.

Exposes `router` (APIRouter) and `templates` so the parent app
(cogniflow-ui_v3.5/app.py) can mount it alongside the configurator.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .config import settings
from .filesystem import (
    list_pipelines, pipeline_name, read_pipeline_json,
    get_all_agent_cards, pipeline_summary, get_events,
    read_agent_file, render_markdown, write_approval, write_command,
    is_pipeline_dir, reset_pipeline,
    get_pause_state, write_pause_file, write_resume_file,
    scan_runtime_state, read_agent_config, approval_routes_preview,
    read_agent_messages, pipeline_approver,
    agents_in_validation_error,
)
from .dag_svg import build_dag_svg, compute_layers
from .versioning import (
    list_runs, find_run, write_run_annotation,
    build_file_tree, list_file_versions,
    read_file_version, write_current_file, render_diff, is_editable,
)
from . import vault_view

# ── Router setup ───────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router    = APIRouter()


def _pipeline_dir(name: str) -> Path:
    """Resolve pipeline dir; raise 404 if not found or invalid."""
    d = (settings.pipelines_root / name).resolve()
    if not is_pipeline_dir(d):
        raise HTTPException(status_code=404, detail=f"Pipeline not found: {name}")
    return d


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    pipelines = list_pipelines(settings.pipelines_root)
    return templates.TemplateResponse(request, "index.html", {
        "pipelines":  pipelines,
        "title":      settings.app_title,
        "root":       str(settings.pipelines_root),
        "versioning": settings.versioning,
        "vault_available": vault_view.vault_exists(),
    })


@router.get("/pipelines/{name}", response_class=HTMLResponse)
async def board(request: Request, name: str):
    pl_dir      = _pipeline_dir(name)
    pl_json     = read_pipeline_json(pl_dir)
    runtime     = scan_runtime_state(pl_dir)
    cards       = get_all_agent_cards(pl_dir, settings.model_context_limit, runtime)
    summary     = pipeline_summary(cards, get_pause_state(pl_dir))
    layers      = compute_layers(pl_json.get("agents", []))
    statuses    = {c["id"]: c["status"] for c in cards}
    dag_svg     = build_dag_svg(pl_json, statuses)
    events      = get_events(pl_dir, settings.event_tail_lines)
    pl_display  = pl_json.get("name", name)

    return templates.TemplateResponse(request, "board.html", {
        "name":             name,
        "pl_name":          pl_display,
        "cards":            cards,
        "summary":          summary,
        "dag_svg":          dag_svg,
        "events":           events,
        "layers":           [[a["id"] for a in l] for l in layers],
        "poll_ms":          settings.poll_interval_ms,
        "title":            settings.app_title,
        "initial_state":    summary["state"],
        "versioning":       settings.versioning,
        "validation":       {
            "blocked": runtime["validation_blocked"],
            "errors":  runtime["pipeline_validation_errors"],
        },
        "leaked_any":       runtime["leaked_any"],
        "current_run_id":   runtime["current_run_id"],
        "approver":         pipeline_approver(pl_dir),
        "vault_available":  vault_view.vault_exists(),
    })


# ── HTMX partials ──────────────────────────────────────────────────────────

@router.get("/pipelines/{name}/cards", response_class=HTMLResponse)
async def cards_partial(request: Request, name: str):
    pl_dir   = _pipeline_dir(name)
    pl_json  = read_pipeline_json(pl_dir)
    runtime  = scan_runtime_state(pl_dir)
    cards    = get_all_agent_cards(pl_dir, settings.model_context_limit, runtime)
    summary  = pipeline_summary(cards, get_pause_state(pl_dir))
    statuses = {c["id"]: c["status"] for c in cards}
    dag_svg  = build_dag_svg(pl_json, statuses)

    resp = templates.TemplateResponse(request, "partials/cards.html", {
        "name":    name,
        "cards":   cards,
        "summary": summary,
        "dag_svg": dag_svg,
        "validation": {
            "blocked": runtime["validation_blocked"],
            "errors":  runtime["pipeline_validation_errors"],
        },
        "leaked_any": runtime["leaked_any"],
    })

    # Emit pipeline-state on every poll so JS can drive the Start button
    triggers: dict = {"pipeline-state": {"state": summary["state"]}}
    if summary["all_done"]:
        triggers["pipeline-complete"] = None
    elif summary["any_approval"]:
        triggers["approval-needed"] = None
    resp.headers["HX-Trigger"] = json.dumps(triggers)

    return resp


@router.get("/pipelines/{name}/events", response_class=HTMLResponse)
async def events_partial(request: Request, name: str):
    pl_dir = _pipeline_dir(name)
    events = get_events(pl_dir, settings.event_tail_lines)
    return templates.TemplateResponse(request, "partials/events.html", {
        "events":  events,
    })


@router.get("/pipelines/{name}/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail(request: Request, name: str, agent_id: str):
    pl_dir  = _pipeline_dir(name)
    pl_json = read_pipeline_json(pl_dir)

    # Get depends_on from pipeline.json
    depends_on = []
    for a in pl_json.get("agents", []):
        if a["id"] == agent_id:
            depends_on = a.get("depends_on", [])
            break

    output_raw  = read_agent_file(pl_dir, agent_id, "output")
    output_html = render_markdown(output_raw)
    context     = read_agent_file(pl_dir, agent_id, "context")
    prompt      = read_agent_file(pl_dir, agent_id, "prompt")
    system      = read_agent_file(pl_dir, agent_id, "system")
    status_raw  = read_agent_file(pl_dir, agent_id, "status")

    agent_dir   = pl_dir / "agents" / agent_id
    agent_cfg   = read_agent_config(agent_dir)
    routes      = approval_routes_preview(agent_cfg)
    messages    = read_agent_messages(pl_dir, agent_id, limit=50)

    return templates.TemplateResponse(request, "partials/detail.html", {
        "name":        name,
        "agent_id":    agent_id,
        "output_html": output_html,
        "output_raw":  output_raw,
        "context":     context,
        "prompt":      prompt,
        "system":      system,
        "status_raw":  status_raw,
        "depends_on":  depends_on,
        "agent_cfg":   agent_cfg,
        "routes":      routes,
        "messages":    messages,
    })


# ── Validation drill-down ──────────────────────────────────────────────────

@router.get("/pipelines/{name}/agents/{agent_id}/violations", response_class=HTMLResponse)
async def agent_violations(request: Request, name: str, agent_id: str,
                           phase: str = "output"):
    """Side panel listing violations for input / output / struct check."""
    pl_dir  = _pipeline_dir(name)
    runtime = scan_runtime_state(pl_dir)

    if phase == "struct":
        violations = [
            err for err in runtime["pipeline_validation_errors"]
            if agent_id in agents_in_validation_error(err)
        ]
        title = f"{agent_id} — structural errors"
    else:
        bucket = "agent_input" if phase == "input" else "agent_output"
        state  = (runtime.get(bucket) or {}).get(agent_id) or {}
        violations = state.get("violations", [])
        title = f"{agent_id} — {'input' if phase == 'input' else 'output'} schema violations"

    return templates.TemplateResponse(request, "partials/violations.html", {
        "title":      title,
        "violations": violations,
        "phase":      phase,
        "agent_id":   agent_id,
    })


# ── Approval ───────────────────────────────────────────────────────────────

@router.post("/pipelines/{name}/agents/{agent_id}/approve", response_class=HTMLResponse)
async def approve_agent(
    request: Request,
    name: str,
    agent_id: str,
    action: str  = Form(...),   # "approve" or "reject"
    note:   str  = Form(""),
):
    pl_dir  = _pipeline_dir(name)
    approve = (action == "approve")
    note_clean = note.strip()
    # OBSERVER_CHANGES § 2.2: rejection note is required (non-empty) so the
    # rejection_feedback message routed to the downstream agent has content.
    if not approve and not note_clean:
        raise HTTPException(
            status_code=400,
            detail="Rejection note is required — please describe why.",
        )
    ok = write_approval(pl_dir, agent_id, approve, note_clean)
    if not ok:
        raise HTTPException(status_code=500, detail="Could not write approval file")

    # Re-render the single card after the decision
    cards   = get_all_agent_cards(pl_dir, settings.model_context_limit)
    card    = next((c for c in cards if c["id"] == agent_id), None)
    summary = pipeline_summary(cards, get_pause_state(pl_dir))

    resp = templates.TemplateResponse(request, "partials/single_card.html", {
        "name":    name,
        "card":    card,
        "summary": summary,
    })
    resp.headers["HX-Trigger"] = '{"approval-resolved": null}'
    return resp


# ── Approval queue (across all agents in a pipeline) ───────────────────────

@router.get("/pipelines/{name}/approval-queue", response_class=HTMLResponse)
async def approval_queue(request: Request, name: str):
    pl_dir  = _pipeline_dir(name)
    runtime = scan_runtime_state(pl_dir)
    cards   = get_all_agent_cards(pl_dir, settings.model_context_limit, runtime)
    queue   = []
    for c in cards:
        if c["status"] != "awaiting_approval":
            continue
        agent_dir = pl_dir / "agents" / c["id"]
        out_path  = agent_dir / "05_output.md"
        preview   = ""
        if out_path.exists():
            try:
                target = out_path.resolve() if out_path.is_symlink() else out_path
                preview = target.read_text(encoding="utf-8")[:80]
            except OSError:
                preview = ""
        # Read 07_approval_request.json for the request timestamp.
        req = agent_dir / "07_approval_request.json"
        requested_at = ""
        if req.exists():
            try:
                requested_at = json.loads(req.read_text(encoding="utf-8")).get("requested_at", "")
            except Exception:
                requested_at = ""
        queue.append({
            "id":           c["id"],
            "preview":      preview,
            "routes":       c["approval_routes"],
            "requested_at": requested_at,
        })
    return templates.TemplateResponse(request, "partials/approval_queue.html", {
        "name":       name,
        "queue":      queue,
    })


# ── Secrets vault (read-only) ──────────────────────────────────────────────

@router.get("/observer/vault", response_class=HTMLResponse)
async def vault_page(request: Request):
    secrets = vault_view.list_secrets()
    return templates.TemplateResponse(request, "vault.html", {
        "title":      settings.app_title,
        "secrets":    secrets,
        "vault_path": str(vault_view.vault_path()),
        "vault_available": vault_view.vault_exists(),
    })


@router.get("/observer/vault/secrets/{name}", response_class=HTMLResponse)
async def vault_secret_detail(request: Request, name: str):
    meta = vault_view.get_secret_meta(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Secret not found: {name}")
    return templates.TemplateResponse(request, "partials/vault_secret.html", {
        "secret": meta,
    })


@router.get("/pipelines/{name}/audit", response_class=HTMLResponse)
async def pipeline_audit(
    request: Request, name: str,
    run: str = "",
    direction: str = "",
    agent: str = "",
):
    pl_dir = _pipeline_dir(name)
    pl_json = read_pipeline_json(pl_dir)
    # The orchestrator stamps audit rows with pipeline_dir.name, not the
    # pipeline.json display name (see orchestrator/agent.py:164). Use the
    # same key here so the join works.
    audit_key  = pl_dir.name
    pl_display = pl_json.get("name", name)

    runs = vault_view.list_runs_with_audit(audit_key)
    if not run:
        # Prefer the run currently executing — but only scan events when
        # there's any chance it's distinct from the newest audited run.
        if runs:
            current = scan_runtime_state(pl_dir)["current_run_id"]
            run = current if current in runs else runs[0]

    rows    = vault_view.audit_for_pipeline(
        audit_key,
        run_id=run or None,
        direction=direction or None,
        agent_id=agent or None,
    )
    summary = vault_view.audit_run_summary(audit_key, run) if run else {
        "outbound": 0, "inbound": 0, "missing": 0, "leaked": 0,
    }
    return templates.TemplateResponse(request, "partials/audit.html", {
        "name":         name,
        "pl_name":      pl_display,
        "rows":         rows,
        "summary":      summary,
        "runs":         runs,
        "current_run":  run,
        "direction":    direction,
        "agent":        agent,
        "vault_available": vault_view.vault_exists(),
    })


# ── Pipeline start / stop ──────────────────────────────────────────────────

@router.post("/pipelines/{name}/start")
async def start_pipeline(name: str):
    pl_dir = _pipeline_dir(name)
    write_command(pl_dir, "start")
    return JSONResponse({"ok": True, "action": "start"})


@router.post("/pipelines/{name}/stop")
async def stop_pipeline(name: str):
    pl_dir = _pipeline_dir(name)
    write_command(pl_dir, "stop")
    return JSONResponse({"ok": True, "action": "stop"})


@router.post("/pipelines/{name}/pause")
async def pause_pipeline(name: str):
    """Request a pause: the orchestrator will emit pipeline_pausing when it
    notices the sentinel and pipeline_paused once the current layer finishes."""
    pl_dir = _pipeline_dir(name)
    ok = write_pause_file(pl_dir)
    return JSONResponse(
        {"ok": ok, "action": "pause"},
        status_code=200 if ok else 500,
    )


@router.post("/pipelines/{name}/resume")
async def resume_pipeline(name: str):
    """Resume a paused pipeline. Orchestrator picks up at the next layer."""
    pl_dir = _pipeline_dir(name)
    ok = write_resume_file(pl_dir)
    return JSONResponse(
        {"ok": ok, "action": "resume"},
        status_code=200 if ok else 500,
    )


@router.post("/pipelines/{name}/reset")
async def reset_pipeline_route(name: str):
    pl_dir = _pipeline_dir(name)
    result = reset_pipeline(pl_dir)
    # Best-effort: also clear any stale command file on a fresh reset
    status = 200 if result["ok"] else 500
    return JSONResponse({"action": "reset", **result}, status_code=status)


# ── Versioning / history ───────────────────────────────────────────────────

def _require_versioning() -> None:
    """Gate: raise 404 when the versioning feature is disabled in config.json."""
    if not settings.versioning:
        raise HTTPException(status_code=404, detail="Versioning feature is disabled")


@router.get("/pipelines/{name}/history", response_class=HTMLResponse)
async def history_page(request: Request, name: str):
    _require_versioning()
    pl_dir  = _pipeline_dir(name)
    pl_json = read_pipeline_json(pl_dir)
    runs    = list_runs(pl_dir)
    tree    = build_file_tree(pl_dir)
    return templates.TemplateResponse(request, "history.html", {
        "name":    name,
        "pl_name": pl_json.get("name", name),
        "runs":    runs,
        "tree":    tree,
        "title":   settings.app_title,
    })


@router.get("/pipelines/{name}/history/runs", response_class=HTMLResponse)
async def history_runs_partial(request: Request, name: str):
    _require_versioning()
    pl_dir = _pipeline_dir(name)
    runs   = list_runs(pl_dir)
    return templates.TemplateResponse(request, "partials/history_runs.html", {
        "name": name,
        "runs": runs,
    })


@router.post("/pipelines/{name}/history/{run_id}/annotation", response_class=HTMLResponse)
async def history_save_annotation(
    request: Request,
    name: str,
    run_id: str,
    label:   str = Form(""),
    comment: str = Form(""),
):
    _require_versioning()
    pl_dir  = _pipeline_dir(name)
    run_dir = find_run(pl_dir, run_id)
    if run_dir is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    ok = write_run_annotation(run_dir, label.strip(), comment.strip())
    if not ok:
        raise HTTPException(status_code=500, detail="Could not save annotation")
    runs = list_runs(pl_dir)
    return templates.TemplateResponse(request, "partials/history_runs.html", {
        "name": name,
        "runs": runs,
    })


@router.get("/pipelines/{name}/history/file", response_class=HTMLResponse)
async def history_file_panel(
    request: Request,
    name:    str,
    logical: str,
    version: str = "current",
):
    _require_versioning()
    pl_dir   = _pipeline_dir(name)
    if ".." in logical.replace("\\", "/").split("/") or logical.startswith(("/", "\\")):
        raise HTTPException(status_code=400, detail="Invalid logical path")
    versions = list_file_versions(pl_dir, logical)
    known    = {v["version"] for v in versions}
    if version not in known:
        version = "current" if "current" in known else (versions[0]["version"] if versions else "current")

    ok, text = read_file_version(pl_dir, logical, version)
    editable_now = is_editable(logical) and version == "current"

    return templates.TemplateResponse(request, "partials/history_file_panel.html", {
        "name":         name,
        "logical":      logical,
        "versions":     versions,
        "current_ver":  version,
        "text":         text if ok else "",
        "read_ok":      ok,
        "read_message": "" if ok else text,
        "editable":     editable_now,
        "editable_any": is_editable(logical),
    })


@router.post("/pipelines/{name}/history/file", response_class=HTMLResponse)
async def history_file_save(
    request: Request,
    name:    str,
    logical: str = Form(...),
    content: str = Form(""),
):
    _require_versioning()
    pl_dir       = _pipeline_dir(name)
    ok, message  = write_current_file(pl_dir, logical, content)
    versions     = list_file_versions(pl_dir, logical)
    version      = "current"
    read_ok, text = read_file_version(pl_dir, logical, version)

    # On validation failure, keep the user's rejected input in the editor so
    # they can fix and retry — otherwise we'd discard their edits and just
    # show the pre-save disk content, which looks like a silent success.
    display_text = content if not ok else (text if read_ok else content)

    # Return 200 regardless: HTMX only swaps 2xx responses by default, and
    # we rely on the re-rendered panel (save_ok + save_message) to show the
    # red flash banner with the error.
    return templates.TemplateResponse(request, "partials/history_file_panel.html", {
        "name":         name,
        "logical":      logical,
        "versions":     versions,
        "current_ver":  version,
        "text":         display_text,
        "read_ok":      read_ok,
        "read_message": "" if read_ok else text,
        "editable":     is_editable(logical),
        "editable_any": is_editable(logical),
        "save_ok":      ok,
        "save_message": message,
    })


@router.get("/pipelines/{name}/history/diff", response_class=HTMLResponse)
async def history_diff(
    request: Request,
    name:    str,
    logical: str,
    a:       str,
    b:       str,
):
    _require_versioning()
    pl_dir = _pipeline_dir(name)
    diff   = render_diff(pl_dir, logical, a, b)
    return templates.TemplateResponse(request, "partials/history_diff.html", {
        "logical": logical,
        "diff":    diff,
    })


