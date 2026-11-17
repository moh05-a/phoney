"""Configuration: a TOML file in the data directory, validated by Pydantic."""

from __future__ import annotations

import os
import platform
import shutil
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "defaults"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerConfig(_Section):
    name: str = Field(default_factory=lambda: platform.node() or "My PC", max_length=60)
    lan_host: str = "0.0.0.0"
    lan_port: int = Field(default=7420, ge=1024, le=65535)
    local_port: int = Field(default=7421, ge=1024, le=65535)


class SecurityConfig(_Section):
    enforce_private_sources: bool = True
    allow_power_actions: bool = False
    pairing_code_ttl_s: int = Field(default=600, ge=60, le=3600)


class IntegrationsConfig(_Section):
    processes: bool = True
    git: bool = True
    ports: bool = True
    claude_code: bool = True
    codex: bool = True
    github: bool = True
    claude_permissions: bool = True
    claude_local_usage: bool = True  # count Claude usage from session files when the status line is absent
    permission_wait_s: int = Field(default=30, ge=5, le=120)


class ProjectConfig(_Section):
    name: str = Field(min_length=1, max_length=60)
    path: Path


class DevConfig(_Section):
    projects: list[ProjectConfig] = []
    process_names: list[str] = [
        "code", "devenv", "pycharm64", "idea64", "webstorm64", "cursor", "node", "python",
        "docker desktop", "com.docker.backend", "postgres", "redis-server", "windowsterminal",
    ]  # fmt: skip
    server_names: list[str] = [
        "node", "python", "deno", "bun", "ruby", "php", "java", "dotnet", "go", "uvicorn", "nginx", "httpd",
        "postgres", "mysqld", "mongod", "redis-server", "com.docker.backend",
    ]  # fmt: skip


class Config(_Section):
    server: ServerConfig = ServerConfig()
    security: SecurityConfig = SecurityConfig()
    integrations: IntegrationsConfig = IntegrationsConfig()
    dev: DevConfig = DevConfig()


def default_data_dir() -> Path:
    override = os.environ.get("PHONEY_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Phoney"


def ensure_user_file(data_dir: Path, name: str) -> Path:
    """Copy the shipped default into the data directory the first time it is needed."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / name
    if not target.exists():
        shutil.copyfile(DEFAULTS_DIR / name, target)
    return target


def load_config(data_dir: Path) -> Config:
    path = ensure_user_file(data_dir, "config.toml")
    with path.open("rb") as fh:
        return Config.model_validate(tomllib.load(fh))
