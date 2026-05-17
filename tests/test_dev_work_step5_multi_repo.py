"""Phase 8: DevWork Step5 multi-repo aggregation tests.

Drives the SM through a 2-mount DevWork with a canned executor and asserts:

  * the persisted Step5 prompt artefact contains both mount rows + the
    B-track limitation block + the aggregation rule (single source of
    truth for the LLM).
  * the SM still routes a single ``problem_category`` per DevWork
    (the per-DevWork single-score-single-category invariant from the
    repo-registry PRD L227).
  * primary mount carries a populated ``worktree_path``; non-primary
    mount renders the "no local worktree" marker (B-track honesty).
"""
from __future__ import annotations

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
from src.models import DevRepoRef
from src.repos.registry import RepoRegistryRepo
from src.storage import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
from src.workspace_manager import WorkspaceManager

DESIGN_FIXTURE = (
    Path(__file__).parent / "fixtures" / "design" / "perfect" / "round1.md"
)


def _build_config(max_rounds: int = 5, default_threshold: int = 80):
    return SimpleNamespace(
        design=SimpleNamespace(
            required_sections=[
                "问题与目标", "用户故事", "场景案例", "范围与非目标",
                "详细操作流程", "验收标准", "技术约束与集成边界",
                "交付切片", "决策记录", "打分 rubric",
            ],
            mockup_sections=["页面结构"],
            allow_optimize_mode=False,
        ),
        scoring=SimpleNamespace(default_threshold=default_threshold),
        devwork=SimpleNamespace(
            max_rounds=max_rounds,
            step2_timeout=10, step3_timeout=10,
            step5_timeout=10,
            # Phase 3 knobs.
            progress_heartbeat_interval_s=0.01,
            step_idle_timeout_s=0.5,
            step4_acpx_wall_ceiling_s=3600,
            step4_findings_wait_timeout_s=0.0,
            step4_findings_wait_interval_s=0.01,
            require_human_exit_confirm=False,
        ),
    )


# ---------------------------------------------------------------------------
# Scripted executor — same shape as tests/test_dev_work_sm.py, kept local so
# we don't accidentally bind that file's behaviour to this test.
# ---------------------------------------------------------------------------

class ScriptedExecutor:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    # Phase 3: dev_work_sm._run_llm now reaches into LLMRunner._build_oneshot_cmd
    # which delegates here. Mirror the production AcpxExecutor surface.
    def _build_acpx_exec_cmd(
        self, agent_type, worktree, timeout_sec,
        task_file=None, prompt=None,
    ):
        cmd: list[str] = [
            "acpx", "--cwd", worktree, "--format", "json",
            "--approve-all", agent_type, "exec",
            "--timeout", str(timeout_sec),
        ]
        if task_file is not None:
            cmd += ["--file", task_file]
        if prompt is not None:
            cmd += ["--prompt", prompt]
        return cmd

    async def run_once(self, agent_type, worktree, timeout_sec,
                       task_file=None, prompt=None, **_kwargs):
        prompt_text = (
            Path(task_file).read_text(encoding="utf-8") if task_file
            else (prompt or "")
        )
        step_tag = _detect_step(prompt_text)
        round_n = _detect_round(prompt_text)
        self.calls.append({
            "agent": agent_type, "worktree": worktree,
            "timeout": timeout_sec, "step": step_tag, "round": round_n,
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
    if "多仓审核打分" in prompt or "打分聚合规则" in prompt:
        return "STEP5"
    return "UNKNOWN"


def _detect_round(prompt: str) -> int:
    m = re.search(r"Round (\d+)", prompt)
    if m:
        return int(m.group(1))
    m = re.search(r"round(\d+)", prompt)
    return int(m.group(1)) if m else 1


def step2_append_h2(step_tag, round_n, prompt, worktree):
    m = re.search(r"在 `([^`]+\.md)` 现有文件末尾", prompt)
    if not m:
        return ("", 1)
    path = Path(m.group(1))
    addition = (
        "\n## 本轮目标\n实现登录闭环。\n"
        "\n## 上下文发现\n- `src/login.py:1-20`：登录入口。\n"
        "\n## 开发计划\n1. 加表单\n2. 加校验\n"
        "\n## 验收映射\n"
        "| AC ID | 场景/输入 | 预期 | 本轮 DW ID | 验证方式 |\n"
        "|---|---|---|---|---|\n"
        "| AC-01 | 正确邮箱密码 | 跳首页 | DW-01 | pytest |\n"
    )
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(addition)
    return ("ok", 0)


def step3_write_ctx(step_tag, round_n, prompt, worktree):
    m = re.search(r"在 `([^`]+\.md)` 写入", prompt)
    if not m:
        return ("", 1)
    out = Path(m.group(1))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "## 浓缩上下文\n\n- foo\n\n"
        "## 模式镜像\n\n- mirror\n\n"
        "## 执行地图\n\n| DW ID | 目标文件 | 动作 | 模式来源 | 验证命令 |\n"
        "|---|---|---|---|---|---|\n"
        "| DW-01 | src/login.py | update | src/app.py:1 | pytest |\n",
        encoding="utf-8",
    )
    return ("ok", 0)


def step4_write_findings(step_tag, round_n, prompt, worktree):
    m = re.search(r"将自审结果写入 `([^`]+\.json)`", prompt)
    if not m:
        return ("", 1)
    out = Path(m.group(1))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({
            "pass": True,
            "plan_execution": [],
            "findings": [],
        }),
        encoding="utf-8",
    )
    return ("ok", 0)


