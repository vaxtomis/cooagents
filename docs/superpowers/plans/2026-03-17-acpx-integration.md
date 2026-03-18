# ACPX Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct CLI subprocess execution with acpx-managed sessions, adding multi-turn revision loops within RUNNING stages.

**Architecture:** Introduce `AcpxExecutor` as a drop-in replacement for `AgentExecutor`, managing named sessions via the `acpx` CLI. The state machine's `_tick_*_running()` handlers gain evaluator + followup logic for multi-turn revision cycles. A new `turns` table tracks per-turn history.

**Tech Stack:** Python 3.12, asyncio subprocess, acpx CLI, Jinja2 templates, SQLite (aiosqlite), FastAPI, pydantic

**Spec:** `docs/superpowers/specs/2026-03-17-acpx-integration-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/acpx_executor.py` | Session lifecycle, command building, NDJSON parsing, exit code mapping |
| Modify | `src/models.py` | Add `TurnResponse`, extend `JobResponse` with session/turn fields |
| Modify | `src/config.py` | Add `AcpxConfig`, `TurnsConfig` pydantic models |
| Modify | `config/settings.yaml` | Add `acpx` and `turns` config sections |
| Modify | `db/schema.sql` | Extend `jobs` table, create `turns` table |
| Modify | `src/job_manager.py` | Add `session_name`, `turn_count`, `events_file` fields + turn CRUD |
| Modify | `src/artifact_manager.py` | Upgrade `render_task()` to Jinja2 |
| Modify | `src/state_machine.py` | Add evaluators + multi-turn tick logic |
| Modify | `src/host_manager.py` | Health check: `which claude/codex` → `acpx --version` |
| Modify | `src/git_utils.py` | Remove `stash_save()`, `stash_pop()` |
| Modify | `src/webhook_notifier.py` | (no code change needed — events are strings, just document new types) |
| Rename | `templates/TASK-claude.md` → `templates/INIT-design.md` | Initial design prompt (remove JSON output format) |
| Rename | `templates/TASK-codex.md` → `templates/INIT-dev.md` | Initial dev prompt (remove JSON output format) |
| Create | `templates/TURN-revision.md` | Auto-evaluation revision prompt (Jinja2) |
| Create | `templates/TURN-dev-fix.md` | Test failure fix prompt (Jinja2) |
| Rename | `templates/TASK-claude-revision.md` → `templates/GATE-revision.md` | Human rejection revision prompt (Jinja2) |
| Rename | `templates/TASK-resume.md` → `templates/RESUME.md` | Crash recovery prompt |
| Modify | `src/app.py` | Wire `AcpxExecutor` instead of `AgentExecutor` |
| Create | `tests/test_acpx_executor.py` | Unit tests for AcpxExecutor |
| Modify | `tests/test_state_machine.py` | Add multi-turn tick tests |
| Modify | `tests/test_e2e.py` | Update mocks from `dispatch()` to `start_session()` |
| Modify | `requirements.txt` | Add `jinja2>=3.1` |

---

### Task 1: Database Schema & Pydantic Models

**Files:**
- Modify: `db/schema.sql:93-109` (extend jobs table, add turns table)
- Modify: `src/models.py:174-183` (extend JobResponse, add TurnResponse)

- [ ] **Step 1: Extend `db/schema.sql`**

Add three columns to the `jobs` table definition and create the `turns` table:

```sql
-- In the jobs CREATE TABLE, add after `resume_count`:
  session_name   TEXT,
  turn_count     INTEGER DEFAULT 1,
  events_file    TEXT,

-- New table after merge_queue
-- 10. turns — per-turn history within a job
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    turn_num    INTEGER NOT NULL,
    prompt_file TEXT,
    verdict     TEXT,
    detail      TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    UNIQUE(job_id, turn_num)
);

CREATE INDEX IF NOT EXISTS idx_turns_job ON turns(job_id);
```

- [ ] **Step 2: Add `TurnResponse` and extend `JobResponse` in `src/models.py`**

After `JobResponse`:

```python
class TurnResponse(BaseModel):
    turn_num: int
    verdict: str | None = None
    detail: str | None = None
    started_at: str
    ended_at: str | None = None
```

Extend `JobResponse`:

```python
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
```

- [ ] **Step 3: Verify schema loads correctly**

Run: `python -c "import asyncio; from src.database import Database; db=Database(':memory:','db/schema.sql'); asyncio.run(db.connect()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add db/schema.sql src/models.py
git commit -m "feat: extend schema with turns table and session tracking fields"
```

---

### Task 2: Configuration Extension

**Files:**
- Modify: `src/config.py:1-71` (add AcpxConfig, TurnsConfig)
- Modify: `config/settings.yaml` (add acpx + turns sections)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test for new config sections**

Add to `tests/test_config.py`:

```python
def test_acpx_config_defaults():
    from src.config import Settings
    s = Settings()
    assert s.acpx.permission_mode == "approve-all"
    assert s.acpx.default_format == "json"
    assert s.acpx.ttl == 600

def test_turns_config_defaults():
    from src.config import Settings
    s = Settings()
    assert s.turns.design_max_turns == 3
    assert s.turns.dev_max_turns == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v -k "acpx or turns"`
Expected: FAIL (AttributeError: 'Settings' object has no attribute 'acpx')

- [ ] **Step 3: Add config models to `src/config.py`**

After `MergeConfig`, add:

```python
class AcpxConfig(BaseModel):
    permission_mode: str = "approve-all"
    default_format: str = "json"
    ttl: int = 600

class TurnsConfig(BaseModel):
    design_max_turns: int = 3
    dev_max_turns: int = 5
```

Extend `Settings`:

```python
class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    timeouts: TimeoutConfig = TimeoutConfig()
    health_check: HealthCheckConfig = HealthCheckConfig()
    merge: MergeConfig = MergeConfig()
    acpx: AcpxConfig = AcpxConfig()
    turns: TurnsConfig = TurnsConfig()
```

- [ ] **Step 4: Update `config/settings.yaml`**

Append:

```yaml
acpx:
  permission_mode: "approve-all"
  default_format: "json"
  ttl: 600

turns:
  design_max_turns: 3
  dev_max_turns: 5
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/config.py config/settings.yaml tests/test_config.py
git commit -m "feat: add acpx and turns configuration sections"
```

---

### Task 3: JobManager Turn Tracking

**Files:**
- Modify: `src/job_manager.py:1-49` (add session/turn fields + methods)
- Modify: `tests/test_job_manager.py`

- [ ] **Step 1: Write failing tests for new JobManager methods**

Add to `tests/test_job_manager.py`:

