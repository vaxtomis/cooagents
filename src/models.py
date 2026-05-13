import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class CreateWebhookSubscriptionRequest(BaseModel):
    """Webhook subscription request.

    Validates ``events`` against the frozen registry so a typo cannot write a
    dead subscription that never fires.
    """
    url: str
    events: list[str] | None = None
    secret: str | None = None
    slug: str | None = None

    @field_validator("events")
    @classmethod
    def _events_in_known_set(cls, v):
        if v is None:
            return v
        from src.webhook_events import KNOWN_EVENTS

        unknown = [e for e in v if e not in KNOWN_EVENTS]
        if unknown:
            raise ValueError(f"unknown event names: {unknown}")
        return v

    @field_validator("slug")
    @classmethod
    def _reject_builtin_slug(cls, v):
        if v in {"openclaw", "hermes"}:
            raise ValueError(
                f"slug {v!r} is reserved for builtin subscriptions"
            )
        return v


class GateActionRequest(BaseModel):
    # actor is not in the body — serves Web only, actor is injected from the
    # authenticated session.
    note: str | None = None


# ---------------------------------------------------------------------------
# Workspace-driven domain models
# ---------------------------------------------------------------------------

class WorkspaceStatus(str, Enum):
    active = "active"
    archived = "archived"


class DesignWorkMode(str, Enum):
    new = "new"
    optimize = "optimize"


class DesignWorkState(str, Enum):
    INIT = "INIT"
    MODE_BRANCH = "MODE_BRANCH"
    PRE_VALIDATE = "PRE_VALIDATE"
    PROMPT_COMPOSE = "PROMPT_COMPOSE"
    LLM_GENERATE = "LLM_GENERATE"
    MOCKUP = "MOCKUP"
    POST_VALIDATE = "POST_VALIDATE"
    PERSIST = "PERSIST"
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


class DesignDocStatus(str, Enum):
    draft = "draft"
    published = "published"
    superseded = "superseded"


class DevWorkStep(str, Enum):
    INIT = "INIT"
    STEP1_VALIDATE = "STEP1_VALIDATE"
    STEP2_ITERATION = "STEP2_ITERATION"
    STEP3_CONTEXT = "STEP3_CONTEXT"
    STEP4_DEVELOP = "STEP4_DEVELOP"
    STEP5_REVIEW = "STEP5_REVIEW"
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


class ProblemCategory(str, Enum):
    req_gap = "req_gap"
    impl_gap = "impl_gap"
    design_hollow = "design_hollow"


class AgentKind(str, Enum):
    claude = "claude"
    codex = "codex"


class Workspace(BaseModel):
    id: str
    title: str
    slug: str
    status: WorkspaceStatus = WorkspaceStatus.active
    root_path: str
    created_at: str
    updated_at: str


class DesignWork(BaseModel):
    id: str
    workspace_id: str
    mode: DesignWorkMode
    parent_version: str | None = None
    needs_frontend_mockup: bool = False
    current_state: DesignWorkState = DesignWorkState.INIT
    loop: int = 0
    missing_sections: list[str] | None = None
    agent: AgentKind = AgentKind.claude
    escalated_at: str | None = None
    escalation_reason: str | None = None
    user_input_path: str | None = None
    output_design_doc_id: str | None = None
    # Phase 3 additions (U7): persisted here instead of in an in-memory cache
    # so a server restart mid-loop can reconstruct prompt + output paths.
    title: str | None = None
    sub_slug: str | None = None
    version: str | None = None
    output_path: str | None = None
    gates_json: str | None = None
    created_at: str
    updated_at: str


class DesignDoc(BaseModel):
    id: str
    workspace_id: str
    slug: str
    version: str
    path: str
    parent_version: str | None = None
    needs_frontend_mockup: bool = False
    rubric_threshold: int = 85
    status: DesignDocStatus = DesignDocStatus.draft
    content_hash: str | None = None
    byte_size: int | None = None
    created_at: str
    published_at: str | None = None