def _step5_writer(payload: dict):
    def _w(step_tag, round_n, prompt, worktree):
        # Phase 8 template: "**必须**将结果写入 `<path>`"
        m = re.search(r"必须\*\*将结果写入 `([^`]+\.json)`", prompt) or \
            re.search(r"将结果写入 `([^`]+\.json)`", prompt)
        if not m:
            return ("", 1)
        out = Path(m.group(1))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), encoding="utf-8")
        return (f"```json\n{json.dumps(payload)}\n```", 0)
    return _w


# ---------------------------------------------------------------------------
# Two-mount fixture — primary backend + non-primary frontend.
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


async def _make_bare_clone(src: Path, bare: Path) -> None:
    bare.parent.mkdir(parents=True, exist_ok=True)
    await run_git("clone", "--bare", str(src), str(bare))


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    store = LocalFileStore(workspaces_root=ws_root)
    repo = WorkspaceFilesRepo(db)
    registry = WorkspaceFileRegistry(store=store, repo=repo)
    wm = WorkspaceManager(
        db, project_root=tmp_path, workspaces_root=ws_root, registry=registry,
    )
    ws = await wm.create_with_scaffold(title="T", slug="t")
    ddm = DesignDocManager(db, registry=registry)
    design_text = DESIGN_FIXTURE.read_text(encoding="utf-8")
    dd = await ddm.persist(
        workspace_row=ws, slug="demo", version="1.0.0",
        markdown=design_text, parent_version=None,
        needs_frontend_mockup=False, rubric_threshold=85,
    )
    await db.execute(
        "UPDATE design_docs SET status='published', published_at=? WHERE id=?",
        ("t", dd["id"]),
    )
    dd["status"] = "published"

    repo_registry = RepoRegistryRepo(db)
    repos = []
    for repo_id, role in (
        ("repo-backend0001", "backend"),
        ("repo-frontend001", "frontend"),
    ):
        src = tmp_path / f"src-{repo_id}"
        await _init_repo(src)
        bare = ws_root / ".coop" / "registry" / "repos" / f"{repo_id}.git"
        await _make_bare_clone(src, bare)
        await repo_registry.upsert(
            id=repo_id, name=repo_id, url=str(src),
            default_branch="main", bare_clone_path=str(bare),
            role=role,
        )
        await repo_registry.update_fetch_status(
            repo_id, status="healthy", bare_clone_path=str(bare),
        )
        repos.append((repo_id, role, str(src)))
    ini = DevIterationNoteManager(db)
    yield dict(
        db=db, wm=wm, ws=ws, ddm=ddm, ini=ini, registry=registry,
        dd=dd, repos=repos, ws_root=ws_root, root=tmp_path,
        repo_registry=repo_registry,
    )
    await db.close()


