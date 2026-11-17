"""AI coding-tool sessions, built from hook events that Claude Code and Codex send to the local listener.

Only what the dashboard shows is kept: tool, project folder name, state and timestamps.
Prompt text, tool inputs and transcripts are dropped on arrival. State lives in memory only.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

from app.database.models import iso, utcnow

TOOLS = {"claude-code": "Claude Code", "codex": "Codex"}

# hook_event_name -> (new session state, event kind shown on the phone)
_EVENTS: dict[str, tuple[str, str]] = {
    "SessionStart": ("idle", "started"),
    "UserPromptSubmit": ("working", "prompt"),
    "Stop": ("idle", "finished"),
    "Notification": ("needs_input", "needs_input"),
    "PermissionRequest": ("needs_input", "needs_input"),
    "SessionEnd": ("ended", "ended"),
}
_SUMMARIES = {
    "started": "Session started",
    "prompt": "Working",
    "finished": "Finished, waiting for you",
    "needs_input": "Needs your input",
    "ended": "Session ended",
}
# Claude Code also sends Notification for things that need no attention.
_ATTENTION_NOTIFICATIONS = {"permission_prompt", "idle_prompt", "elicitation_dialog"}


class AiStore:
    def __init__(self, on_change: Callable[[dict[str, Any] | None], None] | None = None) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=200)
        self._next_id = 1
        self._on_change = on_change
        self._usage: dict[str, Any] = {"five_hour": None, "seven_day": None, "updated_at": None}

    def ingest(self, tool: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Returns the recorded event, or None when the hook payload is not one we track."""
        mapping = _EVENTS.get(str(payload.get("hook_event_name", "")))
        session_id = payload.get("session_id")
        if mapping is None or not isinstance(session_id, str) or not session_id:
            return None
        if payload.get("hook_event_name") == "Notification":
            kind_of_notice = payload.get("notification_type")
            if kind_of_notice is not None and kind_of_notice not in _ATTENTION_NOTIFICATIONS:
                return None
        state, kind = mapping
        now = iso(utcnow())
        key = f"{tool}:{session_id[:64]}"
        cwd = payload.get("cwd")
        project = PurePath(cwd).name[:60] if isinstance(cwd, str) and cwd else "unknown"

        session = self._sessions.get(key)
        if session is None:
            session = {"id": key, "tool": tool, "project": project, "started_at": now}
            self._sessions[key] = session
        session.update(state=state, last_activity_at=now, project=project)
        if state == "ended":
            del self._sessions[key]

        event = {
            "id": self._next_id,
            "ts": now,
            "tool": tool,
            "session_id": key,
            "project": project,
            "kind": kind,
            "summary": _SUMMARIES[kind],
        }
        self._next_id += 1
        self._events.append(event)
        if self._on_change:
            self._on_change(event)
        return event

    def sessions(self) -> dict[str, Any]:
        ordered = sorted(self._sessions.values(), key=lambda s: s["last_activity_at"], reverse=True)
        return {"sessions": ordered}

    def set_usage(self, payload: dict[str, Any]) -> bool:
        """Takes Claude Code status-line data and keeps only percent used and reset time per window.

        Returns False, changing nothing, when the payload carries no usable rate-limit numbers
        (they are absent for accounts without a subscription and before the first reply).
        """
        limits = payload.get("rate_limits")
        if not isinstance(limits, dict):
            return False
        windows: dict[str, Any] = {}
        for key in ("five_hour", "seven_day"):
            window = limits.get(key)
            percent = window.get("used_percentage") if isinstance(window, dict) else None
            if isinstance(percent, bool) or not isinstance(percent, int | float) or not 0 <= percent <= 1000:
                continue
            resets = window.get("resets_at")
            valid = isinstance(resets, int | float) and not isinstance(resets, bool) and 0 < resets < 4e10
            windows[key] = {
                "percent": round(float(percent), 1),
                "resets_at": iso(datetime.fromtimestamp(resets, UTC)) if valid else None,
            }
        if not windows:
            return False
        self._usage = {"five_hour": None, "seven_day": None, **windows, "updated_at": iso(utcnow())}
        return True

    def usage(self) -> dict[str, Any]:
        return dict(self._usage)

    def events(self, since: int = 0) -> list[dict[str, Any]]:
        return [e for e in self._events if e["id"] > since]
