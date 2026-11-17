"""GitHub overview through the official `gh` command-line tool.

The agent never sees or stores a GitHub token: it runs `gh`, which uses the login you already did
with `gh auth login`. Everything here is read-only: your open pull requests, pull requests waiting
for your review, recently pushed repositories and your latest activity.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

QUERY = """
query {
  viewer {
    login
    pullRequests(first: 10, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title isDraft updatedAt reviewDecision
        repository { nameWithOwner }
        commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
      }
    }
    repositories(first: 6, orderBy: {field: PUSHED_AT, direction: DESC},
                 ownerAffiliations: [OWNER, COLLABORATOR]) {
      nodes { nameWithOwner pushedAt isPrivate defaultBranchRef { name } }
    }
  }
  reviews: search(query: "is:open is:pr review-requested:@me archived:false", type: ISSUE, first: 10) {
    nodes {
      ... on PullRequest { number title updatedAt repository { nameWithOwner } author { login } }
    }
  }
}
"""

_CHECKS = {
    "SUCCESS": "passing",
    "FAILURE": "failing",
    "ERROR": "failing",
    "PENDING": "running",
    "EXPECTED": "running",
}
_REVIEW = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes_requested",
    "REVIEW_REQUIRED": "review_required",
}


def _gh(*args: str, timeout: int = 20) -> str | None:
    try:
        done = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def parse_overview(data: dict[str, Any]) -> dict[str, Any]:
    viewer = data.get("viewer") or {}
    prs = []
    for node in (viewer.get("pullRequests") or {}).get("nodes") or []:
        commits = (node.get("commits") or {}).get("nodes") or []
        rollup = (commits[0].get("commit") or {}).get("statusCheckRollup") if commits else None
        prs.append(
            {
                "repo": node["repository"]["nameWithOwner"],
                "number": node["number"],
                "title": node["title"][:140],
                "draft": bool(node.get("isDraft")),
                "checks": _CHECKS.get((rollup or {}).get("state")),  # None: the repository has no checks
                "review": _REVIEW.get(node.get("reviewDecision")),
                "updated_at": node.get("updatedAt"),
            }
        )
    requests = [
        {
            "repo": node["repository"]["nameWithOwner"],
            "number": node["number"],
            "title": node["title"][:140],
            "author": (node.get("author") or {}).get("login"),
            "updated_at": node.get("updatedAt"),
        }
        for node in (data.get("reviews") or {}).get("nodes") or []
        if node  # non-PR search hits come back as empty objects
    ]
    repos = [
        {
            "name": node["nameWithOwner"],
            "pushed_at": node.get("pushedAt"),
            "private": bool(node.get("isPrivate")),
            "branch": (node.get("defaultBranchRef") or {}).get("name"),
        }
        for node in (viewer.get("repositories") or {}).get("nodes") or []
    ]
    return {"login": viewer.get("login"), "prs": prs, "review_requests": requests, "repos": repos}


def parse_events(events: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    """Turns the account's public event feed into short "what I did" lines."""
    lines = []
    for event in events:
        payload = event.get("payload") or {}
        kind = event.get("type")
        ref = str(payload.get("ref") or "").removeprefix("refs/heads/")
        action = payload.get("action")
        if kind == "PushEvent":
            text = f"Pushed to {ref}" if ref else "Pushed"
        elif kind == "PullRequestEvent":
            number = (payload.get("pull_request") or {}).get("number") or payload.get("number")
            text = f"{str(action).capitalize()} pull request" + (f" #{number}" if number else "")
        elif kind == "CreateEvent":
            text = f"Created {payload.get('ref_type', 'repository')}" + (f" {ref}" if ref else "")
        elif kind == "IssuesEvent":
            text = f"{str(action).capitalize()} an issue"
        elif kind in ("IssueCommentEvent", "PullRequestReviewCommentEvent"):
            text = "Commented"
        elif kind == "PullRequestReviewEvent":
            text = "Reviewed a pull request"
        elif kind == "ReleaseEvent":
            text = "Published a release"
        else:
            continue
        lines.append(
            {"repo": (event.get("repo") or {}).get("name"), "text": text, "at": event.get("created_at")}
        )
        if len(lines) >= limit:
            break
    return lines


class GithubCollector:
    topic = "github.overview"
    interval_s = 120.0  # two small API calls per sample: far inside GitHub's rate limits
    min_interval_s = 60.0

    def __init__(self) -> None:
        self._reason: str | None = None
        if shutil.which("gh") is None:
            self._reason = "gh_not_installed"
        elif _gh("auth", "token", timeout=10) is None:
            self._reason = "gh_not_logged_in"

    def unsupported_reason(self) -> str | None:
        return self._reason

    def sample(self) -> dict[str, Any]:
        raw = _gh("api", "graphql", "-f", f"query={QUERY}")
        if raw is None:
            return {
                "error": "github_unreachable",
                "prs": [],
                "review_requests": [],
                "repos": [],
                "activity": [],
            }
        overview = parse_overview(json.loads(raw).get("data") or {})
        events_raw = (
            _gh("api", f"users/{overview['login']}/events?per_page=30") if overview["login"] else None
        )
        overview["activity"] = parse_events(json.loads(events_raw)) if events_raw else []
        return overview
