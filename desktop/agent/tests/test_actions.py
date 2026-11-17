"""The allowlist, confirmation round-trip and the control deck."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.commands.registry import Action, ActionRegistry, ConfirmStore
from app.core.context import AgentContext
from app.database.models import AuditLog
from app.platform.ops import PlatformError
from tests.conftest import FakePlatform


def run(client: TestClient, auth: dict[str, str], action_id: str, token: str | None = None):
    body = {"confirm_token": token} if token else None
    return client.post(f"/api/v1/actions/{action_id}/run", json=body, headers=auth)


def test_action_list_never_exposes_command_lines(client: TestClient, auth: dict[str, str]) -> None:
    response = client.get("/api/v1/actions", headers=auth)
    actions = {a["id"]: a for a in response.json()["actions"]}
    assert set(actions["open_editor"]) == {"id", "name", "kind", "danger", "params"}
    assert "editor.exe" not in response.text and "secret-flag" not in response.text
    assert actions["system.shutdown"]["danger"] is True
    assert actions["run_tests"]["danger"] is True  # run_command is dangerous unless the owner says otherwise
    assert actions["open_editor"]["danger"] is False


def test_safe_actions_run_immediately(
    client: TestClient, auth: dict[str, str], platform: FakePlatform
) -> None:
    assert run(client, auth, "media.play_pause").json() == {"status": "started"}
    assert run(client, auth, "system.lock").status_code == 200
    assert run(client, auth, "open_editor").status_code == 200
    assert platform.calls == [
        ("key", "media.play_pause"),
        ("lock",),
        ("launch", "editor.exe", ["--secret-flag"]),
    ]


def test_unknown_action_is_404_and_nothing_runs(
    client: TestClient, auth: dict[str, str], platform: FakePlatform
) -> None:
    for action_id in ("calc.exe", "system.format", "..%2f..%2fcmd", "open_editor;shutdown"):
        assert run(client, auth, action_id).status_code in (404, 405)  # 405: the path left the API entirely
    assert platform.calls == []


def test_dangerous_action_needs_confirmation(
    client: TestClient, auth: dict[str, str], platform: FakePlatform
) -> None:
    first = run(client, auth, "system.shutdown")
    assert first.status_code == 409
    error = first.json()["error"]
    assert error["code"] == "confirmation_required"
    assert platform.calls == []

    done = run(client, auth, "system.shutdown", error["details"]["confirm_token"])
    assert done.status_code == 200
    assert platform.calls == [("power", "shutdown")]

    replay = run(client, auth, "system.shutdown", error["details"]["confirm_token"])
    assert replay.status_code == 409  # a confirmation works once
    assert len(platform.calls) == 1


def test_confirmation_is_bound_to_action_and_device(
    client: TestClient, auth: dict[str, str], platform: FakePlatform
) -> None:
    token = run(client, auth, "system.restart").json()["error"]["details"]["confirm_token"]
    assert run(client, auth, "system.shutdown", token).status_code == 409
    assert run(client, auth, "system.shutdown", "made-up-token").status_code == 409
    assert platform.calls == []

    store = ConfirmStore()
    issued = store.issue("device-a", "system.shutdown")
    assert store.consume(issued, "device-b", "system.shutdown") is False


def test_run_command_returns_exit_code(
    client: TestClient, auth: dict[str, str], platform: FakePlatform
) -> None:
    token = run(client, auth, "run_tests").json()["error"]["details"]["confirm_token"]
    assert run(client, auth, "run_tests", token).json() == {
        "status": "completed",
        "exit_code": 0,
        "output": "ok",
    }
    assert platform.calls == [("run", "python", ["-m", "pytest"])]


def test_failed_action_reports_reason_and_is_audited(
    client: TestClient, auth: dict[str, str], platform: FakePlatform, ctx: AgentContext
) -> None:
    platform.fail_with = PlatformError("program_not_found")
    response = run(client, auth, "open_editor")
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "program_not_found"
    with ctx.db() as db:
        entry = db.scalars(select(AuditLog).where(AuditLog.event == "action")).one()
    assert entry.ok is False and "open_editor" in entry.detail


def test_every_run_is_audited(client: TestClient, auth: dict[str, str], ctx: AgentContext) -> None:
    run(client, auth, "media.next")
    with ctx.db() as db:
        entry = db.scalars(select(AuditLog).where(AuditLog.event == "action")).one()
    assert entry.ok and entry.detail == "media.next" and entry.device_id


def test_dangerous_actions_are_rate_limited(
    client: TestClient, auth: dict[str, str], platform: FakePlatform
) -> None:
    statuses = []
    for _ in range(4):
        token = run(client, auth, "system.sleep").json()["error"]["details"]["confirm_token"]
        statuses.append(run(client, auth, "system.sleep", token).status_code)
    assert statuses == [200, 200, 200, 429]
    assert len(platform.calls) == 3


def test_power_actions_absent_unless_enabled() -> None:
    registry = ActionRegistry([], allow_power=False)
    assert registry.get("system.shutdown") is None
    assert registry.get("system.lock") is not None


def test_registry_rejects_bad_definitions(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs 'program'"):
        Action(id="x", name="X", kind="launch_app")
    with pytest.raises(ValueError, match="built-in"):
        ActionRegistry([Action(id="mine", name="Mine", kind="power")], allow_power=True)
    with pytest.raises(ValueError, match="already in use"):
        ActionRegistry(
            [Action(id="system.lock", name="L", kind="launch_app", program="a")], allow_power=False
        )
    with pytest.raises(ValueError):
        Action(id="web", name="Web", kind="open_url", url="file:///c:/windows")  # http and https only
    bad = tmp_path / "actions.toml"
    bad.write_text(
        '[[action]]\nid="a"\nname="A"\nkind="launch_app"\nprogram="p"\nshell=true\n', encoding="utf-8"
    )
    with pytest.raises(ValueError):
        ActionRegistry.from_file(bad, allow_power=False)


def set_volume(client: TestClient, auth: dict[str, str], params: object):
    return client.post("/api/v1/actions/volume.set/run", json={"params": params}, headers=auth)


def test_volume_level_is_a_typed_bounded_parameter(
    client: TestClient, auth: dict[str, str], platform: FakePlatform
) -> None:
    listed = {a["id"]: a for a in client.get("/api/v1/actions", headers=auth).json()["actions"]}
    assert listed["volume.set"]["params"] == [{"name": "level", "min": 0, "max": 100}]
    assert set_volume(client, auth, {"level": 40}).json() == {"status": "started"}
    assert platform.calls == [("volume", 40)]
    for bad in ({"level": 101}, {"level": -1}, {"level": "40"}, {"level": 40.5}, {"level": True}, {},
                {"level": 40, "extra": 1}, {"lvl": 40}):  # fmt: skip
        assert set_volume(client, auth, bad).status_code == 400, bad
    assert len(platform.calls) == 1


def test_actions_without_parameters_refuse_them(
    client: TestClient, auth: dict[str, str], platform: FakePlatform
) -> None:
    response = client.post("/api/v1/actions/open_editor/run", json={"params": {"args": 1}}, headers=auth)
    assert response.status_code == 400 and platform.calls == []
    spec = [{"name": "a", "min": 0, "max": 1}]
    with pytest.raises(ValueError, match="only built-in"):
        ActionRegistry(
            [Action(id="x", name="X", kind="launch_app", program="p", params=spec)], allow_power=False
        )


def test_volume_level_action_absent_when_unsupported() -> None:
    assert ActionRegistry([], allow_power=False, volume_level=False).get("volume.set") is None
    assert ActionRegistry([], allow_power=False, volume_level=True).get("volume.set") is not None


# ---- control deck ----------------------------------------------------------


def test_default_deck_is_seeded_from_available_actions(client: TestClient, auth: dict[str, str]) -> None:
    groups = client.get("/api/v1/deck", headers=auth).json()["groups"]
    names = [g["name"] for g in groups]
    assert names == ["Media", "System"]  # "Apps" is skipped: its actions are not in this test allowlist
    assert all(b["available"] for g in groups for b in g["buttons"])


def test_button_lifecycle(client: TestClient, auth: dict[str, str]) -> None:
    group = client.post("/api/v1/deck/groups", json={"name": "Dev"}, headers=auth).json()
    made = client.post(
        "/api/v1/deck/buttons",
        json={"group_id": group["id"], "label": "Editor", "icon": "code", "action_id": "open_editor"},
        headers=auth,
    )
    assert made.status_code == 201
    button = made.json()

    patched = client.patch(
        f"/api/v1/deck/buttons/{button['id']}",
        json={"label": "IDE", "icon": "terminal", "order": 3},
        headers=auth,
    ).json()
    assert (patched["label"], patched["icon"], patched["order"]) == ("IDE", "terminal", 3)

    deck = client.get("/api/v1/deck", headers=auth).json()["groups"]
    assert [b["label"] for g in deck if g["id"] == group["id"] for b in g["buttons"]] == ["IDE"]

    assert client.delete(f"/api/v1/deck/groups/{group['id']}", headers=auth).status_code == 204
    assert (
        client.patch(f"/api/v1/deck/buttons/{button['id']}", json={"label": "x"}, headers=auth).status_code
        == 404
    )


def test_button_cannot_reference_unlisted_action(client: TestClient, auth: dict[str, str]) -> None:
    group = client.get("/api/v1/deck", headers=auth).json()["groups"][0]
    body = {"group_id": group["id"], "label": "Evil", "action_id": "format_disk"}
    assert client.post("/api/v1/deck/buttons", json=body, headers=auth).status_code == 400
    button_id = group["buttons"][0]["id"]
    patch = client.patch(f"/api/v1/deck/buttons/{button_id}", json={"action_id": "format_disk"}, headers=auth)
    assert patch.status_code == 400
    extra = client.post(
        "/api/v1/deck/buttons", json={**body, "action_id": "system.lock", "program": "x"}, headers=auth
    )
    assert extra.status_code == 400
