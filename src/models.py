import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class EnsureRepoRequest(BaseModel):
    repo_path: str
    repo_url: str | None = None


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
    repo_path: str
    prompt: str
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
    issues: list[dict] | None = None
    findings: list[dict] | None = None
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


class WorkspaceMetrics(BaseModel):
    """PRD Phase 8 Success Metrics — lifetime by default; windowed via ?since=&until=.

    Rates are ``0.0`` when their denominator is zero (no division-by-zero leak).
    """
    human_intervention_per_workspace: float
    active_workspaces: int
    first_pass_success_rate: float
    avg_iteration_rounds: float


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
    agent: AgentKind = AgentKind.claude
    # Optional per-DesignWork override. When None, D6 PERSIST falls back
    # first to the LLM-produced front-matter, then to
    # config.scoring.default_threshold (=80). (U2)
    rubric_threshold: int | None = Field(default=None, ge=1, le=100)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str) -> str:
        if not _WORKSPACE_SLUG_RE.match(v):
            raise ValueError(
                "slug must be kebab-case (1-63 chars, no leading/trailing dash, "
                "no consecutive dashes)"
            )
        return v

    @model_validator(mode="after")
    def _check_mode_parent_combo(self) -> "CreateDesignWorkRequest":
        # Enforce the mode/parent_version invariant at the HTTP boundary so
        # clients get an immediate 422 instead of a 201 + ESCALATED row.
        if self.mode == DesignWorkMode.new and self.parent_version is not None:
            raise ValueError("mode=new must not supply parent_version")
        if self.mode == DesignWorkMode.optimize and self.parent_version is None:
            raise ValueError("mode=optimize requires parent_version")
        return self


class DesignWorkProgress(BaseModel):
    id: str
    workspace_id: str
    mode: DesignWorkMode
    current_state: DesignWorkState
    loop: int
    missing_sections: list[str] | None = None
    output_design_doc_id: str | None = None
    escalated_at: str | None = None
    title: str | None = None
    sub_slug: str | None = None
    version: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Phase 4 — DevWork request/response DTOs
# ---------------------------------------------------------------------------


class CreateDevWorkRequest(BaseModel):
    workspace_id: str
    design_doc_id: str
    repo_path: str = Field(..., min_length=1, max_length=500)
    prompt: str = Field(..., min_length=1, max_length=20000)
    agent: AgentKind = AgentKind.claude


class DevWorkProgress(BaseModel):
    id: str
    workspace_id: str
    design_doc_id: str
    current_step: DevWorkStep
    iteration_rounds: int
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

