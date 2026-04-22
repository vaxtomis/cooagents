"""Phase 4: DevWorkStateMachine end-to-end tests (no real LLM)."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.dev_iteration_note_manager import DevIterationNoteManager
from src.dev_work_sm import DevWorkStateMachine
from src.git_utils import run_git
from src.models import DevWorkStep, ProblemCategory
from src.workspace_manager import WorkspaceManager

DESIGN_FIXTURE = Path(__file__).parent / "fixtures" / "design" / "perfect" / "round1.md"


def _build_config(max_rounds=5, default_threshold=80):
    return SimpleNamespace(
        design=SimpleNamespace(
            required_sections=[
                "用户故事", "用户案例", "详细操作流程", "验收标准", "打分 rubric",
            ],
            mockup_sections=["页面结构"],
            allow_optimize_mode=False,
        ),
        scoring=SimpleNamespace(default_threshold=default_threshold),
        devwork=SimpleNamespace(
            max_rounds=max_rounds,
            step2_timeout=10,
            step3_timeout=10,
            step4_timeout=10,
            step5_timeout=10,
            require_human_exit_confirm=False,
        ),
    )


class ScriptedExecutor:
    """Replay scripted outcomes step-by-step.

    Each entry in ``script`` is a callable taking (step_tag, round_n, prompt_text, worktree)
    and returning a ``(stdout, rc)`` pair; it may also write files as side effects.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def run_once(self, agent_type, worktree, timeout_sec,
                       task_file=None, prompt=None):
        prompt_text = Path(task_file).read_text(encoding="utf-8") if task_file else (prompt or "")
        step_tag = _detect_step(prompt_text)
        round_n = _detect_round(prompt_text)
        self.calls.append({
            "agent": agent_type, "worktree": worktree, "timeout": timeout_sec,
            "step": step_tag, "round": round_n,
        })
        if not self.script:
            return ("", 1)
        action = self.script.pop(0)
        return action(step_tag, round_n, prompt_text, worktree)


def _detect_step(prompt: str) -> str:
    if "为 DevWork" in prompt and "迭代设计" in prompt:
        return "STEP2"
    if "浓缩上下文" in prompt:
        return "STEP3"
    if "按迭代设计的" in prompt or "自审结果" in prompt:
        return "STEP4"
    if "打分 rubric" in prompt and "problem_category" in prompt:
        return "STEP5"
    return "UNKNOWN"


def _detect_round(prompt: str) -> int:
    m = re.search(r"Round (\d+)", prompt)
    if m:
        return int(m.group(1))
    m = re.search(r"round(\d+)", prompt)
    return int(m.group(1)) if m else 1


# ---------------------------------------------------------------------------
# Helpers to build scripted actions
# ---------------------------------------------------------------------------

def step2_append_h2(step_tag, round_n, prompt, worktree):
    """Append the three required H2 sections to the iteration note."""
    m = re.search(r"在 `([^`]+\.md)` 现有文件末尾", prompt)
    if not m:
        return ("", 1)
    path = Path(m.group(1))
    addition = (
        "\n## 本轮目标\n"
        "\n实现登录闭环。\n"
        "\n## 开发计划\n"
        "\n1. 加表单\n2. 加校验\n"
        "\n## 用例清单\n"
        "\n| 用例 | 输入 | 预期 | 对应设计章节 |\n"
        "|---|---|---|---|\n"
        "| 登录成功 | 正确邮箱密码 | 跳首页 | 用户故事 |\n"
    )
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(addition)
    return ("ok", 0)


def step2_missing_h2(step_tag, round_n, prompt, worktree):
    """Append only one H2 — validation should catch missing H2s."""
    m = re.search(r"在 `([^`]+\.md)` 现有文件末尾", prompt)
    if not m:
        return ("", 1)
    path = Path(m.group(1))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n## 本轮目标\n仅一个 H2\n")
    return ("ok", 0)


def step2_rc1(step_tag, round_n, prompt, worktree):
    return ("", 1)


def step3_write_ctx(step_tag, round_n, prompt, worktree):
    m = re.search(r"在 `([^`]+\.md)` 写入", prompt)
    if not m:
        return ("", 1)
    out = Path(m.group(1))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("## 浓缩上下文\n\n- foo\n\n## 疑点与风险\n\n- bar\n", encoding="utf-8")
    return ("ok", 0)


def step3_rc1(step_tag, round_n, prompt, worktree):
    return ("", 1)


def step4_write_findings(step_tag, round_n, prompt, worktree):
    m = re.search(r"将自审结果写入 `([^`]+\.json)`", prompt)
    if not m:
        return ("", 1)
    out = Path(m.group(1))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"pass": True, "findings": []}), encoding="utf-8"
    )
    return ("ok", 0)


def step4_no_findings(step_tag, round_n, prompt, worktree):
    return ("", 1)


def _step5_writer(payload: dict):
    def _w(step_tag, round_n, prompt, worktree):
        m = re.search(r"必须\*\*将结果写入 `([^`]+\.json)`", prompt) or \
            re.search(r"将结果写入 `([^`]+\.json)`", prompt)
        if not m:
            return ("", 1)
        out = Path(m.group(1))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), encoding="utf-8")
        # Also echo on stdout as a fenced block (reviewer prefers file though).
        return (f"```json\n{json.dumps(payload)}\n```", 0)
    return _w


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

