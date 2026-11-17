"""The agent's shared state, built once at startup and handed to both listeners."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from app.commands.registry import ActionRegistry, ConfirmStore
from app.core.config import Config, ensure_user_file, load_config
from app.database.models import AuditLog, make_session_factory
from app.integrations.ai import AiStore
from app.integrations.dev import DevEnvironmentCollector, ProjectsCollector
from app.integrations.github import GithubCollector
from app.integrations.permissions import PermissionBroker
from app.monitoring.collectors import Collector, system_collectors
from app.monitoring.hub import Hub
from app.platform.ops import PlatformOps, default_platform
from app.security.ratelimit import Lockout, RateLimiter

if TYPE_CHECKING:
    from app.networking.ws import ClientConnection

audit_log = logging.getLogger("phoney.audit")


@dataclass
class Limits:
    pairing: RateLimiter = field(default_factory=lambda: RateLimiter(5, 60))
    pairing_lockout: Lockout = field(default_factory=lambda: Lockout(10, 300))
    requests: RateLimiter = field(default_factory=lambda: RateLimiter(300, 60))
    actions: RateLimiter = field(default_factory=lambda: RateLimiter(60, 60))
    dangerous: RateLimiter = field(default_factory=lambda: RateLimiter(3, 60))


@dataclass
class AgentContext:
    config: Config
    data_dir: Path
    db: sessionmaker[Session]
    hub: Hub
    actions: ActionRegistry
    platform: PlatformOps
    ai: AiStore
    permissions: PermissionBroker
    confirmations: ConfirmStore = field(default_factory=ConfirmStore)
    limits: Limits = field(default_factory=Limits)
    connections: dict[str, ClientConnection] = field(default_factory=dict)

    def audit(
        self,
        event: str,
        *,
        ok: bool = True,
        device_id: str | None = None,
        ip: str | None = None,
        detail: str = "",
    ) -> None:
        audit_log.info("%s ok=%s device=%s ip=%s %s", event, ok, device_id, ip, detail)
        with self.db() as db:
            db.add(AuditLog(event=event, ok=ok, device_id=device_id, ip=ip, detail=detail[:200]))
            db.commit()

    def features(self) -> list[str]:
        on = self.config.integrations
        flags = {
            "git": on.git,
            "ports": on.ports,
            "claude_code": on.claude_code,
            "codex": on.codex,
            "github": on.github,
            "claude_permissions": on.claude_code and on.claude_permissions,
        }
        return ["actions", "deck", "productivity", *[name for name, enabled in flags.items() if enabled]]


def build_context(
    data_dir: Path,
    *,
    config: Config | None = None,
    platform: PlatformOps | None = None,
    collectors: list[Collector] | None = None,
) -> AgentContext:
    config = config or load_config(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    on = config.integrations

    if collectors is None:
        collectors = system_collectors(include_processes=on.processes)
        if on.git:
            collectors.append(ProjectsCollector(config.dev.projects))
        if on.ports:
            collectors.append(GithubCollector()) if on.github else None
            collectors.append(DevEnvironmentCollector(config.dev.process_names, config.dev.server_names))
    hub = Hub(collectors)

    def ai_changed(event: dict[str, Any] | None) -> None:
        if event is not None:
            hub.publish("ai.events", event)
        hub.publish("ai.sessions", ai.sessions(), kind="data")

    ai = AiStore(on_change=ai_changed)
    if on.claude_code or on.codex:
        hub.register_event_topic("ai.sessions", snapshot=ai.sessions)
        hub.register_event_topic("ai.events")
    if on.claude_code:
        hub.register_event_topic("ai.usage", snapshot=ai.usage)
    permissions = PermissionBroker(
        on_change=lambda: hub.publish("ai.permissions", permissions.pending(), kind="data")
    )
    if on.claude_code and on.claude_permissions:
        hub.register_event_topic("ai.permissions", snapshot=permissions.pending)
    hub.register_event_topic("sync")  # tells other clients that deck, layout or productivity data changed

    platform = platform or default_platform()
    actions = ActionRegistry.from_file(
        ensure_user_file(data_dir, "actions.toml"),
        allow_power=config.security.allow_power_actions,
        volume_level=platform.volume_supported(),
    )
    return AgentContext(
        config=config,
        data_dir=data_dir,
        db=make_session_factory(data_dir / "phoney.db"),
        hub=hub,
        actions=actions,
        platform=platform,
        ai=ai,
        permissions=permissions,
    )
