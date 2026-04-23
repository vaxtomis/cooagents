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


class DesignConfig(BaseModel):
    """DesignWork state machine bounds (Phase 3).

    ``max_loops`` caps D3 <-> D5 iterations. Per PRD R4 this is independent
    from ``devwork.max_rounds``; the two loops are semantically different.
    """

    max_loops: int = 3
    # Per-LLM-call timeout for D4 LLM_GENERATE. Decoupled from the legacy
    # ``TimeoutConfig.design_execution`` (1800s) because that value was tuned
    # for the old 15-stage design phase (agent session lifecycle); the
    # DesignWork one-shot prompt typically completes in <5 min. 600s gives
    # a 2x buffer while keeping feedback fast. (U5 decision.)
    execution_timeout: int = 600
    # Section titles are Markdown ## headings. Order matches
    # templates/design_doc.md.tpl; validator accepts missing only if the
    # corresponding section is absent from this list.
    required_sections: list[str] = Field(
        default_factory=lambda: [
            "用户故事",
            "用户案例",
            "详细操作流程",
            "验收标准",
            "打分 rubric",
        ]
    )
    # When needs_frontend_mockup=True, these sections become mandatory.
    mockup_sections: list[str] = Field(default_factory=lambda: ["页面结构"])
    allow_optimize_mode: bool = False  # v1 stubbed; flip True in a later phase


class DevWorkConfig(BaseModel):
    """DevWork state machine bounds (Phase 4, PRD L191, L177).

    ``max_rounds`` caps Step2<->Step5 iterations. Independent from
    ``design.max_loops`` (PRD R4) because the two loops are semantically
    different (requirements refinement vs. code-quality scoring).
    """

    max_rounds: int = 5
    # Per-step LLM timeouts (seconds). Step2 plans the iteration design
    # (F2=B); Step3 is prompt-side context retrieval; Step4 includes one
    # self-repair attempt inside the same LLM call; Step5 is rubric scoring.
    step2_timeout: int = 600
    step3_timeout: int = 600
    step4_timeout: int = 900
    step5_timeout: int = 600
    # v1 default: Step5 auto-approves when score>=threshold. Phase 5 will
    # flip this to gate on a human confirmation event (PRD L145).
    require_human_exit_confirm: bool = False


class ScoringConfig(BaseModel):
    """Rubric threshold defaults for scoring loops.

    Used as the final fallback when (a) the API request did not supply
    ``rubric_threshold``, AND (b) the LLM-produced front-matter omits it.
    Priority: API request > LLM front-matter > default_threshold. (U2)
    """

    default_threshold: int = 80


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


class HermesWebhookConfig(BaseModel):
    """Outbound notification target for a Hermes Agent gateway.

    Why: Hermes has no OpenClaw-style `/hooks/agent` ingress. Its generic
    webhook platform (``gateway/platforms/webhook.py``) accepts HMAC-SHA256
    signed POSTs on per-route secrets and can turn them into agent prompts.
    cooagents already signs its generic webhooks with the per-subscription
    secret, so a Hermes target is just a subscription URL + shared secret —
    no new delivery code required.
    """
    enabled: bool = False
    # Default route matches the suggestion in references/hermes-integration.md
    url: str = "http://127.0.0.1:8644/webhook/cooagents"
    # ``$ENV:VARNAME`` is resolved by webhook_notifier._resolve_secret.
    secret: str = Field(default_factory=lambda: os.environ.get("HERMES_WEBHOOK_SECRET", ""))
    # Event types pushed to Hermes; empty list means "all events the notifier
    # normally sends to OpenClaw". The Hermes side decides which to act on.
    events: list[str] = []


class HermesConfig(BaseModel):
    """Hermes Agent integration.

    When ``enabled`` is true, cooagents deploys the same ``skills/`` bundle
    it sends to OpenClaw into ``skills_dir`` (typically ``~/.hermes/skills``)
    and, if ``webhook.enabled`` is true, makes sure an outbound webhook
    subscription pointing at the Hermes webhook route is registered. The
    cooagents-setup skill drives that registration during install.
    """
    enabled: bool = False
    skills_dir: str = "~/.hermes/skills"
    deploy_skills: bool = True
    webhook: HermesWebhookConfig = HermesWebhookConfig()


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
    hermes: HermesConfig = HermesConfig()
    tracing: TracingConfig = TracingConfig()
    security: SecurityConfig = SecurityConfig()
    design: DesignConfig = DesignConfig()
    scoring: ScoringConfig = ScoringConfig()
    devwork: DevWorkConfig = DevWorkConfig()
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

    if not settings.hermes.webhook.secret:
        env_secret = os.environ.get("HERMES_WEBHOOK_SECRET", "")
        if env_secret:
            settings.hermes.webhook.secret = env_secret

    return settings
