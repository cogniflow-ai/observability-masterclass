"""
Cogniflow UI — path resolution helpers.

When packaged with PyInstaller (`sys.frozen` is True):
  * `bundle_dir()` is the temporary extraction dir (`sys._MEIPASS`)
    where bundled read-only resources live (templates, static, seed_pipelines).
  * `user_dir()` is the directory containing the executable, where the
    user-editable `config.json` lives — they can edit it without unpacking
    the bundle.

When running from source (`python -m uvicorn app:app` or `python launch.py`):
  * Both `bundle_dir()` and `user_dir()` return the project root
    (the directory containing `app.py`).
"""
from __future__ import annotations
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def bundle_dir() -> Path:
    """Read-only resource root."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).parent
    return Path(__file__).parent


def user_dir() -> Path:
    """Directory holding the user-editable `config.json`. Beside the exe
    when frozen; the project root when running from source."""
    if is_frozen():
        return Path(sys.executable).parent
    return Path(__file__).parent


def user_config_path() -> Path:
    return user_dir() / "config.json"