```python
async def test_create_job_with_session(db, tmp_path):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r1", "T-1", "/repo", "running", "INIT", now, now)
    )
    job_id = await jm.create_job(
        "r1", "h1", "claude", "DESIGN_DISPATCHED", "/task.md", "/wt", "abc123", 1800,
        session_name="run-r1-design"
    )
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["session_name"] == "run-r1-design"
    assert job["turn_count"] == 1

async def test_increment_turn(db, tmp_path):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r1", "T-1", "/repo", "running", "INIT", now, now)
    )
    job_id = await jm.create_job("r1", "h1", "claude", "DESIGN", "/t.md", "/wt", "abc", 1800)
    new_turn = await jm.increment_turn(job_id)
    assert new_turn == 2
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["turn_count"] == 2

async def test_record_and_get_turns(db, tmp_path):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r1", "T-1", "/repo", "running", "INIT", now, now)
    )
    job_id = await jm.create_job("r1", "h1", "claude", "DESIGN", "/t.md", "/wt", "abc", 1800)
    await jm.record_turn(job_id, 1, "/t.md", "revise", "missing ADR")
    await jm.record_turn(job_id, 2, "/rev.md", "accept", "")
    turns = await jm.get_turns(job_id)
    assert len(turns) == 2
    assert turns[0]["verdict"] == "revise"
    assert turns[1]["verdict"] == "accept"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_job_manager.py -v -k "session or turn"`
Expected: FAIL

- [ ] **Step 3: Implement new JobManager methods**

Update `create_job()` to accept optional `session_name`:

```python
async def create_job(self, run_id, host_id, agent_type, stage, task_file, worktree, base_commit, timeout_sec, session_name=None) -> str:
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    await self.db.execute(
        """INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,base_commit,session_name,started_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (job_id, run_id, host_id, agent_type, stage, "starting", task_file, worktree, base_commit, session_name, now)
    )
    return job_id
```

Add new methods:

```python
async def increment_turn(self, job_id) -> int:
    await self.db.execute(
        "UPDATE jobs SET turn_count = turn_count + 1 WHERE id=?", (job_id,)
    )
    job = await self.db.fetchone("SELECT turn_count FROM jobs WHERE id=?", (job_id,))
    return job["turn_count"]

async def record_turn(self, job_id, turn_num, prompt_file, verdict, detail):
    now = datetime.now(timezone.utc).isoformat()
    await self.db.execute(
        """INSERT INTO turns(job_id, turn_num, prompt_file, verdict, detail, started_at)
           VALUES(?,?,?,?,?,?)""",
        (job_id, turn_num, prompt_file, verdict, detail, now)
    )

async def get_turns(self, job_id) -> list[dict]:
    rows = await self.db.fetchall(
        "SELECT * FROM turns WHERE job_id=? ORDER BY turn_num", (job_id,)
    )
    return [dict(r) for r in rows]
```

Update `get_output()` to also check `events.jsonl`:

```python
async def get_output(self, job_id):
    from pathlib import Path
    events_path = Path(".coop") / "jobs" / job_id / "events.jsonl"
    if events_path.exists():
        return events_path.read_text(encoding="utf-8")
    log_path = Path(".coop") / "jobs" / job_id / "stdout.log"
    if log_path.exists():
        return log_path.read_text(encoding="utf-8")
    return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_job_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/job_manager.py tests/test_job_manager.py
git commit -m "feat: add session name and turn tracking to JobManager"
```

---

### Task 4: Jinja2 Template Rendering + Template Refactoring

**Files:**
- Modify: `src/artifact_manager.py:132-139` (upgrade render_task to Jinja2)
- Modify: `requirements.txt` (add jinja2)
- Create: `templates/INIT-design.md`
- Create: `templates/INIT-dev.md`
- Create: `templates/TURN-revision.md`
- Create: `templates/TURN-dev-fix.md`
- Rename: `templates/TASK-claude-revision.md` → `templates/GATE-revision.md`
- Rename: `templates/TASK-resume.md` → `templates/RESUME.md`
- Delete: `templates/TASK-codex-revision.md` (merged into GATE-revision.md)
- Keep: `templates/TASK-claude.md`, `templates/TASK-codex.md` (backward compat until app.py is updated)

- [ ] **Step 1: Add jinja2 to requirements.txt**

Append `jinja2>=3.1` to `requirements.txt`.

- [ ] **Step 2: Install dependencies**

Run: `pip install jinja2>=3.1`

- [ ] **Step 3: Write failing test for Jinja2 rendering**

Add to `tests/test_artifact_manager.py`:

