import pytest
from pydantic import ValidationError

from src.models import (
    AgentKind,
    CreateDesignWorkRequest,
    CreateDevWorkRequest,
    DesignWorkRetrySource,
    DesignDoc,
    DesignDocStatus,
    DesignWork,
    DesignWorkMode,
    DesignWorkState,
    DevIterationNote,
    DevRepoRef,
    DevRepoRefView,
    DevWork,
    DevWorkProgress,
    DevWorkStep,
    ProblemCategory,
    RepoRef,
    RetryDesignWorkRequest,
    Review,
    UpdateRepoPushStateRequest,
    WorkerRepoHandoff,
    Workspace,
    WorkspaceEvent,
    WorkspaceStatus,
)

NOW = "2026-01-01T00:00:00Z"


def test_workspace_happy():
    w = Workspace(
        id="ws-1", title="T", slug="t", root_path="/tmp/t",
        created_at=NOW, updated_at=NOW,
    )
    assert w.status == WorkspaceStatus.active


def test_workspace_missing_required():
    with pytest.raises(ValidationError):
        Workspace(id="ws-1")


def test_design_work_mode_new_no_parent():
    dw = DesignWork(
        id="desw-1", workspace_id="ws-1",
        mode=DesignWorkMode.new,
        created_at=NOW, updated_at=NOW,
    )
    assert dw.current_state == DesignWorkState.INIT
    assert dw.parent_version is None
    assert dw.loop == 0
    assert dw.agent == AgentKind.claude


def test_design_work_mode_optimize_with_parent():
    dw = DesignWork(
        id="desw-2", workspace_id="ws-1",
        mode=DesignWorkMode.optimize, parent_version="1.0.0",
        agent=AgentKind.codex,
        created_at=NOW, updated_at=NOW,
    )
    assert dw.parent_version == "1.0.0"
    assert dw.agent == AgentKind.codex


def test_design_doc_default_rubric_threshold():
    dd = DesignDoc(
        id="des-1", workspace_id="ws-1", slug="abc123def456",
        version="1.0.0", path="designs/DES-abc123def456-1.0.0.md",
        created_at=NOW,
    )
    assert dd.rubric_threshold == 85
    assert dd.status == DesignDocStatus.draft


def test_dev_work_default_indicators():
    dw = DevWork(
        id="dev-1", workspace_id="ws-1", design_doc_id="des-1",
        prompt="do X",
        created_at=NOW, updated_at=NOW,
    )
    assert dw.iteration_rounds == 0
    assert dw.first_pass_success is None
    assert dw.last_score is None
    assert dw.last_problem_category is None
    assert dw.agent == AgentKind.claude
    assert dw.current_step == DevWorkStep.INIT


def test_dev_work_problem_category_enum():
    dw = DevWork(
        id="dev-2", workspace_id="ws-1", design_doc_id="des-1",
        prompt="do X",
        last_problem_category=ProblemCategory.impl_gap,
        created_at=NOW, updated_at=NOW,
    )
    assert dw.last_problem_category == ProblemCategory.impl_gap
    with pytest.raises(ValidationError):
        DevWork(
            id="dev-3", workspace_id="ws-1", design_doc_id="des-1",
            prompt="do X",
            last_problem_category="invalid",
            created_at=NOW, updated_at=NOW,
        )


def test_dev_iteration_note_score_history_list():
    n = DevIterationNote(
        id="note-1", dev_work_id="dev-1", round=1,
        markdown_path="devworks/dev-1/round1.md",
        score_history=[70, 85],
        created_at=NOW,
    )
    assert n.score_history == [70, 85]


def test_review_dev_with_note_link():
    r = Review(
        id="rev-1", dev_work_id="dev-1",
        dev_iteration_note_id="note-1", round=1, created_at=NOW,
    )
    assert r.dev_work_id == "dev-1"
    assert r.dev_iteration_note_id == "note-1"


def test_review_design_work_without_note():
    r = Review(
        id="rev-2", design_work_id="desw-1", round=1, created_at=NOW,
    )
    assert r.design_work_id == "desw-1"
    assert r.dev_iteration_note_id is None


def test_workspace_event_payload_dict():
    e = WorkspaceEvent(
        event_id="uuid-1", event_name="workspace.created",
        workspace_id="ws-1", payload={"title": "T"}, ts=NOW,
    )
    assert e.payload["title"] == "T"
    assert e.id is None


# ---------------------------------------------------------------------------
# Phase 4 (repo-registry) — repo_refs DTOs and validators
# ---------------------------------------------------------------------------

def _ref(mount: str = "frontend", **kwargs) -> DevRepoRef:
    return DevRepoRef(
        repo_id=kwargs.get("repo_id", "repo-aaaaaaaaaaaa"),
        base_branch=kwargs.get("base_branch", "main"),
        mount_name=mount,
        is_primary=kwargs.get("is_primary", False),
        base_rev_lock=kwargs.get("base_rev_lock", False),
    )


def test_dev_repo_ref_mount_regex_rejects_traversal():
    with pytest.raises(ValidationError):
        DevRepoRef(
            repo_id="repo-aaa", base_branch="main", mount_name="../etc",
        )