def _make_sm(env, executor, cfg=None):
    from tests.conftest import make_test_llm_runner
    sm = DevWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        iteration_notes=env["ini"], executor=executor,
        config=cfg or _build_config(), registry=env["registry"],
        llm_runner=make_test_llm_runner(executor),
    )
    sm.workspaces_root = env["ws_root"].resolve()
    return sm


def _two_mount_refs(env) -> list:
    """Backend mount is primary; frontend tagged non-primary."""
    return [
        (
            DevRepoRef(
                repo_id=env["repos"][0][0],  # backend
                base_branch="main", mount_name="backend",
                is_primary=True,
            ),
            None,
        ),
        (
            DevRepoRef(
                repo_id=env["repos"][1][0],  # frontend
                base_branch="main", mount_name="frontend",
                is_primary=False,
            ),
            None,
        ),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_two_mount_devwork_review_picks_impl_gap(env):
    """impl_gap from a 2-mount review loops Step2..Step5 then completes."""
    script = [
        # round 1 — fail with impl_gap
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({
            "score": 60,
            "issues": [
                {"mount": "backend", "dimension": "测试",
                 "severity": "error", "message": "lint fails"}
            ],
            "problem_category": "impl_gap",
        }),
        # round 2 — full iteration rerun (Step2 + Step3 + Step4 + Step5)
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({
            "score": 92, "issues": [], "problem_category": None,
        }),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_two_mount_refs(env),
        prompt="build login multi-repo",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["last_score"] == 92
    # last_problem_category resets to None on COMPLETED only if Step5
    # wrote it; the SM keeps the most recent value, which on the final
    # passing round is None.
    assert final["last_problem_category"] is None
    # Step5 round-1 prompt artefact carries both mount rows + B-track block.
    prompt_path = (
        env["ws_root"] / env["ws"]["slug"] / "devworks" / dw["id"]
        / "prompts" / "step5-round1.md"
    )
    body = prompt_path.read_text(encoding="utf-8")
    assert "| `backend` |" in body
    assert "| `frontend` |" in body
    # Phase 6: B-track limitation note + per-mount placeholder are gone.
    assert "B-track" not in body
    assert "_(无本地 worktree — 多仓 worker 待上线)_" not in body
    # aggregation rule present (priority order: design_hollow > req_gap > impl_gap)
    assert (
        body.index("design_hollow")
        < body.index("req_gap")
        < body.index("impl_gap")
    )
    # primary marked
    assert "✅" in body
    # Phase 6: every mount's worktree path is rendered (no placeholder).
    # Normalize Windows backslashes once so the assertions stay readable.
    body_norm = body.replace("\\", "/")
    worktrees_root = str(env["ws_root"] / ".coop" / "worktrees").replace(
        "\\", "/"
    )
    assert worktrees_root in body_norm
    # Both mounts' subdirectories appear in the table.
    assert "/backend |" in body_norm
    assert "/frontend |" in body_norm


async def test_two_mount_devwork_all_clean_completes(env):
    """First-pass null + score>=threshold completes the DevWork."""
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({
            "score": 92, "issues": [], "problem_category": None,
        }),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_two_mount_refs(env),
        prompt="build login multi-repo",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["last_score"] == 92
    assert final["last_problem_category"] is None
    assert final["first_pass_success"] == 1


async def test_two_mount_devwork_design_hollow_escalates(env):
    """design_hollow at Step5 escalates regardless of score."""
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({
            "score": 10, "issues": [
                {"mount": "frontend", "dimension": "设计",
                 "severity": "error", "message": "no rubric for visual"}
            ],
            "problem_category": "design_hollow",
        }),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_two_mount_refs(env),
        prompt="build login multi-repo",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "ESCALATED"
    assert final["last_problem_category"] == "design_hollow"


async def test_load_mount_table_entries_orders_primary_first(env):
    """Primary appears first regardless of mount_name lex order."""
    sm = _make_sm(env, ScriptedExecutor([]))
    # Build a DevWork with frontend as primary, backend as non-primary —
    # mount_name lex order would put backend first if not for is_primary.
    repo_refs = [
        (
            DevRepoRef(
                repo_id=env["repos"][0][0], base_branch="main",
                mount_name="backend", is_primary=False,
            ),
            None,
        ),
        (
            DevRepoRef(
                repo_id=env["repos"][1][0], base_branch="main",
                mount_name="frontend", is_primary=True,
            ),
            None,
        ),
    ]
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=repo_refs,
        prompt="primary-first ordering",
    )
    entries = await sm._load_mount_table_entries(dw)
    assert [e.mount_name for e in entries] == ["frontend", "backend"]
    assert entries[0].is_primary is True
    assert entries[1].is_primary is False


async def test_load_mount_table_entries_returns_per_mount_worktree_path(env):
    """Phase 6: every mount surfaces its own dev_work_repos.worktree_path."""
    sm = _make_sm(env, ScriptedExecutor([]))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_two_mount_refs(env),
        prompt="worktree gating",
    )
    # Set per-row worktree_path directly (avoid running _s0_init's real
    # ensure_worktree here — that path is exercised by the integration
    # test below).
    await env["db"].execute(
        "UPDATE dev_work_repos SET worktree_path=? WHERE dev_work_id=? "
        "AND mount_name=?",
        ("/wt/backend", dw["id"], "backend"),
    )
    await env["db"].execute(
        "UPDATE dev_work_repos SET worktree_path=? WHERE dev_work_id=? "
        "AND mount_name=?",
        ("/wt/frontend", dw["id"], "frontend"),
    )
    dw = await env["db"].fetchone(
        "SELECT * FROM dev_works WHERE id=?", (dw["id"],)
    )
    entries = await sm._load_mount_table_entries(dw)
    by_mount = {e.mount_name: e for e in entries}
    assert by_mount["backend"].worktree_path == "/wt/backend"
    assert by_mount["frontend"].worktree_path == "/wt/frontend"