```python
async def test_render_task_jinja2(db, tmp_path):
    am = ArtifactManager(db)
    template = tmp_path / "template.md"
    template.write_text("# Task for {{ ticket }}\n{% if feedback %}Feedback: {{ feedback }}{% endif %}")
    output = tmp_path / "out.md"
    await am.render_task(str(template), {"ticket": "T-1", "feedback": "needs ADR"}, str(output))
    content = output.read_text()
    assert "Task for T-1" in content
    assert "Feedback: needs ADR" in content
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_artifact_manager.py -v -k "jinja2"`
Expected: FAIL (Jinja2 conditionals won't work with simple string replace)

- [ ] **Step 5: Upgrade `render_task()` in `src/artifact_manager.py`**

Replace the `render_task` method:

```python
async def render_task(self, template_path, variables: dict, output_path) -> str:
    """Render a task template with Jinja2."""
    from jinja2 import Environment, FileSystemLoader, BaseLoader
    from pathlib import Path

    template_file = Path(template_path)
    env = Environment(
        loader=FileSystemLoader(str(template_file.parent)),
        keep_trailing_newline=True,
    )
    template = env.get_template(template_file.name)
    content = template.render(**variables)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(content, encoding="utf-8")
    return output_path
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_artifact_manager.py -v`
Expected: PASS

- [ ] **Step 7: Create new template files**

Create `templates/INIT-design.md` (from TASK-claude.md, remove JSON output format, use relative paths):

```markdown
# 任务单（设计阶段）

## 基本信息
- ticket: {{ ticket }}

## 输入资料（必须先阅读）
1. {{ req_path }}
2. docs/design/DES-template.md
3. docs/design/ADR-template.md

## 你的目标
基于需求文档完成功能设计，明确架构、接口、数据结构、异常处理、测试策略与发布回滚。

## 输出要求
1. 设计文档：`docs/design/DES-{{ ticket }}.md`
2. 架构决策：`docs/design/ADR-{{ ticket }}-*.md`（如有）
3. 设计说明应可直接指导开发实现。

## 约束
- 不要直接改业务代码（本阶段只做设计）。
- 设计必须覆盖验收标准与边界条件。
- 输出使用 Markdown。

## 完成判定（DoD）
- 设计文档存在且结构完整。
- 至少覆盖：模块设计、接口设计、测试设计。
- 如有关键取舍，补充 ADR。
```

Create `templates/INIT-dev.md` (from TASK-codex.md, remove JSON output format):

```markdown
# 任务单（开发阶段）

## 基本信息
- ticket: {{ ticket }}

## 输入资料（必须先阅读）
1. {{ design_path }}
2. docs/dev/PLAN-template.md
3. docs/dev/TEST-REPORT-template.md

## 你的目标
根据设计文档完成编码、测试与结果记录，确保可回归与可提交。

## 输出要求
1. 代码改动（在当前 worktree）
2. 测试报告：`docs/dev/TEST-REPORT-{{ ticket }}.md`
3. 如有必要：开发计划 `docs/dev/PLAN-{{ ticket }}.md`

## 约束
- 必须先读设计文档再动代码。
- 关键变更需有测试或验证步骤。
- 输出使用 Markdown。

## 完成判定（DoD）
- 核心功能实现完成。
- 测试报告已生成，含 PASS/FAIL 结果。
- 代码可提交，且变更说明清晰。
```

Create `templates/TURN-revision.md`:

```markdown
# 修订指令（Turn {{ turn }}）

## 评估反馈
> {{ feedback }}

## 你的目标
根据上述反馈修订本阶段产出，确保满足完成判定。

{% if missing_artifacts %}
## 缺失制品
请补充以下文件：
{% for artifact in missing_artifacts %}
- {{ artifact }}
{% endfor %}
{% endif %}

## 约束
- 回应所有反馈要点。
- 不要重复已有的正确内容。
- 保持输出格式与之前一致。
```

Create `templates/TURN-dev-fix.md`:

```markdown
# 开发修复指令（Turn {{ turn }}）

## 问题描述
> {{ feedback }}

## 你的目标
修复上述问题，确保测试通过并生成测试报告。

{% if test_failures %}
## 失败的测试
{% for failure in test_failures %}
- {{ failure }}
{% endfor %}
{% endif %}

## 约束
- 先复现问题再修复。
- 更新测试报告：`docs/dev/TEST-REPORT-{{ ticket }}.md`
- 确保所有测试通过。
```

Create `templates/GATE-revision.md` (from TASK-claude-revision.md, converted to Jinja2):

```markdown
# 审批修订指令

## 基本信息
- ticket: {{ ticket }}
- revision: v{{ revision_version }}

## 修订原因
{{ reject_reason }}

## 你的目标
根据审阅反馈修订产出，确保覆盖所有修改意见。

{% if agent_type == "claude" %}
## 输出要求
1. 更新后的设计文档：`docs/design/DES-{{ ticket }}.md`
2. 如有新的架构决策：`docs/design/ADR-{{ ticket }}-*.md`
{% else %}
## 输出要求
1. 代码改动（在当前 worktree）
2. 更新测试报告：`docs/dev/TEST-REPORT-{{ ticket }}.md`
{% endif %}

## 约束
- 修订必须回应所有审阅意见。
- 保持与原始任务要求一致的输出格式。
```

Create `templates/RESUME.md` (from TASK-resume.md, converted to Jinja2):

```markdown
# 中断恢复

## 基本信息
- ticket: {{ ticket }}
- 恢复次数: {{ resume_count }}

## 中断原因
{{ interrupt_reason }}

## 已完成工作
{% if commits_made %}
### 提交记录
{{ commits_made }}
{% endif %}

{% if diff_stat %}
### 变更统计
{{ diff_stat }}
{% endif %}

## 原始任务
{{ original_task_content }}

## 你的目标
继续完成原始任务中未完成的部分。已有的提交记录和代码变更已保留。

## 约束
- 先检查当前代码状态再继续。
- 不要重复已完成的工作。
- 确保最终输出与原始任务要求一致。
```

- [ ] **Step 8: Delete old templates that are fully replaced**

Remove `templates/TASK-codex-revision.md` (functionality merged into GATE-revision.md).

Note: Keep `templates/TASK-claude.md` and `templates/TASK-codex.md` until `state_machine.py` is updated in Task 6 to use the new names. They will be deleted then.

- [ ] **Step 9: Add new webhook event templates to `templates/WEBHOOK-messages.yaml`**

Append:

```yaml
turn.started:
  message: "任务 {{ticket}} 第 {{turn_num}} 轮开始 ({{agent_type}})"
turn.completed:
  message: "任务 {{ticket}} 第 {{turn_num}} 轮完成: {{verdict}}"
session.created:
  message: "任务 {{ticket}} 会话创建: {{session_name}}"
session.closed:
  message: "任务 {{ticket}} 会话关闭: {{session_name}}"
```

- [ ] **Step 10: Commit**

```bash
git add requirements.txt src/artifact_manager.py templates/ tests/test_artifact_manager.py
git rm templates/TASK-codex-revision.md
git commit -m "feat: upgrade template engine to Jinja2 and refactor templates for acpx"
```

---

### Task 5: AcpxExecutor Core

**Files:**
- Create: `src/acpx_executor.py`
- Create: `tests/test_acpx_executor.py`

This is the largest task. The executor manages acpx session lifecycle.

- [ ] **Step 1: Write failing tests for command builders**

Create `tests/test_acpx_executor.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.database import Database
from src.job_manager import JobManager


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def executor(db, tmp_path):
    from src.acpx_executor import AcpxExecutor
    jm = JobManager(db)
    hm = AsyncMock()
    hm.increment_load = AsyncMock()
    hm.decrement_load = AsyncMock()
    am = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()

    ae = AcpxExecutor(db, jm, hm, am, wh, coop_dir=str(tmp_path / ".coop"))
    return ae


def test_build_prompt_cmd(executor):
    cmd = executor._build_acpx_prompt_cmd("claude", "run-abc-design", "/wt", 1800, "/task.md")
    assert cmd == [
        "acpx", "claude",
        "-s", "run-abc-design",
        "--cwd", "/wt",
        "--format", "json",
        "--approve-all",
        "--timeout", "1800",
        "--file", "/task.md",
    ]


def test_build_prompt_cmd_codex(executor):
    cmd = executor._build_acpx_prompt_cmd("codex", "run-abc-dev", "/wt", 3600)
    assert cmd == [
        "acpx", "codex",
        "-s", "run-abc-dev",
        "--cwd", "/wt",
        "--format", "json",
        "--approve-all",
        "--timeout", "3600",
    ]


def test_build_ensure_cmd(executor):
    cmd = executor._build_acpx_ensure_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "claude", "--cwd", "/wt", "sessions", "ensure", "--name", "run-abc-design"]


def test_build_cancel_cmd(executor):
    cmd = executor._build_acpx_cancel_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "claude", "cancel", "-s", "run-abc-design", "--cwd", "/wt"]


def test_build_close_cmd(executor):
    cmd = executor._build_acpx_close_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "claude", "--cwd", "/wt", "sessions", "close", "run-abc-design"]


def test_build_status_cmd(executor):
    cmd = executor._build_acpx_status_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "claude", "status", "-s", "run-abc-design", "--cwd", "/wt", "--format", "json"]


def test_session_name_generation(executor):
    name = executor._make_session_name("run-abc123", "design")
    assert name == "run-abc123-design"
    name2 = executor._make_session_name("run-abc123", "dev", revision=2)
    assert name2 == "run-abc123-dev-r2"


def test_exit_code_mapping(executor):
    assert executor._map_exit_code(0) == "completed"
    assert executor._map_exit_code(1) == "failed"
    assert executor._map_exit_code(2) == "failed"
    assert executor._map_exit_code(3) == "timeout"
    assert executor._map_exit_code(4) == "failed"
    assert executor._map_exit_code(5) == "failed"
    assert executor._map_exit_code(130) == "interrupted"
    assert executor._map_exit_code(99) == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_acpx_executor.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'src.acpx_executor')

- [ ] **Step 3: Implement `src/acpx_executor.py`**

```python
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone


# acpx exit code → JobStatus mapping
_EXIT_CODE_MAP = {
    0: "completed",
    1: "failed",
    2: "failed",
    3: "timeout",
    4: "failed",
    5: "failed",
    130: "interrupted",
}


class AcpxExecutor:
    def __init__(self, db, job_manager, host_manager, artifact_manager, webhook_notifier, config=None, coop_dir=".coop"):
        self.db = db
        self.jobs = job_manager
        self.hosts = host_manager
        self.artifacts = artifact_manager
        self.webhooks = webhook_notifier
        self.config = config
        self.coop_dir = coop_dir
        self._state_machine = None
        self._tasks = {}  # job_id → asyncio.Task

    def set_state_machine(self, sm):
        self._state_machine = sm

    # ------------------------------------------------------------------
    # Session name helpers
    # ------------------------------------------------------------------

    def _make_session_name(self, run_id, phase, revision=None):
        name = f"{run_id}-{phase}"
        if revision and revision > 1:
            name += f"-r{revision}"
        return name

    def _map_exit_code(self, rc):
        return _EXIT_CODE_MAP.get(rc, "failed")

    # ------------------------------------------------------------------
    # Command builders
    # ------------------------------------------------------------------

    def _build_acpx_prompt_cmd(self, agent_type, session_name, worktree, timeout_sec, task_file=None):
        agent = "claude" if agent_type == "claude" else "codex"
        cmd = [
            "acpx", agent,
            "-s", session_name,
            "--cwd", worktree,
            "--format", "json",
            "--approve-all",
            "--timeout", str(timeout_sec),
        ]
        if task_file:
            cmd += ["--file", task_file]
        return cmd

    def _build_acpx_ensure_cmd(self, agent_type, session_name, worktree):
        agent = "claude" if agent_type == "claude" else "codex"
        return ["acpx", agent, "--cwd", worktree, "sessions", "ensure", "--name", session_name]

    def _build_acpx_cancel_cmd(self, agent_type, session_name, worktree):
        agent = "claude" if agent_type == "claude" else "codex"
        return ["acpx", agent, "cancel", "-s", session_name, "--cwd", worktree]

    def _build_acpx_close_cmd(self, agent_type, session_name, worktree):
        agent = "claude" if agent_type == "claude" else "codex"
        return ["acpx", agent, "--cwd", worktree, "sessions", "close", session_name]

    def _build_acpx_status_cmd(self, agent_type, session_name, worktree):
        agent = "claude" if agent_type == "claude" else "codex"
        return ["acpx", agent, "status", "-s", session_name, "--cwd", worktree, "--format", "json"]

    # ------------------------------------------------------------------
    # Core session lifecycle
    # ------------------------------------------------------------------

    async def start_session(self, run_id, host, agent_type, task_file, worktree, timeout_sec, revision=None) -> str:
        """Create an acpx session and send the initial prompt. Returns job_id."""
        from src.git_utils import get_head_commit
        base_commit = await get_head_commit(worktree)

        run = await self.db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
        stage = run["current_stage"] if run else "UNKNOWN"
        phase = "design" if "DESIGN" in stage else "dev"
        session_name = self._make_session_name(run_id, phase, revision)

        job_id = await self.jobs.create_job(
            run_id, host["id"], agent_type, stage, task_file, worktree, base_commit, timeout_sec,
            session_name=session_name,
        )

        # Ensure session exists
        ensure_cmd = self._build_acpx_ensure_cmd(agent_type, session_name, worktree)
        if host["host"] == "local":
            await self._run_cmd(ensure_cmd, worktree)
        else:
            await self._run_ssh_cmd(host, ensure_cmd)

        # Send initial prompt
        prompt_cmd = self._build_acpx_prompt_cmd(agent_type, session_name, worktree, timeout_sec, task_file)

        await self.jobs.update_status(job_id, "running")
        await self.hosts.increment_load(host["id"])
        await self._emit_event(run_id, "session.created", {"session_name": session_name, "agent_type": agent_type})

        if host["host"] == "local":
            process = await self._start_local(prompt_cmd, worktree, job_id)
        else:
            process = await self._start_ssh(host, prompt_cmd, job_id)

        task = asyncio.create_task(self._watch(job_id, process, run_id, host["id"], session_name))
        self._tasks[job_id] = task

        return job_id

    async def send_followup(self, run_id, agent_type, prompt_file, worktree, timeout_sec) -> None:
        """Send a followup prompt to an existing session.

        Launches a background watcher task (non-blocking) that triggers
        state_machine.tick() on completion, just like start_session does.
        """
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job:
            raise RuntimeError(f"No job found for run {run_id}")

        session_name = job["session_name"]
        host_id = job["host_id"]
        host = await self.db.fetchone("SELECT * FROM agent_hosts WHERE id=?", (host_id,))

        prompt_cmd = self._build_acpx_prompt_cmd(agent_type, session_name, worktree, timeout_sec, prompt_file)

        if host and host["host"] == "local":
            process = await self._start_local(prompt_cmd, worktree, job["id"])
        else:
            process = await self._start_ssh(dict(host), prompt_cmd, job["id"])

        # Background watcher — triggers tick on completion
        task = asyncio.create_task(self._watch(job["id"], process, run_id, host_id, session_name))
        self._tasks[job["id"]] = task

    async def cancel_session(self, run_id, agent_type) -> None:
        """Cooperatively cancel the current prompt on the session."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return

        cancel_cmd = self._build_acpx_cancel_cmd(agent_type, job["session_name"], job["worktree"])
        try:
            await self._run_cmd(cancel_cmd, job["worktree"])
        except Exception:
            pass

        # Also cancel the asyncio task
        task = self._tasks.get(job["id"])
        if task:
            task.cancel()

        now = datetime.now(timezone.utc).isoformat()
        await self.jobs.update_status(job["id"], "cancelled", ended_at=now)

    async def close_session(self, run_id, agent_type) -> None:
        """Close the session and release resources."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return

        close_cmd = self._build_acpx_close_cmd(agent_type, job["session_name"], job["worktree"])
        try:
            await self._run_cmd(close_cmd, job["worktree"])
        except Exception:
            pass

        await self._emit_event(run_id, "session.closed", {"session_name": job["session_name"]})

    async def get_session_status(self, run_id, agent_type, host=None) -> dict | None:
        """Query acpx session status."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return None

        status_cmd = self._build_acpx_status_cmd(agent_type, job["session_name"], job["worktree"])

        try:
            if host and host["host"] != "local":
                stdout, _, _ = await self._run_ssh_cmd(host, status_cmd)
            else:
                stdout, _, _ = await self._run_cmd(status_cmd, job["worktree"])
            return json.loads(stdout)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def recover(self, run_id, action):
        """Recover an interrupted job."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job:
            return

        agent_type = job["agent_type"]

        if action == "resume":
            # Send RESUME.md to the same session
            resume_prompt = os.path.join(self.coop_dir, "runs", run_id, "RESUME.md")
            os.makedirs(os.path.dirname(resume_prompt), exist_ok=True)
            await self.artifacts.render_task(
                "templates/RESUME.md",
                {"run_id": run_id, "ticket": "", "resume_count": (job.get("resume_count") or 0) + 1,
                 "interrupt_reason": "process interrupted", "commits_made": "", "diff_stat": "",
                 "agent_output_tail": "", "original_task_content": ""},
                resume_prompt,
            )
            resume_count = (job.get("resume_count") or 0) + 1
            await self.db.execute("UPDATE jobs SET resume_count=? WHERE id=?", (resume_count, job["id"]))
            await self.send_followup(run_id, agent_type, resume_prompt, job["worktree"], 1800)

        elif action == "redo":
            await self.close_session(run_id, agent_type)
            from src.git_utils import run_git
            if job["worktree"] and job.get("base_commit"):
                await run_git("reset", "--hard", job["base_commit"], cwd=job["worktree"])

        elif action == "manual":
            await self.close_session(run_id, agent_type)

    async def restore_on_startup(self):
        """On startup, check acpx session status for stale jobs."""
        jobs = await self.db.fetchall(
            "SELECT * FROM jobs WHERE status IN ('starting','running')"
        )
        now = datetime.now(timezone.utc).isoformat()
        for job in jobs:
            j = dict(job)
            if j.get("session_name"):
                # Look up host for SSH routing
                host = None
                if j.get("host_id"):
                    host = await self.db.fetchone(
                        "SELECT * FROM agent_hosts WHERE id=?", (j["host_id"],)
                    )
                    if host:
                        host = dict(host)
                status = await self.get_session_status(j["run_id"], j["agent_type"], host=host)
                if status and status.get("status") == "running":
                    continue  # Still running, leave it
            await self.jobs.update_status(j["id"], "interrupted", ended_at=now)

    # ------------------------------------------------------------------
    # Process management (private)
    # ------------------------------------------------------------------

    async def _run_cmd(self, cmd, cwd):
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode().strip(), stderr.decode().strip(), proc.returncode

    async def _run_ssh_cmd(self, host, cmd):
        import asyncssh
        import shlex
        remote_cmd = " ".join(shlex.quote(c) for c in cmd)
        connect_args = {"host": host["host"], "known_hosts": None}
        if host.get("ssh_key"):
            connect_args["client_keys"] = [host["ssh_key"]]
        async with asyncssh.connect(**connect_args) as conn:
            result = await conn.run(remote_cmd)
            return result.stdout.strip(), result.stderr.strip(), result.returncode

    async def _start_local(self, cmd, worktree, job_id):
        log_dir = Path(self.coop_dir) / "jobs" / job_id
        log_dir.mkdir(parents=True, exist_ok=True)

        process = await asyncio.create_subprocess_exec(
            *cmd, cwd=worktree,
            stdout=asyncio.subprocess.PIPE,
            stderr=open(log_dir / "stderr.log", "w", encoding="utf-8"),
        )
        return process

    async def _start_ssh(self, host, cmd, job_id):
        import asyncssh
        import shlex
        remote_cmd = " ".join(shlex.quote(c) for c in cmd)
        connect_args = {"host": host["host"], "known_hosts": None}
        if host.get("ssh_key"):
            connect_args["client_keys"] = [host["ssh_key"]]
        conn = await asyncssh.connect(**connect_args)
        process = await conn.create_process(remote_cmd)
        return process

    async def _watch(self, job_id, process, run_id, host_id, session_name):
        """Watch the initial prompt process and parse NDJSON output."""
        try:
            log_dir = Path(self.coop_dir) / "jobs" / job_id
            log_dir.mkdir(parents=True, exist_ok=True)
            events_file = log_dir / "events.jsonl"

            await self._parse_ndjson_stream(process, job_id, events_file)
            await process.wait()
            rc = process.returncode
            now = datetime.now(timezone.utc).isoformat()
            status = self._map_exit_code(rc)

            await self.jobs.update_status(job_id, status, ended_at=now)
            await self.db.execute(
                "UPDATE jobs SET events_file=? WHERE id=?",
                (str(events_file), job_id),
            )

            if status == "completed":
                await self._emit_event(run_id, "job.completed", {"job_id": job_id})
                if self._state_machine:
                    await self._state_machine.tick(run_id)
            elif status == "interrupted":
                await self._emit_event(run_id, "job.interrupted", {"job_id": job_id, "reason": "signal"})
            else:
                await self._emit_event(run_id, "job.failed", {"job_id": job_id, "exit_code": rc})

        except asyncio.CancelledError:
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job_id, "cancelled", ended_at=now)
        except Exception as e:
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job_id, "failed", ended_at=now)
            await self._emit_event(run_id, "job.error", {"job_id": job_id, "error": str(e)})
        finally:
            await self.hosts.decrement_load(host_id)
            self._tasks.pop(job_id, None)

    async def _watch_followup(self, job_id, process, run_id, session_name):
        """Watch a followup prompt (blocking, no background task)."""
        log_dir = Path(self.coop_dir) / "jobs" / job_id
        log_dir.mkdir(parents=True, exist_ok=True)
        events_file = log_dir / "events.jsonl"

        await self._parse_ndjson_stream(process, job_id, events_file)
        await process.wait()
        rc = process.returncode
        status = self._map_exit_code(rc)

        if status != "completed":
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job_id, status, ended_at=now)

    async def _parse_ndjson_stream(self, process, job_id, events_file):
        """Parse NDJSON lines from process stdout, append to events file."""
        with open(events_file, "a", encoding="utf-8") as f:
            async for line in process.stdout:
                line = line.decode().strip() if isinstance(line, bytes) else line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    f.write(json.dumps(msg) + "\n")
                    f.flush()
                except json.JSONDecodeError:
                    f.write(json.dumps({"raw": line}) + "\n")
                    f.flush()

    async def _emit_event(self, run_id, event_type, payload):
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (run_id, event_type, json.dumps(payload), now),
        )
        if self.webhooks:
            await self.webhooks.notify(event_type, {"run_id": run_id, **payload})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_acpx_executor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/acpx_executor.py tests/test_acpx_executor.py
