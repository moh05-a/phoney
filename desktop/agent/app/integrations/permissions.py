"""Answering Claude Code permission prompts from the phone.

Claude Code calls a `PermissionRequest` hook just before it would ask "allow this?" in the terminal.
The agent holds that call open, shows the request on the phone, and returns the answer. If nobody
answers in time, it returns nothing and Claude Code shows its normal prompt, so the terminal always
remains a way to answer.

Requests live in memory only, for as long as they are pending.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Literal

from app.database.models import iso, utcnow

Decision = Literal["allow", "deny"]
MAX_PENDING = 20
SUMMARY_LIMIT = 300


def summarize(tool_name: str, tool_input: Any) -> str:
    """One short line telling the owner what is being asked. Shown on the phone, never stored."""
    data = tool_input if isinstance(tool_input, dict) else {}
    for key in ("command", "file_path", "notebook_path", "url", "path", "pattern", "query"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value[:SUMMARY_LIMIT]
    try:
        return json.dumps(data, ensure_ascii=False)[:SUMMARY_LIMIT] if data else tool_name
    except (TypeError, ValueError):
        return tool_name


@dataclass
class PendingRequest:
    id: str
    tool: str
    project: str
    tool_name: str
    summary: str
    created_at: str
    expires_in_s: int
    decision: Decision | None = None
    answered: asyncio.Event = field(default_factory=asyncio.Event)
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_running_loop)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "project": self.project,
            "tool_name": self.tool_name,
            "summary": self.summary,
            "created_at": self.created_at,
            "expires_in_s": self.expires_in_s,
        }


class PermissionBroker:
    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._pending: dict[str, PendingRequest] = {}
        self._on_change = on_change

    def pending(self) -> dict[str, Any]:
        return {"requests": [request.public() for request in self._pending.values()]}

    def open(self, tool: str, payload: dict[str, Any], wait_s: int) -> PendingRequest | None:
        if len(self._pending) >= MAX_PENDING:
            return None
        cwd = payload.get("cwd")
        request = PendingRequest(
            id="p_" + secrets.token_hex(6),
            tool=tool,
            project=PurePath(cwd).name[:60] if isinstance(cwd, str) and cwd else "unknown",
            tool_name=str(payload.get("tool_name") or "Tool")[:60],
            summary=summarize(str(payload.get("tool_name") or ""), payload.get("tool_input")),
            created_at=iso(utcnow()) or "",
            expires_in_s=wait_s,
        )
        self._pending[request.id] = request
        self._changed()
        return request

    def answer(self, request_id: str, decision: Decision) -> PendingRequest | None:
        request = self._pending.get(request_id)
        if request is None or request.decision is not None:
            return None
        request.decision = decision
        # The waiter may live on another event loop thread (it does in tests), so wake it safely.
        request.loop.call_soon_threadsafe(request.answered.set)
        return request

    async def wait(self, request: PendingRequest, timeout_s: float) -> Decision | None:
        """The decision, or None when time ran out. Always removes the request."""
        try:
            await asyncio.wait_for(request.answered.wait(), timeout_s)
        except TimeoutError:
            pass
        finally:
            self._pending.pop(request.id, None)
            self._changed()
        return request.decision

    def _changed(self) -> None:
        if self._on_change:
            self._on_change()


def hook_response(decision: Decision | None) -> dict[str, Any]:
    """The JSON Claude Code expects. An empty object means "no decision: ask in the terminal"."""
    if decision is None:
        return {}
    behavior: dict[str, Any] = {"behavior": decision}
    if decision == "deny":
        behavior["message"] = "Denied from the Phoney panel."
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": behavior}}
