"""Status-line command for Claude Code that also tells the Phoney agent how much of the plan is used.

Claude Code runs the configured status-line command with session data as JSON on stdin and shows
whatever the command prints. This script forwards only the rate-limit numbers to the agent's local
listener, then prints a short status line so the terminal still shows something useful.
It never fails Claude Code: any problem ends in a plain status line and exit code 0.

Usage: python -m app.integrations.statusline_forward <port>
"""

from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any


def status_text(data: dict[str, Any]) -> str:
    """What the terminal shows: model, context use, and the 5-hour window when known."""
    parts = []
    model = (data.get("model") or {}).get("display_name")
    if model:
        parts.append(str(model))
    context = (data.get("context_window") or {}).get("used_percentage")
    if isinstance(context, int | float):
        parts.append(f"ctx {context:.0f}%")
    five_hour = ((data.get("rate_limits") or {}).get("five_hour") or {}).get("used_percentage")
    if isinstance(five_hour, int | float):
        parts.append(f"5h {five_hour:.0f}%")
    return " | ".join(parts) or "Claude Code"


def main() -> int:
    try:
        data = json.loads(sys.stdin.buffer.read(256 * 1024))
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        data = {}
    limits = data.get("rate_limits")
    if isinstance(limits, dict):
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{int(sys.argv[1])}/local/statusline",
                data=json.dumps({"rate_limits": limits}).encode(),  # nothing else leaves this process
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(request, timeout=1).close()
        except Exception:  # agent not running: the status line must still appear
            pass
    print(status_text(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
