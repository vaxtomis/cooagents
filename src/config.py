from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from src.exceptions import BadRequestError

ROOT = Path(__file__).resolve().parents[1]

# Mirrors src.models.LOCAL_HOST_ID; duplicated here to keep src.config free
# of the heavier src.models import chain (FastAPI / pydantic enums).
_LOCAL_HOST_ID = "local"
_SSH_HOST_PATTERN = re.compile(r"^[\w.\-]+@[\w.\-]+(?::\d+)?$")

# Repo Registry handle: alphanumeric + _ . -, leading [A-Za-z0-9], 1-63 chars.
# Intentionally looser than ``_WORKSPACE_SLUG_RE`` (which lives in src/models)
# because operator-facing repo names commonly carry casing, dots, and
# underscores (e.g. "Frontend.web", "api_v2"). src.config keeps src.models
# out of its import graph, so the regex is duplicated here on purpose.
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$")


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
    session_mode: str | None = "auto"
    cleanup_enabled: bool = True
    cleanup_interval_s: int = Field(default=60, ge=10, le=3600)
    lease_ttl_s: int = Field(default=120, ge=30, le=3600)
    lease_grace_s: int = Field(default=120, ge=0, le=3600)
    terminate_grace_s: int = Field(default=15, ge=1, le=300)
    kill_grace_s: int = Field(default=10, ge=1, le=300)
    cleanup_kill_enabled: bool = True


class TurnsConfig(BaseModel):
    # Why: tick_*_running accepts on ``turn >= max_turns``; turn_count starts
    # at 1, so ``1 >= 1`` force-accepted immediately and made the revise branch
    # dead code. Default of 3 lets the evaluator request up to 2 follow-ups
    # before force-accepting.
    design_max_turns: int = 3
    dev_max_turns: int = 3


class DesignConfig(BaseModel):
    """DesignWork state machine bounds (Phase 3).

    ``max_loops`` caps D3 <-> D5 iterations. Per PRD R4 this is independent
    from ``devwork.max_rounds``; the two loops are semantically different.
    """

    max_loops: int = Field(default=3, ge=0, le=50)
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
            "场景案例",
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

    max_rounds: int = Field(default=10, ge=0, le=50)
    # Per-step LLM wall-clock timeouts (seconds). Step2 plans the
    # iteration design (F2=B); Step3 is prompt-side context retrieval;
    # Step5 is rubric scoring. Step4 is intentionally absent here — it
    # is bounded by ``step4_acpx_wall_ceiling_s`` + ``step_idle_timeout_s``
    # (Phase 3 design: idle_timeout is the active bound).
    step2_timeout: int = 600
    step3_timeout: int = 600
    step5_timeout: int = 600
    # Phase 3 (devwork-acpx-overhaul): how often the heartbeat coroutine ticks
    # while an LLM call is in flight. 15s gives <=30s heartbeat cadence even
    # when the first tick is delayed by a slow process startup.
    progress_heartbeat_interval_s: int = 15
    # Phase 3: idle window. When no heartbeat advances for this many seconds
    # the runner kills the subprocess and the SM escalates with
    # reason="idle_timeout: <step_tag>". In oneshot mode this rarely fires
    # because process-alive == advance; Phase 4 makes it real by plugging
    # acpx status into the heartbeat callback.
    step_idle_timeout_s: int = 300
    # Phase 3: Step4 only — the wall-clock --timeout argument passed to acpx
    # for STEP4_DEVELOP. PRD "Step4 取消 timeout=900s 硬卡": idle_timeout is
    # the active bound; this 3600s acts as a backstop so acpx itself does
    # not kill the process before our wrapper does.
    step4_acpx_wall_ceiling_s: int = 3600
    # Step4 writes findings itself via the LLM. After the LLM process exits,
    # allow a tiny grace period before treating a missing artifact as impl_gap.
    step4_findings_wait_timeout_s: float = Field(default=2.0, ge=0, le=10)
    step4_findings_wait_interval_s: float = Field(default=0.1, ge=0.01, le=1)
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
    # repr=False keeps the token out of repr()/str() so accidental logging of the
    # settings tree does not leak it.
    token: str = Field(
        default_factory=lambda: os.environ.get("OPENCLAW_HOOK_TOKEN", ""),
        repr=False,
    )
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
    # repr=False keeps the secret out of repr()/str().
    secret: str = Field(
        default_factory=lambda: os.environ.get("HERMES_WEBHOOK_SECRET", ""),
        repr=False,
    )
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