git commit -m "feat: implement AcpxExecutor with session lifecycle management"
```

---

### Task 6: State Machine Multi-Turn Logic

**Files:**
- Modify: `src/state_machine.py:49-67` (constructor: accept config for max_turns)
- Modify: `src/state_machine.py:271-380` (tick handlers for DESIGN/DEV QUEUED + RUNNING)
- Modify: `tests/test_state_machine.py`

- [ ] **Step 1: Write failing tests for evaluators and multi-turn**

First, update the `sm` fixture to pass `job_manager`:

```python
@pytest.fixture
async def sm(db, mocks, tmp_path):
    webhook, executor, host_mgr, merge_mgr = mocks
    am = ArtifactManager(db)
    jm = JobManager(db)  # ADD THIS

    async def _fake_ensure_worktree(repo_path, ticket, phase):
        branch = f"feat/{ticket}-{phase}"
        wt = str(tmp_path / f".worktrees/{ticket}-{phase}")
        return branch, wt

    am.render_task = AsyncMock(return_value="task-path")

    machine = StateMachine(
        db, am, host_mgr, executor, webhook, merge_mgr,
        str(tmp_path),
        ensure_worktree_fn=_fake_ensure_worktree,
        job_manager=jm,  # ADD THIS
    )
    return machine
```

Add the `JobManager` import at the top of the file:
```python
from src.job_manager import JobManager
```

Then add the new test functions:

```python
async def test_evaluate_design_accept(sm, db, tmp_path):
    """Evaluator accepts when both design and adr artifacts exist."""
    artifacts = [
        {"kind": "design", "path": "DES-T1.md"},
        {"kind": "adr", "path": "ADR-T1.md"},
    ]
    verdict, detail = sm._evaluate_design(artifacts)
    assert verdict == "accept"

