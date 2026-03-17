from enum import Enum

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RunStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Stage(str, Enum):
    INIT = "INIT"
    REQ_COLLECTING = "REQ_COLLECTING"
    REQ_REVIEW = "REQ_REVIEW"
    DESIGN_QUEUED = "DESIGN_QUEUED"
    DESIGN_DISPATCHED = "DESIGN_DISPATCHED"
    DESIGN_RUNNING = "DESIGN_RUNNING"
    DESIGN_REVIEW = "DESIGN_REVIEW"
    DEV_QUEUED = "DEV_QUEUED"
    DEV_DISPATCHED = "DEV_DISPATCHED"
    DEV_RUNNING = "DEV_RUNNING"
    DEV_REVIEW = "DEV_REVIEW"
    MERGE_QUEUED = "MERGE_QUEUED"
    MERGING = "MERGING"
    MERGED = "MERGED"
    MERGE_CONFLICT = "MERGE_CONFLICT"
    FAILED = "FAILED"


class GateName(str, Enum):
    req = "req"
    design = "design"
    dev = "dev"


class ArtifactKind(str, Enum):
    req = "req"
    design = "design"
    adr = "adr"
    code = "code"
    test_report = "test-report"


class ArtifactStatus(str, Enum):
    draft = "draft"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"


class JobStatus(str, Enum):
    starting = "starting"
    running = "running"
    completed = "completed"
    failed = "failed"
    timeout = "timeout"
    cancelled = "cancelled"
    interrupted = "interrupted"


class RecoverAction(str, Enum):
    resume = "resume"
    redo = "redo"
    manual = "manual"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateRunRequest(BaseModel):
    ticket: str
    repo_path: str
    description: str | None = None
    preferences: dict | None = None


class ApproveRequest(BaseModel):
    gate: GateName
    by: str
    comment: str | None = None


class RejectRequest(BaseModel):
    gate: GateName
    by: str
    reason: str


class RetryRequest(BaseModel):
    by: str
    note: str | None = None


class RecoverRequest(BaseModel):
    action: RecoverAction


class SubmitRequirementRequest(BaseModel):
    content: str


class CreateWebhookRequest(BaseModel):
    url: str
    events: list[str] | None = None
    secret: str | None = None


class CreateAgentHostRequest(BaseModel):
    id: str
    host: str
    agent_type: str
    max_concurrent: int = 2
    ssh_key: str | None = None
    labels: list[str] | None = None


class UpdateAgentHostRequest(BaseModel):
    host: str | None = None
    agent_type: str | None = None
    max_concurrent: int | None = None
    ssh_key: str | None = None
    labels: list[str] | None = None


class MergeRequest(BaseModel):
    priority: int = 0


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class RunResponse(BaseModel):
    run_id: str
    ticket: str
    status: str
    current_stage: str
    description: str | None
    created_at: str
    updated_at: str
    warning: str | None = None


class RunDetailResponse(RunResponse):
    steps: list[dict]
    approvals: list[dict]
    recent_events: list[dict]
    artifacts: list[dict]


class ArtifactResponse(BaseModel):
    id: int
    run_id: str
    kind: str
    path: str
    version: int
    status: str
    byte_size: int | None
    created_at: str


class ArtifactContentResponse(ArtifactResponse):
    content: str
    diff_from_prev: str | None = None


class TurnResponse(BaseModel):
    turn_num: int
    verdict: str | None = None
    detail: str | None = None
    started_at: str
    ended_at: str | None = None


class JobResponse(BaseModel):
    id: str
    run_id: str
    host_id: str | None
    agent_type: str
    stage: str
    status: str
    started_at: str
    ended_at: str | None
    session_name: str | None = None
    turn_count: int = 1
    turns: list[TurnResponse] | None = None


class AgentHostResponse(BaseModel):
    id: str
    host: str
    agent_type: str
    max_concurrent: int
    status: str
    current_load: int = 0


class WebhookResponse(BaseModel):
    id: int
    url: str
    events: list[str] | None
    status: str


class ErrorResponse(BaseModel):
    error: str
    message: str
    current_stage: str | None = None
    details: dict | None = None


class HealthResponse(BaseModel):
    status: str
    uptime: float
    db: str
    active_runs: int
    active_jobs: int