def test_create_dev_work_request_unique_mounts():
    with pytest.raises(ValidationError) as exc:
        CreateDevWorkRequest(
            workspace_id="ws-1", design_doc_id="des-1", prompt="p",
            repo_refs=[_ref("frontend"), _ref("frontend")],
        )
    assert "duplicate" in str(exc.value).lower()


def test_create_dev_work_request_empty_refs_rejected():
    with pytest.raises(ValidationError):
        CreateDevWorkRequest(
            workspace_id="ws-1", design_doc_id="des-1", prompt="p",
            repo_refs=[],
        )


def test_create_dev_work_request_double_primary_rejected():
    with pytest.raises(ValidationError) as exc:
        CreateDevWorkRequest(
            workspace_id="ws-1", design_doc_id="des-1", prompt="p",
            repo_refs=[
                _ref("frontend", is_primary=True),
                _ref("backend", is_primary=True),
            ],
        )
    assert "is_primary" in str(exc.value)


def test_create_dev_work_request_happy_one_primary():
    req = CreateDevWorkRequest(
        workspace_id="ws-1", design_doc_id="des-1", prompt="p",
        repo_refs=[
            _ref("frontend", is_primary=True),
            _ref("backend"),
        ],
    )
    assert len(req.repo_refs) == 2
    assert sum(1 for r in req.repo_refs if r.is_primary) == 1
    assert req.agent is None


def test_create_design_work_request_optional_repo_refs_default_empty():
    req = CreateDesignWorkRequest(
        workspace_id="ws-1", title="T", slug="t",
        user_input="hello", mode=DesignWorkMode.new,
    )
    assert req.repo_refs == []
    assert req.agent is None


def test_create_design_work_request_with_repo_refs():
    req = CreateDesignWorkRequest(
        workspace_id="ws-1", title="T", slug="t",
        user_input="hello", mode=DesignWorkMode.new,
        repo_refs=[RepoRef(repo_id="repo-aaa", base_branch="main")],
    )
    assert len(req.repo_refs) == 1


def test_retry_design_work_request_allows_null_agent_and_empty_repo_refs():
    req = RetryDesignWorkRequest(
        title="T",
        slug="retry-t",
        user_input="hello",
        needs_frontend_mockup=True,
        agent=None,
        repo_refs=[],
    )
    assert req.agent is None
    assert req.repo_refs == []


@pytest.mark.parametrize(
    "field",
    ["title", "slug", "user_input", "needs_frontend_mockup", "repo_refs"],
)
def test_retry_design_work_request_rejects_non_clearable_nulls(field):
    with pytest.raises(ValidationError):
        RetryDesignWorkRequest(**{field: None})


def test_retry_design_work_request_rejects_bad_slug():
    with pytest.raises(ValidationError):
        RetryDesignWorkRequest(slug="Bad Slug")


def test_design_work_retry_source_happy():
    source = DesignWorkRetrySource(
        title="T",
        slug="retry-t",
        user_input="hello",
        agent=AgentKind.codex,
        repo_refs=[RepoRef(repo_id="repo-aaa", base_branch="main")],
    )
    assert source.agent == AgentKind.codex
    assert source.repo_refs[0].base_branch == "main"


# Phase 5 (repo-registry) — worker handoff DTOs ------------------------------


def test_worker_handoff_inherits_dev_repo_ref_view_fields():
    """WorkerRepoHandoff is a DevRepoRefView + url + ssh_key_path + push_err."""
    base_fields = set(DevRepoRefView.model_fields.keys())
    handoff_fields = set(WorkerRepoHandoff.model_fields.keys())
    assert base_fields.issubset(handoff_fields)
    assert {"url", "ssh_key_path", "push_err"}.issubset(handoff_fields)


def test_worker_handoff_happy():
    h = WorkerRepoHandoff(
        repo_id="repo-1",
        mount_name="backend",
        base_branch="main",
        devwork_branch="devwork/x/backend",
        push_state="pending",
        url="git@gh:org/repo.git",
        ssh_key_path="/home/agent/.ssh/id_a",
    )
    assert h.url == "git@gh:org/repo.git"
    assert h.ssh_key_path == "/home/agent/.ssh/id_a"
    assert h.push_err is None
    assert h.is_primary is False  # default from DevRepoRefView


def test_update_push_state_request_rejects_pending():
    """The Literal forces the worker to use the dedicated state values."""
    with pytest.raises(ValidationError):
        UpdateRepoPushStateRequest(push_state="pending")


def test_update_push_state_request_rejects_oversize_error_msg():
    with pytest.raises(ValidationError):
        UpdateRepoPushStateRequest(
            push_state="failed", error_msg="x" * 2049,
        )


def test_update_push_state_request_happy_pushed():
    r = UpdateRepoPushStateRequest(push_state="pushed")
    assert r.push_state == "pushed"
    assert r.error_msg is None


def test_dev_work_progress_repos_default_empty():
    """The new repos[] field is additive — old payloads still parse."""
    p = DevWorkProgress(
        id="dev-x",
        workspace_id="ws-x",
        design_doc_id="des-x",
        current_step=DevWorkStep.STEP1_VALIDATE,
        iteration_rounds=0,
        created_at="t",
        updated_at="t",
    )
    assert p.repos == []
    assert p.repo_refs == []