async def test_evaluate_design_revise_missing_design(sm, db, tmp_path):
    """Evaluator requests revision when design doc is missing."""
    artifacts = [{"kind": "adr", "path": "ADR-T1.md"}]
    verdict, detail = sm._evaluate_design(artifacts)
    assert verdict == "revise"
    assert "设计文档" in detail or "design" in detail.lower()

async def test_evaluate_design_revise_missing_adr(sm, db, tmp_path):
    """Evaluator requests revision when ADR is missing."""
    artifacts = [{"kind": "design", "path": "DES-T1.md"}]
    verdict, detail = sm._evaluate_design(artifacts)
    assert verdict == "revise"
    assert "ADR" in detail

async def test_evaluate_dev_accept(sm, db, tmp_path):
    """Evaluator accepts when test report exists."""
    artifacts = [{"kind": "test-report", "path": "TEST-REPORT-T1.md"}]
    verdict, detail = sm._evaluate_dev(artifacts)
    assert verdict == "accept"

async def test_evaluate_dev_revise_missing_report(sm, db, tmp_path):
    """Evaluator requests revision when test report is missing."""
    artifacts = []
    verdict, detail = sm._evaluate_dev(artifacts)
    assert verdict == "revise"

async def test_tick_design_running_multi_turn_revise(sm, mocks, db, tmp_path):
    """Design running: evaluator returns revise → send_followup, stay in DESIGN_RUNNING."""
    _, executor, _, _ = mocks
    executor.send_followup = AsyncMock()
    executor.close_session = AsyncMock()

    run = await sm.create_run("T-MT", str(tmp_path))
    rid = run["run_id"]
    await sm.submit_requirement(rid, "# Req")
    await sm.approve(rid, "req", "user1")

    # Simulate job dispatched and running
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,turn_count,started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("job-mt1", rid, "local", "claude", "DESIGN_DISPATCHED", "completed", "/t.md", str(tmp_path), "run-mt-design", 1, now)
    )
    await db.execute("UPDATE runs SET current_stage='DESIGN_RUNNING' WHERE id=?", (rid,))

    # No design artifact → evaluator should say "revise"
    run = await sm.tick(rid)
    assert run["current_stage"] == "DESIGN_RUNNING"  # stays in RUNNING
    executor.send_followup.assert_called_once()

