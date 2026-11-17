"""Claude usage counted from Claude Code's own session files.

Claude Code reports exact plan usage (percent of the 5-hour and weekly limits) only to a status-line
command, and the status line runs only in the terminal. The VS Code extension never runs it. So for
everyone else this module reads the session files Claude Code keeps on the PC
(`~/.claude/projects/**/*.jsonl`, or under `CLAUDE_CONFIG_DIR`), which hold the token counts the API
returned for every reply, from the terminal and the IDE extensions alike.

What it keeps: per reply, only a time stamp and a token total. Message text is never stored.
What it cannot know: the plan's limits, which Anthropic does not publish. So it reports tokens and
compares them with your own busiest 5-hour window and busiest week, which makes a useful gauge
without pretending to be the real percentage.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.database.models import iso

WINDOW = timedelta(hours=5)  # Claude's session window
KEEP = timedelta(days=35)  # history used for "your busiest window"
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def claude_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path.home() / ".claude"


def parse_line(raw: bytes) -> tuple[str, datetime, int] | None:
    """One session-file line to (reply id, time, tokens), or None for anything that is not a reply."""
    if b'"assistant"' not in raw or b'"usage"' not in raw:  # cheap filter before parsing JSON
        return None
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return None
    message = entry.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    stamp = entry.get("timestamp")
    if not isinstance(usage, dict) or not isinstance(stamp, str):
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    tokens = sum(
        value
        for key in _TOKEN_FIELDS
        if isinstance(value := usage.get(key), int) and not isinstance(value, bool) and value > 0
    )
    # A reply is written once per content block, all with the same message id and usage.
    reply_id = f"{message.get('id')}:{entry.get('requestId')}" if message.get("id") else entry.get("uuid")
    if not isinstance(reply_id, str) or not reply_id:
        return None
    return reply_id, when, tokens


@dataclass
class SessionFiles:
    """Reads session files incrementally: each poll parses only lines appended since the last one."""

    root: Path
    _offsets: dict[Path, int] = field(default_factory=dict)
    _replies: dict[str, tuple[datetime, int]] = field(default_factory=dict)

    def refresh(self, now: datetime) -> None:
        cutoff = now - KEEP
        try:
            paths = list(self.root.rglob("*.jsonl"))
        except OSError:
            paths = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff.timestamp():
                continue
            offset = self._offsets.get(path, 0)
            if stat.st_size < offset:  # the file was replaced; read it again
                offset = 0
            if stat.st_size != offset:
                self._offsets[path] = self._read(path, offset)
        self._replies = {key: value for key, value in self._replies.items() if value[0] >= cutoff}

    def _read(self, path: Path, offset: int) -> int:
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                for raw in handle:
                    if not raw.endswith(b"\n"):
                        break  # Claude Code is still writing this line; read it next time
                    offset += len(raw)
                    reply = parse_line(raw)
                    if reply is not None:
                        key, when, tokens = reply
                        known = self._replies.get(key)
                        self._replies[key] = (
                            (min(when, known[0]), max(tokens, known[1])) if known else (when, tokens)
                        )
        except OSError:
            pass
        return offset

    def replies(self) -> list[tuple[datetime, int]]:
        return list(self._replies.values())


def summarize(replies: Iterable[tuple[datetime, int]], now: datetime, local_tz: Any = None) -> dict[str, Any]:
    """Current 5-hour window, the last 7 days, and the busiest of each for comparison.

    Windows follow the usual reading of Claude's limits: a window opens at the hour of the first
    reply after the previous one closed, and lasts five hours.
    """
    ordered = sorted(replies)
    blocks: list[dict[str, Any]] = []
    for when, tokens in ordered:
        if not blocks or when >= blocks[-1]["end"]:
            start = when.replace(minute=0, second=0, microsecond=0)
            blocks.append({"start": start, "end": start + WINDOW, "tokens": 0, "messages": 0})
        blocks[-1]["tokens"] += tokens
        blocks[-1]["messages"] += 1

    current = blocks[-1] if blocks and now < blocks[-1]["end"] else None
    five_hour = None
    if current is not None:
        five_hour = {
            "tokens": current["tokens"],
            "messages": current["messages"],
            "started_at": iso(current["start"]),
            "resets_at": iso(current["end"]),
            "peak_tokens": max(block["tokens"] for block in blocks),
        }

    def day_of(when: datetime) -> date:
        return when.astimezone(local_tz).date()

    today = day_of(now)
    daily: dict[date, int] = {}
    for when, tokens in ordered:
        daily[day_of(when)] = daily.get(day_of(when), 0) + tokens
    week_start = now - timedelta(days=7)
    week = [(when, tokens) for when, tokens in ordered if when > week_start]
    span = (today - min(daily)).days if daily else 0
    rolling = [
        sum(daily.get(today - timedelta(days=back + offset), 0) for offset in range(7))
        for back in range(span + 1)
    ]
    week_tokens = sum(tokens for _, tokens in week)
    return {
        "five_hour": five_hour,
        "seven_day": {
            "tokens": week_tokens,
            "messages": len(week),
            "peak_tokens": max([week_tokens, *rolling]),
        },
        "days": [
            {
                "date": (today - timedelta(days=back)).isoformat(),
                "tokens": daily.get(today - timedelta(days=back), 0),
            }
            for back in range(6, -1, -1)
        ],
    }


class ClaudeLocalUsageCollector:
    topic = "ai.local_usage"
    interval_s = 60.0
    min_interval_s = 30.0

    def __init__(self, home: Path | None = None) -> None:
        self._files = SessionFiles((home or claude_home()) / "projects")

    def unsupported_reason(self) -> str | None:
        return None if self._files.root.is_dir() else "claude_code_not_found"

    def sample(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        self._files.refresh(now)
        return {**summarize(self._files.replies(), now), "updated_at": iso(now)}
