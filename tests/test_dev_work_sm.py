"""Phase 4: DevWorkStateMachine end-to-end tests (no real LLM)."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agent_hosts.repo import AgentHostRepo
from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.dev_iteration_note_manager import DevIterationNoteManager
from src.dev_work_steps import (
    _apply_plan_verification_checkboxes,
    _extract_plan_checklist_items,
    _missing_plan_verification_ids,
)
from src.dev_work_sm import DevWorkStateMachine
from src.exceptions import BadRequestError, NotFoundError
from src.storage import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
from src.git_utils import run_git
from src.models import DevRepoRef, DevWorkStep, ProblemCategory
from src.repos.registry import RepoRegistryRepo
from src.workspace_manager import WorkspaceManager


def _refs_arg(env, mount: str = "backend") -> list:
    """Helper: build the validated repo_refs tuple list for sm.create()."""
    return [(
        DevRepoRef(
            repo_id=env["repo_id"],
            base_branch="main",
            mount_name=mount,
        ),
        None,
    )]

DESIGN_FIXTURE = Path(__file__).parent / "fixtures" / "design" / "perfect" / "round1.md"


def _build_config(max_rounds=5, default_threshold=80):
    return SimpleNamespace(
        design=SimpleNamespace(
            required_sections=[
                "用户故事", "场景案例", "详细操作流程", "验收标准", "打分 rubric",
            ],
            mockup_sections=["页面结构"],
            allow_optimize_mode=False,
        ),
        scoring=SimpleNamespace(default_threshold=default_threshold),
        devwork=SimpleNamespace(
            max_rounds=max_rounds,
            step2_timeout=10,
            step3_timeout=10,
            step5_timeout=10,
            # Phase 3: keep test heartbeats fast and idle window short so a
            # stuck wrapper test doesn't hang the suite.
            progress_heartbeat_interval_s=0.01,
            step_idle_timeout_s=0.5,
            step4_acpx_wall_ceiling_s=3600,
            step4_findings_wait_timeout_s=0.0,
            step4_findings_wait_interval_s=0.01,
            require_human_exit_confirm=False,
        ),
        preferred_dev_agent="claude",
    )


class ScriptedExecutor:
    """Replay scripted outcomes step-by-step.

    Each entry in ``script`` is a callable taking (step_tag, round_n, prompt_text, worktree)
    and returning a ``(stdout, rc)`` pair; it may also write files as side effects.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    # Phase 3: dev_work_sm._run_llm now reaches into LLMRunner._build_oneshot_cmd
    # which delegates here. Mirror the production AcpxExecutor surface with a
    # stable, easy-to-assert command list so existing tests keep passing.
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
    if "STEP5 generated/dependency diff repair" in prompt:
        return "STEP5_PREFLIGHT_REPAIR"
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
    """Append the required H2 sections to the iteration note."""
    m = re.search(r"在 `([^`]+\.md)` 现有文件末尾", prompt)
    if not m:
        return ("", 1)
    path = Path(m.group(1))
    addition = (
        "\n## 本轮目标\n"
        "\n实现登录闭环。\n"
        "\n## 上下文发现\n"
        "\n- `src/login.py:1-20`：登录入口与验证命令候选。\n"
        "\n## 开发计划\n"
        "\n- [ ] DW-01: 加表单\n- [ ] DW-02: 加校验\n"
        "  - [ ] DW-02.1: 校验空邮箱\n"
        "- [ ] DW-03: 补充失败态\n"
        "\n## 用例清单\n"
        "\n| 用例 | 输入 | 预期 | 对应设计章节 |\n"
        "|---|---|---|---|\n"
        "| 登录成功 | 正确邮箱密码 | 跳首页 | 用户故事 |\n"
    )
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(addition)
    return ("ok", 0)


def step2_append_h2_with_tech_stack(step_tag, round_n, prompt, worktree):
    """Append the base H2 sections plus the optional recommended stack block."""
    m = re.search(r"在 `([^`]+\.md)` 现有文件末尾", prompt)
    if not m:
        return ("", 1)
    path = Path(m.group(1))
    addition = (
        "\n## 本轮目标\n"
        "\n实现登录闭环。\n"
        "\n## 推荐技术栈\n"
        "\n- React 18\n- FastAPI\n"
        "\n## 上下文发现\n"
        "\n- `src/login.py:1-20`：登录入口与验证命令候选。\n"
        "\n## 开发计划\n"
        "\n- [ ] DW-01: 加表单\n- [ ] DW-02: 加校验\n"
        "  - [ ] DW-02.1: 校验空邮箱\n"
        "- [ ] DW-03: 补充失败态\n"
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
    out.write_text(
        "## 浓缩上下文\n\n- foo\n\n"
        "## 模式镜像\n\n- mirror\n\n"
        "## 执行地图\n\n| DW ID | 目标文件 | 动作 | 模式来源 | 验证命令 |\n"
        "|---|---|---|---|---|\n"
        "| DW-01 | src/login.py | update | src/app.py:1 | pytest |\n",
        encoding="utf-8",
    )
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
        json.dumps({
            "pass": True,
            "plan_execution": [
                {"id": "DW-01", "status": "done", "evidence": ["login.py:1"]},
            ],
            "findings": [],
        }),
        encoding="utf-8",
    )
    return ("ok", 0)


def step4_no_findings(step_tag, round_n, prompt, worktree):
    return ("", 1)


def _last_json_output_path(prompt: str) -> Path | None:
    matches = re.findall(r"`([^`]+\.json)`", prompt)
    return Path(matches[-1]) if matches else None


def _step5_plan_ids_from_prompt(prompt: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"\|\s*`([A-Za-z][A-Za-z0-9_-]*-\d+(?:\.\d+)*)`\s*\|",
        prompt,
    ):
        plan_id = match.group(1)
        if plan_id not in seen:
            seen.add(plan_id)
            ids.append(plan_id)
    return ids


def _payload_with_default_plan_verification(
    payload: dict, prompt: str,
) -> dict:
    if "plan_verification" in payload:
        return dict(payload)
    plan_ids = _step5_plan_ids_from_prompt(prompt)
    if not plan_ids:
        return dict(payload)
    delivered = payload.get("problem_category") is None
    status = "done" if delivered else "unverified"
    return {
        **payload,
        "plan_verification": [
            {
                "id": plan_id,
                "status": status,
                "implemented": delivered,
                "verified": delivered,
                "verification_mode": "test_default",
            }
            for plan_id in plan_ids
        ],
    }


def _first_mount_worktree_path(prompt: str, fallback: str) -> str:
    for line in prompt.splitlines():
        mount_line = re.search(r"- mount `[^`]+`: `([^`]+)`", line)
        if mount_line:
            return mount_line.group(1)
        if not line.startswith("| `"):
            continue
        cols = [col.strip() for col in line.strip().strip("|").split("|")]
        if len(cols) < 7 or cols[-1].startswith("_("):
            continue
        return cols[-1].strip("`")
    return fallback


