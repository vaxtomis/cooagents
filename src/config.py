from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8321


class DatabaseConfig(BaseModel):
    path: str = ".coop/state.db"


class TimeoutConfig(BaseModel):
    dispatch_startup: int = 300
    design_execution: int = 1800
    dev_execution: int = 3600
    review_reminder: int = 86400


class HealthCheckConfig(BaseModel):
    interval: int = 60
    ssh_timeout: int = 5


class MergeConfig(BaseModel):
    auto_rebase: bool = True
    max_resume_count: int = 3


class AcpxConfig(BaseModel):
    permission_mode: str = "approve-all"
    default_format: str = "json"
    ttl: int = 600
    json_strict: bool = True
    model: str | None = None
    allowed_tools_design: str | None = None
    allowed_tools_dev: str | None = None


class TurnsConfig(BaseModel):
    design_max_turns: int = 1
    dev_max_turns: int = 1


class OpenclawTarget(BaseModel):
    type: str = "local"              # "local" or "ssh"
    skills_dir: str = "~/.openclaw/skills"
    host: str | None = None          # SSH only
    port: int = 22                   # SSH only
    user: str | None = None          # SSH only
    key: str | None = None           # SSH only


class OpenclawHooksConfig(BaseModel):
    enabled: bool = False
    url: str = "http://127.0.0.1:18789/hooks/agent"
    token: str = ""
    default_channel: str = "last"
    default_to: str = ""


class OpenclawConfig(BaseModel):
    deploy_skills: bool = True
    targets: list[OpenclawTarget] = []
    hooks: OpenclawHooksConfig = OpenclawHooksConfig()


class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    timeouts: TimeoutConfig = TimeoutConfig()
    health_check: HealthCheckConfig = HealthCheckConfig()
    merge: MergeConfig = MergeConfig()
    acpx: AcpxConfig = AcpxConfig()
    turns: TurnsConfig = TurnsConfig()
    openclaw: OpenclawConfig = OpenclawConfig()


def load_settings(path: Path | str | None = None) -> Settings:
    """Load settings from a YAML file.

    Parameters
    ----------
    path:
        Path to the YAML configuration file. Defaults to
        ``<project_root>/config/settings.yaml``.

    Returns
    -------
    Settings
        Populated settings instance. Any missing keys fall back to defaults.
    """
    if path is None:
        path = ROOT / "config" / "settings.yaml"
    path = Path(path)

    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
    else:
        data = {}

    return Settings.model_validate(data)


def load_agent_hosts(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Load the list of agent host definitions from a YAML file.

    Parameters
    ----------
    path:
        Path to the YAML configuration file. Defaults to
        ``<project_root>/config/agents.yaml``.

    Returns
    -------
    list[dict]
        List of host definition dicts (may be empty).
    """
    if path is None:
        path = ROOT / "config" / "agents.yaml"
    path = Path(path)

    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}

    return data.get("hosts", []) or []