class OSSConfig(BaseModel):
    """Aliyun OSS backend config (Phase 6).

    Resolution rule (mirrors OpenclawHooksConfig.token / HermesWebhookConfig.secret):
      * YAML writes non-empty value → YAML overrides env
      * YAML omits the field → ``default_factory`` reads env at construction
      * YAML writes ``""`` → field becomes empty → ``load_settings`` block
        re-reads env (so operators can commit the YAML structure with
        placeholders and still populate from env at runtime)

    Five fields are env-backed: ``bucket``, ``region``, ``endpoint``,
    ``access_key_id``, ``access_key_secret``. Two fields are YAML-only:
    ``enabled`` (bool toggle; no env var per PRD L376) and ``prefix``
    (not a secret; YAML keeps the deploy-time structure obvious).

    When ``enabled=True`` the lifespan factory constructs ``OSSFileStore``
    instead of ``LocalFileStore``. ``load_settings`` enforces that every
    required value is non-empty and raises ``BadRequestError`` otherwise.
    """
    enabled: bool = False
    bucket: str = Field(
        default_factory=lambda: os.environ.get("OSS_BUCKET", "")
    )
    region: str = Field(
        default_factory=lambda: os.environ.get("OSS_REGION", "")
    )
    endpoint: str = Field(
        default_factory=lambda: os.environ.get("OSS_ENDPOINT", "")
    )
    # Optional key prefix applied to every object key. Empty = no prefix.
    # When set, must end with "/". Validation lives in OSSFileStore.__init__.
    prefix: str = ""
    # repr=False on both keys: the secret obviously, the id by convention so
    # the credential pair never lands in a log line together.
    access_key_id: str = Field(
        default_factory=lambda: os.environ.get("OSS_ACCESS_KEY_ID", ""),
        repr=False,
    )
    access_key_secret: str = Field(
        default_factory=lambda: os.environ.get("OSS_ACCESS_KEY_SECRET", ""),
        repr=False,
    )


class StorageConfig(BaseModel):
    """Storage subsystem config.

    OSS is a write-only backup target in Phase 1–7b: every workspace
    artifact write propagates through ``register()`` → local + PUT OSS +
    DB upsert. Phase 8 will introduce Agent-side hydration / CAS primitives.
    """
    oss: OSSConfig = OSSConfig()