def step4_invalid_findings_json(step_tag, round_n, prompt, worktree):
    out = _last_json_output_path(prompt)
    if out is None:
        return ("", 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{not-json", encoding="utf-8")
    return ("ok", 0)


def step4_write_findings_but_fail(step_tag, round_n, prompt, worktree):
    step4_write_findings(step_tag, round_n, prompt, worktree)
    return ("failed after writing findings", 1)


def step4_success_without_rewriting_findings(step_tag, round_n, prompt, worktree):
    assert "System retry feedback" in prompt
    assert "Step4 failed" in prompt
    return ("ok", 0)


def step4_write_findings_expect_retry_feedback(step_tag, round_n, prompt, worktree):
    assert "System retry feedback" in prompt
    assert "Step4 findings JSON invalid" in prompt
    return step4_write_findings(step_tag, round_n, prompt, worktree)


def step4_write_findings_and_stage_node_modules(
    step_tag, round_n, prompt, worktree,
):
    result = step4_write_findings(step_tag, round_n, prompt, worktree)
    repo_worktree = _first_mount_worktree_path(prompt, worktree)
    noise = Path(repo_worktree) / "node_modules" / "noise.js"
    noise.parent.mkdir(parents=True, exist_ok=True)
    noise.write_text("module.exports = 1;\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "node_modules/noise.js"],
        cwd=repo_worktree,
        check=True,
    )
    return result


def step4_write_findings_and_make_unborn_head(
    step_tag, round_n, prompt, worktree,
):
    result = step4_write_findings(step_tag, round_n, prompt, worktree)
    repo_worktree = _first_mount_worktree_path(prompt, worktree)
    branch = subprocess.check_output(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=repo_worktree,
        text=True,
    ).strip()
    subprocess.run(
        ["git", "update-ref", "-d", f"refs/heads/{branch}"],
        cwd=repo_worktree,
        check=True,
    )
    return result


def step5_preflight_repair_node_modules(step_tag, round_n, prompt, worktree):
    assert step_tag == "STEP5_PREFLIGHT_REPAIR"
    repo_worktree = _first_mount_worktree_path(prompt, worktree)
    ignore = Path(repo_worktree) / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if "node_modules/" not in existing:
        ignore.write_text(f"{existing}node_modules/\n", encoding="utf-8")
    subprocess.run(
        ["git", "reset", "HEAD", "--", "node_modules/noise.js"],
        cwd=repo_worktree,
        check=True,
    )
    noise = Path(repo_worktree) / "node_modules" / "noise.js"
    if noise.exists():
        noise.unlink()
    subprocess.run(["git", "add", ".gitignore"], cwd=repo_worktree, check=True)
    return ("repaired generated diff", 0)


def step5_preflight_repair_noop(step_tag, round_n, prompt, worktree):
    assert step_tag == "STEP5_PREFLIGHT_REPAIR"
    return ("did not repair", 0)


def step5_invalid_review_json(step_tag, round_n, prompt, worktree):
    out = _last_json_output_path(prompt)
    if out is None:
        return ("", 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{not-json", encoding="utf-8")
    return ("not json", 0)


def step5_missing_review_file_with_stdout(step_tag, round_n, prompt, worktree):
    payload = _payload_with_default_plan_verification(
        {"score": 90, "issues": [], "problem_category": None},
        prompt,
    )
    return (f"review chatter\n```json\n{json.dumps(payload)}\n```", 0)


def step5_write_review_but_fail(step_tag, round_n, prompt, worktree):
    writer = _step5_writer(
        {"score": 90, "issues": [], "problem_category": None}
    )
    writer(step_tag, round_n, prompt, worktree)
    return ("failed after writing review", 1)


def step5_success_without_rewriting_review(step_tag, round_n, prompt, worktree):
    assert "System retry feedback" in prompt
    assert "Step5 unparseable" in prompt
    payload = _payload_with_default_plan_verification(
        {"score": 91, "issues": [], "problem_category": None},
        prompt,
    )
    return (f"```json\n{json.dumps(payload)}\n```", 0)


def _step5_writer_expect_retry_feedback(payload: dict):
    writer = _step5_writer(payload)

    def _w(step_tag, round_n, prompt, worktree):
        assert "System retry feedback" in prompt
        assert "Step5 unparseable" in prompt
        return writer(step_tag, round_n, prompt, worktree)

    return _w


def _step5_writer_expect_plan_coverage_feedback(payload: dict):
    writer = _step5_writer(payload)

    def _w(step_tag, round_n, prompt, worktree):
        assert "System retry feedback" in prompt
        assert "plan_verification missing active plan ids" in prompt
        return writer(step_tag, round_n, prompt, worktree)

    return _w


def _step5_writer(payload: dict):
    def _w(step_tag, round_n, prompt, worktree):
        m = re.search(r"必须\*\*将结果写入 `([^`]+\.json)`", prompt) or \
            re.search(r"将结果写入 `([^`]+\.json)`", prompt)
        if not m:
            return ("", 1)
        resolved_payload = _payload_with_default_plan_verification(
            payload, prompt,
        )
        out = Path(m.group(1))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(resolved_payload), encoding="utf-8")
        # Also echo on stdout as a fenced block (reviewer prefers file though).
        return (f"```json\n{json.dumps(resolved_payload)}\n```", 0)
    return _w


def step4_write_findings(step_tag, round_n, prompt, worktree):
    out = _last_json_output_path(prompt)
    if out is None:
        return ("", 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({
            "pass": True,
            "plan_execution": [
                {"id": "DW-01", "status": "done", "evidence": ["login.py:1"]},
            ],
            "findings": [],
        }),
        encoding="utf-8",
    )
    return ("ok", 0)


def _step5_writer(payload: dict):
    def _w(step_tag, round_n, prompt, worktree):
        out = _last_json_output_path(prompt)
        if out is None:
            return ("", 1)
        resolved_payload = _payload_with_default_plan_verification(
            payload, prompt,
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(resolved_payload), encoding="utf-8")
        return (f"```json\n{json.dumps(resolved_payload)}\n```", 0)
    return _w


def test_plan_verification_checkbox_patch_checks_delivered_done_items():
    body = (
        "# 迭代设计 — Round 1\n\n"
        "## 本轮目标\n\n做登录。\n\n"
        "## 开发计划\n\n"
        "- [ ] DW-01: 加表单\n"
        "- [ ] DW-02: 加校验\n"
        "  - [ ] DW-02.1: 校验空邮箱\n"
        "- [ ] DW-03: 补充失败态\n\n"
        "## 用例清单\n\n"
        "- [ ] 非计划 checkbox 不应改变\n"
    )

    updated = _apply_plan_verification_checkboxes(body, [
        {"id": "DW-01", "status": "done", "verified": True},
        {
            "id": "DW-02.1",
            "status": "done",
            "implemented": True,
            "verified": False,
        },
        {"id": "DW-02", "status": "deferred", "verified": True},
        {
            "id": "DW-03",
            "status": "done",
            "implemented": False,
            "verified": True,
        },
    ])

    assert "- [x] DW-01: 加表单" in updated
    assert "- [ ] DW-02: 加校验" in updated
    assert "  - [x] DW-02.1: 校验空邮箱" in updated
    assert "- [ ] DW-03: 补充失败态" in updated
    assert "- [ ] 非计划 checkbox 不应改变" in updated


def test_plan_coverage_ignores_cancelled_items():
    body = (
        "# 迭代设计 — Round 1\n\n"
        "## 开发计划\n\n"
        "- [x] DW-01: [P0] 加表单\n"
        "- [ ] ~~DW-02: [P1] 取消的旧计划~~\n"
        "  - [ ] DW-02.1: [P1] 子计划\n"
        "\n## 用例清单\n"
    )

    items = _extract_plan_checklist_items(body)
    assert [item.id for item in items] == ["DW-01", "DW-02", "DW-02.1"]
    assert items[0].importance == "P0"
    assert items[1].cancelled is True

    missing = _missing_plan_verification_ids(
        plan_items=items,
        plan_verification=[
            {"id": "DW-01", "status": "done", "verified": True},
        ],
    )

    assert missing == ["DW-02.1"]


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
    # Publish without requiring a linked design_work row.
    await db.execute(
        "UPDATE design_docs SET status='published', published_at=? WHERE id=?",
        ("t", dd["id"]),
    )
    dd["status"] = "published"
    repo_dir = tmp_path / "repo"
    await _init_repo(repo_dir)
    repo_id = "repo-test00000001"
    bare_dir = ws_root / ".coop" / "registry" / "repos" / f"{repo_id}.git"
    await _make_bare_clone(repo_dir, bare_dir)
    repo_registry = RepoRegistryRepo(db)
    await repo_registry.upsert(
        id=repo_id,
        name="testrepo",
        url=str(repo_dir),
        default_branch="main",
        bare_clone_path=str(bare_dir),
        role="backend",
    )
    await repo_registry.update_fetch_status(
        repo_id, status="healthy", bare_clone_path=str(bare_dir),
    )
    ini = DevIterationNoteManager(db)
    yield dict(db=db, wm=wm, ws=ws, ddm=ddm, ini=ini, registry=registry,
               dd=dd, repo=str(repo_dir), repo_id=repo_id,
               ws_root=ws_root, root=tmp_path,
               repo_registry=repo_registry)
    await db.close()


def _make_sm(env, executor, cfg=None, agent_host_repo=None):
    from tests.conftest import make_test_llm_runner
    sm = DevWorkStateMachine(
        db=env["db"],
        workspaces=env["wm"],
        design_docs=env["ddm"],
        iteration_notes=env["ini"],
        executor=executor,
        config=cfg or _build_config(),
        registry=env["registry"],
        agent_host_repo=agent_host_repo,
        llm_runner=make_test_llm_runner(executor),
    )
    # Override workspaces_root so _s0_init's bare-clone lookup matches the
    # registry row (the manager normally derives this from settings).
    sm.workspaces_root = env["ws_root"].resolve()
    return sm


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
            repo_refs=_refs_arg(env),
            prompt="build login",
        )


async def test_create_uses_requested_agent_when_configured(env):
    host_repo = AgentHostRepo(env["db"])
    await host_repo.upsert(id="local", host="local", agent_type="codex")
    await host_repo.update_health("local", status="healthy")
    sm = _make_sm(env, ScriptedExecutor([]), agent_host_repo=host_repo)

    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
        agent="codex",
    )

    assert dw["agent"] == "codex"
    assert dw["agent_host_id"] == "local"


async def test_create_falls_back_to_configured_agent_when_requested_unavailable(env):
    host_repo = AgentHostRepo(env["db"])
    await host_repo.upsert(id="local", host="local", agent_type="codex")
    await host_repo.update_health("local", status="healthy")
    sm = _make_sm(env, ScriptedExecutor([]), agent_host_repo=host_repo)

    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
        agent="claude",
    )

    assert dw["agent"] == "codex"
    assert dw["agent_host_id"] == "local"