async def test_load_mount_table_entries_legacy_row_keeps_none(env):
    """Pre-Phase-6 in-flight rows keep worktree_path=None gracefully."""
    sm = _make_sm(env, ScriptedExecutor([]))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_two_mount_refs(env),
        prompt="legacy row",
    )
    # Don't populate dev_work_repos.worktree_path — simulate a row created
    # before the Phase 6 migration.
    dw = await env["db"].fetchone(
        "SELECT * FROM dev_works WHERE id=?", (dw["id"],)
    )
    entries = await sm._load_mount_table_entries(dw)
    for e in entries:
        assert e.worktree_path is None


async def test_load_mount_table_entries_returns_empty_tuple_for_no_refs(env):
    """No dev_work_repos rows → empty tuple (not list, not None)."""
    sm = _make_sm(env, ScriptedExecutor([]))
    # Insert a bare dev_works row without any dev_work_repos.
    now = sm._now()
    dev_id = "dev-orphan00001"
    await env["db"].execute(
        """INSERT INTO dev_works
           (id, workspace_id, design_doc_id, prompt,
            worktree_path, worktree_branch, current_step,
            iteration_rounds, first_pass_success, last_score,
            last_problem_category, agent, agent_host_id, gates_json,
            escalated_at, completed_at, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            dev_id, env["ws"]["id"], env["dd"]["id"], "p",
            None, None, "INIT", 0, None, None, None,
            "claude", "local", None, None, None, now, now,
        ),
    )
    dw = await env["db"].fetchone(
        "SELECT * FROM dev_works WHERE id=?", (dev_id,)
    )
    entries = await sm._load_mount_table_entries(dw)
    assert entries == ()


async def test_s0_init_creates_worktree_per_mount(env):
    """Phase 6: every mount gets its own git worktree at INIT."""
    sm = _make_sm(env, ScriptedExecutor([]))
    sm.workspaces_root = env["ws_root"].resolve()
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_two_mount_refs(env),
        prompt="multi-mount init",
    )
    # First tick runs _s0_init.
    await sm.tick(dw["id"])
    rows = await env["db"].fetchall(
        "SELECT mount_name, worktree_path FROM dev_work_repos "
        "WHERE dev_work_id=? ORDER BY mount_name",
        (dw["id"],),
    )
    paths = {r["mount_name"]: r["worktree_path"] for r in rows}
    # Both mounts populated with distinct paths.
    assert paths["backend"] is not None
    assert paths["frontend"] is not None
    assert paths["backend"] != paths["frontend"]
    # Layout: <ws>/.coop/worktrees/<branch_safe>/<mount_name>/
    for mount, p in paths.items():
        path_obj = Path(p)
        assert path_obj.exists(), f"{mount} worktree missing on disk: {p}"
        # ``.git`` is a file in a worktree (gitlink), not a directory.
        assert (path_obj / ".git").exists(), (
            f"{mount} worktree has no .git entry: {p}"
        )
        # Path must be under .coop/worktrees and end in the mount name.
        norm = str(path_obj).replace("\\", "/")
        assert "/.coop/worktrees/" in norm, norm
        assert norm.rstrip("/").endswith(f"/{mount}"), norm
    # Back-compat: dev_works.worktree_path mirrors primary (backend) path.
    refreshed = await env["db"].fetchone(
        "SELECT * FROM dev_works WHERE id=?", (dw["id"],)
    )
    assert refreshed["worktree_path"] == paths["backend"]
    assert refreshed["current_step"] == "STEP1_VALIDATE"


async def test_s5_review_pre_flight_escalates_on_empty_rubric(env):
    """Design doc lacking ## 打分 rubric → SM escalates with design_hollow."""
    # Corrupt the design doc so the rubric section is missing.
    dd_abs = (
        env["ws_root"] / env["ws"]["slug"] / env["dd"]["path"]
    )
    dd_abs.write_text(
        "## 用户故事\n\n- foo\n\n## 验收标准\n\n- bar\n",
        encoding="utf-8",
    )
    # Re-publish (status survives but content changed). _s1_validate also
    # rejects this missing-required-sections content; we want Step5 to also
    # gate on the rubric specifically — bypass Step1 by jumping straight to
    # Step5 via a hand-crafted dev_works row.
    sm = _make_sm(env, ScriptedExecutor([]))
    now = sm._now()
    dev_id = "dev-norubric0001"
    await env["db"].execute(
        """INSERT INTO dev_works
           (id, workspace_id, design_doc_id, prompt,
            worktree_path, worktree_branch, current_step,
            iteration_rounds, first_pass_success, last_score,
            last_problem_category, agent, agent_host_id, gates_json,
            escalated_at, completed_at, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            dev_id, env["ws"]["id"], env["dd"]["id"], "p",
            "/wt", "devwork/x", "STEP5_REVIEW",
            0, None, None, None,
            "claude", "local", None, None, None, now, now,
        ),
    )
    dw = await env["db"].fetchone(
        "SELECT * FROM dev_works WHERE id=?", (dev_id,)
    )
    await sm._s5_review(dw)
    refreshed = await env["db"].fetchone(
        "SELECT * FROM dev_works WHERE id=?", (dev_id,)
    )
    assert refreshed["current_step"] == "ESCALATED"
    assert refreshed["last_problem_category"] == "design_hollow"
