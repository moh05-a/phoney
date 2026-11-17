"""Installs and removes the hook entries that make Claude Code and Codex report to the agent.

Nothing here runs unless the owner types the command. The change is printed and confirmed first,
and the previous settings file is kept next to the original as a backup.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

EVENTS = {
    "claude-code": ["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop", "Notification"],
    "codex": ["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop", "PermissionRequest"],
}
MARKER = "/local/hooks/"


def settings_path(tool: str) -> Path:
    return Path.home() / (".claude/settings.json" if tool == "claude-code" else ".codex/hooks.json")


def handler(tool: str, port: int) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}{MARKER}{tool}"
    if tool == "claude-code":
        # Claude Code posts the event JSON itself. A failed or slow post never blocks the session.
        return {"type": "http", "url": url, "timeout": 5}
    # Codex runs a command and pipes the event JSON to it; the forwarder posts it to the agent.
    command = f'"{sys.executable}" -m app.integrations.hook_forward codex {port}'
    return {"type": "command", "command": command, "timeout": 5}


def _is_ours(entry: dict[str, Any]) -> bool:
    text = str(entry.get("url", "")) + str(entry.get("command", ""))
    return MARKER in text or "app.integrations.hook_forward" in text


def _is_permission(entry: dict[str, Any]) -> bool:
    return _is_ours(entry) and str(entry.get("url", "")).endswith("/permission")


def _is_session(entry: dict[str, Any]) -> bool:
    return _is_ours(entry) and not _is_permission(entry)


def add_hooks(settings: dict[str, Any], tool: str, port: int) -> dict[str, Any]:
    """Returns settings with our handlers added. Existing hooks are left exactly as they are."""
    result = remove_hooks(settings, _is_session)
    hooks = result.setdefault("hooks", {})
    for event in EVENTS[tool]:
        hooks.setdefault(event, []).append({"hooks": [handler(tool, port)]})
    return result


def permission_handler(port: int, wait_s: int) -> dict[str, Any]:
    # The agent answers within wait_s; the margin keeps Claude Code from giving up first.
    return {
        "type": "http",
        "url": f"http://127.0.0.1:{port}{MARKER}claude-code/permission",
        "timeout": wait_s + 15,
    }


def add_permission_hook(settings: dict[str, Any], port: int, wait_s: int) -> dict[str, Any]:
    result = remove_hooks(settings, _is_permission)
    result.setdefault("hooks", {}).setdefault("PermissionRequest", []).append(
        {"hooks": [permission_handler(port, wait_s)]}
    )
    return result


def remove_hooks(settings: dict[str, Any], match: Any = _is_ours) -> dict[str, Any]:
    result = json.loads(json.dumps(settings))  # deep copy
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event in list(hooks):
        groups = []
        for group in hooks[event] if isinstance(hooks[event], list) else []:
            kept = [h for h in group.get("hooks", []) if not match(h)]
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        del result["hooks"]
    return result


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} does not contain a JSON object; not touching it.")
    return data


def _confirm_and_write(path: Path, updated: dict[str, Any], key: str) -> bool:
    print(f"File: {path}\nNew '{key}' value:\n")
    print(json.dumps(updated.get(key, "(removed)"), indent=2))
    if input("\nWrite this change? [y/N] ").strip().lower() != "y":
        print("Cancelled. Nothing was written.")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, path.with_suffix(path.suffix + ".phoney-backup"))
    path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    return True


def status_line(port: int) -> dict[str, Any]:
    command_line = f'"{sys.executable}" -m app.integrations.statusline_forward {port}'
    return {"type": "command", "command": command_line}


def _status_line_is_ours(settings: dict[str, Any]) -> bool:
    current = settings.get("statusLine")
    return isinstance(current, dict) and "statusline_forward" in str(current.get("command", ""))


def set_status_line(settings: dict[str, Any], port: int) -> dict[str, Any]:
    """Returns settings with our status line. Refuses to replace one the user already configured."""
    if "statusLine" in settings and not _status_line_is_ours(settings):
        raise ValueError("a status line is already configured")
    return {**settings, "statusLine": status_line(port)}


def remove_status_line(settings: dict[str, Any]) -> dict[str, Any]:
    if not _status_line_is_ours(settings):
        return settings
    return {k: v for k, v in settings.items() if k != "statusLine"}


def usage_command(action: str, port: int) -> int:
    path = settings_path("claude-code")
    current = _load(path)
    if action == "show":
        print(f"This would be set in {path}:\n")
        print(json.dumps({"statusLine": status_line(port)}, indent=2))
        return 0
    try:
        updated = remove_status_line(current) if action == "uninstall" else set_status_line(current, port)
    except ValueError:
        print(
            "You already have a status line configured in Claude Code, and Phoney will not replace it.\n"
            "To forward usage anyway, call the forwarder from your own script:\n"
            f"  {status_line(port)['command']}"
        )
        return 1
    if updated == current:
        print("Nothing to change.")
        return 0
    if not _confirm_and_write(path, updated, "statusLine"):
        return 1
    print("Done. Restart Claude Code sessions to pick it up.")
    return 0


def permission_command(action: str, port: int) -> int:
    from app.core.config import default_data_dir, load_config

    wait_s = load_config(default_data_dir()).integrations.permission_wait_s
    path = settings_path("claude-code")
    current = _load(path)
    if action == "show":
        print(f"This entry would be merged into {path}:\n")
        print(
            json.dumps(
                {"hooks": {"PermissionRequest": [{"hooks": [permission_handler(port, wait_s)]}]}}, indent=2
            )
        )
        return 0
    if action == "uninstall":
        updated = remove_hooks(current, _is_permission)
    else:
        updated = add_permission_hook(current, port, wait_s)
    if updated == current:
        print("Nothing to change.")
        return 0
    if not _confirm_and_write(path, updated, "hooks"):
        return 1
    print("Done. Restart Claude Code sessions to pick it up.")
    return 0


def command(action: str, tool: str, port: int) -> int:
    if tool == "claude-usage":
        return usage_command(action, port)
    if tool == "claude-permissions":
        return permission_command(action, port)
    if tool not in EVENTS:
        print(f"Unknown tool '{tool}'. Choose one of: {', '.join(EVENTS)}")
        return 2
    path = settings_path(tool)
    current = _load(path)
    updated = remove_hooks(current) if action == "uninstall" else add_hooks(current, tool, port)

    if action == "show":
        print(f"These entries would be merged into {path}:\n")
        print(json.dumps({"hooks": {e: [{"hooks": [handler(tool, port)]}] for e in EVENTS[tool]}}, indent=2))
        return 0
    if updated == current:
        print("Nothing to change.")
        return 0
    if not _confirm_and_write(path, updated, "hooks"):
        return 1
    print(
        "Done." + (" In Codex, run /hooks once to review and trust the new hooks." if tool == "codex" else "")
    )
    return 0