async def test_create_omitted_agent_uses_configured_agent(env):
    host_repo = AgentHostRepo(env["db"])
    await host_repo.upsert(id="local", host="local", agent_type="codex")
    await host_repo.update_health("local", status="healthy")
    sm = _make_sm(env, ScriptedExecutor([]), agent_host_repo=host_repo)

    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
        agent=None,
    )

    assert dw["agent"] == "codex"
    assert dw["agent_host_id"] == "local"


async def test_s0_init_creates_devwork_branch_from_base_branch(env):
    repo = Path(env["repo"])
    await run_git("checkout", "-b", "develop", cwd=str(repo))
    (repo / "develop-only.txt").write_text("develop\n", encoding="utf-8")
    await run_git("add", "develop-only.txt", cwd=str(repo))
    await run_git("commit", "-m", "develop commit", cwd=str(repo))

    bare = (
        env["ws_root"] / ".coop" / "registry" / "repos"
        / f"{env['repo_id']}.git"
    )
    await run_git(
        "--git-dir", str(bare),
        "fetch", str(repo), "refs/heads/develop:refs/heads/develop",
    )
    develop_sha, _, _ = await run_git(
        "--git-dir", str(bare), "rev-parse", "develop",
    )

    sm = _make_sm(env, ScriptedExecutor([]))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=[
            (
                DevRepoRef(
                    repo_id=env["repo_id"],
                    base_branch="develop",
                    mount_name="backend",
                ),
                None,
            ),
        ],
        prompt="build from develop",
    )

    await sm.tick(dw["id"])
    refreshed = await env["db"].fetchone(
        "SELECT worktree_path FROM dev_works WHERE id=?", (dw["id"],)
    )
    worktree = Path(refreshed["worktree_path"])

    head_sha, _, _ = await run_git("rev-parse", "HEAD", cwd=str(worktree))
    assert head_sha == develop_sha
    assert (
        worktree / "develop-only.txt"
    ).read_text(encoding="utf-8") == "develop\n"


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
        repo_refs=_refs_arg(env),
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


async def test_rubric_threshold_override_wins_over_design_doc(env):
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
        repo_refs=_refs_arg(env),
        prompt="build login",
        rubric_threshold=95,
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "ESCALATED"
    assert final["last_score"] == 90
    gates = json.loads(final["gates_json"])
    assert gates["rubric_threshold_override"] == 95


async def test_step5_non_null_category_cannot_complete_even_with_high_score(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer({
            "score": 95,
            "issues": [{"message": "AC-02 is still missing"}],
            "problem_category": "req_gap",
        }),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor, cfg=_build_config(max_rounds=1))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "ESCALATED"
    assert final["last_score"] == 95
    assert final["last_problem_category"] == ProblemCategory.req_gap.value


async def test_step5_round2_prompt_includes_previous_actual_b(env):
    def _round2_step4_writer(step_tag, round_n, prompt, worktree):
        assert round_n == 2
        assert "上一轮 `actual_score_b`=50" in prompt
        assert "优先实现未实现的开发计划" in prompt
        return step4_write_findings(step_tag, round_n, prompt, worktree)

    def _round2_writer(step_tag, round_n, prompt, worktree):
        assert round_n == 2
        assert "DevWork Step5" in prompt
        assert "上一轮实际实现分值 `b`：50" in prompt
        return _step5_writer({
            "score": 90,
            "score_breakdown": {
                "plan_score_a": 100,
                "actual_score_b": 90,
                "final_score": 90,
                "previous_actual_score_b": 50,
            },
            "issues": [],
            "problem_category": None,
        })(step_tag, round_n, prompt, worktree)

    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer({
            "score": 40,
            "score_breakdown": {"plan_score_a": 80, "actual_score_b": 50, "final_score": 40},
            "issues": [{"message": "AC-02 is still missing"}],
            "problem_category": "req_gap",
        }),
        step2_append_h2,
        step3_write_ctx,
        _round2_step4_writer,
        _round2_writer,
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["last_score"] == 90


async def test_max_rounds_override_wins_over_config(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer(
            {
                "score": 40,
                "issues": [{"message": "needs work"}],
                "problem_category": "req_gap",
            }
        ),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor, cfg=_build_config(max_rounds=5))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
        max_rounds=0,
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "ESCALATED"
    assert final["iteration_rounds"] == 1
    gates = json.loads(final["gates_json"])
    assert gates["max_rounds_override"] == 0


