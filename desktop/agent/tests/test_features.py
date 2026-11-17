"""Metrics, layout, productivity, developer and AI integrations, and the PC-only listener."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.local import create_local_app
from app.core.config import Config, ProjectConfig, load_config
from app.core.context import AgentContext, build_context
from app.integrations import dev, hooks
from app.integrations.ai import AiStore
from app.monitoring.collectors import system_collectors
from tests.conftest import FakePlatform
from tests.test_live import WS, connect, receive_until

# ---- real collectors -------------------------------------------------------


def test_real_collectors_produce_documented_fields() -> None:
    samples = {c.topic: c.sample() for c in system_collectors() if c.unsupported_reason() is None}
    cpu, mem, net = samples["system.cpu"], samples["system.memory"], samples["system.network"]
    assert 0 <= cpu["percent"] <= 100 and len(cpu["per_core"]) == cpu["logical_cores"]
    assert mem["total"] > mem["used"] > 0 and 0 < mem["percent"] <= 100
    assert net["up_bps"] == 0 and net["down_bps"] == 0  # first sample has no baseline
    assert samples["system.disk"]["volumes"] and samples["system.disk"]["volumes"][0]["total"] > 0
    assert {"read_bps", "write_bps"} <= set(samples["system.disk_io"])
    processes = samples["system.processes"]["items"]
    assert 0 < len(processes) <= 15 and {"pid", "name", "cpu_percent", "mem_bytes"} <= set(processes[0])


def test_cpu_percent_works_when_sampled_from_different_threads() -> None:
    import threading
    import time

    from app.monitoring.collectors import CpuCollector

    collector = CpuCollector()
    deadline = time.perf_counter() + 0.3
    while time.perf_counter() < deadline:  # keep one core busy so there is something to measure
        pass
    result: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: result.update(collector.sample()))
    thread.start()
    thread.join()
    assert max(result["per_core"]) > 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows fast path")
def test_fast_process_listing_agrees_with_psutil() -> None:
    import os

    import psutil

    from app.platform.winproc import list_processes

    mine = next(p for p in list_processes() if p.pid == os.getpid())
    reference = psutil.Process()
    times = reference.cpu_times()
    assert mine.name.lower().startswith("python")
    assert abs(mine.cpu_seconds - (times.user + times.system)) < 1.0
    assert 0 < mine.mem_bytes <= reference.memory_info().rss * 1.5
    assert len(list_processes()) > 20


def test_process_collector_is_cheap_and_measures_cpu() -> None:
    import time

    from app.monitoring.collectors import ProcessCollector

    collector = ProcessCollector()
    deadline = time.perf_counter() + 0.4
    while time.perf_counter() < deadline:  # this test process burns one core
        pass
    started = time.process_time()
    sample = collector.sample()
    cost = time.process_time() - started
    assert cost < 0.25, f"one sample used {cost:.2f}s of CPU"
    busiest = sample["items"][0]
    assert busiest["cpu_percent"] > 0 and all(i["cpu_percent"] <= 100 for i in sample["items"])


def test_network_collector_reports_adapters_that_sum_to_the_total() -> None:
    import time

    from app.monitoring.collectors import NetworkCollector

    collector = NetworkCollector()
    collector.sample()
    time.sleep(0.2)
    sample = collector.sample()
    assert sample["nics"] and {"name", "up_bps", "down_bps"} <= set(sample["nics"][0])
    assert sample["down_bps"] == sum(n["down_bps"] for n in sample["nics"])


def test_media_collector_degrades_field_by_field(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.monitoring.collectors import MediaCollector
    from app.platform import winmedia

    monkeypatch.setattr(winmedia, "volume_supported", lambda: True)
    monkeypatch.setattr(winmedia, "now_playing_supported", lambda: True)
    monkeypatch.setattr(winmedia, "get_volume", lambda: (64, False))
    monkeypatch.setattr(winmedia, "now_playing", lambda: None)  # nothing is playing
    collector = MediaCollector()
    assert collector.unsupported_reason() is None
    assert collector.sample() == {
        "volume": 64, "muted": False, "title": None, "artist": None, "album": None, "app": None,
        "playing": False, "position_s": None, "duration_s": None, "art": None}  # fmt: skip
    track = {"title": "Song", "artist": "Band", "album": "LP", "app": "Player", "playing": True,
             "position_s": 61.0, "duration_s": 200.0, "art": "abc123"}  # fmt: skip
    monkeypatch.setattr(winmedia, "now_playing", lambda: track)
    assert collector.sample()["title"] == "Song" and collector.sample()["playing"] is True

    monkeypatch.setattr(winmedia, "volume_supported", lambda: False)
    monkeypatch.setattr(winmedia, "now_playing_supported", lambda: False)
    assert MediaCollector().unsupported_reason() == "audio_extra_not_installed"


def test_media_app_name_is_shortened() -> None:
    from app.platform.winmedia import _app_name

    assert _app_name("Spotify.exe") == "Spotify"
    assert _app_name("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify") == "Spotify"
    assert _app_name("") == ""


def test_unsupported_collectors_report_a_reason() -> None:
    reasons = {c.topic: c.unsupported_reason() for c in system_collectors()}
    assert reasons["system.cpu"] is None
    assert reasons["system.gpu"] in (None, "no_nvidia_gpu", "nvidia_ml_py_not_installed")


def test_snapshot_endpoint(client: TestClient, auth: dict[str, str]) -> None:
    body = client.get("/api/v1/system/snapshot", headers=auth).json()
    assert set(body) == {"system.cpu", "system.memory"}  # the fake GPU collector is unsupported


def test_default_config_file_is_created_and_valid(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert (tmp_path / "config.toml").exists()
    assert config.server.lan_port == 7420 and config.security.allow_power_actions is False
    (tmp_path / "config.toml").write_text("[server]\nlan_prot = 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(tmp_path)  # a typo in the config fails loudly instead of being ignored


def test_agent_starts_from_shipped_defaults(tmp_path: Path) -> None:
    ctx = build_context(tmp_path, platform=FakePlatform())  # type: ignore[arg-type]
    ids = {a.id for a in ctx.actions.all()}
    assert {"open_explorer", "open_terminal", "media.play_pause", "system.lock"} <= ids
    assert "system.shutdown" not in ids


# ---- layout ----------------------------------------------------------------

LAYOUT: dict[str, Any] = {
    "schema": 1,
    "pages": [{"id": "p1", "name": "System", "widgets": [
        {"id": "w1", "type": "cpu", "name": "CPU", "enabled": True,
         "position": {"x": 0, "y": 0}, "size": {"w": 2, "h": 2}, "config": {"per_core": True}},
    ]}],
}  # fmt: skip


def test_layout_round_trip(client: TestClient, auth: dict[str, str]) -> None:
    assert client.get("/api/v1/layout", headers=auth).json() == {"layout": None}
    assert client.put("/api/v1/layout", json=LAYOUT, headers=auth).status_code == 200
    assert client.get("/api/v1/layout", headers=auth).json() == {"layout": LAYOUT}


def test_layout_is_validated(client: TestClient, auth: dict[str, str]) -> None:
    too_wide = {"schema": 1, "pages": [{"id": "p", "name": "P", "widgets": [
        {**LAYOUT["pages"][0]["widgets"][0], "size": {"w": 9, "h": 1}}]}]}  # fmt: skip
    assert client.put("/api/v1/layout", json=too_wide, headers=auth).status_code == 400
    assert client.put("/api/v1/layout", json={"schema": 1, "pages": []}, headers=auth).status_code == 400


# ---- productivity ----------------------------------------------------------


def test_tasks_crud(client: TestClient, auth: dict[str, str]) -> None:
    task = client.post("/api/v1/tasks", json={"title": "Ship it", "priority": True}, headers=auth).json()
    assert task["done"] is False and task["priority"] is True and task["created_at"].endswith("Z")
    done = client.patch(f"/api/v1/tasks/{task['id']}", json={"done": True}, headers=auth).json()
    assert done["done"] is True and done["done_at"]
    assert len(client.get("/api/v1/tasks", headers=auth).json()["tasks"]) == 1
    assert client.delete(f"/api/v1/tasks/{task['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/tasks", headers=auth).json()["tasks"] == []
    assert client.delete(f"/api/v1/tasks/{task['id']}", headers=auth).status_code == 404
    assert client.post("/api/v1/tasks", json={"title": ""}, headers=auth).status_code == 400


def test_notes_crud(client: TestClient, auth: dict[str, str]) -> None:
    note = client.post("/api/v1/notes", json={"text": "remember"}, headers=auth).json()
    edited = client.patch(f"/api/v1/notes/{note['id']}", json={"text": "remember this"}, headers=auth).json()
    assert edited["text"] == "remember this"
    assert [n["id"] for n in client.get("/api/v1/notes", headers=auth).json()["notes"]] == [note["id"]]
    assert client.delete(f"/api/v1/notes/{note['id']}", headers=auth).status_code == 204


# ---- developer -------------------------------------------------------------


def test_parse_git_status() -> None:
    output = "# branch.oid abc\n# branch.head feature/x\n# branch.upstream origin/x\n# branch.ab +2 -1\n" \
             "1 .M N... 100644 100644 100644 a b src/a.py\n? new.txt\n"  # fmt: skip
    assert dev.parse_status(output) == {
        "branch": "feature/x", "ahead": 2, "behind": 1, "changed_files": 1, "untracked_files": 1, "dirty": True,
    }  # fmt: skip
    clean = dev.parse_status("# branch.oid abc\n# branch.head main\n")
    assert clean["dirty"] is False and clean["branch"] == "main"


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)  # fmt: skip


def test_git_status_and_commits_against_a_real_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first commit")
    project = ProjectConfig(name="repo", path=repo)

    status = dev.project_status(project)
    assert (status["branch"], status["dirty"]) == ("main", False)
    (repo / "a.txt").write_text("two")
    (repo / "b.txt").write_text("new")
    status = dev.project_status(project)
    assert (status["changed_files"], status["untracked_files"], status["dirty"]) == (1, 1, True)

    commits = dev.project_commits(project)
    assert len(commits) == 1 and commits[0]["subject"] == "first commit"
    assert dev.project_status(ProjectConfig(name="x", path=tmp_path))["error"] == "not_a_git_repository"


def test_dev_environment_collector_shape() -> None:
    sample = dev.DevEnvironmentCollector(["python"], ["python"]).sample()
    assert any(app["name"] == "python" for app in sample["apps"])  # this test runs in python
    assert all({"port", "pid", "process"} <= set(item) for item in sample["listeners"])


def test_disabled_integration_answers_501_and_agent_still_works(
    tmp_path: Path, platform: FakePlatform
) -> None:
    from app.main import create_lan_app
    from tests.conftest import pair_device

    config = Config()
    config.security.enforce_private_sources = False
    config.integrations.git = config.integrations.ports = False
    config.integrations.claude_code = config.integrations.codex = False
    ctx = build_context(tmp_path, config=config, platform=platform)  # type: ignore[arg-type]
    with TestClient(create_lan_app(ctx)) as client:
        auth = {"Authorization": f"Bearer {pair_device(client, ctx)['token']}"}
        assert client.get("/api/v1/dev/ports", headers=auth).status_code == 501
        assert client.get("/api/v1/dev/projects", headers=auth).status_code == 501
        agent = client.get("/api/v1/agent", headers=auth).json()
        assert "ai.sessions" not in agent["capabilities"]["topics"]
        assert "git" not in agent["capabilities"]["features"]
        assert client.get("/api/v1/system/snapshot", headers=auth).status_code == 200


# ---- AI tools --------------------------------------------------------------


def hook_event(name: str, **extra: Any) -> dict[str, Any]:
    return {"session_id": "abc123", "cwd": "C:\\Users\\me\\code\\my-app", "hook_event_name": name,
            "transcript_path": "C:\\secret\\transcript.jsonl", **extra}  # fmt: skip


def test_ai_session_lifecycle() -> None:
    store = AiStore()
    store.ingest("claude-code", hook_event("SessionStart", source="startup"))
    assert store.sessions()["sessions"][0]["state"] == "idle"
    store.ingest("claude-code", hook_event("UserPromptSubmit", prompt="my private prompt"))
    session = store.sessions()["sessions"][0]
    assert (session["state"], session["project"], session["tool"]) == ("working", "my-app", "claude-code")
    store.ingest(
        "claude-code", hook_event("Notification", notification_type="permission_prompt", message="x")
    )
    assert store.sessions()["sessions"][0]["state"] == "needs_input"
    store.ingest("claude-code", hook_event("Stop", last_assistant_message="private answer"))
    assert store.sessions()["sessions"][0]["state"] == "idle"
    store.ingest("claude-code", hook_event("SessionEnd"))
    assert store.sessions()["sessions"] == []
    assert [e["kind"] for e in store.events()] == ["started", "prompt", "needs_input", "finished", "ended"]
    assert [e["id"] for e in store.events(since=3)] == [4, 5]


def test_ai_store_keeps_no_prompt_or_transcript_content() -> None:
    store = AiStore()
    store.ingest("claude-code", hook_event("UserPromptSubmit", prompt="my private prompt"))
    store.ingest("claude-code", hook_event("Stop", last_assistant_message="private answer"))
    stored = repr(store.sessions()) + repr(store.events())
    for secret in ("private", "transcript", "Users\\\\me"):
        assert secret not in stored


def test_ai_store_ignores_what_it_does_not_track() -> None:
    store = AiStore()
    assert store.ingest("claude-code", hook_event("PreToolUse", tool_name="Bash")) is None
    assert store.ingest("claude-code", {"hook_event_name": "Stop"}) is None  # no session id
    assert store.ingest("claude-code", hook_event("Notification", notification_type="auth_success")) is None
    assert store.sessions()["sessions"] == [] and store.events() == []


@pytest.fixture
def local(ctx: AgentContext) -> TestClient:
    return TestClient(create_local_app(ctx), base_url="http://127.0.0.1:7421")


def test_hook_events_reach_a_subscribed_phone(client: TestClient, local: TestClient, token: str) -> None:
    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        socket.send_json({"type": "subscribe", "id": "1", "topics": ["ai.sessions", "ai.events"]})
        initial = receive_until(socket, lambda m: m.get("topic") == "ai.sessions")
        assert initial["payload"] == {"sessions": []}
        assert (
            local.post("/local/hooks/claude-code", json=hook_event("UserPromptSubmit", prompt="p")).json()
            == {}
        )
        event = receive_until(socket, lambda m: m["type"] == "event" and m["topic"] == "ai.events")
        assert event["payload"]["kind"] == "prompt" and event["payload"]["project"] == "my-app"
        sessions = receive_until(socket, lambda m: m.get("topic") == "ai.sessions")
        assert sessions["payload"]["sessions"][0]["state"] == "working"
        assert local.post("/local/hooks/codex", json=hook_event("Stop")).status_code == 200
        finished = receive_until(
            socket, lambda m: m["type"] == "event" and m["payload"]["kind"] == "finished"
        )
        assert finished["payload"]["tool"] == "codex"


def test_hook_endpoint_tolerates_garbage(local: TestClient, ctx: AgentContext) -> None:
    assert local.post("/local/hooks/claude-code", content=b"not json").json() == {}
    assert local.post("/local/hooks/claude-code", json=[1, 2]).json() == {}
    assert local.post("/local/hooks/unknown-tool", json={}).status_code == 404
    ctx.config.integrations.claude_code = False
    local.post("/local/hooks/claude-code", json=hook_event("SessionStart"))
    assert ctx.ai.sessions()["sessions"] == []


def test_hook_settings_merge_preserves_existing_hooks_and_is_reversible() -> None:
    existing = {"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}
    merged = hooks.add_hooks(existing, "claude-code", 7421)
    assert merged["model"] == "x"
    assert merged["hooks"]["Stop"][0] == existing["hooks"]["Stop"][0]
    assert merged["hooks"]["Stop"][1]["hooks"][0] == {
        "type": "http", "url": "http://127.0.0.1:7421/local/hooks/claude-code", "timeout": 5}  # fmt: skip
    assert set(merged["hooks"]) == set(hooks.EVENTS["claude-code"])
    assert hooks.add_hooks(merged, "claude-code", 7421) == merged  # installing twice changes nothing
    assert hooks.remove_hooks(merged) == existing
    assert hooks.remove_hooks(hooks.add_hooks({}, "codex", 7421)) == {}
    assert (
        "hook_forward codex 7421"
        in hooks.add_hooks({}, "codex", 7421)["hooks"]["Stop"][0]["hooks"][0]["command"]
    )


# ---- PC-only listener ------------------------------------------------------


def test_pairing_page_issues_a_working_code(local: TestClient, client: TestClient) -> None:
    import re

    page = local.get("/pair")
    assert page.status_code == 200 and "<svg" in page.text
    code = re.search(r"class=code>([A-Z0-9]{4}-[A-Z0-9]{4})<", page.text).group(1)
    assert client.post("/api/v1/pair", json={"pairing_code": code, "device_name": "P"}).status_code == 201


def test_status_page_lists_devices_and_revokes(
    local: TestClient, client: TestClient, ctx: AgentContext
) -> None:
    from tests.conftest import pair_device

    paired = pair_device(client, ctx, "Desk <b>phone</b>")
    page = local.get("/")
    assert (
        "Desk &lt;b&gt;phone&lt;/b&gt;" in page.text and "<b>phone</b>" not in page.text
    )  # names are escaped
    assert (
        local.post(f"/local/devices/{paired['device_id']}/revoke", follow_redirects=False).status_code == 303
    )
    assert (
        client.get("/api/v1/agent", headers={"Authorization": f"Bearer {paired['token']}"}).status_code == 401
    )


def test_local_listener_refuses_foreign_host_and_origin(ctx: AgentContext) -> None:
    app = create_local_app(ctx)
    assert TestClient(app, base_url="http://evil.example").get("/pair").status_code == 403
    local = TestClient(app, base_url="http://127.0.0.1:7421")
    assert local.post("/local/devices/x/revoke", headers={"Origin": "http://evil.example"}).status_code == 403
    assert local.get("/").status_code == 200


# ---- tray ------------------------------------------------------------------


def test_tray_menu_actions(ctx: AgentContext) -> None:
    from app.tray import menu_actions

    opened: list[str] = []
    stopped: list[bool] = []
    actions = dict(menu_actions(ctx, lambda: stopped.append(True), open_target=opened.append))
    assert list(actions) == ["Open status page", "Pair a phone", "Open data folder", "Quit Phoney"]
    actions["Open status page"]()
    actions["Pair a phone"]()
    actions["Open data folder"]()
    assert opened == ["http://127.0.0.1:7421/", "http://127.0.0.1:7421/pair", str(ctx.data_dir)]
    assert stopped == []
    actions["Quit Phoney"]()
    assert stopped == [True]


def test_tray_quit_stops_both_servers(ctx: AgentContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import socket
    import threading
    import urllib.request

    from app import main

    def free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    ctx.config.server.lan_host = "127.0.0.1"
    ctx.config.server.lan_port, ctx.config.server.local_port = free_port(), free_port()
    seen: dict[str, object] = {}

    def fake_tray(
        _ctx: AgentContext, stop
    ):  # stands in for the real icon; "clicks" Quit after a health check
        def click_quit() -> None:
            url = f"http://127.0.0.1:{ctx.config.server.lan_port}/api/v1/health"
            for _ in range(50):
                try:
                    seen["status"] = urllib.request.urlopen(url, timeout=1).status
                    break
                except OSError:
                    threading.Event().wait(0.1)
            stop()

        threading.Thread(target=click_quit, daemon=True).start()
        return None

    monkeypatch.setattr(main, "start_tray", fake_tray)
    asyncio.run(asyncio.wait_for(main.serve(ctx), timeout=20))  # returns only if both servers stopped
    assert seen["status"] == 200


# ---- Claude usage ----------------------------------------------------------

STATUS = {
    "model": {"display_name": "Opus"},
    "cwd": "C:\\secret\\project",
    "cost": {"total_cost_usd": 1.23},
    "context_window": {"used_percentage": 8},
    "rate_limits": {
        "five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
        "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600},
    },
}


def test_usage_keeps_only_percent_and_reset_time() -> None:
    store = AiStore()
    assert store.usage() == {"five_hour": None, "seven_day": None, "updated_at": None}
    assert store.set_usage(STATUS) is True
    usage = store.usage()
    assert usage["five_hour"] == {"percent": 23.5, "resets_at": "2025-02-01T16:00:00.000Z"}
    assert usage["seven_day"]["percent"] == 41.2 and usage["updated_at"]
    assert "secret" not in repr(usage) and "1.23" not in repr(usage) and "Opus" not in repr(usage)


def test_usage_ignores_payloads_without_numbers() -> None:
    store = AiStore()
    store.set_usage(STATUS)
    before = store.usage()
    for payload in ({}, {"rate_limits": None}, {"rate_limits": {}}, {"rate_limits": {"five_hour": {}}},
                    {"rate_limits": {"five_hour": {"used_percentage": "50"}}},
                    {"rate_limits": {"five_hour": {"used_percentage": True}}}):  # fmt: skip
        assert store.set_usage(payload) is False
    assert store.usage() == before
    assert store.set_usage({"rate_limits": {"five_hour": {"used_percentage": 60}}}) is True  # one window only
    assert store.usage()["seven_day"] is None and store.usage()["five_hour"]["resets_at"] is None


def test_usage_reaches_a_subscribed_phone(
    client: TestClient, local: TestClient, token: str, auth: dict[str, str]
) -> None:
    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        socket.send_json({"type": "subscribe", "id": "1", "topics": ["ai.usage"]})
        first = receive_until(socket, lambda m: m.get("topic") == "ai.usage")
        assert first["payload"]["five_hour"] is None
        assert local.post("/local/statusline", json=STATUS).json() == {}
        update = receive_until(socket, lambda m: m.get("topic") == "ai.usage")
        assert update["payload"]["five_hour"]["percent"] == 23.5
    assert client.get("/api/v1/ai/usage", headers=auth).json()["seven_day"]["percent"] == 41.2
    assert local.post("/local/statusline", content=b"junk").json() == {}


def test_status_line_text() -> None:
    from app.integrations.statusline_forward import status_text

    assert status_text(STATUS) == "Opus | ctx 8% | 5h 24%"
    assert status_text({"model": {"display_name": "Opus"}}) == "Opus"
    assert status_text({}) == "Claude Code"


def test_status_line_install_never_replaces_the_users_own() -> None:
    mine = {"model": "x", "statusLine": {"type": "command", "command": "my-script.sh"}}
    with pytest.raises(ValueError):
        hooks.set_status_line(mine, 7421)
    assert hooks.remove_status_line(mine) == mine  # uninstall leaves a foreign status line alone
    installed = hooks.set_status_line({"model": "x"}, 7421)
    assert installed["model"] == "x" and "statusline_forward 7421" in installed["statusLine"]["command"]
    assert hooks.set_status_line(installed, 7421) == installed
    assert hooks.remove_status_line(installed) == {"model": "x"}


# ---- now playing: progress and cover -----------------------------------------


def test_track_progress_advances_while_playing() -> None:
    from datetime import UTC, datetime, timedelta

    from app.platform.winmedia import progress

    measured = datetime.now(UTC) - timedelta(seconds=10)
    position, duration = progress(60.0, 0.0, 200.0, measured, playing=True)
    assert duration == 200.0 and 69.5 <= position <= 71.0
    assert progress(60.0, 0.0, 200.0, measured, playing=False) == (60.0, 200.0)  # paused: stays put
    assert progress(199.0, 0.0, 200.0, measured, playing=True) == (200.0, 200.0)  # never past the end
    assert progress(0.0, 0.0, 0.0, measured, playing=True) == (None, None)  # player gives no timeline
    assert progress(60.0, 0.0, 200.0, datetime(1601, 1, 1, tzinfo=UTC), playing=True) == (60.0, 200.0)


def test_cover_art_endpoint(
    client: TestClient, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.platform import winmedia

    monkeypatch.setitem(winmedia._art, "bytes", None)
    assert client.get("/api/v1/media/art", headers=auth).status_code == 404
    assert client.get("/api/v1/media/art").status_code == 401
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    monkeypatch.setitem(winmedia._art, "bytes", png)
    response = client.get("/api/v1/media/art", headers=auth)
    assert response.content == png and response.headers["content-type"] == "image/png"
    monkeypatch.setitem(winmedia._art, "bytes", b"\xff\xd8\xff" + b"x" * 20)
    assert client.get("/api/v1/media/art", headers=auth).headers["content-type"] == "image/jpeg"


# ---- GitHub --------------------------------------------------------------------

GRAPHQL = {
    "viewer": {
        "login": "me",
        "pullRequests": {"nodes": [
            {"number": 4, "title": "Add diagrams", "isDraft": False, "updatedAt": "2026-11-16T20:13:50Z",
             "reviewDecision": "APPROVED", "repository": {"nameWithOwner": "me/hotel"},
             "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]}},
            {"number": 9, "title": "WIP", "isDraft": True, "updatedAt": "2026-11-15T10:00:00Z",
             "reviewDecision": None, "repository": {"nameWithOwner": "me/site"},
             "commits": {"nodes": [{"commit": {"statusCheckRollup": None}}]}},
        ]},
        "repositories": {"nodes": [
            {"nameWithOwner": "me/hotel", "pushedAt": "2026-11-16T20:12:42Z", "isPrivate": True,
             "defaultBranchRef": {"name": "main"}},
            {"nameWithOwner": "me/empty", "pushedAt": None, "isPrivate": False, "defaultBranchRef": None},
        ]},
    },
    "reviews": {"nodes": [
        {"number": 7, "title": "Fix login", "updatedAt": "2026-11-16T09:00:00Z",
         "repository": {"nameWithOwner": "team/app"}, "author": {"login": "sam"}},
        {},
    ]},
}  # fmt: skip


def test_github_overview_parsing() -> None:
    from app.integrations.github import parse_overview

    overview = parse_overview(GRAPHQL)
    assert overview["login"] == "me"
    first, second = overview["prs"]
    assert (first["repo"], first["number"], first["checks"], first["review"]) == (
        "me/hotel",
        4,
        "failing",
        "approved",
    )
    assert (second["draft"], second["checks"], second["review"]) == (True, None, None)
    assert overview["review_requests"] == [
        {
            "repo": "team/app",
            "number": 7,
            "title": "Fix login",
            "author": "sam",
            "updated_at": "2026-11-16T09:00:00Z",
        }
    ]
    assert overview["repos"][0] == {
        "name": "me/hotel",
        "pushed_at": "2026-11-16T20:12:42Z",
        "private": True,
        "branch": "main",
    }
    assert overview["repos"][1]["branch"] is None
    assert parse_overview({}) == {"login": None, "prs": [], "review_requests": [], "repos": []}


def test_github_activity_lines() -> None:
    from app.integrations.github import parse_events

    events = [
        {
            "type": "PushEvent",
            "repo": {"name": "me/os"},
            "created_at": "t1",
            "payload": {"ref": "refs/heads/master"},
        },
        {
            "type": "PullRequestEvent",
            "repo": {"name": "me/hotel"},
            "created_at": "t2",
            "payload": {"action": "opened", "pull_request": {"number": 4}},
        },
        {"type": "WatchEvent", "repo": {"name": "x/y"}, "created_at": "t3", "payload": {"action": "started"}},
        {
            "type": "CreateEvent",
            "repo": {"name": "me/care"},
            "created_at": "t4",
            "payload": {"ref": "main", "ref_type": "branch"},
        },
    ]
    assert parse_events(events) == [
        {"repo": "me/os", "text": "Pushed to master", "at": "t1"},
        {"repo": "me/hotel", "text": "Opened pull request #4", "at": "t2"},
        {"repo": "me/care", "text": "Created branch main", "at": "t4"},
    ]
    assert len(parse_events(events * 10, limit=5)) == 5


def test_github_collector_reports_why_it_cannot_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations import github

    monkeypatch.setattr(github.shutil, "which", lambda _: None)
    assert github.GithubCollector().unsupported_reason() == "gh_not_installed"
    monkeypatch.setattr(github.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(github, "_gh", lambda *a, **k: None)
    assert github.GithubCollector().unsupported_reason() == "gh_not_logged_in"
    monkeypatch.setattr(github, "_gh", lambda *a, **k: "token")
    collector = github.GithubCollector()
    assert collector.unsupported_reason() is None
    monkeypatch.setattr(github, "_gh", lambda *a, **k: None)  # network down later on
    assert collector.sample()["error"] == "github_unreachable"


# ---- Claude Code permission prompts answered from the phone ---------------------

PROMPT = {
    "session_id": "abc123", "cwd": "C:\\code\\my-app", "hook_event_name": "PermissionRequest",
    "tool_name": "Bash", "tool_input": {"command": "npm test", "description": "Run tests"},
}  # fmt: skip


def test_permission_summary_is_short_and_specific() -> None:
    from app.integrations.permissions import summarize

    assert summarize("Bash", {"command": "npm test", "description": "x"}) == "npm test"
    assert summarize("Edit", {"file_path": "C:/a/b.py", "old_string": "secret code"}) == "C:/a/b.py"
    assert summarize("Mystery", {}) == "Mystery"
    assert summarize("Mystery", {"depth": 3}) == '{"depth": 3}'
    assert len(summarize("Bash", {"command": "x" * 5000})) == 300


def test_permission_hook_answers_nothing_when_no_phone_listens(local: TestClient, ctx: AgentContext) -> None:
    assert local.post("/local/hooks/claude-code/permission", json=PROMPT).json() == {}
    assert ctx.ai.sessions()["sessions"][0]["state"] == "needs_input"  # the session is still flagged
    assert ctx.permissions.pending() == {"requests": []}
    assert local.post("/local/hooks/claude-code/permission", content=b"junk").json() == {}
    assert local.post("/local/hooks/claude-code/permission", json={"hook_event_name": "Stop"}).json() == {}


@pytest.mark.parametrize(("decision", "expected"), [
    ("allow", {"behavior": "allow"}),
    ("deny", {"behavior": "deny", "message": "Denied from the Phoney panel."}),
])  # fmt: skip
def test_phone_answers_a_permission_prompt(
    client: TestClient, local: TestClient, token: str, auth: dict[str, str], ctx: AgentContext,
    decision: str, expected: dict[str, str],
) -> None:  # fmt: skip
    import threading

    answer: dict[str, Any] = {}
    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        socket.send_json({"type": "subscribe", "id": "1", "topics": ["ai.permissions"]})
        assert receive_until(socket, lambda m: m.get("topic") == "ai.permissions")["payload"] == {
            "requests": []
        }

        hook = threading.Thread(  # Claude Code's call stays open until the phone answers
            target=lambda: answer.update(
                local.post("/local/hooks/claude-code/permission", json=PROMPT).json()
            )
        )
        hook.start()
        shown = receive_until(
            socket, lambda m: m.get("topic") == "ai.permissions" and m["payload"]["requests"]
        )
        request = shown["payload"]["requests"][0]
        assert (request["tool_name"], request["summary"], request["project"]) == (
            "Bash",
            "npm test",
            "my-app",
        )
        assert "description" not in repr(request)

        url = f"/api/v1/ai/permissions/{request['id']}"
        assert client.post(url, json={"decision": "maybe"}, headers=auth).status_code == 400
        assert client.post(url, json={"decision": decision}).status_code == 401
        answered = client.post(url, json={"decision": decision}, headers=auth)
        assert answered.status_code == 200, answered.text
        assert answered.json()["decision"] == decision
        hook.join(timeout=5)
        cleared = receive_until(socket, lambda m: m.get("topic") == "ai.permissions")
        assert cleared["payload"] == {"requests": []}
        assert client.post(url, json={"decision": decision}, headers=auth).status_code == 404  # only once

    assert answer == {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": expected}}
    with ctx.db() as db:
        from sqlalchemy import select

        from app.database.models import AuditLog

        entry = db.scalars(select(AuditLog).where(AuditLog.event == "permission")).one()
    assert entry.detail == f"{decision}: Bash" and entry.ok is (decision == "allow")


def test_unanswered_permission_prompt_falls_back_to_the_terminal(
    client: TestClient, local: TestClient, token: str, ctx: AgentContext
) -> None:
    ctx.config.integrations.permission_wait_s = 5
    import app.integrations.permissions as module

    original = module.PermissionBroker.wait

    async def quick(self, request, timeout_s):  # same code path, shorter clock
        return await original(self, request, 0.2)

    module.PermissionBroker.wait = quick  # type: ignore[method-assign]
    try:
        with client.websocket_connect(WS) as socket:
            connect(socket, token)
            socket.send_json({"type": "subscribe", "id": "1", "topics": ["ai.permissions"]})
            receive_until(socket, lambda m: m.get("topic") == "ai.permissions")
            assert local.post("/local/hooks/claude-code/permission", json=PROMPT).json() == {}
        assert ctx.permissions.pending() == {"requests": []}
    finally:
        module.PermissionBroker.wait = original  # type: ignore[method-assign]


def test_permission_feature_can_be_turned_off(
    client: TestClient, local: TestClient, token: str, auth: dict[str, str], ctx: AgentContext
) -> None:
    ctx.config.integrations.claude_permissions = False
    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        socket.send_json({"type": "subscribe", "id": "1", "topics": ["ai.permissions"]})
        assert local.post("/local/hooks/claude-code/permission", json=PROMPT).json() == {}
    assert (
        client.post("/api/v1/ai/permissions/p_x", json={"decision": "allow"}, headers=auth).status_code == 501
    )


def test_permission_hook_install_is_separate_and_reversible() -> None:
    base = hooks.add_hooks({"model": "x"}, "claude-code", 7421)
    both = hooks.add_permission_hook(base, 7421, 30)
    entry = both["hooks"]["PermissionRequest"][0]["hooks"][0]
    assert entry == {
        "type": "http",
        "url": "http://127.0.0.1:7421/local/hooks/claude-code/permission",
        "timeout": 45,
    }
    assert hooks.add_permission_hook(both, 7421, 30) == both
    assert hooks.add_hooks(both, "claude-code", 7421) == both  # reinstalling session hooks keeps it
    assert hooks.remove_hooks(both, hooks._is_permission) == base
    assert hooks.remove_hooks(both) == {"model": "x"}
