"""The action allowlist: built-ins plus whatever the owner put in actions.toml on the PC."""

from __future__ import annotations

import secrets
import time
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.platform.ops import PlatformOps

Kind = Literal["launch_app", "run_command", "open_path", "open_url", "media", "volume", "lock", "power"]
USER_KINDS = ("launch_app", "run_command", "open_path", "open_url")
CONFIRM_TTL_S = 30


class Param(BaseModel):
    """A typed, bounded value the phone may send with a built-in action."""

    model_config = ConfigDict(extra="forbid")

    name: str
    min: int
    max: int


class InvalidParams(ValueError):
    pass


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_.]{1,64}$")
    name: str = Field(min_length=1, max_length=40)
    kind: Kind
    danger: bool | None = None
    program: str | None = None
    args: list[str] = []
    cwd: Path | None = None
    timeout_s: int = Field(default=60, ge=1, le=3600)
    path: Path | None = None
    url: HttpUrl | None = None
    params: list[Param] = []

    @model_validator(mode="after")
    def _check(self) -> Action:
        needs = {"launch_app": "program", "run_command": "program", "open_path": "path", "open_url": "url"}
        field = needs.get(self.kind)
        if field and getattr(self, field) is None:
            raise ValueError(f"action '{self.id}' of kind {self.kind} needs '{field}'")
        if self.danger is None:
            # Unknown commands are treated as dangerous unless the owner says otherwise.
            self.danger = self.kind in ("run_command", "power")
        if self.kind == "power":
            self.danger = True
        return self

    def public(self) -> dict[str, Any]:
        """What the phone is allowed to know. Never the command line or the path."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "danger": bool(self.danger),
            "params": [p.model_dump() for p in self.params],
        }

    def check_params(self, given: dict[str, Any]) -> dict[str, int]:
        """Exactly the declared names, whole numbers, within range. Anything else is refused."""
        if set(given) != {p.name for p in self.params}:
            raise InvalidParams("wrong parameter names")
        checked = {}
        for spec in self.params:
            value = given[spec.name]
            if isinstance(value, bool) or not isinstance(value, int) or not spec.min <= value <= spec.max:
                raise InvalidParams(f"'{spec.name}' must be a whole number from {spec.min} to {spec.max}")
            checked[spec.name] = value
        return checked


def _builtins(allow_power: bool, volume_level: bool) -> list[Action]:
    items = [
        Action(id="media.play_pause", name="Play / Pause", kind="media"),
        Action(id="media.next", name="Next track", kind="media"),
        Action(id="media.prev", name="Previous track", kind="media"),
        Action(id="media.stop", name="Stop", kind="media"),
        Action(id="volume.up", name="Volume up", kind="volume"),
        Action(id="volume.down", name="Volume down", kind="volume"),
        Action(id="volume.mute", name="Mute", kind="volume"),
        Action(id="system.lock", name="Lock PC", kind="lock"),
    ]
    if volume_level:
        level = Param(name="level", min=0, max=100)
        items.append(Action(id="volume.set", name="Set volume", kind="volume", params=[level]))
    if allow_power:
        items += [
            Action(id="system.sleep", name="Sleep", kind="power"),
            Action(id="system.restart", name="Restart", kind="power"),
            Action(id="system.shutdown", name="Shut down", kind="power"),
        ]
    return items


class ActionRegistry:
    def __init__(self, user_actions: list[Action], allow_power: bool, volume_level: bool = False) -> None:
        self._actions: dict[str, Action] = {a.id: a for a in _builtins(allow_power, volume_level)}
        for action in user_actions:
            if action.params:
                raise ValueError(f"action '{action.id}': only built-in actions take parameters")
            if action.kind not in USER_KINDS:
                raise ValueError(
                    f"action '{action.id}': kind '{action.kind}' is built-in and cannot be defined"
                )
            if action.id in self._actions:
                raise ValueError(f"action id '{action.id}' is already in use")
            self._actions[action.id] = action

    @classmethod
    def from_file(cls, path: Path, allow_power: bool, volume_level: bool = False) -> ActionRegistry:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        actions = [Action.model_validate(item) for item in raw.get("action", [])]
        return cls(actions, allow_power, volume_level)

    def get(self, action_id: str) -> Action | None:
        return self._actions.get(action_id)

    def all(self) -> list[Action]:
        return list(self._actions.values())


def execute(action: Action, ops: PlatformOps, params: dict[str, int] | None = None) -> dict[str, Any]:
    """Blocking. Raises PlatformError when the OS refuses. `params` must come from `check_params`."""
    if action.id == "volume.set":
        ops.set_volume((params or {})["level"])
        return {"status": "started"}
    match action.kind:
        case "media" | "volume":
            ops.press_key(action.id)
        case "lock":
            ops.lock()
        case "power":
            ops.power(action.id.removeprefix("system."))
        case "launch_app":
            ops.launch(action.program or "", action.args, action.cwd)
        case "open_path":
            ops.open_target(str(action.path))
        case "open_url":
            ops.open_target(str(action.url))
        case "run_command":
            exit_code, output = ops.run(action.program or "", action.args, action.cwd, action.timeout_s)
            return {"status": "completed", "exit_code": exit_code, "output": output}
    return {"status": "started"}


class ConfirmStore:
    """Short-lived, single-use tokens proving the user confirmed a dangerous action on the phone."""

    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, str, float]] = {}

    def issue(self, device_id: str, action_id: str) -> str:
        now = time.monotonic()
        self._tokens = {t: v for t, v in self._tokens.items() if v[2] > now}
        token = secrets.token_urlsafe(16)
        self._tokens[token] = (device_id, action_id, now + CONFIRM_TTL_S)
        return token

    def consume(self, token: str, device_id: str, action_id: str) -> bool:
        entry = self._tokens.pop(token, None)
        return entry is not None and entry[:2] == (device_id, action_id) and entry[2] > time.monotonic()
