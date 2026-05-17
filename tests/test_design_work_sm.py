"""Phase 3: DesignWorkStateMachine end-to-end tests (no real LLM)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.database import Database
from src.agent_hosts.repo import AgentHostRepo
from src.design_doc_manager import DesignDocManager
from src.design_work_sm import DesignWorkStateMachine
from src.exceptions import BadRequestError
from src.models import DesignWorkMode
from src.storage import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
from src.workspace_manager import WorkspaceManager

FIXTURES = Path(__file__).parent / "fixtures" / "design"


class StubExecutor:
    """Replay fixture markdown by copying into the output path baked into
    the prompt file. Each call advances the round counter.
    """

    _OUTPUT_RE = re.compile(r"[A-Za-z]?:?[\\/][^\s`]*\.md")

    def __init__(self, scenario_dir: Path):
        self.scenario_dir = scenario_dir
        self.call_count = 0

    async def run_once(
        self, agent_type, worktree, timeout_sec, task_file=None, prompt=None,
        **_kwargs,
    ):
        self.call_count += 1
        prompt_text = Path(task_file).read_text(encoding="utf-8")
        output_path = None
        for line in prompt_text.splitlines():
            if line.strip().startswith("将最终 markdown 写入"):
                # The next thing on this line is `\`$output_path\`` — extract
                # anything between backticks.
                m = re.search(r"`([^`]+\.md)`", line)
                if m:
                    output_path = m.group(1)
                    break
        if output_path is None:
            m = self._OUTPUT_RE.search(prompt_text)
            if m:
                output_path = m.group(0)
        if output_path is None:
            return ("", 1)
        fixture = self.scenario_dir / f"round{self.call_count}.md"
        if not fixture.exists():
            return ("", 1)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(
            fixture.read_bytes()  # preserve LF; validator reads utf-8
        )
        return ("ok", 0)


def _build_config(max_loops=3, default_threshold=80):
    return SimpleNamespace(
        design=SimpleNamespace(
            max_loops=max_loops,
            execution_timeout=30,
            required_sections=[
                "问题与目标", "用户故事", "场景案例", "范围与非目标",
                "详细操作流程", "验收标准", "技术约束与集成边界",
                "交付切片", "决策记录", "打分 rubric",
            ],
            mockup_sections=["页面结构"],
            allow_optimize_mode=False,
        ),
        scoring=SimpleNamespace(default_threshold=default_threshold),
    )


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
    yield dict(
        db=db, wm=wm, ws=ws, ddm=ddm, registry=registry, root=tmp_path,
    )
    await db.close()


async def test_happy_path_new(env):
    stub = StubExecutor(FIXTURES / "perfect")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="demo",
        user_input="make it simple and clean, please",
        mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_state"] == "COMPLETED"
    assert stub.call_count == 1
    target = env["root"] / "ws" / "t" / "designs" / "DES-demo-1.0.0.md"
    assert target.exists()
    row = await env["db"].fetchone(
        "SELECT * FROM design_docs WHERE slug='demo'"
    )
    assert row["status"] == "published"
    assert row["rubric_threshold"] == 85  # from fixture front-matter
    ev = await env["db"].fetchone(
        "SELECT * FROM workspace_events "
        "WHERE event_name='design_work.completed' AND correlation_id=?",
        (dw["id"],),
    )
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["design_doc_id"] == row["id"]
    assert payload["slug"] == "demo"


async def test_create_uses_requested_agent_when_configured(env):
    stub = StubExecutor(FIXTURES / "perfect")
    host_repo = AgentHostRepo(env["db"])
    await host_repo.upsert(id="local", host="local", agent_type="codex")
    await host_repo.update_health("local", status="healthy")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
        agent_host_repo=host_repo,
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="codex",
        user_input="make it simple and clean, please",
        mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="codex",
    )
    assert dw["agent"] == "codex"
    assert dw["agent_host_id"] == "local"


async def test_create_falls_back_to_configured_agent_when_requested_unavailable(env):
    stub = StubExecutor(FIXTURES / "perfect")
    host_repo = AgentHostRepo(env["db"])
    await host_repo.upsert(id="local", host="local", agent_type="codex")
    await host_repo.update_health("local", status="healthy")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
        agent_host_repo=host_repo,
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="fallback",
        user_input="make it simple and clean, please",
        mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
    )
    assert dw["agent"] == "codex"
    assert dw["agent_host_id"] == "local"


async def test_create_omitted_agent_uses_configured_agent(env):
    stub = StubExecutor(FIXTURES / "perfect")
    host_repo = AgentHostRepo(env["db"])
    await host_repo.upsert(id="local", host="local", agent_type="codex")
    await host_repo.update_health("local", status="healthy")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
        agent_host_repo=host_repo,
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="auto",
        user_input="make it simple and clean, please",
        mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent=None,
    )
    assert dw["agent"] == "codex"
    assert dw["agent_host_id"] == "local"


async def test_rubric_api_override_wins(env):
    stub = StubExecutor(FIXTURES / "perfect")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="demo-x",
        user_input="please override rubric threshold",
        mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
        rubric_threshold=95,
    )
    await sm.run_to_completion(dw["id"])
    row = await env["db"].fetchone(
        "SELECT * FROM design_docs WHERE slug='demo-x'"
    )
    assert row["rubric_threshold"] == 95


async def test_missing_then_fixed_next_round(env):
    stub = StubExecutor(FIXTURES / "missing_then_fixed")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="demo2",
        user_input="x" * 50, mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_state"] == "COMPLETED"
    assert stub.call_count == 2


async def test_validation_errors_are_fed_into_next_prompt(env):
    scenario_dir = env["root"] / "format_error_then_fixed"
    scenario_dir.mkdir()
    perfect = (FIXTURES / "perfect" / "round1.md").read_text(encoding="utf-8")
    invalid = perfect.replace("- [ ] AC-", "- AC-")
    (scenario_dir / "round1.md").write_text(invalid, encoding="utf-8")
    (scenario_dir / "round2.md").write_text(perfect, encoding="utf-8")
    stub = StubExecutor(scenario_dir)
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="format-feedback",
        user_input="x" * 50, mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_state"] == "COMPLETED"
    prompt = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"designs/.drafts/{dw['id']}-prompt-loop1.md",
    )
    assert "validation_error:" in prompt
    assert "AC-xx" in prompt


async def test_prompt_includes_uploaded_attachments(env):
    await env["registry"].put_markdown(
        workspace_row=env["ws"],
        relative_path="attachments/brief.md",
        text="# Product brief\n\nInclude enterprise audit trails.",
        kind="attachment",
    )
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=StubExecutor(FIXTURES / "perfect"),
        config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T",
        sub_slug="with-attachment", user_input="x" * 50,
        mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
        attachment_paths=["attachments/brief.md"],
    )
    for _ in range(4):
        dw = await sm.tick(dw["id"])
    assert dw["current_state"] == "LLM_GENERATE"
    prompt = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"designs/.drafts/{dw['id']}-prompt-loop0.md",
    )
    assert "## Supplemental Materials" in prompt
    assert "attachments/brief.md" in prompt
    assert "Include enterprise audit trails." in prompt


async def test_prompt_includes_original_file_attachment_path(env):
    await env["registry"].put_bytes(
        workspace_row=env["ws"],
        relative_path="attachments/requirements.pdf",
        data=b"%PDF original bytes",
        kind="attachment",
    )
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=StubExecutor(FIXTURES / "perfect"),
        config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T",
        sub_slug="with-pdf", user_input="x" * 50,
        mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
        attachment_paths=["attachments/requirements.pdf"],
    )
    for _ in range(4):
        dw = await sm.tick(dw["id"])
    assert dw["current_state"] == "LLM_GENERATE"
    prompt = await env["registry"].read_text(
        workspace_slug=env["ws"]["slug"],
        relative_path=f"designs/.drafts/{dw['id']}-prompt-loop0.md",
    )
    assert "Original file attachment preserved for reference." in prompt
    assert "attachments/requirements.pdf" in prompt
    assert ".pdf`" in prompt


async def test_recovers_existing_output_for_current_prompt(env):
    stub = StubExecutor(FIXTURES / "perfect")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="Recovered",
        sub_slug="recovered", user_input="x" * 50,
        mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
    )
    for _ in range(4):
        dw = await sm.tick(dw["id"])
    assert dw["current_state"] == "LLM_GENERATE"

    await env["registry"].put_markdown(
        workspace_row=env["ws"],
        relative_path=dw["output_path"],
        text=(FIXTURES / "perfect" / "round1.md").read_text(encoding="utf-8"),
        kind="design_doc",
    )

    final = await sm.run_to_completion(dw["id"])
    assert final["current_state"] == "COMPLETED"
    assert stub.call_count == 0
    ev = await env["db"].fetchone(
        "SELECT * FROM workspace_events "
        "WHERE event_name='design_work.llm_completed' AND correlation_id=?",
        (dw["id"],),
    )
    payload = json.loads(ev["payload_json"])
    assert payload["recovered"] is True


async def test_llm_executor_failure_escalates_without_content_loop(env):
    class FailingExecutor:
        def __init__(self):
            self.call_count = 0

        async def run_once(self, *_args, **_kwargs):
            self.call_count += 1
            return ("failed", 1)

    stub = FailingExecutor()
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(max_loops=3),
        registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="exec-fail",
        user_input="x" * 50, mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
    )

    final = await sm.run_to_completion(dw["id"])

    assert final["current_state"] == "ESCALATED"
    assert final["loop"] == 0
    assert stub.call_count == 1
    assert final["escalation_reason"] == "LLM call failed rc=1"
    round_ev = await env["db"].fetchone(
        "SELECT * FROM workspace_events "
        "WHERE event_name='design_work.round_completed' AND correlation_id=?",
        (dw["id"],),
    )
    assert round_ev is None


async def test_escalate_on_max_loops(env):
    stub = StubExecutor(FIXTURES / "always_missing")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(max_loops=3),
        registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="demo3",
        user_input="x" * 50, mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_state"] == "ESCALATED"
    row = await env["db"].fetchone(
        "SELECT * FROM design_works WHERE id=?", (dw["id"],)
    )
    assert row["escalation_reason"] == "post-validate failed"
    ev = await env["db"].fetchone(
        "SELECT * FROM workspace_events WHERE event_name='design_work.escalated' "
        "AND correlation_id=?",
        (dw["id"],),
    )
    assert ev is not None
    payload = json.loads(ev["payload_json"])
    assert payload["reason"] == "post-validate failed"
    assert payload["missing_sections"]


async def test_create_max_loops_override_wins_over_config(env):
    stub = StubExecutor(FIXTURES / "always_missing")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(max_loops=3),
        registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="demo-override",
        user_input="x" * 50, mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude", max_loops=1,
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_state"] == "ESCALATED"
    assert stub.call_count == 2
    row = await env["db"].fetchone(
        "SELECT gates_json FROM design_works WHERE id=?", (dw["id"],)
    )
    assert json.loads(row["gates_json"])["max_loops_override"] == 1


async def test_create_rejects_max_loops_override_above_config(env):
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=StubExecutor(FIXTURES / "perfect"),
        config=_build_config(max_loops=1), registry=env["registry"],
    )
    with pytest.raises(BadRequestError, match="exceeds configured cap"):
        await sm.create(
            workspace_id=env["ws"]["id"], title="T",
            sub_slug="demo-too-many-loops", user_input="x" * 50,
            mode=DesignWorkMode.new, parent_version=None,
            needs_frontend_mockup=False, agent="claude", max_loops=2,
        )


async def test_optimize_mode_stubbed(env):
    stub = StubExecutor(FIXTURES / "perfect")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="demo4",
        user_input="x" * 50, mode=DesignWorkMode.optimize,
        parent_version="1.0.0",
        needs_frontend_mockup=False, agent="claude",
    )
    with pytest.raises(NotImplementedError):
        await sm.run_to_completion(dw["id"])
    row = await env["db"].fetchone(
        "SELECT * FROM design_works WHERE id=?", (dw["id"],)
    )
    assert row["current_state"] == "ESCALATED"


async def test_mockup_full_loop(env):
    stub = StubExecutor(FIXTURES / "mockup_with_link")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="demo5",
        user_input="x" * 50, mode=DesignWorkMode.new,
        parent_version=None, needs_frontend_mockup=True, agent="claude",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_state"] == "COMPLETED"
    md = (
        env["root"] / "ws" / "t" / "designs" / "DES-demo5-1.0.0.md"
    ).read_text(encoding="utf-8")
    assert "页面结构" in md
    assert "设计图链接或路径" in md
    # mockup_recorded event emitted
    ev = await env["db"].fetchone(
        "SELECT * FROM workspace_events "
        "WHERE event_name='design_work.mockup_recorded' AND correlation_id=?",
        (dw["id"],),
    )
    assert ev is not None


async def test_pre_validate_rejects_too_short_input(env):
    stub = StubExecutor(FIXTURES / "perfect")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="too-short",
        user_input="hi",  # 2 chars < 10 min
        mode=DesignWorkMode.new, parent_version=None,
        needs_frontend_mockup=False, agent="claude",
    )
    final = await sm.run_to_completion(dw["id"])
    assert final["current_state"] == "ESCALATED"
    assert stub.call_count == 0  # never reached the LLM


async def test_cancel(env):
    stub = StubExecutor(FIXTURES / "perfect")
    sm = DesignWorkStateMachine(
        db=env["db"], workspaces=env["wm"], design_docs=env["ddm"],
        executor=stub, config=_build_config(), registry=env["registry"],
    )
    dw = await sm.create(
        workspace_id=env["ws"]["id"], title="T", sub_slug="cancel-me",
        user_input="x" * 50, mode=DesignWorkMode.new,
        parent_version=None, needs_frontend_mockup=False, agent="claude",
    )
    await sm.cancel(dw["id"])
    row = await env["db"].fetchone(
        "SELECT * FROM design_works WHERE id=?", (dw["id"],)
    )
    assert row["current_state"] == "CANCELLED"
