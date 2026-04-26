"""
Cogniflow UI — bundled launcher.

This is the entry point for the PyInstaller bundle. Behaviour:

  1. Read host / port from the user-editable `config.json` (beside the exe
     when frozen; in the project root when running from source).
  2. Open the user's default browser at http://host:port after a short
     delay, so the page appears once uvicorn has bound the socket.
  3. Run uvicorn in the foreground. Closing the console window terminates
     the server.

Reload mode is intentionally disabled in the bundle — file watching is
not useful in a packaged distribution and would prevent a clean exit.
"""
from __future__ import annotations
import threading
import time
import webbrowser

import uvicorn

from app import app, HOST, PORT, APP_TITLE, APP_VERSION


def _open_browser_when_ready(url: str, delay_s: float = 1.5) -> None:
    """Open the user's default browser after `delay_s` seconds. Runs in a
    daemon thread so it never blocks shutdown."""
    def opener():
        time.sleep(delay_s)
        try:
            webbrowser.open(url, new=2)
        except Exception as e:
            # Browser autostart is a nicety, not a hard requirement. If it
            # fails (no GUI, locked-down system), the user can still type
            # the URL manually.
            print(f"[launch] couldn't open browser automatically: {e}", flush=True)

    threading.Thread(target=opener, daemon=True).start()


def main() -> None:
    url = f"http://{HOST}:{PORT}"
    print("=" * 60, flush=True)
    print(f"  {APP_TITLE} v{APP_VERSION}", flush=True)
    print(f"  Serving on {url}", flush=True)
    print(f"  Close this window to stop the server.", flush=True)
    print("=" * 60, flush=True)

    _open_browser_when_ready(url)

    # Pass the app object directly (not "app:app" string) so PyInstaller
    # bundles don't need module re-resolution at runtime.
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
