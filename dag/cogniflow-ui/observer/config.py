"""Cogniflow Observer — configuration loaded from config.json."""
from __future__ import annotations
import json
from pathlib import Path


_CONFIG_DIR  = Path(__file__).parent
_CONFIG_FILE = _CONFIG_DIR / "config.json"


class Settings:
    def __init__(self) -> None:
        data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        orch_raw = Path(data.get("orchestrator_root", "."))
        self.orchestrator_root:   Path = (orch_raw if orch_raw.is_absolute() else _CONFIG_DIR / orch_raw).resolve()
        self.pipelines_root:      Path = self.orchestrator_root / "pipelines"
        self.poll_interval_ms:    int  = int(data.get("poll_interval_ms", 2000))
        self.model_context_limit: int  = int(data.get("model_context_limit", 180000))
        self.event_tail_lines:    int  = int(data.get("event_tail_lines", 50))
        self.app_title:           str  = data.get("app_title", "Cogniflow Observer")
        self.approver:            str  = data.get("approver", "operator")
        self.host:                str  = data.get("host", "0.0.0.0")
        self.port:                int  = int(data.get("port", 8000))
        self.versioning:          bool = bool(data.get("versioning", False))


settings = Settings()
