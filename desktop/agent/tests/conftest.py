from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Config
from app.core.context import AgentContext, build_context
from app.main import create_lan_app
from app.security.auth import create_pairing_code


class FakePlatform:
    """Records what would have been done to the machine. Tests never touch the real OS."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.fail_with: Exception | None = None

    def _record(self, *call: Any) -> None:
        if self.fail_with:
            raise self.fail_with
        self.calls.append(call)

    def press_key(self, key: str) -> None:
        self._record("key", key)

    def lock(self) -> None:
        self._record("lock")

    def power(self, mode: str) -> None:
        self._record("power", mode)

    def launch(self, program: str, args: list[str], cwd: Path | None) -> None:
        self._record("launch", program, args)

    def run(self, program: str, args: list[str], cwd: Path | None, timeout_s: int) -> tuple[int, str]:
        self._record("run", program, args)
        return 0, "ok"

    def open_target(self, target: str) -> None:
        self._record("open", target)

    def volume_supported(self) -> bool:
        return True

    def set_volume(self, level: int) -> None:
        self._record("volume", level)


class FakeCollector:
    interval_s = 0.02
    min_interval_s = 0.02

    def __init__(self, topic: str, reason: str | None = None) -> None:
        self.topic = topic
        self.reason = reason
        self.samples = 0

    def unsupported_reason(self) -> str | None:
        return self.reason

    def sample(self) -> dict[str, Any]:
        self.samples += 1
        return {"n": self.samples}


ACTIONS_TOML = """
[[action]]
id = "open_editor"
name = "Editor"
kind = "launch_app"
program = "editor.exe"
args = ["--secret-flag"]

[[action]]
id = "run_tests"
name = "Run tests"
kind = "run_command"
program = "python"
args = ["-m", "pytest"]
"""


@pytest.fixture
def platform() -> FakePlatform:
    return FakePlatform()


@pytest.fixture
def collectors() -> list[FakeCollector]:
    return [
        FakeCollector("system.cpu"),
        FakeCollector("system.memory"),
        FakeCollector("system.gpu", "no_nvidia_gpu"),
    ]


@pytest.fixture
def ctx(tmp_path: Path, platform: FakePlatform, collectors: list[FakeCollector]) -> AgentContext:
    (tmp_path / "actions.toml").write_text(ACTIONS_TOML, encoding="utf-8")
    config = Config()
    config.security.enforce_private_sources = False  # the test client is not a real network peer
    config.security.allow_power_actions = True
    return build_context(tmp_path, config=config, platform=platform, collectors=collectors)  # type: ignore[arg-type]


@pytest.fixture
def client(ctx: AgentContext) -> Iterator[TestClient]:
    with TestClient(create_lan_app(ctx)) as test_client:
        yield test_client


def pair_device(client: TestClient, ctx: AgentContext, name: str = "Test phone") -> dict[str, Any]:
    with ctx.db() as db:
        code = create_pairing_code(db, 600)
    response = client.post("/api/v1/pair", json={"pairing_code": code, "device_name": name})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def token(client: TestClient, ctx: AgentContext) -> str:
    return pair_device(client, ctx)["token"]


@pytest.fixture
def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