async def test_tick_design_running_multi_turn_accept(sm, mocks, db, tmp_path):
    """Design running: evaluator returns accept → advance to DESIGN_REVIEW."""
    _, executor, _, _ = mocks
    executor.close_session = AsyncMock()

    run = await sm.create_run("T-ACC", str(tmp_path))
    rid = run["run_id"]
    await sm.submit_requirement(rid, "# Req")
    await sm.approve(rid, "req", "user1")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,turn_count,started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("job-acc1", rid, "local", "claude", "DESIGN_DISPATCHED", "completed", "/t.md", str(tmp_path), "run-acc-design", 1, now)
    )
    await db.execute("UPDATE runs SET current_stage='DESIGN_RUNNING' WHERE id=?", (rid,))

    # Create design + ADR artifacts so evaluator accepts
    design_dir = tmp_path / "docs" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "DES-T-ACC.md").write_text("# Design")
    (design_dir / "ADR-T-ACC-001.md").write_text("# ADR")

    run = await sm.tick(rid)
    assert run["current_stage"] == "DESIGN_REVIEW"
    executor.close_session.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_state_machine.py -v -k "evaluate or multi_turn"`
Expected: FAIL (StateMachine has no _evaluate_design)

- [ ] **Step 3: Implement evaluators and multi-turn tick logic**

In `src/state_machine.py`, update `__init__` to accept `job_manager` and `config`:

```python
def __init__(self, db, artifact_manager, host_manager, agent_executor,
             webhook_notifier, merge_manager=None, coop_dir=".coop",
             ensure_worktree_fn=None, config=None, job_manager=None):
    # ... existing ...
    self._config = config
    self.jobs = job_manager
    # Max turns defaults
    self._design_max_turns = 3
    self._dev_max_turns = 5
    if config:
        self._design_max_turns = getattr(getattr(config, 'turns', None), 'design_max_turns', 3)
        self._dev_max_turns = getattr(getattr(config, 'turns', None), 'dev_max_turns', 5)
