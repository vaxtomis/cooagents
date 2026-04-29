"""Phase 10 (devwork-acpx-overhaul) acceptance smoke.

Five PRD success-metric assertions that can be made without a real
acpx subprocess. The remaining metrics (heartbeat cadence, real
acpx process count, real session names) live in
``scripts/phase10_acceptance.py --mode real`` which the operator
runs once on a Linux box.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.dev_iteration_note_manager import DevIterationNoteManager
from src.dev_work_sm import DevWorkStateMachine
from src.git_utils import run_git
from src.models import DevRepoRef
from src.workspace_manager import WorkspaceManager
from tests.conftest import make_test_llm_runner
from tests.test_dev_work_sm import (
    DESIGN_FIXTURE,
    ScriptedExecutor,
    _build_config as _build_dev_config,
    _step5_writer,
    step2_append_h2,
    step3_write_ctx,
    step4_write_findings,
)
from tests.test_smoke_e2e import (
    _build_registry_stack,
    _init_repo,
    _seed_repo,
)


# ---------------------------------------------------------------------------
# Shared two-mount harness
# ---------------------------------------------------------------------------


async def _drive_two_mount_round(
    tmp_path: Path,
    executor_script: list,
    *,
    finalize_score: int = 90,
):
    """Drive a single happy-path round through DevWorkStateMachine.

    Returns ``(db, sm, dw_id, mount_paths, final_row)`` so each test can
    inspect the SM state after termination. Caller is responsible for
    closing the db (``await db.close()``).
    """
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()

    ws_root = tmp_path / "ws"
    registry = _build_registry_stack(db, ws_root)
    wm = WorkspaceManager(
        db, project_root=tmp_path, workspaces_root=ws_root,
        registry=registry,
    )
    ws = await wm.create_with_scaffold(title="T", slug="t")
    ddm = DesignDocManager(db, registry=registry)
    ini = DevIterationNoteManager(db)

    design_text = DESIGN_FIXTURE.read_text(encoding="utf-8")
    dd = await ddm.persist(
        workspace_row=ws, slug="demo", version="1.0.0",
        markdown=design_text, parent_version=None,
        needs_frontend_mockup=False, rubric_threshold=85,
    )
    await db.execute(
        "UPDATE design_docs SET status='published', published_at=? WHERE id=?",
        ("2026-04-30", dd["id"]),
    )

    repo_fe_dir = tmp_path / "repo_fe"
    repo_be_dir = tmp_path / "repo_be"
    await _init_repo(repo_fe_dir)
    await _init_repo(repo_be_dir)

    fe_id = "repo-fe000000001"
    be_id = "repo-be000000002"
    await _seed_repo(db, ws_root, repo_fe_dir, repo_id=fe_id)
    await _seed_repo(db, ws_root, repo_be_dir, repo_id=be_id)

    refs = [
        (
            DevRepoRef(repo_id=fe_id, base_branch="main",
                       mount_name="frontend"),
            None,
        ),
        (
            DevRepoRef(repo_id=be_id, base_branch="main",
                       mount_name="backend", is_primary=True),
            None,
        ),
    ]

    executor = ScriptedExecutor(executor_script)
    sm = DevWorkStateMachine(
        db=db, workspaces=wm, design_docs=ddm, iteration_notes=ini,
        executor=executor, config=_build_dev_config(), registry=registry,
        llm_runner=make_test_llm_runner(executor),
    )
    sm.workspaces_root = ws_root.resolve()
    dw = await sm.create(
        workspace_id=ws["id"], design_doc_id=dd["id"],
        repo_refs=refs, prompt="add hello to each mount",
    )
    final = await asyncio.wait_for(
        sm.run_to_completion(dw["id"]), timeout=30,
    )
    rows = await db.fetchall(
        "SELECT mount_name, worktree_path FROM dev_work_repos "
        "WHERE dev_work_id=? ORDER BY mount_name",
        (dw["id"],),
    )
    mount_paths = {r["mount_name"]: r["worktree_path"] for r in rows}
    return db, sm, dw["id"], mount_paths, final


def _make_two_mount_step4_writer(mount_paths: dict[str, str]):
    """step4_write_findings sibling that touches a file in EACH mount.

    The script leaves the canonical findings JSON exactly where
    ``step4_write_findings`` would, so the SM accepts it; in addition it
    drops a ``HELLO.txt`` into every mount worktree so ``git status``
    reports a non-empty diff per mount.
    """
    def _w(step_tag, round_n, prompt, worktree):
        m = re.search(r"将自审结果写入 `([^`]+\.json)`", prompt)
        if not m:
            return ("", 1)
        out = Path(m.group(1))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"pass": True, "findings": []}), encoding="utf-8",
        )
        for mount_path in mount_paths.values():
            (Path(mount_path) / "HELLO.txt").write_text(
                "phase10\n", encoding="utf-8",
            )
        return ("ok", 0)
    return _w


# ---------------------------------------------------------------------------
# Case 1 — Single round opens 3 sessions / makes 4 prompt calls
# ---------------------------------------------------------------------------


async def test_round_invokes_three_acpx_calls(tmp_path):
    """PRD §SM #7: 单 round acpx 进程数 4 → 3.

    Distinction: PRD's "process count" maps to **unique sessions opened**
    (Step3 and Step4 share the build session = one process).
    "Prompt call count" = number of step-handler dispatches = 4 per round
    (plan, step3-build, step4-build, review).
    """
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    db, sm, dw_id, _mounts, final = await _drive_two_mount_round(
        tmp_path, script,
    )
    try:
        assert final["current_step"] == "COMPLETED", final
        assert sm.llm_runner.prompt_call_count == 4, (
            f"expected 4 prompt calls per round (plan + step3-build + "
            f"step4-build + review); got "
            f"{sm.llm_runner.prompt_call_count}"
        )
        assert sm.llm_runner.oneshot_call_count == 0, (
            "DevWork happy path should not invoke oneshot mode"
        )
        unique_sessions = set(sm.llm_runner.created_sessions)
        assert len(unique_sessions) == 3, (
            f"expected 3 unique acpx sessions per round (plan/build/review); "
            f"got {sorted(unique_sessions)}"
        )
        assert any(s.endswith("-plan") for s in unique_sessions)
        assert any(s.endswith("-build") for s in unique_sessions)
        assert any(s.endswith("-review") for s in unique_sessions)
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Case 2 — Both mount worktrees get a non-empty diff
# ---------------------------------------------------------------------------


async def test_two_mount_worktrees_both_get_diffs(tmp_path):
    """PRD §SM #3: 非 primary mount 改动落地 100%.

    A single Step4 dispatch must be able to touch both worktrees. We
    capture both mount paths up front, then build a Step4 helper that
    drops a stub file in each before emitting the findings JSON.
    """
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    try:
        ws_root = tmp_path / "ws"
        registry = _build_registry_stack(db, ws_root)
        wm = WorkspaceManager(
            db, project_root=tmp_path, workspaces_root=ws_root,
            registry=registry,
        )
        ws = await wm.create_with_scaffold(title="T", slug="t")
        ddm = DesignDocManager(db, registry=registry)
        ini = DevIterationNoteManager(db)

        design_text = DESIGN_FIXTURE.read_text(encoding="utf-8")
        dd = await ddm.persist(
            workspace_row=ws, slug="demo", version="1.0.0",
            markdown=design_text, parent_version=None,
            needs_frontend_mockup=False, rubric_threshold=85,
        )
        await db.execute(
            "UPDATE design_docs SET status='published', "
            "published_at=? WHERE id=?",
            ("2026-04-30", dd["id"]),
        )

        repo_fe_dir = tmp_path / "repo_fe"
        repo_be_dir = tmp_path / "repo_be"
        await _init_repo(repo_fe_dir)
        await _init_repo(repo_be_dir)

        fe_id = "repo-fe000000001"
        be_id = "repo-be000000002"
        await _seed_repo(db, ws_root, repo_fe_dir, repo_id=fe_id)
        await _seed_repo(db, ws_root, repo_be_dir, repo_id=be_id)

        refs = [
            (
                DevRepoRef(repo_id=fe_id, base_branch="main",
                           mount_name="frontend"),
                None,
            ),
            (
                DevRepoRef(repo_id=be_id, base_branch="main",
                           mount_name="backend", is_primary=True),
                None,
            ),
        ]

        # Two-mount executor needs to know mount_paths up-front. Build a
        # closure-based writer that consults a dict the script itself
        # populates from a probe step (we read dev_work_repos after
        # _s0_init via a lightweight pre-Step2 step? No — simpler: run
        # the SM, then snapshot mount paths after _s0_init by inserting
        # a Step1->Step2 hook). Practical: construct mount_paths *after*
        # ``sm.create`` returns by querying dev_work_repos, then start
        # the run.
        mount_paths_holder: dict[str, str] = {}

        def step4_two_mounts(step_tag, round_n, prompt, worktree):
            return _make_two_mount_step4_writer(mount_paths_holder)(
                step_tag, round_n, prompt, worktree,
            )

        script = [
            step2_append_h2,
            step3_write_ctx,
            step4_two_mounts,
            _step5_writer(
                {"score": 90, "issues": [], "problem_category": None}
            ),
        ]
        executor = ScriptedExecutor(script)
        sm = DevWorkStateMachine(
            db=db, workspaces=wm, design_docs=ddm, iteration_notes=ini,
            executor=executor, config=_build_dev_config(),
            registry=registry,
            llm_runner=make_test_llm_runner(executor),
        )
        sm.workspaces_root = ws_root.resolve()
        dw = await sm.create(
            workspace_id=ws["id"], design_doc_id=dd["id"],
            repo_refs=refs, prompt="add hello to each mount",
        )

        # Snapshot mount paths after _s0_init has materialized them.
        # _s0_init runs synchronously inside ``run_to_completion``, but
        # we can pre-load them by stepping through manually: use the
        # initial _s0_init transition (driven below) and read the row.
        # Simpler: call run_to_completion and rely on the closure being
        # captured by reference — populate the holder from inside the
        # SM via a lightweight pre-step. To keep things simple, drive
        # the SM until ``current_step`` is past INIT, then populate.
        # But ``run_to_completion`` is one shot. Instead we use the
        # alternative: kick off a background task that polls
        # dev_work_repos until rows materialize, then fills the holder.
        async def _populate_mount_paths():
            for _ in range(200):
                rows = await db.fetchall(
                    "SELECT mount_name, worktree_path FROM dev_work_repos "
                    "WHERE dev_work_id=? AND worktree_path IS NOT NULL",
                    (dw["id"],),
                )
                if rows and len(rows) == 2:
                    for r in rows:
                        mount_paths_holder[r["mount_name"]] = (
                            r["worktree_path"]
                        )
                    return
                await asyncio.sleep(0.02)

        populate = asyncio.create_task(_populate_mount_paths())
        final = await asyncio.wait_for(
            sm.run_to_completion(dw["id"]), timeout=30,
        )
        await populate
        assert final["current_step"] == "COMPLETED", final
        assert "frontend" in mount_paths_holder
        assert "backend" in mount_paths_holder

        # Both worktrees should report a HELLO.txt-shaped diff.
        for mount_name, mount_path in mount_paths_holder.items():
            stdout, _stderr, rc = await run_git(
                "status", "--porcelain", cwd=mount_path,
            )
            assert rc == 0
            assert stdout.strip(), (
                f"mount {mount_name!r} at {mount_path} reports empty "
                f"git status; expected HELLO.txt added"
            )
            assert "HELLO.txt" in stdout, (
                f"mount {mount_name!r} status missing HELLO.txt: {stdout!r}"
            )
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Case 3 — session_anchor_path persisted and well-shaped
# ---------------------------------------------------------------------------


async def test_session_anchor_path_persisted(tmp_path):
    """PRD §schema: dev_works.session_anchor_path is filled by _s0_init.

    Path shape must be ``<workspaces_root>/<slug>/devworks/<dev_id>``.
    """
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    db, _sm, dw_id, _mounts, final = await _drive_two_mount_round(
        tmp_path, script,
    )
    try:
        assert final["current_step"] == "COMPLETED", final
        row = await db.fetchone(
            "SELECT session_anchor_path FROM dev_works WHERE id=?",
            (dw_id,),
        )
        assert row is not None
        anchor = row["session_anchor_path"]
        assert anchor, "session_anchor_path should be non-empty"
        anchor_path = Path(anchor)
        assert anchor_path.name == dw_id, (
            f"session_anchor_path leaf must equal dev_id; got "
            f"{anchor_path.name!r} vs {dw_id!r}"
        )
        assert anchor_path.parent.name == "devworks", (
            f"session_anchor_path parent must be 'devworks'; got "
            f"{anchor_path.parent.name!r}"
        )
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Case 4 — Dead-code corpses stayed dead (regression seam)
# ---------------------------------------------------------------------------


def test_dead_code_corpses_stayed_dead():
    """PRD §SM #5: 死代码 0 残留.

    Three corpse markers must remain absent across ``src/``. Re-introducing
    any of them is the kind of regression this seam is designed to catch.
    """
    src_dir = Path(__file__).resolve().parent.parent / "src"
    forbidden = ("_BTRACK_LIMITATION_NOTE", "allowed_tools_design",
                 "allowed_tools_dev")
    hits: dict[str, list[str]] = {token: [] for token in forbidden}
    for py in src_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for token in forbidden:
            if token in text:
                hits[token].append(str(py.relative_to(src_dir.parent)))
    for token, files in hits.items():
        assert files == [], (
            f"corpse marker {token!r} re-introduced in: {files}"
        )


# ---------------------------------------------------------------------------
# Case 5 — progress field projects through the API surface
# ---------------------------------------------------------------------------


def test_progress_field_projects_during_long_step():
    """PRD §SM #2 (API surface only): ``GET /dev-works/{id}`` exposes
    ``progress`` when ``current_progress_json`` is populated.

    This case does not spawn acpx — it asserts the projection helpers
    used by the route still surface the heartbeat snapshot. Real
    cadence (heartbeat ≤ 30 s) lives in real-mode harness.
    """
    from routes.dev_works import _decode_progress, _row_to_progress

    payload = {
        "step": "STEP4_DEVELOP",
        "round": 2,
        "elapsed_s": 45,
        "last_heartbeat_at": "2026-04-30T01:23:45+00:00",
        "dispatch_id": "ad-test1234",
    }
    snap = _decode_progress(json.dumps(payload, ensure_ascii=False))
    assert snap is not None, "valid heartbeat JSON must decode to a snapshot"
    assert snap.step == "STEP4_DEVELOP"
    assert snap.round == 2
    assert snap.elapsed_s == 45
    assert snap.last_heartbeat_at == "2026-04-30T01:23:45+00:00"
    assert snap.dispatch_id == "ad-test1234"

    # Project through _row_to_progress to prove the route surface still
    # carries the field through to the response model.
    row = {
        "id": "dev-test00000001",
        "workspace_id": "ws-test00000001",
        "design_doc_id": "dd-test00000001",
        "current_step": "STEP4_DEVELOP",
        "iteration_rounds": 1,
        "first_pass_success": None,
        "last_score": None,
        "last_problem_category": None,
        "escalated_at": None,
        "completed_at": None,
        "worktree_path": None,
        "worktree_branch": None,
        "created_at": "2026-04-30T00:00:00+00:00",
        "updated_at": "2026-04-30T00:00:00+00:00",
        "current_progress_json": json.dumps(payload, ensure_ascii=False),
    }
    projected = _row_to_progress(row)
    assert projected.progress is not None
    assert projected.progress.step == "STEP4_DEVELOP"
    assert projected.progress.elapsed_s == 45

    # Tolerance check: malformed JSON projects to None, not 500.
    row_bad = dict(row, current_progress_json="not json{")
    projected_bad = _row_to_progress(row_bad)
    assert projected_bad.progress is None
