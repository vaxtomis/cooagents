from enum import Enum

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GateName(str, Enum):
    req = "req"
    design = "design"
    dev = "dev"


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


