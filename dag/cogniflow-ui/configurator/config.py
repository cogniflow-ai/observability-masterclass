"""Cogniflow Configurator — configuration.

Settings are loaded from config.json next to this file. Environment variables
of the same name (uppercase) still override individual fields for quick local
tweaks (e.g. CFG_PORT=8002 uvicorn ...).
"""
from __future__ import annotations
import json
import os
import shutil
import sys
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def _find_claude() -> str:
    """Locate the claude CLI. Mirrors the orchestrator's search order."""
    env_bin = os.getenv("CLAUDE_BIN")
    if env_bin:
        return env_bin
    if sys.platform == "win32":
        search_names = ("claude.cmd", "claude.exe", "claude.bat", "claude")
    else:
        search_names = ("claude",)
    for name in search_names:
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
            Path(os.environ.get("APPDATA", ""))      / "npm"     / "claude.cmd",
            Path("C:/Program Files/Anthropic/Claude/claude.exe"),
        ]
    else:
        candidates = [
            Path("/usr/local/bin/claude"),
            Path("/opt/homebrew/bin/claude"),
            Path.home() / ".local" / "bin" / "claude",
            Path.home() / ".npm-global" / "bin" / "claude",
        ]
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"config.json is not valid JSON: {e}") from e


def _get(cfg: dict, key: str, default):
    env_val = os.getenv(key.upper())
    if env_val is not None and env_val != "":
        return env_val
    return cfg.get(key, default)


def _parse_tag_list(raw) -> list[str]:
    """Normalise a taglines list coming from JSON (list) or env var (CSV)."""
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


class Settings:
    def __init__(self) -> None:
        cfg = _load_config()
        raw = Path(_get(cfg, "pipelines_root", "."))
        self.pipelines_root: Path = (
            raw if raw.is_absolute() else CONFIG_PATH.parent / raw
        ).resolve()
        self.cfg_port: int = int(_get(cfg, "cfg_port", 8001))
        self.cfg_max_versions: int = int(_get(cfg, "cfg_max_versions", 50))
        readonly_raw = _get(cfg, "cfg_readonly", False)
        self.cfg_readonly: bool = (
            readonly_raw if isinstance(readonly_raw, bool)
            else str(readonly_raw).lower() == "true"
        )
        self.model_context_limit: int = int(
            _get(cfg, "model_context_limit", 180000)
        )
        self.app_title: str = _get(cfg, "app_title", "Cogniflow Configurator")
        self._trash_override = _get(cfg, "cfg_trash_dir", "")
        self._prompt_tpl_override = _get(cfg, "prompt_templates_dir", "")
        self._claude_bin_override: str = _get(cfg, "claude_bin", "") or ""
        self.meta_prompt_timeout_s: int = int(
            _get(cfg, "meta_prompt_timeout_s", 300)
        )
        enable_specialize_raw = _get(cfg, "enable_specialize", True)
        self.enable_specialize: bool = (
            enable_specialize_raw if isinstance(enable_specialize_raw, bool)
            else str(enable_specialize_raw).lower() == "true"
        )
        enable_pipeline_templates_raw = _get(cfg, "enable_pipeline_templates", True)
        self.enable_pipeline_templates: bool = (
            enable_pipeline_templates_raw if isinstance(enable_pipeline_templates_raw, bool)
            else str(enable_pipeline_templates_raw).lower() == "true"
        )
        self.default_taglines_system: list[str] = _parse_tag_list(
            _get(cfg, "default_taglines_system",
                 ["role", "responsibilities", "guardrails"]))
        self.default_taglines_task: list[str] = _parse_tag_list(
            _get(cfg, "default_taglines_task",
                 ["description", "goals", "context", "input",
                  "output", "format", "guardrails"]))
        # Agent types offered in the New/Add Agent modals and the agent
        # detail panel's Type select. Editable per UI version via config.json
        # (or the AGENT_TYPES env var, as a CSV). Order is preserved in the UI.
        self.agent_types: list[str] = _parse_tag_list(
            _get(cfg, "agent_types",
                 ["orchestrator", "worker", "reviewer", "synthesizer",
                  "router", "classifier", "validator", "summarizer"]))

    @property
    def cfg_trash_dir(self) -> Path:
        raw = self._trash_override
        return Path(raw) if raw else self.pipelines_root / ".trash"

    @property
    def templates_dir(self) -> Path:
        return self.pipelines_root / "templates"

    @property
    def prompt_templates_dir(self) -> Path:
        raw = self._prompt_tpl_override
        if raw:
            return Path(raw).resolve()
        return Path(__file__).resolve().parent / "prompt_templates"

    @property
    def claude_bin(self) -> str:
        """Resolved path to the claude CLI, or empty string if not found."""
        return self._claude_bin_override or _find_claude()


settings = Settings()
