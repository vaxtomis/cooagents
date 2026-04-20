from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8321


class DatabaseConfig(BaseModel):
    path: str = ".coop/state.db"


class TimeoutConfig(BaseModel):
    dispatch_startup: int = 300
    dispatch_ensure: int = 120
    dispatch_ensure_max_retries: int = 2
    dispatch_reconcile_grace: int = 30
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
    # Why: tick_*_running accepts on ``turn >= max_turns``; turn_count starts
    # at 1, so ``1 >= 1`` force-accepted immediately and made the revise branch
    # dead code. Default of 3 lets the evaluator request up to 2 follow-ups
    # before force-accepting, matching the bundled TURN-revision templates.
    design_max_turns: int = 3
    dev_max_turns: int = 3


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
    # Why: committed YAML must never hold secrets. Prefer env var OPENCLAW_HOOK_TOKEN;
    # fall back to YAML only if the env var is absent. A YAML value of "" means "read env".
    token: str = Field(default_factory=lambda: os.environ.get("OPENCLAW_HOOK_TOKEN", ""))
    default_channel: str = "last"
    default_to: str = ""


class TracingConfig(BaseModel):
    enabled: bool = True
    retention_days: int = 7
    debug_retention_days: int = 3
    orphan_retention_days: int = 3
    cleanup_interval_hours: int = 24


class OpenclawConfig(BaseModel):
    deploy_skills: bool = True
    targets: list[OpenclawTarget] = []
    hooks: OpenclawHooksConfig = OpenclawHooksConfig()


class SecurityConfig(BaseModel):
    """Security boundaries enforced at API layer.

    Why: public-web deployment means untrusted input can reach `repo_path` /
    `repo_url`. A workspace root and a host allowlist bound the blast radius
    if any layer above (auth, validation) is ever bypassed.
    """
    workspace_root: str = "~/cooagents-workspace"
    allowed_repo_hosts: list[str] = ["github.com", "gitee.com"]
    allowed_repo_schemes: list[str] = ["https", "ssh", "git"]
    # Proxies allowed to set X-Forwarded-For. Rate limiting and logging read
    # the real client IP only when the immediate peer is on this list. Default
    # loopback only — matches `host: 127.0.0.1` deployment behind nginx/caddy.
    trusted_proxies: list[str] = ["127.0.0.1", "::1"]
    allowed_origins: list[str] = []

    def resolved_workspace_root(self) -> Path:
        return Path(self.workspace_root).expanduser().resolve()


class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    timeouts: TimeoutConfig = TimeoutConfig()
    health_check: HealthCheckConfig = HealthCheckConfig()
    merge: MergeConfig = MergeConfig()
    acpx: AcpxConfig = AcpxConfig()
    turns: TurnsConfig = TurnsConfig()
    openclaw: OpenclawConfig = OpenclawConfig()
    tracing: TracingConfig = TracingConfig()
    security: SecurityConfig = SecurityConfig()
    preferred_design_agent: str = "claude"
    preferred_dev_agent: str = "claude"


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

    settings = Settings.model_validate(data)

    # Env var overrides empty YAML token (so operators can ship the YAML without secrets).
    if not settings.openclaw.hooks.token:
        env_token = os.environ.get("OPENCLAW_HOOK_TOKEN", "")
        if env_token:
            settings.openclaw.hooks.token = env_token

    return settings


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