```

Note: `self.jobs` is needed for `increment_turn()` and `record_turn()` calls in the tick handlers. Callers (app.py, tests) must pass the `job_manager` parameter.

Add evaluator methods:

```python
def _evaluate_design(self, artifacts, job=None) -> tuple[str, str]:
    has_design = any(a["kind"] == "design" for a in artifacts)
    has_adr = any(a["kind"] == "adr" for a in artifacts)
    if not has_design:
        return ("revise", "未生成设计文档 DES-{ticket}.md")
    if not has_adr:
        return ("revise", "未生成架构决策记录 ADR-{ticket}.md")
    return ("accept", "")

def _evaluate_dev(self, artifacts, job=None, worktree=None) -> tuple[str, str]:
    has_test_report = any(a["kind"] == "test-report" for a in artifacts)
    if not has_test_report:
        return ("revise", "未生成测试报告 TEST-REPORT-{ticket}.md")
    return ("accept", "")
```

Replace `_tick_design_running`:

```python
async def _tick_design_running(self, run: dict) -> None:
    job = await self.db.fetchone(
        "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
        (run["id"],),
    )
    if not job or job["status"] != "completed":
        return

    turn = job.get("turn_count") or 1
    wt = run.get("design_worktree", "")

    await self.artifacts.scan_and_register(run["id"], run["ticket"], "DESIGN_RUNNING", wt)
    all_artifacts = await self.artifacts.get_by_run(run["id"])
    verdict, detail = self._evaluate_design(all_artifacts)

    if verdict == "accept" or turn >= self._design_max_turns:
        await self.artifacts.submit_all(run["id"], "DESIGN_RUNNING")
        if hasattr(self.executor, 'close_session'):
            await self.executor.close_session(run["id"], "claude")
        await self._update_stage(run["id"], "DESIGN_RUNNING", "DESIGN_REVIEW")
    elif verdict == "revise":
        revision_path = os.path.join(self.coop_dir, "runs", run["id"], f"TURN-revision-{turn+1}.md")
        os.makedirs(os.path.dirname(revision_path), exist_ok=True)
        await self.artifacts.render_task(
            "templates/TURN-revision.md",
            {"turn": turn + 1, "feedback": detail, "ticket": run["ticket"],
             "missing_artifacts": []},
            revision_path,
        )
        await self._emit(run["id"], "turn.completed", {"turn_num": turn, "verdict": verdict, "detail": detail})
        if hasattr(self.executor, 'send_followup'):
            await self._emit(run["id"], "turn.started", {"turn_num": turn + 1, "agent_type": "claude"})
            if self.jobs:
                await self.jobs.increment_turn(job["id"])
                await self.jobs.record_turn(job["id"], turn, revision_path, verdict, detail)
            # send_followup re-enters _watch which triggers tick on completion — non-blocking
            await self.executor.send_followup(run["id"], "claude", revision_path, wt, 1800)
```

Replace `_tick_dev_running`:

```python
async def _tick_dev_running(self, run: dict) -> None:
    job = await self.db.fetchone(
        "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
        (run["id"],),
    )
    if not job or job["status"] != "completed":
        return

    turn = job.get("turn_count") or 1
    wt = run.get("dev_worktree", "")

    await self.artifacts.scan_and_register(run["id"], run["ticket"], "DEV_RUNNING", wt)
    all_artifacts = await self.artifacts.get_by_run(run["id"])
    verdict, detail = self._evaluate_dev(all_artifacts)

    if verdict == "accept" or turn >= self._dev_max_turns:
        await self.artifacts.submit_all(run["id"], "DEV_RUNNING")
        if hasattr(self.executor, 'close_session'):
            await self.executor.close_session(run["id"], "codex")
        await self._update_stage(run["id"], "DEV_RUNNING", "DEV_REVIEW")
    elif verdict == "revise":
        revision_path = os.path.join(self.coop_dir, "runs", run["id"], f"TURN-dev-fix-{turn+1}.md")
        os.makedirs(os.path.dirname(revision_path), exist_ok=True)
        await self.artifacts.render_task(
            "templates/TURN-dev-fix.md",
            {"turn": turn + 1, "feedback": detail, "ticket": run["ticket"],
             "test_failures": []},
            revision_path,
        )
        await self._emit(run["id"], "turn.completed", {"turn_num": turn, "verdict": verdict, "detail": detail})
        if hasattr(self.executor, 'send_followup'):
            await self._emit(run["id"], "turn.started", {"turn_num": turn + 1, "agent_type": "codex"})
            if self.jobs:
                await self.jobs.increment_turn(job["id"])
                await self.jobs.record_turn(job["id"], turn, revision_path, verdict, detail)
            await self.executor.send_followup(run["id"], "codex", revision_path, wt, 3600)
```

Update `_tick_design_queued` and `_tick_dev_queued` to use new template names and `start_session` when available:

```python
async def _tick_design_queued(self, run: dict) -> None:
    host = await self.hosts.select_host("claude")
    if not host:
        return

    branch, wt = await self._resolve_worktree(run["repo_path"], run["ticket"], "design")

    now = datetime.now(timezone.utc).isoformat()
    await self.db.execute(
        "UPDATE runs SET design_worktree=?, design_branch=?, updated_at=? WHERE id=?",
        (wt, branch, now, run["id"]),
    )

    task_path = os.path.join(self.coop_dir, "runs", run["id"], "TASK-design.md")
    os.makedirs(os.path.dirname(task_path), exist_ok=True)

    template = "templates/INIT-design.md"
    if not Path(template).exists():
        template = "templates/TASK-claude.md"

    await self.artifacts.render_task(
        template,
        {
            "run_id": run["id"],
            "ticket": run["ticket"],
            "repo_path": run["repo_path"],
            "worktree": wt,
            "req_path": f"docs/req/REQ-{run['ticket']}.md",
        },
        task_path,
    )

    if hasattr(self.executor, 'start_session'):
        await self.executor.start_session(run["id"], host, "claude", task_path, wt, 1800)
    else:
        await self.executor.dispatch(run["id"], host, "claude", task_path, wt, 1800)
    await self._update_stage(run["id"], "DESIGN_QUEUED", "DESIGN_DISPATCHED")