async def test_create_rejects_max_rounds_override_above_config(env):
    sm = _make_sm(
        env,
        ScriptedExecutor([]),
        cfg=_build_config(max_rounds=1),
    )
    with pytest.raises(BadRequestError, match="exceeds configured cap"):
        await sm.create(
            workspace_id=env["ws"]["id"],
            design_doc_id=env["dd"]["id"],
            repo_refs=_refs_arg(env),
            prompt="build login",
            max_rounds=2,
        )


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
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["first_pass_success"] == 0  # round2 passed
    assert final["iteration_rounds"] == 1

    round2_prompt = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"devworks/{dw['id']}/prompts/step2-round2.md",
    )
    previous_note_abs = (
        env["ws_root"] / env["ws"]["slug"] / "devworks" / dw["id"]
        / "iteration-round-1.md"
    ).as_posix()
    assert previous_note_abs in round2_prompt
    assert "首轮，无上一轮迭代设计" not in round2_prompt
    assert "必须保留所有历史 PLAN" in round2_prompt
    assert "追加缩进子 PLAN" in round2_prompt


async def test_step2_writes_feedback_file_on_round_2(env):
    """Round 2's Step2 must persist feedback-for-round2.md via the registry.

    Phase 4: previous-round review markdown is materialized to a workspace
    file rather than embedded into the prompt. The path-based prompt
    references that file by absolute path.
    """
    script = [
        # round 1: req_gap to force a round 2.
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 30, "issues": [{"m": "do better"}],
                        "problem_category": "req_gap"}),
        # round 2: pass.
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 95, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"

    rows = await env["db"].fetchall(
        "SELECT relative_path, byte_size, kind FROM workspace_files "
        "WHERE workspace_id=? AND relative_path LIKE ?",
        (env["ws"]["id"], f"devworks/{dw['id']}/feedback/%"),
    )
    feedback_rows = [
        r for r in rows
        if r["relative_path"].endswith("feedback-for-round2.md")
    ]
    assert feedback_rows, (
        f"expected feedback-for-round2.md to be registered; got {rows}"
    )
    assert feedback_rows[0]["byte_size"] > 0
    assert feedback_rows[0]["kind"] == "feedback"


async def test_step2_feedback_includes_next_round_hints(env):
    """Phase 5: Round 1's next_round_hints surface in feedback-for-round2.md.

    When round-1 Step5 emits a non-empty `next_round_hints` array, round-2's
    feedback markdown gets a `## 下一轮提示` H2 listing each hint. Round 2
    drives to COMPLETED so the test stays self-contained.
    """
    round1_payload = {
        "score": 30,
        "score_breakdown": {
            "plan_score_a": 90,
            "actual_score_b": 33,
            "final_score": 30,
        },
        "issues": [{"m": "do better"}],
        "problem_category": "req_gap",
        "next_round_hints": [
            {"kind": "missing_feature", "message": "no /logout endpoint"},
            {"kind": "optimization", "mount": "backend",
             "message": "auth.py:42-58 can use lru_cache"},
            {"message": "bare hint, no kind no mount"},
        ],
    }
    script = [
        # round 1: req_gap to force round 2 + carries hints.
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer(round1_payload),
        # round 2: pass.
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 95, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"

    body = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"devworks/{dw['id']}/feedback/feedback-for-round2.md",
    )
    assert "## 下一轮提示" in body
    assert "missing_feature" in body
    assert "no /logout endpoint" in body
    assert "auth.py:42-58" in body
    # Mount hint should surface as a parenthesised prefix.
    assert "(backend)" in body
    assert "PLAN 扩展限制" in body
    assert "plan_score_a >= 90" in body
    assert "谨慎新增和细化计划" in body
    # Render guard: hint without kind/mount has no double-space artefact.
    assert "- bare hint, no kind no mount" in body
    assert "-  " not in body  # no "- <space><space>" anywhere


async def test_step2_feedback_low_plan_score_encourages_plan_expansion(env):
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({
            "score": 35,
            "score_breakdown": {
                "plan_score_a": 65,
                "actual_score_b": 54,
                "final_score": 35,
            },
            "issues": [{"message": "core acceptance is not planned"}],
            "problem_category": "req_gap",
        }),
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 95, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"

    body = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"devworks/{dw['id']}/feedback/feedback-for-round2.md",
    )
    assert "PLAN 补齐建议" in body
    assert "plan_score_a <= 70" in body
    assert "鼓励新增和细化计划" in body
    assert "主动补齐遗漏主 PLAN" in body


async def test_step2_prompt_artifact_under_32kib(env):
    """Persisted Step2 prompt has a generous budget for richer guidance."""
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 95, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"

    prompt_bytes = await env["registry"].read_bytes(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"devworks/{dw['id']}/prompts/step2-round1.md",
    )
    assert len(prompt_bytes) <= 32 * 1024, (
        f"step2 prompt grew past 32 KiB: {len(prompt_bytes)} bytes"
    )


async def test_step2_recommended_tech_stack_requires_iteration_section(env):
    script = [
        step2_append_h2_with_tech_stack,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
        recommended_tech_stack="React 18 + FastAPI",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"

    prompt_text = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"devworks/{dw['id']}/prompts/step2-round1.md",
    )
    assert "React 18 + FastAPI" in prompt_text
    assert "`## 推荐技术栈`" in prompt_text

    note_text = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"devworks/{dw['id']}/iteration-round-1.md",
    )
    assert "## 推荐技术栈" in note_text


async def test_impl_gap_routes_to_step2(env):
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 50, "issues": [],
                        "problem_category": "impl_gap"}),
        # impl_gap -> full iteration rerun (Step2 + Step3 + Step4 + Step5):
        # impl failures are part of the iteration design loop.
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
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
        repo_refs=_refs_arg(env),
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
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor, cfg=_build_config(max_rounds=1))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "ESCALATED"
    assert final["iteration_rounds"] == 1
    assert len(executor.calls) == 4
    gates = json.loads(final["gates_json"])
    assert gates["resume_after_max_rounds"]["completed_round"] == 1


async def test_continue_after_max_rounds_escalation_resumes_with_extra_rounds(env):
    script = [
        # round 1: fail and escalate because max_rounds=1.
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 10, "issues": [],
                        "problem_category": "req_gap"}),
        # round 2: after human continuation, pass.
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 95, "issues": [],
                        "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor, cfg=_build_config(max_rounds=1))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    escalated = await sm.run_to_completion(dw["id"])
    assert escalated["current_step"] == "ESCALATED"
    assert sm.can_continue_after_escalation(escalated) is True

    resumed = await sm.continue_after_escalation(
        dw["id"], additional_rounds=1, rubric_threshold=90,
    )
    assert resumed["current_step"] == DevWorkStep.STEP2_ITERATION.value
    assert resumed["iteration_rounds"] == 1
    assert resumed["escalated_at"] is None
    gates = json.loads(resumed["gates_json"])
    assert gates["max_rounds_override"] == 2
    assert gates["rubric_threshold_override"] == 90
    assert gates["resume_history"][-1]["rubric_threshold"] == 90
    assert "resume_after_max_rounds" not in gates

    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["iteration_rounds"] == 1
    assert final["first_pass_success"] == 0


async def test_step1_invalid_design_escalates(env):
    # Corrupt the design doc file so validator fails.
    dd_abs = env["ws_root"] / env["ws"]["slug"] / env["dd"]["path"]
    dd_abs.write_text("no front-matter here", encoding="utf-8")
    script = []  # LLM never invoked
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
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
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["iteration_rounds"] == 1  # round 1 looped; round 2 passed
    feedback = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"devworks/{dw['id']}/feedback/feedback-for-round2.md",
    )
    assert "System validation feedback" in feedback
    assert "Step2 missing H2" in feedback


