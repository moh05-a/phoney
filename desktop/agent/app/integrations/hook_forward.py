"""Tiny forwarder for tools whose hooks run a command: reads the event JSON on stdin and posts it
to the agent's local listener. Never fails the calling tool, whatever happens.

Usage: python -m app.integrations.hook_forward <tool> <port>
"""

from __future__ import annotations

import json
import sys
import urllib.request


def main() -> int:
    raw = sys.stdin.buffer.read(256 * 1024)
    try:
        tool, port = sys.argv[1], int(sys.argv[2])
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/local/hooks/{tool}",
            data=raw,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=2).close()
    except Exception:  # agent not running, bad arguments: the coding tool must not notice
        pass
    try:
        # Codex requires JSON on stdout for Stop; an empty object means "carry on".
        if json.loads(raw).get("hook_event_name") == "Stop":
            print("{}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