class AgentHostConfig(BaseModel):
    """One entry in ``config/agents.yaml`` ``hosts`` list (Phase 8a).

    ``host`` accepts either the literal ``"local"`` or an SSH spec
    ``"user@host[:port]"``. ``ssh_key`` paths are expanded with
    ``Path(p).expanduser()`` at load time so ``~/.ssh/...`` works.
    """
    id: str = Field(..., min_length=1, max_length=64)
    host: str = Field(..., min_length=1)
    agent_type: Literal["claude", "codex", "both"] = "both"
    max_concurrent: int = Field(1, ge=1, le=64)
    ssh_key: str | None = None
    labels: list[str] = Field(default_factory=list)

    @field_validator("host")
    @classmethod
    def _validate_host(cls, v: str) -> str:
        if v == _LOCAL_HOST_ID:
            return v
        if not _SSH_HOST_PATTERN.match(v):
            raise ValueError(
                f"host must be 'local' or 'user@host[:port]', got {v!r}"
            )
        return v

    @field_validator("ssh_key")
    @classmethod
    def _expand_ssh_key(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return str(Path(v).expanduser())


class AgentsConfig(BaseModel):
    """Top-level shape of ``config/agents.yaml`` (Phase 8a)."""
    hosts: list[AgentHostConfig] = Field(default_factory=list)
    # Default ON: every SSH connection must verify the host key against
    # ``ssh_known_hosts_path``. Flip to False only for throwaway dev hosts;
    # in any deployment that crosses an untrusted network this is MITM bait.
    ssh_strict_host_key: bool = True
    # Path to the known_hosts file consulted when ``ssh_strict_host_key`` is
    # True. Defaults to the operator's user file. ``~`` is expanded at load.
    ssh_known_hosts_path: str = "~/.ssh/known_hosts"
    # Paths under which an ``ssh_key`` value is accepted. Anything outside
    # these roots is rejected at upsert/load time so an attacker who can
    # write a host row cannot point ``client_keys`` at, e.g., /etc/shadow.
    ssh_key_allowed_roots: list[str] = Field(
        default_factory=lambda: ["~/.ssh"]
    )

    @field_validator("hosts")
    @classmethod
    def _no_duplicate_ids(cls, v: list[AgentHostConfig]) -> list[AgentHostConfig]:
        seen: set[str] = set()
        for h in v:
            if h.id in seen:
                raise ValueError(f"duplicate agent host id in agents.yaml: {h.id!r}")
            seen.add(h.id)
        return v

    @field_validator("ssh_known_hosts_path")
    @classmethod
    def _expand_known_hosts(cls, v: str) -> str:
        return str(Path(v).expanduser())

    @model_validator(mode="after")
    def _validate_host_ssh_keys(self) -> "AgentsConfig":
        # Catch malformed YAML at load time rather than at first dispatch.
        for h in self.hosts:
            if h.ssh_key and not self.is_ssh_key_path_allowed(h.ssh_key):
                raise ValueError(
                    f"agents.yaml host {h.id!r}: ssh_key {h.ssh_key!r} "
                    f"is outside ssh_key_allowed_roots="
                    f"{self.ssh_key_allowed_roots}"
                )
        return self

    def _resolved_allowed_roots(self) -> list[Path]:
        # Resolve at call time — Pydantic v2 does not run field validators on
        # defaults, and this list rarely runs in a hot loop.
        out: list[Path] = []
        for p in self.ssh_key_allowed_roots:
            try:
                out.append(Path(p).expanduser().resolve())
            except (OSError, RuntimeError):
                continue
        return out

    def is_ssh_key_path_allowed(self, path: str) -> bool:
        """Return True iff ``path`` resolves under one of the allowed roots."""
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return False
        for root in self._resolved_allowed_roots():
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return True
        return False


# Closed enum mirroring ``src.models.RepoRole``. Duplicated here to keep
# src.config out of src.models's import chain.
_VALID_REPO_ROLES: frozenset[str] = frozenset(
    {"backend", "frontend", "fullstack", "infra", "docs", "other"}
)


class RepoConfig(BaseModel):
    """One entry in ``config/repos.yaml`` ``repos`` list (Phase 1, repo-registry).

    ``name`` is the operator-facing handle (stable across restarts; the DB
    primary key ``id`` is allocated lazily by the registry on first sync).
    ``ssh_key_path`` is expanded with ``Path(p).expanduser()`` at load time
    and stored on ``repos.ssh_key_path`` verbatim. ``role`` (Phase 4) drives
    primary-ref auto-selection in DevWork creation.
    """
    name: str = Field(..., min_length=1, max_length=63)
    url: str = Field(..., min_length=1)
    default_branch: str = Field("main", min_length=1, max_length=200)
    ssh_key_path: str | None = None
    role: str = "other"

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _REPO_NAME_RE.match(v):
            raise ValueError(
                "repo name must match [A-Za-z0-9][A-Za-z0-9_.\\-]{0,62}"
            )
        return v

    @field_validator("ssh_key_path")
    @classmethod
    def _expand_ssh_key(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return str(Path(v).expanduser())

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        if v not in _VALID_REPO_ROLES:
            raise ValueError(
                f"role must be one of {sorted(_VALID_REPO_ROLES)}; got {v!r}"
            )
        return v


class ReposFetchConfig(BaseModel):
    """Phase 2 fetcher knobs. Declared here so ``repos.yaml`` shape is stable
    across phases; Phase 1 readers ignore the values."""
    interval_s: int = Field(300, ge=10, le=86400)
    parallel: int = Field(4, ge=1, le=64)
    # Wall-clock cap on a single ``git clone`` / ``git fetch`` invocation.
    # Protects the loop's semaphore slots and the route's request worker
    # from hostile/stalled remotes that ``BatchMode=yes`` cannot guard.
    timeout_s: int = Field(120, ge=1, le=3600)


class ReposConfig(BaseModel):
    """Top-level shape of ``config/repos.yaml`` (Phase 1, repo-registry)."""
    repos: list[RepoConfig] = Field(default_factory=list)
    fetch: ReposFetchConfig = ReposFetchConfig()
    # Mirrors the agents.yaml flag for symmetry. Phase 2's fetcher reads it.
    ssh_strict_host_key: bool = True

    @field_validator("repos")
    @classmethod
    def _no_duplicate_names(cls, v: list[RepoConfig]) -> list[RepoConfig]:
        seen: set[str] = set()
        for r in v:
            if r.name in seen:
                raise ValueError(
                    f"duplicate repo name in repos.yaml: {r.name!r}"
                )
            seen.add(r.name)
        return v


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
    storage: StorageConfig = StorageConfig()
    # Phase 8a: populated by load_settings() from config/agents.yaml. Kept on
    # Settings (not constructed eagerly) so tests can inject AgentsConfig
    # instances directly without touching the on-disk file.
    agents: AgentsConfig = AgentsConfig()
    # Phase 1 (repo-registry): populated by load_settings() from
    # config/repos.yaml. Empty list when the file is absent.
    repos: ReposConfig = ReposConfig()
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

    # Empty YAML value → re-read env (symmetric with openclaw.hooks.token and
    # hermes.webhook.secret pattern above). Non-empty YAML takes precedence.
    for attr, env_name in (
        ("bucket", "OSS_BUCKET"),
        ("region", "OSS_REGION"),
        ("endpoint", "OSS_ENDPOINT"),
        ("access_key_id", "OSS_ACCESS_KEY_ID"),
        ("access_key_secret", "OSS_ACCESS_KEY_SECRET"),
    ):
        if not getattr(settings.storage.oss, attr):
            env_val = os.environ.get(env_name, "")
            if env_val:
                setattr(settings.storage.oss, attr, env_val)

    # Phase 8a: load agents.yaml siblings to settings.yaml. The file is
    # optional — missing or empty file becomes AgentsConfig(hosts=[]).
    settings.agents = load_agents(path.parent / "agents.yaml")
    # Phase 1 (repo-registry): load repos.yaml side-by-side. Missing file
    # is fine — empty registry is a valid first-run state.
    settings.repos = load_repos(path.parent / "repos.yaml")

    if settings.storage.oss.enabled:
        missing = [
            name for name, val in (
                ("bucket", settings.storage.oss.bucket),
                ("region", settings.storage.oss.region),
                ("endpoint", settings.storage.oss.endpoint),
                ("access_key_id", settings.storage.oss.access_key_id),
                ("access_key_secret", settings.storage.oss.access_key_secret),
            )
            if not val
        ]
        if missing:
            raise BadRequestError(
                "settings.storage.oss.enabled=true requires: "
                f"{missing}. Set them in config/settings.yaml under "
                "'storage.oss' or via env vars OSS_BUCKET / OSS_REGION / "
                "OSS_ENDPOINT / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET."
            )

    return settings


def load_agents(path: Path | str | None = None) -> AgentsConfig:
    """Load ``config/agents.yaml`` into an :class:`AgentsConfig`.

    Missing file → empty hosts list (no error). Top-level YAML must be a
    mapping with optional ``hosts`` and ``ssh_strict_host_key`` keys.
    Duplicate ``id`` values across ``hosts`` raise :class:`BadRequestError`.
    """
    if path is None:
        path = ROOT / "config" / "agents.yaml"
    path = Path(path)

    if not path.exists():
        return AgentsConfig()

    with path.open("r", encoding="utf-8") as fh:
        data: Any = yaml.safe_load(fh) or {}

    # Allow legacy shape `hosts: [...]` at the top level (no wrapper key).
    if isinstance(data, list):
        data = {"hosts": data}
    if not isinstance(data, dict):
        raise BadRequestError(
            f"agents.yaml must be a mapping or list of hosts, got {type(data).__name__}"
        )

    try:
        return AgentsConfig.model_validate(data)
    except Exception as exc:  # pydantic ValidationError or our own ValueError
        raise BadRequestError(f"invalid agents.yaml: {exc}") from exc


def load_repos(path: Path | str | None = None) -> ReposConfig:
    """Load ``config/repos.yaml`` into a :class:`ReposConfig`.

    Missing file → empty repos list. Top-level YAML must be a mapping with
    optional ``repos``, ``fetch``, and ``ssh_strict_host_key`` keys, or the
    legacy list shape ``[{name: ..., url: ...}, ...]``. Duplicate ``name``
    values raise :class:`BadRequestError`.
    """
    if path is None:
        path = ROOT / "config" / "repos.yaml"
    path = Path(path)

    if not path.exists():
        return ReposConfig()

    with path.open("r", encoding="utf-8") as fh:
        data: Any = yaml.safe_load(fh) or {}

    # Allow legacy shape `[ {name, url}, ... ]` at the top level.
    if isinstance(data, list):
        data = {"repos": data}
    if not isinstance(data, dict):
        raise BadRequestError(
            f"repos.yaml must be a mapping or list of repos, got {type(data).__name__}"
        )

    try:
        return ReposConfig.model_validate(data)
    except Exception as exc:  # pydantic ValidationError or our own ValueError
        raise BadRequestError(f"invalid repos.yaml: {exc}") from exc