async def test_step4_format_error_is_visible_in_retry_prompt(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_invalid_findings_json,
        step4_write_findings_expect_retry_feedback,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"


async def test_step4_waits_for_transient_findings_visibility(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    cfg = _build_config()
    cfg.devwork.step4_findings_wait_timeout_s = 0.2
    cfg.devwork.step4_findings_wait_interval_s = 0.01
    sm = _make_sm(env, ScriptedExecutor(script), cfg=cfg)

    original_index_existing = env["registry"].index_existing
    misses = {"count": 0}

    async def flaky_index_existing(**kwargs):
        rel = kwargs.get("relative_path", "")
        if rel.endswith("step4-findings-round1.json") and misses["count"] == 0:
            misses["count"] += 1
            raise NotFoundError("transient missing")
        return await original_index_existing(**kwargs)

    env["registry"].index_existing = flaky_index_existing
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert misses["count"] == 1
    assert final["current_step"] == "COMPLETED"
    assert final["iteration_rounds"] == 0


async def test_step4_retry_stays_in_same_iteration_round(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_invalid_findings_json,
        step4_write_findings_expect_retry_feedback,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "COMPLETED"
    assert final["iteration_rounds"] == 0
    rows = await env["db"].fetchall(
        "SELECT round, markdown_path FROM dev_iteration_notes "
        "WHERE dev_work_id=? ORDER BY round",
        (dw["id"],),
    )
    assert [(r["round"], r["markdown_path"]) for r in rows] == [
        (1, f"devworks/{dw['id']}/iteration-round-1.md")
    ]
    dev_root = env["ws_root"] / env["ws"]["slug"] / "devworks" / dw["id"]
    assert not (dev_root / "iteration-round-2.md").exists()
    assert not (dev_root / "prompts" / "step4-round2.md").exists()


async def test_step4_second_validation_failure_escalates(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_invalid_findings_json,
        step4_invalid_findings_json,
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "ESCALATED"
    assert final["iteration_rounds"] == 0
    assert final["last_problem_category"] == ProblemCategory.impl_gap.value
    assert sm.can_resume_step_after_escalation(final) is True
    assert sm.resume_step_for_escalation(final) == "STEP4_DEVELOP"

    resumed = await sm.resume_step_after_escalation(dw["id"])
    assert resumed["current_step"] == "STEP4_DEVELOP"
    assert resumed["escalated_at"] is None
    gates = json.loads(resumed["gates_json"])
    assert "resume_after_step_failure" not in gates


async def test_step4_retry_does_not_accept_stale_findings_file(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings_but_fail,
        step4_success_without_rewriting_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "ESCALATED"
    assert final["iteration_rounds"] == 0
    assert final["last_problem_category"] == ProblemCategory.impl_gap.value
    findings = (
        env["ws_root"]
        / env["ws"]["slug"]
        / "devworks"
        / dw["id"]
        / "artifacts"
        / "step4-findings-round1.json"
    )
    assert not findings.exists()


async def test_step5_parse_error_is_visible_in_retry_prompt(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        step5_invalid_review_json,
        _step5_writer_expect_retry_feedback(
            {"score": 90, "issues": [], "problem_category": None}
        ),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_step"] == "COMPLETED"
    assert final["iteration_rounds"] == 0
    dev_root = env["ws_root"] / env["ws"]["slug"] / "devworks" / dw["id"]
    assert not (dev_root / "iteration-round-2.md").exists()
    assert not (dev_root / "prompts" / "step5-round2.md").exists()
    reviews = await env["db"].fetchall(
        "SELECT rv.round AS review_round, n.round AS note_round "
        "FROM reviews rv "
        "JOIN dev_iteration_notes n ON n.id=rv.dev_iteration_note_id "
        "WHERE rv.dev_work_id=?",
        (dw["id"],),
    )
    assert [(r["review_round"], r["note_round"]) for r in reviews] == [(1, 1)]


async def test_step5_missing_review_file_uses_stdout_and_persists_artifact(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        step5_missing_review_file_with_stdout,
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "COMPLETED"
    assert final["iteration_rounds"] == 0
    artifacts = env["ws_root"] / env["ws"]["slug"] / "devworks" / dw["id"] / "artifacts"
    failure_meta = artifacts / "step5-review-round1-attempt1-failure.json"
    failure_stdout = artifacts / "step5-review-round1-attempt1-stdout.md"
    review = artifacts / "step5-review-round1.json"
    assert not failure_meta.exists()
    assert not failure_stdout.exists()
    payload = json.loads(review.read_text(encoding="utf-8"))
    assert payload["score"] == 90
    reviews = await env["db"].fetchall(
        "SELECT round FROM reviews WHERE dev_work_id=? ORDER BY created_at",
        (dw["id"],),
    )
    assert [r["round"] for r in reviews] == [1]


async def test_step5_retry_uses_fresh_stdout_not_stale_review_file(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        step5_write_review_but_fail,
        step5_success_without_rewriting_review,
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "COMPLETED"
    assert final["iteration_rounds"] == 0
    artifacts = env["ws_root"] / env["ws"]["slug"] / "devworks" / dw["id"] / "artifacts"
    review = artifacts / "step5-review-round1.json"
    assert json.loads(review.read_text(encoding="utf-8"))["score"] == 91
    reviews = await env["db"].fetchall(
        "SELECT round, score FROM reviews WHERE dev_work_id=?", (dw["id"],),
    )
    assert [(r["round"], r["score"]) for r in reviews] == [(1, 91)]


async def test_step5_preflight_repairs_generated_dependency_paths(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings_and_stage_node_modules,
        step5_preflight_repair_node_modules,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "COMPLETED"
    assert final["iteration_rounds"] == 0
    assert len(executor.calls) == 5
    assert any(
        call["step"] == "STEP5_PREFLIGHT_REPAIR" for call in executor.calls
    )
    repo_row = await env["db"].fetchone(
        "SELECT worktree_path FROM dev_work_repos WHERE dev_work_id=?",
        (dw["id"],),
    )
    diff_names, _err, _rc = await run_git(
        "diff", "HEAD", "--name-only",
        cwd=repo_row["worktree_path"],
    )
    assert "node_modules/noise.js" not in diff_names
    assert ".gitignore" in diff_names


async def test_step5_preflight_repair_failure_does_not_rerun_step4(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings_and_stage_node_modules,
        step5_preflight_repair_noop,
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "ESCALATED"
    assert final["iteration_rounds"] == 0
    assert final["last_problem_category"] == ProblemCategory.impl_gap.value
    assert len(executor.calls) == 4
    assert any(
        call["step"] == "STEP5_PREFLIGHT_REPAIR" for call in executor.calls
    )
    event = await env["db"].fetchone(
        "SELECT payload_json FROM workspace_events "
        "WHERE event_name='dev_work.escalated' AND correlation_id=?",
        (dw["id"],),
    )
    assert "generated/dependency diff repair failed" in json.loads(
        event["payload_json"]
    )["reason"]


async def test_step5_preflight_repairs_unborn_head_before_review(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings_and_make_unborn_head,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "COMPLETED"
    repo_row = await env["db"].fetchone(
        "SELECT worktree_path FROM dev_work_repos WHERE dev_work_id=?",
        (dw["id"],),
    )
    _head, _err, rc = await run_git(
        "rev-parse", "--verify", "HEAD^{commit}",
        cwd=repo_row["worktree_path"], check=False,
    )
    assert rc == 0


async def test_step5_repeated_parse_failure_escalates_without_round_inflation(env):
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        step5_invalid_review_json,
        step5_invalid_review_json,
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "ESCALATED"
    assert final["iteration_rounds"] == 0
    dev_root = env["ws_root"] / env["ws"]["slug"] / "devworks" / dw["id"]
    assert not (dev_root / "iteration-round-2.md").exists()
    assert not (dev_root / "prompts" / "step5-round2.md").exists()
    artifacts = dev_root / "artifacts"
    assert (artifacts / "step5-review-round1-attempt1-failure.json").exists()
    assert (artifacts / "step5-review-round1-attempt2-failure.json").exists()
    reviews = await env["db"].fetchall(
        "SELECT id FROM reviews WHERE dev_work_id=?", (dw["id"],),
    )
    assert reviews == []


async def test_step5_plan_verification_checks_iteration_plan_items(env):
    payload = {
        "score": 90,
        "issues": [],
        "score_breakdown": {
            "plan_score_a": 95,
            "actual_score_b": 95,
            "final_score": 90,
            "plan_coverage": 0.95,
            "execution_coverage": 0.95,
        },
        "plan_verification": [
            {"id": "DW-01", "status": "done", "verified": True},
            {
                "id": "DW-02.1",
                "status": "done",
                "implemented": True,
                "verified": False,
            },
            {"id": "DW-02", "status": "deferred", "verified": True},
            {
                "id": "DW-03",
                "status": "done",
                "implemented": False,
                "verified": True,
            },
        ],
        "problem_category": None,
    }
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer(payload),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "COMPLETED"
    note_body = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"devworks/{dw['id']}/iteration-round-1.md",
    )
    assert "- [x] DW-01: 加表单" in note_body
    assert "  - [x] DW-02.1: 校验空邮箱" in note_body
    assert "- [ ] DW-02: 加校验" in note_body
    assert "- [ ] DW-03: 补充失败态" in note_body

    review = await env["db"].fetchone(
        "SELECT findings_json, score_breakdown_json FROM reviews WHERE dev_work_id=?",
        (dw["id"],),
    )
    assert json.loads(review["findings_json"]) == payload["plan_verification"]
    assert json.loads(review["score_breakdown_json"]) == payload["score_breakdown"]


async def test_step5_retries_when_plan_verification_misses_active_items(env):
    incomplete_payload = {
        "score": 90,
        "issues": [],
        "plan_verification": [
            {"id": "DW-01", "status": "done", "verified": True},
        ],
        "problem_category": None,
    }
    complete_payload = {
        "score": 90,
        "issues": [],
        "plan_verification": [
            {"id": "DW-01", "status": "done", "verified": True},
            {"id": "DW-02", "status": "done", "verified": True},
            {"id": "DW-02.1", "status": "done", "verified": True},
            {"id": "DW-03", "status": "done", "verified": True},
        ],
        "problem_category": None,
    }
    script = [
        step2_append_h2,
        step3_write_ctx,
        step4_write_findings,
        _step5_writer(incomplete_payload),
        _step5_writer_expect_plan_coverage_feedback(complete_payload),
    ]
    executor = ScriptedExecutor(script)
    sm = _make_sm(env, executor)
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_step"] == "COMPLETED"
    gates = json.loads(final["gates_json"])
    assert gates["step5_retry_round1"] == 1
    assert executor.script == []
    review = await env["db"].fetchone(
        "SELECT findings_json FROM reviews WHERE dev_work_id=?",
        (dw["id"],),
    )
    assert json.loads(review["findings_json"]) == complete_payload["plan_verification"]


async def test_step2_front_matter_not_tampered(env):
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
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
        repo_refs=_refs_arg(env),
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
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    await sm.cancel(dw["id"])
    row = await env["db"].fetchone(
        "SELECT * FROM dev_works WHERE id=?", (dw["id"],)
    )
    assert row["current_step"] == "CANCELLED"


# ---- _select_primary_ref unit tests (Phase 4) -------------------------------

def test_select_primary_explicit_wins_over_role_priority():
    """Explicit is_primary=True overrides role-based selection."""
    rows = [
        {"mount_name": "frontend", "is_primary": 0, "repo_role": "backend"},
        {"mount_name": "infra", "is_primary": 1, "repo_role": "infra"},
        {"mount_name": "backend", "is_primary": 0, "repo_role": "backend"},
    ]
    picked = DevWorkStateMachine._select_primary_ref(rows)
    assert picked["mount_name"] == "infra"


def test_select_primary_role_priority_then_mount_tiebreak():
    """No explicit primary → REPO_ROLE_PRIMARY_PRIORITY decides; ties broken
    lexicographically by mount_name."""
    rows = [
        {"mount_name": "b-svc", "is_primary": 0, "repo_role": "backend"},
        {"mount_name": "a-svc", "is_primary": 0, "repo_role": "backend"},
        {"mount_name": "ui",    "is_primary": 0, "repo_role": "frontend"},
    ]
    picked = DevWorkStateMachine._select_primary_ref(rows)
    assert picked["mount_name"] == "a-svc"


def test_select_primary_unknown_role_falls_to_lowest_priority():
    """A NULL/unknown role row sorts last (priority = len(priorities))."""
    rows = [
        {"mount_name": "weird", "is_primary": 0, "repo_role": None},
        {"mount_name": "docs",  "is_primary": 0, "repo_role": "docs"},
    ]
    picked = DevWorkStateMachine._select_primary_ref(rows)
    assert picked["mount_name"] == "docs"


async def test_workspace_md_shows_devwork(env):
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"],
        design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env),
        prompt="build login",
    )
    await sm.run_to_completion(dw["id"])
    md = (env["ws_root"] / "t" / "workspace.md").read_text(encoding="utf-8")
    assert f"devworks/DEV-{dw['id']}" in md


# ---------------------------------------------------------------------------
# Phase 2: LLMRunner wiring assertions
# ---------------------------------------------------------------------------

async def test_dev_work_sm_routes_through_llm_runner(env, fake_llm_runner):
    """_run_llm must call llm_runner.run_oneshot, not executor.run_once."""
    from src.dev_work_sm import DevWorkStateMachine

    class _NoopExecutor:
        async def run_once(self, *a, **kw):  # pragma: no cover - fail if hit
            raise AssertionError("executor.run_once must not be called")

    fake_llm_runner.run_oneshot_return = ("ok", 0)
    sm = DevWorkStateMachine(
        db=env["db"],
        workspaces=env["wm"],
        design_docs=env["ddm"],
        iteration_notes=env["ini"],
        executor=_NoopExecutor(),
        config=_build_config(),
        registry=env["registry"],
        llm_runner=fake_llm_runner,
    )
    sm.workspaces_root = env["ws_root"].resolve()

    dw_row = {
        "id": "dw-x",
        "workspace_id": env["ws"]["id"],
        "agent_host_id": "local",
    }
    rc, stdout = await sm._run_llm(
        dw_row,
        agent="claude",
        worktree=str(env["ws_root"]),
        timeout=10,
        task_file="/tmp/task.md",
        step_tag="STEP2",
        round_n=1,
    )
    assert (rc, stdout) == (0, "ok")
    assert len(fake_llm_runner.calls) == 1
    call = fake_llm_runner.calls[0]
    # Phase 3: SM now drives via run_with_progress; the call carries the
    # rendered acpx command + cwd instead of the old (agent, task_file)
    # tuple. Agent and task_file are reachable through the cmd vector.
    assert call["kind"] == "progress"
    assert call["step_tag"] == "STEP2"
    assert call["cwd"] == str(env["ws_root"])
    assert "claude" in call["cmd"]
    assert "/tmp/task.md" in call["cmd"]


def test_dev_work_sm_requires_llm_runner(env):
    """Phase 2: llm_runner is a required keyword-only argument."""
    from src.dev_work_sm import DevWorkStateMachine

    with pytest.raises(TypeError):
        DevWorkStateMachine(
            db=env["db"],
            workspaces=env["wm"],
            design_docs=env["ddm"],
            iteration_notes=env["ini"],
            executor=None,
            config=_build_config(),
            registry=env["registry"],
        )


# ---------------------------------------------------------------------------
# Phase 3: progress heartbeats + idle_timeout + step4 wall ceiling
# ---------------------------------------------------------------------------


def _make_phase3_sm(env, fake_llm_runner):
    """Build an SM wired to the per-test fake LLM runner.

    All Phase 3 SM tests share this helper so each test only needs to set
    fake_llm_runner.progress_ticks / .next_idle_timeout and call _run_llm.
    """
    from src.dev_work_sm import DevWorkStateMachine

    class _NoopExecutor:
        async def run_once(self, *a, **kw):  # pragma: no cover - fail if hit
            raise AssertionError("executor.run_once must not be called")

    sm = DevWorkStateMachine(
        db=env["db"],
        workspaces=env["wm"],
        design_docs=env["ddm"],
        iteration_notes=env["ini"],
        executor=_NoopExecutor(),
        config=_build_config(),
        registry=env["registry"],
        llm_runner=fake_llm_runner,
    )
    sm.workspaces_root = env["ws_root"].resolve()
    return sm


async def _insert_minimal_dev_work(db, *, dev_id: str, workspace_id: str,
                                   design_doc_id: str) -> dict:
    """INSERT a row for tests that exercise _run_llm in isolation."""
    now = "2026-04-28T00:00:00+00:00"
    await db.execute(
        """INSERT INTO dev_works
           (id, workspace_id, design_doc_id, prompt,
            worktree_path, worktree_branch, current_step,
            iteration_rounds, agent, agent_host_id,
            created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (dev_id, workspace_id, design_doc_id, "test",
         None, None, "INIT", 0, "claude", "local", now, now),
    )
    return await db.fetchone("SELECT * FROM dev_works WHERE id=?", (dev_id,))


async def test_run_llm_logs_progress_event_per_tick(env, fake_llm_runner):
    """Each tick emits exactly one ``dev_work.progress`` row in the table."""
    from collections import namedtuple

    Tick = namedtuple("Tick", ["ts", "elapsed_s"])
    fake_llm_runner.progress_ticks = [
        Tick(ts="2026-04-28T00:00:15+00:00", elapsed_s=15),
        Tick(ts="2026-04-28T00:00:30+00:00", elapsed_s=30),
    ]
    fake_llm_runner.run_oneshot_return = ("ok", 0)

    sm = _make_phase3_sm(env, fake_llm_runner)
    dw = await _insert_minimal_dev_work(
        env["db"], dev_id="dev-progress1",
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
    )

    rc, _ = await sm._run_llm(
        dw, agent="claude", worktree=str(env["ws_root"]),
        timeout=10, task_file="/tmp/x.md",
        step_tag="STEP4_DEVELOP", round_n=1,
    )
    assert rc == 0
    rows = await env["db"].fetchall(
        "SELECT * FROM workspace_events WHERE event_name=? "
        "AND correlation_id=?",
        ("dev_work.progress", dw["id"]),
    )
    assert len(rows) == 2
    payloads = [json.loads(r["payload_json"]) for r in rows]
    assert payloads[0]["elapsed_s"] == 15
    assert payloads[1]["elapsed_s"] == 30
    assert payloads[0]["step"] == "STEP4_DEVELOP"


async def test_run_llm_writes_progress_json_per_tick(env, fake_llm_runner):
    """Every tick overwrites dev_works.current_progress_json."""
    from collections import namedtuple

    Tick = namedtuple("Tick", ["ts", "elapsed_s"])

    captured: list[str] = []

    async def heartbeat_spy_factory(real_heartbeat, dw_id):
        async def spy(tick):
            await real_heartbeat(tick)
            row = await env["db"].fetchone(
                "SELECT current_progress_json FROM dev_works WHERE id=?",
                (dw_id,),
            )
            captured.append(row["current_progress_json"])
        return spy

    fake_llm_runner.progress_ticks = [
        Tick(ts="2026-04-28T00:00:15+00:00", elapsed_s=15),
        Tick(ts="2026-04-28T00:00:30+00:00", elapsed_s=30),
    ]
    fake_llm_runner.run_oneshot_return = ("ok", 0)

    sm = _make_phase3_sm(env, fake_llm_runner)
    dw = await _insert_minimal_dev_work(
        env["db"], dev_id="dev-progress2",
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
    )

    # Wrap fake_llm_runner.run_with_progress to spy on current_progress_json
    # immediately after each heartbeat fires.
    orig_run = fake_llm_runner.run_with_progress

    async def spy_run(*, cmd, cwd, heartbeat, heartbeat_interval_s,
                     idle_timeout_s, step_tag):
        spied_hb = await heartbeat_spy_factory(heartbeat, dw["id"])
        return await orig_run(
            cmd=cmd, cwd=cwd, heartbeat=spied_hb,
            heartbeat_interval_s=heartbeat_interval_s,
            idle_timeout_s=idle_timeout_s, step_tag=step_tag,
        )

    fake_llm_runner.run_with_progress = spy_run

    await sm._run_llm(
        dw, agent="claude", worktree=str(env["ws_root"]),
        timeout=10, task_file="/tmp/x.md",
        step_tag="STEP2_ITERATION", round_n=1,
    )
    assert len(captured) == 2
    snap1 = json.loads(captured[0])
    snap2 = json.loads(captured[1])
    assert snap1["elapsed_s"] == 15
    assert snap2["elapsed_s"] == 30
    assert snap2["last_heartbeat_at"] == "2026-04-28T00:00:30+00:00"


async def test_run_llm_clears_progress_json_after_dispatch_close(
    env, fake_llm_runner
):
    """current_progress_json must be NULL after the call returns (any outcome)."""
    from collections import namedtuple

    Tick = namedtuple("Tick", ["ts", "elapsed_s"])
    fake_llm_runner.progress_ticks = [
        Tick(ts="2026-04-28T00:00:15+00:00", elapsed_s=15),
    ]
    fake_llm_runner.run_oneshot_return = ("ok", 0)

    sm = _make_phase3_sm(env, fake_llm_runner)
    dw = await _insert_minimal_dev_work(
        env["db"], dev_id="dev-progress3",
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
    )
    await sm._run_llm(
        dw, agent="claude", worktree=str(env["ws_root"]),
        timeout=10, task_file="/tmp/x.md",
        step_tag="STEP3_CONTEXT", round_n=1,
    )
    row = await env["db"].fetchone(
        "SELECT current_progress_json FROM dev_works WHERE id=?", (dw["id"],),
    )
    assert row["current_progress_json"] is None


async def test_run_llm_idle_timeout_marks_dispatch_timeout(
    env, fake_llm_runner
):
    """IdleTimeoutError → dispatch_state='timeout', step_completed rc=124."""
    from src.llm_runner import IdleTimeoutError

    fake_llm_runner.next_idle_timeout = IdleTimeoutError(
        step_tag="STEP4_DEVELOP", idle_window_s=300,
    )

    sm = _make_phase3_sm(env, fake_llm_runner)
    dw = await _insert_minimal_dev_work(
        env["db"], dev_id="dev-progress4",
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
    )
    rc, _ = await sm._run_llm(
        dw, agent="claude", worktree=str(env["ws_root"]),
        timeout=10, task_file="/tmp/x.md",
        step_tag="STEP4_DEVELOP", round_n=1,
    )
    assert rc == 124
    rows = await env["db"].fetchall(
        "SELECT * FROM workspace_events WHERE event_name=? "
        "AND correlation_id=?",
        ("dev_work.step_completed", dw["id"]),
    )
    assert len(rows) == 1
    assert json.loads(rows[0]["payload_json"])["rc"] == 124


async def test_s4_develop_passes_wall_ceiling_to_run_llm(
    env, fake_llm_runner
):
    """Phase 7 regression: _s4_develop must hand step4_acpx_wall_ceiling_s
    (3600 in test config) to _run_llm as ``timeout=`` — not a per-step value.

    Replaces the deleted ``test_step4_uses_wall_ceiling_not_step4_timeout``
    which asserted the same invariant inside ``_run_llm`` before the
    Phase 7 cleanup moved that wiring up to the caller.
    """
    sm = _make_phase3_sm(env, fake_llm_runner)
    dw = await _insert_minimal_dev_work(
        env["db"], dev_id="dev-step4-ceiling",
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
    )
    # _s4_develop reads dw["worktree_path"]; populate it.
    dw = dict(dw)
    dw["worktree_path"] = str(env["ws_root"])

    # Stub the prerequisites _s4_develop touches before calling _run_llm.
    async def _fake_latest_for(dev_work_id):
        return {"markdown_path": "devworks/x/iteration-note.md"}

    async def _fake_load_mounts(_dw):
        return []

    async def _noop_put_markdown(**_kw):
        return None

    sm.iteration_notes.latest_for = _fake_latest_for  # type: ignore[assignment]
    sm._load_mount_table_entries = _fake_load_mounts  # type: ignore[assignment]
    sm.registry.put_markdown = _noop_put_markdown  # type: ignore[assignment]

    captured: dict[str, object] = {}

    async def _spy_run_llm(_dw, **kwargs):
        if not captured:
            captured.update(kwargs)
        return (0, "")

    sm._run_llm = _spy_run_llm  # type: ignore[assignment]

    # Short-circuit the post-_run_llm body which expects a real findings file.
    async def _fake_index_existing(**_kw):
        return None

    sm.registry.index_existing = _fake_index_existing  # type: ignore[assignment]

    try:
        await sm._s4_develop(dw)
    except Exception:
        # The post-_run_llm flow may raise once it hits the missing
        # findings JSON; we only care that _run_llm was invoked with the
        # ceiling, which happens before any of that.
        pass

    assert captured.get("step_tag") == "STEP4_DEVELOP"
    assert captured.get("timeout") == 3600, (
        f"Step4 must use step4_acpx_wall_ceiling_s (3600); "
        f"got timeout={captured.get('timeout')!r}"
    )


# ---------------------------------------------------------------------------
# Phase 9: session lifecycle (per-round plan/build/review)
# ---------------------------------------------------------------------------


async def test_s0_init_persists_session_anchor_path(env):
    """Phase 9: _s0_init must populate session_anchor_path on the dev_works row."""
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 90, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env), prompt="anchor",
    )
    # Drive _s0_init via tick(); anchor must be persisted regardless of
    # whether the rest of the SM runs to completion.
    await sm.tick(dw["id"])
    refreshed = await env["db"].fetchone(
        "SELECT session_anchor_path FROM dev_works WHERE id=?", (dw["id"],),
    )
    anchor = refreshed["session_anchor_path"]
    assert anchor, "session_anchor_path must be populated by _s0_init"
    expected_tail = Path("devworks") / dw["id"]
    assert Path(anchor).match(f"*/{expected_tail.as_posix()}") or \
        Path(anchor).parts[-2:] == ("devworks", dw["id"]), (
            f"anchor should end at devworks/<dev_id>; got {anchor!r}"
        )


async def test_round_uses_three_session_names(env):
    """Phase 9: Step4 starts from a cold build session after Step3."""
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 92, "issues": [], "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env), prompt="phase 9 sessions",
    )
    await sm.run_to_completion(dw["id"])
    dev_id = dw["id"]
    expected = [
        f"dw-{dev_id}-r1-plan",
        f"dw-{dev_id}-r1-build",
        f"dw-{dev_id}-r1-build",
        f"dw-{dev_id}-r1-review",
    ]
    assert sm.llm_runner.created_sessions == expected


async def test_round_transition_deletes_prior_round_sessions(env):
    """Phase 9: round 1 sessions are torn down before round 2 opens its own."""
    script = [
        # round 1 fails req_gap → loop
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 50, "issues": [],
                       "problem_category": "req_gap"}),
        # round 2 passes
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 92, "issues": [],
                       "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env), prompt="round-transition",
    )
    await sm.run_to_completion(dw["id"])
    deleted = sm.llm_runner.deleted_sessions
    dev_id = dw["id"]
    # All three r1 sessions must appear in the delete log; same for r2.
    for role in ("plan", "build", "review"):
        assert f"dw-{dev_id}-r1-{role}" in deleted
        assert f"dw-{dev_id}-r2-{role}" in deleted
    # Strict ordering: every r1 delete precedes every r2 delete.
    r1_indices = [
        deleted.index(f"dw-{dev_id}-r1-{role}")
        for role in ("plan", "build", "review")
    ]
    r2_indices = [
        deleted.index(f"dw-{dev_id}-r2-{role}")
        for role in ("plan", "build", "review")
    ]
    assert max(r1_indices) < min(r2_indices)


async def test_terminal_completed_deletes_all_sessions(env):
    """Phase 9: COMPLETED branch leaves zero live sessions."""
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 92, "issues": [],
                       "problem_category": None}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script))
    dw = await sm.create(
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env), prompt="completed",
    )
    await sm.run_to_completion(dw["id"])
    dev_id = dw["id"]
    deleted = sm.llm_runner.deleted_sessions
    for role in ("plan", "build", "review"):
        assert f"dw-{dev_id}-r1-{role}" in deleted
    # Cache is empty after COMPLETED.
    assert sm._active_sessions.get(dev_id, {}) == {}


async def test_terminal_escalated_deletes_all_sessions(env):
    """Phase 9: ESCALATED branch leaves zero live sessions.

    Force escalation by configuring max_rounds=1 and feeding a req_gap
    scoring outcome — the second round attempt trips _escalate.
    """
    script = [
        step2_append_h2, step3_write_ctx, step4_write_findings,
        _step5_writer({"score": 30, "issues": [],
                       "problem_category": "req_gap"}),
    ]
    sm = _make_sm(env, ScriptedExecutor(script), cfg=_build_config(max_rounds=1))
    dw = await sm.create(
        workspace_id=env["ws"]["id"], design_doc_id=env["dd"]["id"],
        repo_refs=_refs_arg(env), prompt="escalated",
    )
    await sm.run_to_completion(dw["id"])
    final = await env["db"].fetchone(
        "SELECT current_step FROM dev_works WHERE id=?", (dw["id"],),
    )
    assert final["current_step"] == "ESCALATED"
    dev_id = dw["id"]
    deleted = sm.llm_runner.deleted_sessions
    # All three r1 sessions are torn down even on the escalate path.
    for role in ("plan", "build", "review"):
        assert f"dw-{dev_id}-r1-{role}" in deleted
    assert sm._active_sessions.get(dev_id, {}) == {}

