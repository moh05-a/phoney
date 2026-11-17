"""System-tray icon, so the agent can run without a console window.

Optional (`pip install -e ".[tray]"`). Without the packages the agent runs exactly as before.
"""

from __future__ import annotations

import logging
import os
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core.context import AgentContext

log = logging.getLogger("phoney.tray")
ICON_FILE = Path(__file__).resolve().parent / "web" / "icon-180.png"


def menu_actions(
    ctx: AgentContext, stop: Callable[[], None], open_target: Callable[[str], Any] | None = None
) -> list[tuple[str, Callable[[], None]]]:
    """The tray menu as (label, action) pairs. Kept separate from pystray so it can be tested."""
    open_url = open_target or webbrowser.open
    open_folder = open_target or os.startfile  # type: ignore[attr-defined]
    base = f"http://127.0.0.1:{ctx.config.server.local_port}"
    return [
        ("Open status page", lambda: open_url(f"{base}/")),
        ("Pair a phone", lambda: open_url(f"{base}/pair")),
        ("Open data folder", lambda: open_folder(str(ctx.data_dir))),
        ("Quit Phoney", stop),
    ]


def start_tray(ctx: AgentContext, stop: Callable[[], None]) -> Any | None:
    """Shows the icon on its own thread. Returns the icon (call .stop() on shutdown) or None."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        log.info("tray icon not shown (install the 'tray' extra to get one)")
        return None

    def guarded(action: Callable[[], None]) -> Callable[..., None]:
        def run(*_: Any) -> None:
            try:
                action()
            except Exception:
                log.exception("tray menu action failed")

        return run

    actions = menu_actions(ctx, stop)
    items = [
        pystray.MenuItem(label, guarded(action), default=(index == 0))
        for index, (label, action) in enumerate(actions[:-1])
    ]
    quit_label, quit_action = actions[-1]
    items += [pystray.Menu.SEPARATOR, pystray.MenuItem(quit_label, guarded(quit_action))]
    icon = pystray.Icon(
        "phoney", Image.open(ICON_FILE), f"Phoney agent · {ctx.config.server.name}", pystray.Menu(*items)
    )
    threading.Thread(target=icon.run, name="tray", daemon=True).start()
    log.info("tray icon started")
    return icon
