"""DevWork lifecycle routes (Phase 4 + Phase 5).

Endpoints:
    POST   /api/v1/dev-works                — create + background drive
    GET    /api/v1/dev-works                — list; workspace_id REQUIRED
    GET    /api/v1/dev-works/{id}           — progress snapshot
    POST   /api/v1/dev-works/{id}/tick      — manual single-step advance
    POST   /api/v1/dev-works/{id}/cancel    — move to CANCELLED
    POST   /api/v1/dev-works/{id}/repos/{mount}/push-state
                                            — worker writeback for push
                                              outcomes (Phase 5)

C1 (v1 invariant): at most one active DevWork per design_doc. POST of a
second DevWork against the same ``design_doc_id`` while a prior one is not
in a terminal state returns 409. Enforced in two layers: fast-path SELECT
here (returns 409) and a partial UNIQUE index on the DB (returns 409 on
race-winner's INSERT via sqlite3.IntegrityError).

Phase 4 (repo-registry): ``repo_path`` is replaced by ``repo_refs``. The
4-step validation chain runs *after* the active-DevWork-per-design_doc
fast-path so a duplicate doesn't pay the inspector cost.

Phase 5 (repo-registry): GET /api/v1/dev-works/{id} also returns a
``repos[]`` block carrying ``url`` + ``ssh_key_path`` (worker-facing
handoff). The Phase 4 ``repo_refs`` field stays — UI consumers don't
need ``url``.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request, Response
from slowapi import Limiter

from routes._repo_refs_validation import validate_dev_repo_refs
from src.exceptions import ConflictError, NotFoundError
from src.models import (
    CreateDevWorkRequest,
    DevRepoRefView,
    DevWorkProgress,
    DevWorkStep,
    UpdateRepoPushStateRequest,
    WorkerRepoHandoff,
)
from src.request_utils import client_ip

limiter = Limiter(key_func=client_ip)
router = APIRouter(tags=["dev-works"])

# Derived from DevWorkStep so adding a step updates both the enum and this
# tuple consistently. _TERMINAL must match src.dev_work_sm._TERMINAL.
_TERMINAL_STEPS = frozenset({
    DevWorkStep.COMPLETED,
    DevWorkStep.ESCALATED,
    DevWorkStep.CANCELLED,
})
_NON_TERMINAL = tuple(
    s.value for s in DevWorkStep if s not in _TERMINAL_STEPS
)


def _handoff_to_repo_ref(handoff: WorkerRepoHandoff) -> DevRepoRefView:
    """Project the worker handoff down to the Phase 4 repo_refs view.

    Avoids a second query against ``dev_work_repos`` — the worker handoff
    row is a strict superset of :class:`DevRepoRefView`'s fields.
    """
    return DevRepoRefView(
        repo_id=handoff.repo_id,
        mount_name=handoff.mount_name,
        base_branch=handoff.base_branch,
        base_rev=handoff.base_rev,
        devwork_branch=handoff.devwork_branch,
        push_state=handoff.push_state,
        is_primary=handoff.is_primary,
    )


def _row_to_worker_handoff(r: dict) -> WorkerRepoHandoff:
    return WorkerRepoHandoff(
        repo_id=r["repo_id"],
        mount_name=r["mount_name"],
        base_branch=r["base_branch"],
        base_rev=r.get("base_rev"),
        devwork_branch=r["devwork_branch"],
        push_state=r["push_state"],
        is_primary=bool(r.get("is_primary")),
        url=r["url"],
        ssh_key_path=r.get("ssh_key_path"),
        push_err=r.get("push_err"),
    )


async def _load_worker_repos(
    state_repo, dev_id: str
) -> list[WorkerRepoHandoff]:
    rows = await state_repo.list_for_dev_work(dev_id)
    return [_row_to_worker_handoff(r) for r in rows]


async def _load_worker_repos_batch(
    state_repo, dev_ids: list[str]
) -> dict[str, list[WorkerRepoHandoff]]:
    """Bulk variant — avoids N+1 on the list-DevWork endpoint."""
    grouped_rows = await state_repo.list_for_dev_works_batch(dev_ids)
    return {
        dwid: [_row_to_worker_handoff(r) for r in rows]
        for dwid, rows in grouped_rows.items()
    }


def _row_to_progress(
    row: dict,
    repo_refs: list[DevRepoRefView] | None = None,
    repos: list[WorkerRepoHandoff] | None = None,
) -> DevWorkProgress:
    fps = row.get("first_pass_success")
    return DevWorkProgress(
        id=row["id"],
        workspace_id=row["workspace_id"],
        design_doc_id=row["design_doc_id"],
        current_step=row["current_step"],
        iteration_rounds=row["iteration_rounds"],
        first_pass_success=bool(fps) if fps is not None else None,
        last_score=row.get("last_score"),
        last_problem_category=row.get("last_problem_category"),
        escalated_at=row.get("escalated_at"),
        completed_at=row.get("completed_at"),
        worktree_path=row.get("worktree_path"),
        worktree_branch=row.get("worktree_branch"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        repo_refs=repo_refs or [],
        repos=repos or [],
    )


@router.post("/dev-works", status_code=201)
@limiter.limit("10/minute")
async def create_dev_work(
    req: CreateDevWorkRequest, request: Request, response: Response
) -> DevWorkProgress:
    db = request.app.state.db
    placeholders = ",".join("?" for _ in _NON_TERMINAL)
    existing = await db.fetchone(
        f"SELECT id FROM dev_works WHERE design_doc_id=? "
        f"AND current_step IN ({placeholders})",
        (req.design_doc_id, *_NON_TERMINAL),
    )
    if existing is not None:
        raise ConflictError(
            f"design_doc {req.design_doc_id} already has active DevWork "
            f"{existing['id']}; v1 permits at most one active DevWork per "
            f"design_doc",
            current_stage=None,
        )

    # 4-step validation chain runs AFTER the active-DevWork fast-path so a
    # duplicate request doesn't pay the inspector cost.
    validated = await validate_dev_repo_refs(
        req.repo_refs,
        request.app.state.repo_registry_repo,
        request.app.state.repo_inspector,
    )

    sm = request.app.state.dev_work_sm
    try:
        dw = await sm.create(
            workspace_id=req.workspace_id,
            design_doc_id=req.design_doc_id,
            repo_refs=validated,
            prompt=req.prompt,
            agent=req.agent.value,
        )
    except sqlite3.IntegrityError as exc:
        # Partial UNIQUE index on dev_works(design_doc_id) WHERE step not in
        # terminal set — caught race that slipped past the SELECT above.
        raise ConflictError(
            f"design_doc {req.design_doc_id} already has active DevWork "
            f"(race): {exc}",
            current_stage=None,
        ) from exc
    sm.schedule_driver(dw["id"])
    response.headers["Location"] = f"/api/v1/dev-works/{dw['id']}"
    state_repo = request.app.state.dev_work_repo_state
    repos = await _load_worker_repos(state_repo, dw["id"])
    refs = [_handoff_to_repo_ref(h) for h in repos]
    return _row_to_progress(dw, refs, repos)


@router.get("/dev-works")
async def list_dev_works(
    request: Request, workspace_id: str
) -> list[DevWorkProgress]:
    db = request.app.state.db
    state_repo = request.app.state.dev_work_repo_state
    rows = await db.fetchall(
        "SELECT * FROM dev_works WHERE workspace_id=? ORDER BY created_at DESC",
        (workspace_id,),
    )
    dev_ids = [r["id"] for r in rows]
    repos_by_id = await _load_worker_repos_batch(state_repo, dev_ids)
    return [
        _row_to_progress(
            r,
            [_handoff_to_repo_ref(h) for h in repos_by_id.get(r["id"], [])],
            repos_by_id.get(r["id"], []),
        )
        for r in rows
    ]


@router.get("/dev-works/{dev_id}")
async def get_dev_work(dev_id: str, request: Request) -> DevWorkProgress:
    db = request.app.state.db
    state_repo = request.app.state.dev_work_repo_state
    row = await db.fetchone("SELECT * FROM dev_works WHERE id=?", (dev_id,))
    if not row:
        raise NotFoundError(f"dev_work {dev_id!r} not found")
    repos = await _load_worker_repos(state_repo, dev_id)
    refs = [_handoff_to_repo_ref(h) for h in repos]
    return _row_to_progress(row, refs, repos)


@router.post("/dev-works/{dev_id}/tick")
@limiter.limit("30/minute")
async def tick_dev_work(dev_id: str, request: Request) -> DevWorkProgress:
    sm = request.app.state.dev_work_sm
    state_repo = request.app.state.dev_work_repo_state
    dw = await sm.tick(dev_id)
    repos = await _load_worker_repos(state_repo, dev_id)
    refs = [_handoff_to_repo_ref(h) for h in repos]
    return _row_to_progress(dw, refs, repos)


@router.post("/dev-works/{dev_id}/cancel", status_code=204)
@limiter.limit("10/minute")
async def cancel_dev_work(dev_id: str, request: Request) -> Response:
    sm = request.app.state.dev_work_sm
    await sm.cancel(dev_id)
    return Response(status_code=204)


@router.post(
    "/dev-works/{dev_id}/repos/{mount_name}/push-state",
    response_model=DevWorkProgress,
)
@limiter.limit("30/minute")
async def update_repo_push_state(
    dev_id: str,
    mount_name: str,
    payload: UpdateRepoPushStateRequest,
    request: Request,
) -> DevWorkProgress:
    """Worker writeback for ``dev_work_repos.push_state`` (Phase 5).

    Forward-only outcome state machine; idempotent on ``pushed -> pushed``;
    rejects ``pushed -> failed`` with 409. The ``pending`` state is
    rejected at the boundary by the pydantic Literal on the request
    model, so this handler only sees ``pushed`` / ``failed``.
    """
    db = request.app.state.db
    state_repo = request.app.state.dev_work_repo_state
    # Existence of the parent dev_work first — keeps 404 ordering stable
    # (existing dev_work + missing mount → 404 from the repo class).
    dw = await db.fetchone("SELECT * FROM dev_works WHERE id=?", (dev_id,))
    if dw is None:
        raise NotFoundError(f"dev_work {dev_id!r} not found")
    await state_repo.update_push_state(
        dev_id,
        mount_name,
        push_state=payload.push_state,
        error_msg=payload.error_msg,
    )
    repos = await _load_worker_repos(state_repo, dev_id)
    refs = [_handoff_to_repo_ref(h) for h in repos]
    return _row_to_progress(dw, refs, repos)