class DevWork(BaseModel):
    id: str
    workspace_id: str
    design_doc_id: str
    prompt: str
    recommended_tech_stack: str | None = None
    worktree_path: str | None = None
    worktree_branch: str | None = None
    current_step: DevWorkStep = DevWorkStep.INIT
    iteration_rounds: int = 0
    first_pass_success: bool | None = None
    last_score: int | None = None
    last_problem_category: ProblemCategory | None = None
    agent: AgentKind = AgentKind.claude
    gates: dict | None = None
    escalated_at: str | None = None
    completed_at: str | None = None
    created_at: str
    updated_at: str
    # Phase 4 (repo-registry): repo binding moved to dev_work_repos table.
    repo_refs: list["DevRepoRefView"] = Field(default_factory=list)


class DevIterationNote(BaseModel):
    id: str
    dev_work_id: str
    round: int
    markdown_path: str
    score_history: list[int] | None = None
    created_at: str


class Review(BaseModel):
    id: str
    dev_work_id: str | None = None
    design_work_id: str | None = None
    dev_iteration_note_id: str | None = None
    round: int
    score: int | None = None
    score_breakdown: dict | None = None
    issues: list[dict] | None = None
    findings: list[dict] | None = None
    next_round_hints: list[dict] | None = None
    problem_category: ProblemCategory | None = None
    reviewer: str | None = None
    created_at: str


class WorkspaceEvent(BaseModel):
    id: int | None = None
    event_id: str
    event_name: str
    workspace_id: str | None = None
    correlation_id: str | None = None
    payload: dict | None = None
    ts: str


class PaginationMeta(BaseModel):
    limit: int
    offset: int
    total: int
    has_more: bool


# ---------------------------------------------------------------------------
# Phase 2 — Workspace lifecycle DTOs
# ---------------------------------------------------------------------------

# Kebab-case slug, 1-63 chars, no leading/trailing dash, no consecutive dashes.
# Mirrors Docker/k8s naming rules. Single source of truth: keep in sync with
# src.workspace_manager._SLUG_RE (the manager enforces the same shape as
# defense-in-depth at the FS boundary).
_WORKSPACE_SLUG_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9]|-(?!-)){0,61}[a-z0-9]$|^[a-z0-9]$"
)


class CreateWorkspaceRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    slug: str

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str) -> str:
        if not _WORKSPACE_SLUG_RE.match(v):
            raise ValueError(
                "slug must be kebab-case (1-63 chars, no leading/trailing dash, "
                "no consecutive dashes)"
            )
        return v


class WorkspaceSyncReport(BaseModel):
    fs_only: list[str] = Field(default_factory=list)
    db_only: list[str] = Field(default_factory=list)
    in_sync: list[str] = Field(default_factory=list)


class WorkspaceAttachment(BaseModel):
    filename: str
    markdown_path: str
    content_hash: str | None = None
    byte_size: int | None = None
    converted_from: Literal["md", "docx"]
    image_paths: list[str] = Field(default_factory=list)


class WorkspaceMetrics(BaseModel):
    """PRD Phase 8 Success Metrics — lifetime by default; windowed via ?since=&until=.

    Rates are ``0.0`` when their denominator is zero (no division-by-zero leak).
    """
    human_intervention_per_workspace: float
    active_workspaces: int
    first_pass_success_rate: float
    avg_iteration_rounds: float


class WorkspacePage(BaseModel):
    items: list[Workspace] = Field(default_factory=list)
    pagination: PaginationMeta


class RepoRegistryMetrics(BaseModel):
    """PRD Phase 9 (repo-registry) Success Metrics 2 and 3 — lifetime by
    default; ``?since=&until=`` windows ``multi_repo_dev_work_share``.
    ``healthy_repos_share`` is a current-state snapshot and ignores the
    window (parallel to ``WorkspaceMetrics.active_workspaces``).

    Metric 1 (DevWork creation reject rate) is deferred to a follow-up
    PRP; the schema has no ``dev_works.state`` / ``last_err`` columns.
    """
    multi_repo_dev_work_share: float
    healthy_repos_share: float


# ---------------------------------------------------------------------------
# Phase 3 — DesignWork request/response DTOs
# ---------------------------------------------------------------------------