```

Apply same pattern to `_tick_dev_queued`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state_machine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/state_machine.py tests/test_state_machine.py
git commit -m "feat: add multi-turn evaluation loop to state machine RUNNING stages"
```

---

### Task 7: Host Manager Health Check Update

**Files:**
- Modify: `src/host_manager.py:101-122` (health_check method)

- [ ] **Step 1: Update health check to use acpx**

In `src/host_manager.py`, replace the health_check method:

```python
async def health_check(self, host_id):
    host = await self.db.fetchone("SELECT * FROM agent_hosts WHERE id=?", (host_id,))
    if not host:
        return False
    if host["host"] == "local":
        import shutil
        has_acpx = shutil.which("acpx")
        if not has_acpx:
            # Fallback: check for direct CLI availability
            has_acpx = shutil.which("claude") or shutil.which("codex")
        status = "active" if has_acpx else "offline"
    else:
        try:
            import asyncssh
            async with asyncssh.connect(
                host["host"],
                known_hosts=None,
                client_keys=[host["ssh_key"]] if host.get("ssh_key") else None,
            ) as conn:
                result = await conn.run("acpx --version")
                status = "active" if result.returncode == 0 else "offline"
        except Exception:
            status = "offline"
    await self.set_status(host_id, status)
    return status == "active"
```

- [ ] **Step 2: Commit**

```bash
git add src/host_manager.py
git commit -m "feat: update host health check to detect acpx availability"
```

---

### Task 8: Wire AcpxExecutor into App

**Files:**
- Modify: `src/app.py:1-119` (replace AgentExecutor with AcpxExecutor)

- [ ] **Step 1: Update imports and instantiation in `src/app.py`**

Change:
```python
from src.agent_executor import AgentExecutor
```
To:
```python
from src.acpx_executor import AcpxExecutor
```

Change:
```python
executor = AgentExecutor(db, jobs, hosts, artifacts, webhooks, coop_dir=".coop")
```
To:
```python
executor = AcpxExecutor(db, jobs, hosts, artifacts, webhooks, config=settings, coop_dir=".coop")
```

- [ ] **Step 2: Pass config and job_manager to StateMachine**

Change:
```python
sm = StateMachine(db, artifacts, hosts, executor, webhooks, merger, coop_dir=".coop")
```
To:
```python
sm = StateMachine(db, artifacts, hosts, executor, webhooks, merger, coop_dir=".coop", config=settings, job_manager=jobs)
```

- [ ] **Step 3: Commit**

```bash
git add src/app.py
git commit -m "feat: wire AcpxExecutor as the default executor in app startup"
```

---

### Task 9: Remove Git Stash Functions

**Files:**
- Modify: `src/git_utils.py:192-207` (remove stash_save, stash_pop)
- Modify: `tests/test_git_utils.py` (remove stash tests if any)

- [ ] **Step 1: Remove `stash_save()` and `stash_pop()` from `src/git_utils.py`**

Delete lines 192-207 (the two stash functions).

- [ ] **Step 2: Verify no remaining references to stash functions**

Run: `grep -r "stash_save\|stash_pop" src/ tests/ routes/`
Expected: Only hits in `agent_executor.py` (old file, kept for reference but no longer imported)

- [ ] **Step 3: Commit**

```bash
git add src/git_utils.py
git commit -m "refactor: remove git stash functions (replaced by acpx session persistence)"
```

---

### Task 10: Update E2E Tests

**Files:**
- Modify: `tests/test_e2e.py` (update imports and mocks)

- [ ] **Step 1: Update test_e2e.py to use AcpxExecutor**

Change import:
```python
from src.agent_executor import AgentExecutor
```
To:
```python
from src.acpx_executor import AcpxExecutor
```

Change instantiation:
```python
executor = AgentExecutor(db, jobs, hosts, artifacts, webhooks, coop_dir=coop)
```
To:
```python
executor = AcpxExecutor(db, jobs, hosts, artifacts, webhooks, coop_dir=coop)
```

Pass `job_manager` to StateMachine:
```python
sm = StateMachine(db, artifacts, hosts, executor, webhooks, merger, coop_dir=coop, ensure_worktree_fn=fake_ensure_worktree, job_manager=jobs)
```

Update mock targets from `executor.dispatch` to `executor.start_session`:
- Replace `patch.object(executor, "dispatch", ...)` with `patch.object(executor, "start_session", ...)`
- The mock functions should have the same signature as `start_session` (add `revision=None` parameter)
- Add `executor.close_session = AsyncMock()` to the setup

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: update e2e tests to use AcpxExecutor"
```

---

### Task 11: Cleanup Old Templates

**Files:**
- Delete: `templates/TASK-claude.md`
- Delete: `templates/TASK-codex.md`

- [ ] **Step 1: Verify no remaining references to old template names**

Run: `grep -r "TASK-claude\.md\|TASK-codex\.md" src/ tests/ routes/`
Expected: No hits (state_machine.py should now use INIT-design.md / INIT-dev.md with fallback)

- [ ] **Step 2: Delete old templates**

Remove `templates/TASK-claude.md` and `templates/TASK-codex.md`.

- [ ] **Step 3: Remove fallback logic from state_machine.py**

Remove the `if not Path(template).exists()` fallback blocks in `_tick_design_queued` and `_tick_dev_queued`.

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git rm templates/TASK-claude.md templates/TASK-codex.md
git add src/state_machine.py
git commit -m "refactor: remove legacy TASK-* templates, use INIT-* exclusively"
```

---

## Verification Checklist

After all tasks are complete:

- [ ] `pytest tests/ -v` — all tests pass
- [ ] `python -c "from src.acpx_executor import AcpxExecutor; print('OK')"` — module loads
- [ ] `python -c "from src.app import app; print('OK')"` — app loads
- [ ] No references to `AgentExecutor` in `src/app.py`
- [ ] No references to `stash_save`/`stash_pop` in `src/` (except `agent_executor.py` which is kept for reference)
- [ ] `templates/` directory contains: `INIT-design.md`, `INIT-dev.md`, `TURN-revision.md`, `TURN-dev-fix.md`, `GATE-revision.md`, `RESUME.md`, `WEBHOOK-messages.yaml`
- [ ] `db/schema.sql` has `turns` table and extended `jobs` columns
