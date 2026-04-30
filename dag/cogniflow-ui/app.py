"""
Cogniflow UI v3.5 — unified FastAPI app combining the Observer and the
Configurator.

Layout:
  - Observer routes mounted at root: "/", "/pipelines/{name}/...",
    "/pipelines/{name}/history/...", "/observer/vault*"
  - Configurator routes mounted alongside: "/configurator", "/pipeline/{name}/...",
    "/templates", "/prompt-templates", "/vault*"
  - Static assets isolated per package: /static/observer/* and /static/configurator/*
  - On startup, bundled `seed_pipelines/` are overlaid onto the orchestrator's
    pipelines folder (see seeding.py).

Note: the observer's vault viewer was relocated from `/vault*` to
`/observer/vault*` to avoid colliding with the configurator's vault CRUD.

Run from source:    uvicorn app:app --reload
Run as exe:         see launch.py for the bundled entry point.
"""
from __future__ import annotations
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from observer.app import router as observer_router
from observer.config import settings as observer_settings
from configurator.app import router as configurator_router
from configurator.config import settings as configurator_settings
from seeding import seed_pipelines
from paths import bundle_dir, user_dir, user_config_path, is_frozen


BUNDLE_DIR = bundle_dir()
USER_DIR = user_dir()

logger = logging.getLogger("cogniflow-ui")


def _load_top_config() -> dict:
    """Read the user-editable top-level `config.json`. Beside the exe when
    frozen; in the project root when running from source."""
    p = user_config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{p} is not valid JSON: {e}") from e
    return {}


_cfg = _load_top_config()
APP_TITLE = _cfg.get("app_title", "Cogniflow UI")
APP_VERSION = str(_cfg.get("app_version", "0.0.0"))
HOST = _cfg.get("host", "127.0.0.1")
PORT = int(_cfg.get("port", 8000))


def _resolve_orchestrator_root() -> Path | None:
    """Resolve `orchestrator_root` from the top-level config.

    Relative paths are resolved against USER_DIR (i.e. beside the .exe in a
    frozen build, or the project root when running from source). Returns
    None when the field is absent — in that case we fall back to whatever
    the bundled subpackage configs say.
    """
    raw = _cfg.get("orchestrator_root")
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = USER_DIR / p
    return p.resolve()


_orch_override = _resolve_orchestrator_root()
if _orch_override is not None:
    # Top-level config wins. The observer's settings model an
    # orchestrator_root + pipelines_root pair; the configurator's settings
    # store pipelines_root directly. Push both consistently so a single
    # edit beside the exe reaches both subsystems.
    observer_settings.orchestrator_root = _orch_override
    observer_settings.pipelines_root = (_orch_override / "pipelines").resolve()
    configurator_settings.pipelines_root = (_orch_override / "pipelines").resolve()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: overlay bundled seed pipelines onto the orchestrator's
    pipelines folder. Idempotent across same-version restarts. No-op if
    seed_pipelines/ doesn't exist (e.g. running from source without seeds)."""
    seed_root = BUNDLE_DIR / "seed_pipelines"
    pipelines_root = observer_settings.pipelines_root
    result = seed_pipelines(
        seed_root=seed_root,
        pipelines_root=pipelines_root,
        app_version=APP_VERSION,
    )
    if result["skipped"]:
        reason = result.get("reason", f"version={result['version_after']}")
        print(
            f"[seed] skipped ({reason}, target={pipelines_root})",
            flush=True,
        )
    else:
        print(
            f"[seed] {result['version_before'] or '<none>'} -> "
            f"{result['version_after']}: {len(result['seeded'])} pipelines / "
            f"{result['files_written']} files written into {pipelines_root}",
            flush=True,
        )
    yield


app = FastAPI(title=APP_TITLE, lifespan=lifespan)

# Per-package static mounts — keeps the two `app.js` files from colliding.
app.mount(
    "/static/observer",
    StaticFiles(directory=str(BUNDLE_DIR / "observer" / "static")),
    name="static-observer",
)
app.mount(
    "/static/configurator",
    StaticFiles(directory=str(BUNDLE_DIR / "configurator" / "static")),
    name="static-configurator",
)

# Routers. URL paths are disjoint:
#   - Observer: "/", "/pipelines/..." (plural), "/observer/vault*"
#   - Configurator: "/configurator", "/pipeline/..." (singular),
#     "/templates", "/prompt-templates", "/vault*"
app.include_router(observer_router)
app.include_router(configurator_router)


if __name__ == "__main__":
    import threading
    import time
    import webbrowser
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    url = f"http://{HOST}:{PORT}"

    def _open_browser_when_ready(delay_s: float = 1.5) -> None:
        time.sleep(delay_s)
        try:
            webbrowser.open(url, new=2)
        except Exception as e:
            print(f"[app] couldn't open browser automatically: {e}", flush=True)

    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    uvicorn.run("app:app", host=HOST, port=PORT, reload=True)