class CreateDesignWorkRequest(BaseModel):
    workspace_id: str
    title: str = Field(..., min_length=1, max_length=120)
    slug: str  # DesignDoc sub-slug; unique within the workspace (U1).
    user_input: str = Field(..., min_length=1, max_length=20000)
    mode: DesignWorkMode = DesignWorkMode.new
    parent_version: str | None = None
    needs_frontend_mockup: bool = False
    agent: AgentKind | None = None
    # Optional per-DesignWork override. When None, D6 PERSIST falls back
    # first to the LLM-produced front-matter, then to
    # config.scoring.default_threshold (=80). (U2)
    rubric_threshold: int | None = Field(default=None, ge=1, le=100)
    # Optional per-DesignWork override for D3 <-> D5 retry loops. None uses
    # config.design.max_loops.
    max_loops: int | None = Field(default=None, ge=0, le=50)
    # Phase 4 (repo-registry): optional repo binding. Empty list keeps
    # pure-doc DesignWorks creatable.
    repo_refs: list["RepoRef"] = Field(default_factory=list)
    # Uploaded supplemental markdown files under attachments/. .docx uploads
    # are converted to markdown before their paths are referenced here.
    attachment_paths: list[str] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str) -> str:
        if not _WORKSPACE_SLUG_RE.match(v):
            raise ValueError(
                "slug must be kebab-case (1-63 chars, no leading/trailing dash, "
                "no consecutive dashes)"
            )
        return v

    @field_validator("attachment_paths")
    @classmethod
    def _check_attachment_paths(cls, v: list[str]) -> list[str]:
        from src.design_attachments import validate_attachment_paths

        return validate_attachment_paths(v)

    @model_validator(mode="after")
    def _check_mode_parent_combo(self) -> "CreateDesignWorkRequest":
        # Enforce the mode/parent_version invariant at the HTTP boundary so
        # clients get an immediate 422 instead of a 201 + ESCALATED row.
        if self.mode == DesignWorkMode.new and self.parent_version is not None:
            raise ValueError("mode=new must not supply parent_version")
        if self.mode == DesignWorkMode.optimize and self.parent_version is None:
            raise ValueError("mode=optimize requires parent_version")
        return self


class RetryDesignWorkRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = None
    user_input: str | None = Field(default=None, min_length=1, max_length=20000)
    needs_frontend_mockup: bool | None = None
    agent: AgentKind | None = None
    # Omitted means "reuse the source repo bindings"; [] means "retry with no
    # repo bindings".
    repo_refs: list["RepoRef"] | None = None
    # Omitted means "reuse source attachments"; [] means "retry with none".
    attachment_paths: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_non_clearable_nulls(cls, data):
        """Keep omitted-vs-overridden retry fields unambiguous."""
        if not isinstance(data, dict):
            return data
        nullable = {"agent"}
        retry_fields = {
            "title",
            "slug",
            "user_input",
            "needs_frontend_mockup",
            "agent",
            "repo_refs",
            "attachment_paths",
        }
        explicit_nulls = [
            name for name, value in data.items()
            if name in retry_fields and value is None and name not in nullable
        ]
        if explicit_nulls:
            raise ValueError(
                f"fields cannot be null when provided: {sorted(explicit_nulls)}"
            )
        return data

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str | None) -> str | None:
        if v is not None and not _WORKSPACE_SLUG_RE.match(v):
            raise ValueError(
                "slug must be kebab-case (1-63 chars, no leading/trailing dash, "
                "no consecutive dashes)"
            )
        return v

    @field_validator("attachment_paths")
    @classmethod
    def _check_attachment_paths(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        from src.design_attachments import validate_attachment_paths

        return validate_attachment_paths(v)


class DesignWorkRetrySource(BaseModel):
    title: str
    slug: str
    user_input: str
    needs_frontend_mockup: bool = False
    agent: AgentKind | None = None
    repo_refs: list["RepoRef"] = Field(default_factory=list)
    attachment_paths: list[str] = Field(default_factory=list)


class DesignWorkProgress(BaseModel):
    id: str
    workspace_id: str
    mode: DesignWorkMode
    current_state: DesignWorkState
    loop: int
    max_loops: int = 0
    missing_sections: list[str] | None = None
    output_design_doc_id: str | None = None
    escalated_at: str | None = None
    escalation_reason: str | None = None
    title: str | None = None
    sub_slug: str | None = None
    version: str | None = None
    created_at: str
    updated_at: str
    is_running: bool = False
    # Phase 4 (repo-registry): persisted refs from design_work_repos.
    repo_refs: list["DesignRepoRefView"] = Field(default_factory=list)
    attachment_paths: list[str] = Field(default_factory=list)


class DesignWorkPage(BaseModel):
    items: list[DesignWorkProgress] = Field(default_factory=list)
    pagination: PaginationMeta


# ---------------------------------------------------------------------------
# Phase 4 — DevWork request/response DTOs
# ---------------------------------------------------------------------------


class CreateDevWorkRequest(BaseModel):
    workspace_id: str
    design_doc_id: str
    prompt: str = Field(..., min_length=1, max_length=20000)
    agent: AgentKind | None = None
    # Optional human guidance for the planning round. When disabled, Step2
    # should reuse historical choices or let the agent infer a stack from the
    # design doc and repos.
    recommend_tech_stack: bool = False
    recommended_tech_stack: str | None = Field(default=None, max_length=4000)
    # Optional per-DevWork overrides. None means use the design_doc/global
    # threshold and config.devwork.max_rounds defaults.
    rubric_threshold: int | None = Field(default=None, ge=1, le=100)
    max_rounds: int | None = Field(default=None, ge=0, le=50)
    # Phase 4 (repo-registry): replaces the free-form ``repo_path`` field.
    # At least one ref required; ``mount_name`` must be unique within the
    # payload, and at most one ref may carry ``is_primary=True``. The
    # 4-step validation chain (existence → health → branch resolves →
    # in-payload mount uniqueness) lives in ``routes/_repo_refs_validation``.
    repo_refs: list["DevRepoRef"] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_repo_refs(self) -> "CreateDevWorkRequest":
        mounts = [r.mount_name for r in self.repo_refs]
        if len(set(mounts)) != len(mounts):
            raise ValueError(
                f"duplicate mount_name in repo_refs: {sorted(mounts)}"
            )
        primaries = sum(1 for r in self.repo_refs if r.is_primary)
        if primaries > 1:
            raise ValueError(
                "at most one repo_ref may have is_primary=True; "
                f"got {primaries}"
            )
        if not self.recommend_tech_stack:
            self.recommended_tech_stack = None
            return self
        stack = (self.recommended_tech_stack or "").strip()
        if not stack:
            raise ValueError(
                "recommended_tech_stack is required when "
                "recommend_tech_stack=True"
            )
        self.recommended_tech_stack = stack
        return self


class ContinueDevWorkRequest(BaseModel):
    additional_rounds: int = Field(..., ge=1, le=50)
    rubric_threshold: int | None = Field(default=None, ge=1, le=100)


class ProgressSnapshot(BaseModel):
    """Phase 3 (devwork-acpx-overhaul): one heartbeat tick projected onto
    the GET /dev-works/{id} response.

    ``None`` on :class:`DevWorkProgress.progress` means no LLM call is in
    flight — the SM clears it on dispatch close. Decoded from the
    ``dev_works.current_progress_json`` blob written by the heartbeat
    callback in ``DevWorkStateMachine._run_llm``.
    """

    last_heartbeat_at: str
    elapsed_s: int
    step: str
    round: int
    dispatch_id: str | None = None


class DevWorkProgress(BaseModel):
    id: str
    workspace_id: str
    design_doc_id: str
    recommended_tech_stack: str | None = None
    current_step: DevWorkStep
    iteration_rounds: int
    max_rounds: int = 0
    first_pass_success: bool | None = None
    last_score: int | None = None
    last_problem_category: ProblemCategory | None = None
    escalated_at: str | None = None
    completed_at: str | None = None
    # F1: expose worktree paths so operators / UI can inspect the sandbox.
    worktree_path: str | None = None
    worktree_branch: str | None = None
    created_at: str
    updated_at: str
    is_running: bool = False
    continue_available: bool = False
    resume_available: bool = False
    resume_step: DevWorkStep | None = None
    # Phase 3 (devwork-acpx-overhaul): latest heartbeat tick from the
    # in-flight LLM call. ``None`` when no call is running (or this DevWork
    # has never reached an LLM step).
    progress: ProgressSnapshot | None = None
    # Phase 4 (repo-registry): persisted refs from dev_work_repos.
    repo_refs: list["DevRepoRefView"] = Field(default_factory=list)
    # Phase 5 (repo-registry): worker-facing handoff. Same row source as
    # repo_refs, additive ``url`` / ``ssh_key_path`` / ``push_err`` so
    # the agent host worker can clone, push, and writeback push outcomes
    # without a follow-up GET on /api/v1/repos/{id}. UI consumers keep
    # reading repo_refs.
    repos: list["WorkerRepoHandoff"] = Field(default_factory=list)


class DevWorkPage(BaseModel):
    items: list[DevWorkProgress] = Field(default_factory=list)
    pagination: PaginationMeta


# ---------------------------------------------------------------------------
# Phase 8a — Agent host dispatch DTOs
# ---------------------------------------------------------------------------

AgentHostType = Literal["claude", "codex", "both"]
HealthStatus = Literal["unknown", "healthy", "unhealthy"]
DispatchState = Literal["queued", "running", "succeeded", "failed", "timeout"]
CorrelationKind = Literal["design_work", "dev_work"]

# Reserved id for the always-present in-process host. Hard-coded so the same
# string can be used in schema defaults, dispatch_decider fallback, and
# sync_from_config without import cycles.
LOCAL_HOST_ID = "local"

# Mirrors src.config._SSH_HOST_PATTERN. Duplicated so HTTP-layer validation
# does not pull in the YAML config module.
_API_SSH_HOST_PATTERN = re.compile(r"^[\w.\-]+@[\w.\-]+(?::\d+)?$")


def _validate_agent_host_field(v: str) -> str:
    if v == LOCAL_HOST_ID:
        return v
    if not _API_SSH_HOST_PATTERN.match(v):
        raise ValueError(
            f"host must be 'local' or 'user@host[:port]', got {v!r}"
        )
    return v


class AgentHost(BaseModel):
    id: str
    host: str
    agent_type: AgentHostType
    max_concurrent: int = 1
    ssh_key: str | None = None
    labels: list[str] = Field(default_factory=list)
    health_status: HealthStatus = "unknown"
    last_health_at: str | None = None
    last_health_err: str | None = None
    created_at: str
    updated_at: str


class CreateAgentHostRequest(BaseModel):
    """Operator-facing create payload.

    The ``id`` field is optional; when omitted the repo allocates ``ah-<hex12>``.
    The reserved id ``"local"`` is rejected at the route layer.
    """
    id: str | None = None
    host: str = Field(..., min_length=1, max_length=200)
    agent_type: AgentHostType = "both"
    max_concurrent: int = Field(1, ge=1, le=64)
    ssh_key: str | None = None
    labels: list[str] = Field(default_factory=list)

    @field_validator("host")
    @classmethod
    def _check_host(cls, v: str) -> str:
        return _validate_agent_host_field(v)


class UpdateAgentHostRequest(BaseModel):
    """Partial update — every field is optional."""
    host: str | None = Field(None, min_length=1, max_length=200)
    agent_type: AgentHostType | None = None
    max_concurrent: int | None = Field(None, ge=1, le=64)
    ssh_key: str | None = None
    labels: list[str] | None = None

    @field_validator("host")
    @classmethod
    def _check_host(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_agent_host_field(v)


class AgentDispatch(BaseModel):
    id: str
    host_id: str
    workspace_id: str
    correlation_id: str
    correlation_kind: CorrelationKind
    state: DispatchState
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    created_at: str
    updated_at: str


# Repo registry (Phase 1 + Phase 3) ------------------------------------------
# Duplicate the regex from src.config to keep src.models free of src.config
# imports (avoids an import cycle through the FastAPI app).
_REPO_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$")
_REPO_FETCH_STATUSES = ("unknown", "healthy", "error")


def _validate_repo_name(v: str) -> str:
    if not _REPO_NAME_PATTERN.match(v):
        raise ValueError(
            "repo name must match [A-Za-z0-9][A-Za-z0-9_.\\-]{0,62}"
        )
    return v


class RepoRole(str, Enum):
    """Repo classification used by primary-ref auto-selection.

    Closed enum so reviewer prompts and UI badges don't fork by free-text.
    Default ``other``; pick the closest match if a repo doesn't fit.
    """
    backend = "backend"
    frontend = "frontend"
    fullstack = "fullstack"
    infra = "infra"
    docs = "docs"
    other = "other"


# Used by _s0_init / Phase 5 worker for primary-ref auto-selection. Lower
# index = higher priority. Don't reorder casually — operators rely on this
# when they leave is_primary unset on multi-repo DevWorks.
REPO_ROLE_PRIMARY_PRIORITY: tuple[RepoRole, ...] = (
    RepoRole.backend,
    RepoRole.fullstack,
    RepoRole.frontend,
    RepoRole.infra,
    RepoRole.docs,
    RepoRole.other,
)


class Repo(BaseModel):
    """Response model — mirrors columns of the ``repos`` table."""
    id: str
    name: str
    url: str
    local_path: str | None = None
    default_branch: str = "main"
    ssh_key_path: str | None = None
    bare_clone_path: str | None = None
    role: RepoRole = RepoRole.other
    fetch_status: Literal["unknown", "healthy", "error"] = "unknown"
    last_fetched_at: str | None = None
    last_fetch_err: str | None = None
    created_at: str
    updated_at: str


class RepoPage(BaseModel):
    items: list["Repo"] = Field(default_factory=list)
    pagination: PaginationMeta


class CreateRepoRequest(BaseModel):
    """Operator-facing create payload.

    ``id`` is optional; the route allocates ``repo-<hex12>`` when omitted.
    """
    id: str | None = None
    name: str = Field(..., min_length=1, max_length=63)
    url: str = Field(..., min_length=1)
    local_path: str | None = None
    default_branch: str = Field("main", min_length=1, max_length=200)
    ssh_key_path: str | None = None
    role: RepoRole = RepoRole.other

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _validate_repo_name(v)


class UpdateRepoRequest(BaseModel):
    """Partial update — every field is optional."""
    name: str | None = Field(None, min_length=1, max_length=63)
    url: str | None = Field(None, min_length=1)
    local_path: str | None = None
    default_branch: str | None = Field(None, min_length=1, max_length=200)
    ssh_key_path: str | None = None
    role: RepoRole | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_repo_name(v)


# Branch / ref name allowlist. Mirrors src.git_utils._BRANCH_RE and
# src.repos.inspector._REF_RE — same shape, kept here as a string literal so
# src.models stays free of the git_utils import (which pulls asyncio chains).
_REF_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9/_.\-]{0,199}$")


class RepoRef(BaseModel):
    """DesignWork-side repo binding. ``base_branch`` resolved against the
    bare clone at create time."""
    repo_id: str = Field(..., min_length=1)
    base_branch: str = Field(..., min_length=1, max_length=200)

    @field_validator("base_branch")
    @classmethod
    def _check_base_branch(cls, v: str) -> str:
        # Defense-in-depth: validate at the DTO boundary so direct callers
        # of validate_*_repo_refs (which only see the inspector layer's
        # _validate_ref later) cannot bypass the allowlist.
        if not _REF_NAME_RE.match(v):
            raise ValueError(
                "base_branch must match [a-zA-Z0-9][a-zA-Z0-9/_.-]{0,199}"
            )
        return v


class DevRepoRef(RepoRef):
    """DevWork-side repo binding. Adds ``mount_name`` (unique per DevWork),
    ``base_rev_lock`` (legacy request flag; base SHA is always captured at
    create), and
    ``is_primary`` (explicit override of role-based primary selection)."""
    mount_name: str = Field(..., min_length=1, max_length=63)
    base_rev_lock: bool = False
    is_primary: bool = False

    @field_validator("mount_name")
    @classmethod
    def _check_mount(cls, v: str) -> str:
        if not _REPO_NAME_PATTERN.match(v):
            raise ValueError(
                "mount_name must match [A-Za-z0-9][A-Za-z0-9_.\\-]{0,62}"
            )
        return v


class DesignRepoRefView(BaseModel):
    """Read-only view over a ``design_work_repos`` row."""
    repo_id: str
    branch: str
    rev: str | None = None


class DevRepoRefView(BaseModel):
    """Read-only view over a ``dev_work_repos`` row.

    Phase 4 progress contract — this is *not* the Phase 5 worker handoff
    payload (which adds url / ssh_key_path bits).

    Phase 6: ``worktree_path`` is the per-mount git worktree absolute path
    populated by ``_s0_init``. ``None`` only on legacy in-flight rows
    created before Phase 6.
    """
    repo_id: str
    mount_name: str
    base_branch: str
    base_rev: str | None = None
    devwork_branch: str
    push_state: str
    is_primary: bool = False
    worktree_path: str | None = None


class WorkerRepoHandoff(DevRepoRefView):
    """Worker-facing handoff payload (Phase 5).

    Extends :class:`DevRepoRefView` with the operational config the agent
    host worker needs to clone + push without a follow-up GET on
    ``/api/v1/repos/{id}``. ``url`` and ``ssh_key_path`` are surfaced
    verbatim — they are operational config in v1, not secrets (see
    Phase 5 plan, decisions log).

    ``push_err`` is exposed here (and intentionally not on
    :class:`DevRepoRefView`) so a worker that just reported ``failed``
    can see the persisted, sanitised tail of its own error message
    without a follow-up read.
    """
    url: str
    ssh_key_path: str | None = None
    push_err: str | None = None


class UpdateRepoPushStateRequest(BaseModel):
    """Worker → cooagents writeback for ``dev_work_repos.push_state``.

    Forward-only outcomes; the SM still owns ``pending``. The route
    rejects ``pending`` here so a malformed worker can't unwind state.
    The boundary ``error_msg`` cap (2048) keeps the request body bounded;
    the persistence layer (``_MAX_PUSH_ERR_LEN`` in
    :mod:`src.repos.dev_work_repo_state`) does the final 256-char trim.
    """
    push_state: Literal["pushed", "failed"]
    error_msg: str | None = Field(default=None, max_length=2048)


# Inspector response DTOs (Phase 3) ------------------------------------------

class RepoBranches(BaseModel):
    default_branch: str
    branches: list[str] = Field(default_factory=list)


class RepoTreeEntry(BaseModel):
    path: str
    type: Literal["blob", "tree"]
    mode: str
    size: int | None = None


class RepoTree(BaseModel):
    ref: str
    path: str
    entries: list[RepoTreeEntry] = Field(default_factory=list)
    truncated: bool = False


class RepoBlob(BaseModel):
    ref: str
    path: str
    size: int
    binary: bool
    content: str | None = None


class RepoLogEntry(BaseModel):
    sha: str
    author: str
    email: str
    committed_at: str
    subject: str


class RepoLog(BaseModel):
    ref: str
    path: str | None = None
    entries: list[RepoLogEntry] = Field(default_factory=list)


class RepoLogPage(BaseModel):
    ref: str
    path: str | None = None
    items: list[RepoLogEntry] = Field(default_factory=list)
    pagination: PaginationMeta


# Resolve forward references for models that hold list[\"DevRepoRefView\"] /
# list[\"RepoRef\"] / list[\"DevRepoRef\"]. The view DTOs and ref DTOs are
# defined later in this module on purpose — keeping route-facing request
# models above keeps the file's top-down narrative readable.
DevWork.model_rebuild()
CreateDesignWorkRequest.model_rebuild()
RetryDesignWorkRequest.model_rebuild()
DesignWorkRetrySource.model_rebuild()
DesignWorkProgress.model_rebuild()
CreateDevWorkRequest.model_rebuild()
DevWorkProgress.model_rebuild()

