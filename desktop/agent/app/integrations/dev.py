"""Developer dashboard sources: git status of registered projects, dev-server ports, running dev apps.

Read-only. Only folders listed in config.toml are inspected, and only with fixed git commands.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

from app.core.config import ProjectConfig

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _git(path: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def parse_status(output: str) -> dict[str, Any]:
    """Parses `git status --porcelain=v2 --branch`."""
    branch, ahead, behind, changed, untracked = None, 0, 0, 0, 0
    for line in output.splitlines():
        if line.startswith("# branch.head "):
            branch = line.removeprefix("# branch.head ")
        elif line.startswith("# branch.ab "):
            plus, minus = line.removeprefix("# branch.ab ").split()
            ahead, behind = int(plus), abs(int(minus))
        elif line.startswith("? "):
            untracked += 1
        elif line and line[0] in "12u":
            changed += 1
    return {
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "changed_files": changed,
        "untracked_files": untracked,
        "dirty": bool(changed or untracked),
    }


def project_status(project: ProjectConfig) -> dict[str, Any]:
    base: dict[str, Any] = {"id": project.name, "name": project.name}
    out = _git(project.path, "status", "--porcelain=v2", "--branch")
    if out is None:
        return {**base, "error": "not_a_git_repository"}
    return {**base, **parse_status(out)}


def project_commits(project: ProjectConfig, limit: int = 10) -> list[dict[str, str]]:
    out = _git(project.path, "log", f"-{limit}", "--pretty=format:%h%x1f%s%x1f%an%x1f%cr")
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append(
                {"hash": parts[0], "subject": parts[1][:120], "author": parts[2], "when": parts[3]}
            )
    return commits


class ProjectsCollector:
    topic = "dev.projects"
    interval_s = 10.0
    min_interval_s = 5.0

    def __init__(self, projects: list[ProjectConfig]) -> None:
        self._projects = projects

    def unsupported_reason(self) -> str | None:
        return None

    def sample(self) -> dict[str, Any]:
        return {"projects": [project_status(p) for p in self._projects]}


def _stem(name: str) -> str:
    return name.lower().removesuffix(".exe")


class DevEnvironmentCollector:
    """Listening TCP ports owned by known dev tools, and which dev applications are running."""

    topic = "dev.ports"
    interval_s = 10.0
    min_interval_s = 5.0

    def __init__(self, process_names: list[str], server_names: list[str]) -> None:
        self._names = {n.lower() for n in process_names}
        # Editors open many internal ports; only processes that actually serve are listed as servers.
        self._servers = {n.lower() for n in server_names}

    def unsupported_reason(self) -> str | None:
        return None

    def sample(self) -> dict[str, Any]:
        names: dict[int, str] = {}
        apps: dict[str, int] = {}
        for proc in psutil.process_iter(["pid", "name"]):
            name = proc.info["name"] or ""
            names[proc.info["pid"]] = name
            if _stem(name) in self._names:
                apps[_stem(name)] = apps.get(_stem(name), 0) + 1

        listeners: dict[int, dict[str, Any]] = {}
        try:
            connections = psutil.net_connections(kind="tcp")
        except psutil.AccessDenied:
            connections = []
        for conn in connections:
            if conn.status != psutil.CONN_LISTEN or not conn.laddr or conn.pid is None:
                continue
            name = names.get(conn.pid, "")
            if _stem(name) in self._servers:
                listeners[conn.laddr.port] = {"port": conn.laddr.port, "pid": conn.pid, "process": name}
        return {
            "listeners": sorted(listeners.values(), key=lambda item: item["port"]),
            "apps": [{"name": n, "count": c} for n, c in sorted(apps.items())],
        }