async def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    await run_git("init", cwd=str(path))
    await run_git("config", "user.email", "test@example.com", cwd=str(path))
    await run_git("config", "user.name", "Test User", cwd=str(path))
    await run_git("checkout", "-b", "main", cwd=str(path), check=False)
    (path / "README.md").write_text("# demo\n")
    await run_git("add", "README.md", cwd=str(path))
    await run_git("commit", "-m", "init", cwd=str(path))


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    ws_root = tmp_path / "ws"
    wm = WorkspaceManager(db, project_root=tmp_path, workspaces_root=ws_root)
    ws = await wm.create_with_scaffold(title="T", slug="t")
    ddm = DesignDocManager(db, workspaces_root=ws_root)
    design_text = DESIGN_FIXTURE.read_text(encoding="utf-8")
    dd = await ddm.persist(
        workspace_row=ws, slug="demo", version="1.0.0",
        markdown=design_text, parent_version=None,
        needs_frontend_mockup=False, rubric_threshold=85,
    )
    # Publish without requiring a linked design_work row.
    await db.execute(
        "UPDATE design_docs SET status='published', published_at=? WHERE id=?",
        ("t", dd["id"]),
    )
    dd["status"] = "published"
    repo_dir = tmp_path / "repo"
    await _init_repo(repo_dir)
    ini = DevIterationNoteManager(db, workspaces_root=ws_root)
    yield dict(db=db, wm=wm, ws=ws, ddm=ddm, ini=ini,
               dd=dd, repo=str(repo_dir), ws_root=ws_root, root=tmp_path)
    await db.close()


def _make_sm(env, executor, cfg=None):
    return DevWorkStateMachine(
        db=env["db"],
        workspaces=env["wm"],
        design_docs=env["ddm"],
        iteration_notes=env["ini"],
        executor=executor,
        config=cfg or _build_config(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_create_requires_published_design_doc(env):
    sm = _make_sm(env, ScriptedExecutor([]))
    # Swap design_doc to draft
    await env["db"].execute(
        "UPDATE design_docs SET status='draft' WHERE id=?", (env["dd"]["id"],)
    )
    from src.exceptions import BadRequestError
    with pytest.raises(BadRequestError):
        await sm.create(
            workspace_id=env["ws"]["id"],
            design_doc_id=env["dd"]["id"],
            repo_path=env["repo"],
            prompt="build login",
        )


async def test_happy_path_first_pass(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["first_pass_success"] == 1
    assert final["last_score"] == 90
    # Worktree was created under ws_root/.coop/worktrees/
    assert final["worktree_path"].startswith(str(env["ws_root"] / ".coop" / "worktrees"))
    # dev_work.completed event emitted
    ev = await env["db"].fetchone(
        "SELECT * FROM workspace_events WHERE event_name='dev_work.completed' "
        "AND correlation_id=?",
        (dw["id"],),
    )
    assert ev is not None


async def test_req_gap_routes_to_step2(env):
    # Round 1: fail with req_gap; Round 2: pass.
    script = [
        # round 1
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 40, "issues": [{"m": "need more"}],
                        "problem_category": "req_gap"}),
        # round 2
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["first_pass_success"] == 0  # round2 passed
    assert final["iteration_rounds"] == 1


async def test_impl_gap_routes_to_step4(env):
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 50, "issues": [],
                        "problem_category": "impl_gap"}),
        # impl_gap -> only Step4 + Step5 rerun (no Step2/Step3)
        step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"


async def test_design_hollow_escalates_immediately(env):
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 10, "issues": [],
                        "problem_category": "design_hollow"}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "ESCALATED"
    # human_intervention event emitted
    ev = await env["db"].fetchone(
        "SELECT * FROM workspace_events "
        "WHERE event_name='workspace.human_intervention' AND correlation_id=?",
        (dw["id"],),
    )
    assert ev is not None


async def test_max_rounds_escalates(env):
    # Always req_gap: max_rounds=1 means first round already consumes budget.
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 10, "issues": [],
                        "problem_category": "req_gap"}),
        # round 2 attempt: but max_rounds=1, so _loop_or_escalate on first
        # failure already writes ESCALATED; SM won't call Step2 again.
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor, cfg=_build_config(max_rounds=1))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "ESCALATED"


async def test_step1_invalid_design_escalates(env):
    # Corrupt the design doc file so validator fails.
    Path(env["dd"]["path"]).write_text("no front-matter here", encoding="utf-8")
    script = []  # LLM never invoked
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "ESCALATED"
    assert final["last_problem_category"] == ProblemCategory.design_hollow.value


async def test_step2_missing_h2_loops_as_req_gap(env):
    script = [
        step2_missing_h2,   # round 1: only one H2 appended -> loop
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["iteration_rounds"] == 1  # round 1 looped; round 2 passed


async def test_step2_front_matter_not_tampered(env):
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    await sm.run_to_completion(dw["id"])
    note_path = (
        env["ws_root"] / "t" / "devworks" / dw["id"]
        / "iteration-round-1.md"
    )
    body = note_path.read_text(encoding="utf-8")
    assert body.startswith("---\ndev_work_id: " + dw["id"])
    assert "# 迭代设计 — Round 1" in body


async def test_step3_first_failure_retries_in_place(env):
    # Fail Step3 once, then succeed. SM should not advance iteration_rounds.
    script = [
        step2_append_h2,
        step3_rc1,            # first Step3 fails
        step3_write_ctx,      # retry succeeds
        step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    # iteration_rounds stayed at 0 — the retry did not consume round budget.
    assert final["iteration_rounds"] == 0


async def test_cancel_moves_to_cancelled(env):
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    await sm.cancel(dw["id"])
    row = await env["db"].fetchone(
        "SELECT * FROM dev_works WHERE id=?", (dw["id"],)
    )
    assert row["current_step"] == "CANCELLED"


async def test_workspace_md_shows_devwork(env):
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_path=env["repo"],
        prompt="build login",
    )
    await sm.run_to_completion(dw["id"])
    md = (env["ws_root"] / "t" / "workspace.md").read_text(encoding="utf-8")
    assert f"devworks/DEV-{dw['id']}" in md
