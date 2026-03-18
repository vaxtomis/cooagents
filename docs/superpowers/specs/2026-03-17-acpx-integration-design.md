# 设计文档：通过 acpx 集成 Claude Code 与 Codex

> 日期：2026-03-17
> 状态：已批准
> 方案：方案 A — acpx CLI 子进程调用 + 多轮会话

## 1. 背景与目标

### 现状

cooagents 的 `agent_executor.py` 直接通过子进程调用 Claude Code（`claude -p ...`）和 Codex（`codex -q ...`）CLI。每次 dispatch 是一个独立进程，跑完即结束，不保留对话上下文。

### 问题

- 无会话持久化：agent 崩溃后丢失所有上下文，resume 后从零开始
- 无多轮交互：设计/开发阶段只能一次性 prompt，无法自动迭代修订
- 输出解析脆弱：依赖 raw stdout 读取
- 取消操作粗暴：直接 SIGKILL

### 目标

引入 [acpx](https://github.com/anthropics/acpx)（Agent Client Protocol CLI）作为中间层，获得：
- **多轮会话**：在 RUNNING 阶段支持评估-修订循环
- **会话持久化**：崩溃后可恢复完整对话历史
- **结构化输出**：NDJSON 事件流替代 raw stdout
- **协作式取消**：通过 ACP 协议优雅中断

## 2. 方案选型

评估了 3 种方案：

| 方案 | 描述 | 结论 |
|------|------|------|
| A. acpx CLI 子进程调用 | 通过 subprocess 调用 acpx CLI，利用命名 session 实现多轮对话 | **选定** |
| B. acpx 长驻进程 + IPC | 利用 queue owner 常驻 + `--no-wait` 异步入队 | 过于复杂，冷启动开销不是瓶颈 |
| C. 直接对接 ACP 协议 | Python 实现 ACP JSON-RPC 客户端 | 重造轮子，工作量最大 |

选择方案 A 的理由：
1. acpx 已解决 session 持久化、崩溃恢复、进程管理、ACP 协议适配
2. 进程启动开销（~1-2s）相对 agent 执行时间（分钟级）可忽略
3. SSH 远程执行自然适配：`ssh host acpx claude -s ...`
4. Python 端无需理解 ACP 协议

## 3. 新的执行层 —— AcpxExecutor

替换 `AgentExecutor`，核心职责从"管理子进程生命周期"变为"管理 acpx session 生命周期"。

### Session 命名规则

```
run-{run_id}-{phase}
```

例：`run-abc123-design`、`run-abc123-dev`。

**多轮修订 vs 审批打回的区别：**
- **多轮修订（RUNNING 阶段内部）**：使用同一个 session 名称，向同一 session 追加 prompt，保留完整对话历史
- **审批打回（REVIEW → 重新 QUEUED）**：创建新 session，名称追加后缀 `-r2`、`-r3`，因为打回意味着新一轮独立的工作周期

### 核心接口

```python
class AcpxExecutor:

    async def start_session(self, run_id, host, agent_type, task_file, worktree, timeout_sec) -> str:
        """创建 session 并发送初始 prompt，返回 job_id

        两步操作：
        1. acpx {agent} --cwd {worktree} sessions ensure --name {session_name}
        2. acpx {agent} -s {session_name} --cwd {worktree} --format json --approve-all --timeout {timeout_sec} --file {task_file}
        """

    async def send_followup(self, run_id, agent_type, prompt_file, worktree, timeout_sec) -> None:
        """向已有 session 发送追加 prompt（阻塞等待完成）

        acpx {agent} -s {session_name} --cwd {worktree} --format json --approve-all --timeout {timeout_sec} --file {prompt_file}
        """

    async def cancel_session(self, run_id, agent_type) -> None:
        """协作式取消当前进行中的 prompt

        acpx {agent} cancel -s {session_name} --cwd {worktree}
        """

    async def close_session(self, run_id, agent_type) -> None:
        """关闭 session，释放资源

        acpx {agent} --cwd {worktree} sessions close {session_name}
        """

    async def get_session_status(self, run_id, agent_type) -> dict:
        """查询 session 状态（本地或 SSH 路由）

        本地：acpx {agent} status -s {session_name} --cwd {worktree} --format json
        SSH： ssh {host} acpx {agent} status -s {session_name} --cwd {remote_worktree} --format json
        """
```

### 命令构建

```python
def _build_acpx_prompt_cmd(self, agent_type, session_name, worktree, timeout_sec, task_file=None):
    """构建 acpx prompt 命令（用于 start_session 和 send_followup）"""
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
    """构建 session 创建命令（幂等，已存在则复用）"""
    agent = "claude" if agent_type == "claude" else "codex"
    return ["acpx", agent, "--cwd", worktree, "sessions", "ensure", "--name", session_name]

def _build_acpx_cancel_cmd(self, agent_type, session_name, worktree):
    """构建协作式取消命令"""
    agent = "claude" if agent_type == "claude" else "codex"
    return ["acpx", agent, "cancel", "-s", session_name, "--cwd", worktree]

def _build_acpx_close_cmd(self, agent_type, session_name, worktree):
    """构建 session 关闭命令"""
    agent = "claude" if agent_type == "claude" else "codex"
    return ["acpx", agent, "--cwd", worktree, "sessions", "close", session_name]

def _build_acpx_status_cmd(self, agent_type, session_name, worktree):
    """构建 session 状态查询命令"""
    agent = "claude" if agent_type == "claude" else "codex"
    return ["acpx", agent, "status", "-s", session_name, "--cwd", worktree, "--format", "json"]
```

> **注意**：acpx 没有 `--max-turns` 全局选项。如需限制 agent 轮次，通过 cooagents 的
> `turns.design_max_turns` / `turns.dev_max_turns` 配置控制，在状态机层面限制 followup 次数。

### 执行方式

- **本地**：`asyncio.create_subprocess_exec(*cmd, ...)`
- **SSH**：`ssh {host} acpx claude -s ... --cwd {remote_worktree} ... --file ...`
  - 注意：`--cwd` 必须使用远程主机上的 worktree 路径，可能与本地数据库存储的路径不同

### 输出解析

acpx `--format json` 输出的是 **原始 ACP JSON-RPC 消息**（不是简化的事件信封）。每行是一个完整的 JSON-RPC message：

```python
async def _parse_ndjson_stream(self, process, job_id):
    """解析 acpx --format json 的 ACP JSON-RPC 流"""
    async for line in process.stdout:
        msg = json.loads(line)
        # 写入 .coop/jobs/{job_id}/events.jsonl
        await self._append_event(job_id, msg)

        if "error" in msg:
            # JSON-RPC 错误响应：{"jsonrpc":"2.0","error":{"code":-32603,"message":"..."}}
            await self._handle_error(job_id, msg["error"])
        elif "result" in msg and msg["result"].get("stopReason") == "end_turn":
            # prompt 完成：{"jsonrpc":"2.0","id":"...","result":{"stopReason":"end_turn"}}
            await self._handle_turn_complete(job_id)
        # 其他消息（tool_call、session/update 等）仅记录，不处理
```

### acpx 退出码映射

| acpx 退出码 | 含义 | 映射到 JobStatus |
|-------------|------|-----------------|
| 0 | 成功 | `completed` |
| 1 | 运行时错误 | `failed` |
| 2 | 用法错误（参数错误） | `failed` |
| 3 | 超时 | `timeout` |
| 4 | 无 session | `failed` |
| 5 | 权限拒绝 | `failed` |
| 130 | 中断（SIGINT） | `interrupted` |

### 与 AgentExecutor 的对应关系

| 当前方法 | 新方法 | 变化 |
|---------|--------|------|
| `dispatch()` | `start_session()` | spawn 原始 CLI → acpx 创建 session |
| `_build_command()` | `_build_acpx_cmd()` | `claude -p` → `acpx claude -s` |
| `_watch()` | 内置于 `_parse_ndjson_stream()` | acpx `--timeout` 替代手动超时 |
| `cancel()` | `cancel_session()` | SIGKILL → 协作式 cancel |
| `recover()` | `send_followup()` + acpx session resume | git stash/pop → session 恢复 |
| 无 | `close_session()` | 新增：阶段完成后释放 session |

## 4. 状态机多轮循环改造

### 设计原则

不新增状态枚举，在 `DESIGN_RUNNING` / `DEV_RUNNING` 内部引入轮次计数器，tick 逻辑变为评估-修订循环：

```
DESIGN_RUNNING ←────────────────┐
      │                         │
  检查本轮结果                    │
      │                         │
  ┌───┴───┐                     │
  ↓       ↓                     │
满意    需要修订 → send_followup ──┘
  ↓
DESIGN_REVIEW
```

### `_tick_design_running()` 改造

```python
async def _tick_design_running(self, run: dict) -> None:
    job = await self.jobs.get_active_job(run["id"])
    if not job or job["status"] != "completed":
        return

    turn = job.get("turn_count", 1)
    ticket = run["ticket"]
    await self.artifacts.scan_and_register(run["id"], ticket, "DESIGN_RUNNING", run["design_worktree"])
    # 评估时使用该 run 的全部制品（含之前轮次产出的），而非仅本轮新增
    all_artifacts = await self.artifacts.get_by_run(run["id"])
    verdict, detail = self._evaluate_design(all_artifacts, job)

    if verdict == "accept":
        await self._submit_artifacts(run["id"], all_artifacts)
        await self.executor.close_session(run["id"], "claude")
        await self._advance(run["id"], Stage.DESIGN_REVIEW)

    elif verdict == "revise" and turn < self.max_turns:
        revision_prompt = await self.artifacts.render_task(
            "templates/TURN-revision.md",
            {"run_id": run["id"], "turn": turn, "feedback": detail, ...},
            task_path
        )
        await self.executor.send_followup(
            run["id"], "claude", revision_prompt, run["design_worktree"], 1800
        )
        await self.jobs.increment_turn(job["id"])
        # 状态保持 DESIGN_RUNNING

    else:
        await self.executor.close_session(run["id"], "claude")
        await self._advance(run["id"], Stage.DESIGN_REVIEW)
```

### 评估器

第一版基于制品存在性检查：

```python
def _evaluate_design(self, artifacts, job) -> tuple[str, str]:
    has_design = any(a["kind"] == "design" for a in artifacts)
    has_adr = any(a["kind"] == "adr" for a in artifacts)
    if not has_design:
        return ("revise", "未生成设计文档 DES-{ticket}.md")
    if not has_adr:
        return ("revise", "未生成架构决策记录 ADR-{ticket}.md")
    return ("accept", "")

def _evaluate_dev(self, artifacts, job, worktree) -> tuple[str, str]:
    has_test_report = any(a["kind"] == "test-report" for a in artifacts)
    if not has_test_report:
        return ("revise", "未生成测试报告 TEST-REPORT-{ticket}.md")
    return ("accept", "")
```

### 最大轮次控制

```yaml
# config/settings.yaml
turns:
  design_max_turns: 3
  dev_max_turns: 5
```

超过上限强制推进到人工 REVIEW。

## 5. Job 与数据库 Schema 变更

### `jobs` 表扩展

```sql
ALTER TABLE jobs ADD COLUMN session_name   TEXT;
ALTER TABLE jobs ADD COLUMN turn_count     INTEGER DEFAULT 1;
ALTER TABLE jobs ADD COLUMN events_file    TEXT;
```

### 新增 `turns` 表

```sql
CREATE TABLE turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    turn_num    INTEGER NOT NULL,
    prompt_file TEXT,       -- 本轮使用的 prompt 文件（turn 1 = 初始任务，turn 2+ = 修订 prompt）
    verdict     TEXT,       -- accept / revise / failed
    detail      TEXT,       -- 评估详情
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    UNIQUE(job_id, turn_num)
);
```

### JobManager 新增方法

```python
async def increment_turn(self, job_id) -> int
async def record_turn(self, job_id, turn_num, prompt_file, verdict, detail)
async def get_turns(self, job_id) -> list[dict]
```

### 输出存储

```
.coop/jobs/{job_id}/
  ├── events.jsonl      # 全量 NDJSON 事件
  ├── turn-1.jsonl      # 第 1 轮事件
  ├── turn-2.jsonl      # 第 2 轮事件
  └── stderr.log
```

`get_output()` 优先读 `events.jsonl`，回退到 `stdout.log`（兼容老数据）。

### 响应模型扩展

```python
class JobResponse(BaseModel):
    # 现有字段不变，新增：
    session_name: str | None = None
    turn_count: int = 1
    turns: list[TurnResponse] | None = None

class TurnResponse(BaseModel):
    turn_num: int
    verdict: str | None
    detail: str | None
    started_at: str
    ended_at: str | None
```

### 迁移策略

新字段均有默认值，老数据无需迁移。`turns` 是新增表。输出格式变化对已完成 job 无影响。

## 6. Host Manager 与 SSH 适配

改动很小：

- **健康检查**：从 `which claude`/`which codex` 改为 `acpx --version`
- **SSH 命令封装**：命令从 `claude -p ...` 变为 `acpx claude -s ...`，传输层不变
- **`agent_type` 语义不变**

### 部署要求

所有 agent host 需安装：`npm install -g acpx`

### 可选配置扩展

```yaml
# config/agents.yaml
hosts:
  - id: local-pc
    host: local
    agent_type: both
    max_concurrent: 2
    acpx_args: []           # 额外 acpx 参数
    claude_model: null      # 覆盖模型
    codex_model: null
```

## 7. 模板体系改造

### 拆分为初始 + 轮次模板

| 模板 | 阶段 | 用途 |
|------|------|------|
| `INIT-design.md` | 设计 Turn 1 | 初始设计任务 |
| `INIT-dev.md` | 开发 Turn 1 | 初始开发任务 |
| `TURN-revision.md` | Turn 2+ | 自动评估不达标时的修订指令 |
| `TURN-dev-fix.md` | 开发 Turn 2+ | 测试失败时的修复指令 |
| `GATE-revision.md` | 审批打回 | 人工拒绝后的修订 |
| `RESUME.md` | 崩溃恢复 | 适配 acpx session 的恢复 prompt |

### 模板变化要点

- 去掉 `claude -p` 模式的 JSON 输出格式约束（ACP 协议接管）
- 去掉 worktree 绝对路径（`--cwd` 已设定，用相对路径）
- 去掉 `run_id`/`stage` 等元数据（acpx session 追踪）

### 模板渲染引擎

从简单 `{{var}}` 字符串替换升级为 **Jinja2**，支持条件和循环。

## 8. 恢复与容错机制

### 改造核心

去掉 git stash/pop 机制，利用 acpx session 持久化实现恢复：

| 场景 | 当前 | 改造后 |
|------|------|--------|
| agent 崩溃 | 丢失对话上下文 | acpx session 保留历史，agent 可 resume |
| 超时中断 | git stash + 重新 dispatch | 向同一 session 追加 prompt |
| SSH 断开 | 任务失败 | 远程 queue owner 可能仍在运行 |
| 审批打回 | 新进程无上下文 | 同一 session 追加修订 prompt |

### `recover()` 改造

```python
async def recover(self, run_id, action):
    if action == RecoverAction.resume:
        # 向同一 session 发送 RESUME.md
        await self.send_followup(run_id, agent_type, resume_prompt, worktree, timeout)

    elif action == RecoverAction.redo:
        # 关闭旧 session，重置 worktree，全新开始
        await self.close_session(run_id, agent_type)
        await git_utils.reset_to_commit(worktree, job["base_commit"])
        await self.start_session(run_id, host, agent_type, task_file, worktree, timeout)

    elif action == RecoverAction.manual:
        await self.close_session(run_id, agent_type)
```

### 可移除的旧逻辑

- `git_utils.stash_save()` / `stash_pop()`
- `snapshot_json` 字段
- `_on_interrupted()` 中的 stash 逻辑

### `restore_on_startup()` 改造

查询 acpx session 状态替代批量标记。注意：对 SSH 主机上的 job，需要通过 SSH 路由 status 查询。

```python
async def restore_on_startup(self):
    stale_jobs = await self.jobs.get_jobs_by_status(["starting", "running"])
    for job in stale_jobs:
        host = await self.hosts.get_host(job["host_id"])
        # get_session_status 内部根据 host 决定本地执行还是 SSH 路由
        status = await self.get_session_status(
            job["run_id"], job["agent_type"], host=host
        )
        if status and status.get("status") == "running":
            pass  # queue owner 仍在运行
        else:
            await self.jobs.update_status(job["id"], "interrupted")
```

## 9. 配置变更与依赖

### `config/settings.yaml` 新增

```yaml
acpx:
  permission_mode: "approve-all"
  default_format: "json"
  ttl: 600

turns:
  design_max_turns: 3
  dev_max_turns: 5
```

### Python 依赖

- 新增：`jinja2`
- 保留：`asyncssh`
- 系统依赖：所有 agent host 需 `npm install -g acpx`

## 10. Webhook 事件扩展

新增事件类型：

| 事件 | 触发 | payload |
|------|------|---------|
| `turn.started` | 每轮 prompt 发送 | `{run_id, job_id, turn_num, agent_type}` |
| `turn.completed` | 每轮结果返回 | `{run_id, job_id, turn_num, verdict, detail}` |
| `session.created` | session 创建 | `{run_id, session_name, agent_type}` |
| `session.closed` | session 关闭 | `{run_id, session_name}` |

可选新增 OpenClaw 函数：`get_turn_history`。

## 11. 影响范围总结

| 模块 | 改动 | 说明 |
|------|------|------|
| `src/agent_executor.py` | 重写 → `src/acpx_executor.py` | 核心执行层 |
| `src/state_machine.py` | 中等 | RUNNING 阶段加多轮循环 |
| `src/job_manager.py` | 中等 | 新增 session/turn 字段和方法 |
| `src/host_manager.py` | 小改 | 健康检查目标改为 acpx |
| `src/models.py` | 小改 | 新增 TurnResponse 模型 |
| `db/schema.sql` | 小改 | 扩展 jobs 表 + 新增 turns 表 |
| `templates/` | 重构 | 拆分为初始 + 轮次模板 |
| `src/artifact_manager.py` | 小改 | render_task 改用 Jinja2 |
| `config/` | 小改 | 新增 acpx 和 turns 配置 |
| `routes/runs.py` | 小改 | `recover_run()` 需适配新 `recover()` 签名 |
| `src/git_utils.py` | 删减 | 移除 stash 相关方法 |
| `src/merge_manager.py` | 无变化 | |
| `src/webhook_notifier.py` | 小改 | 新增事件类型 |
| `tests/test_agent_executor.py` | 重写 → `tests/test_acpx_executor.py` | 测试新执行层 |
| `tests/test_state_machine.py` | 中等 | mock 改为 `start_session`/`send_followup`/`close_session`，新增多轮 tick 测试 |

## 12. 测试影响

### 需要重写的测试

- `tests/test_agent_executor.py` → `tests/test_acpx_executor.py`
  - 测试 `_build_acpx_prompt_cmd()` 命令构建
  - 测试 `_build_acpx_ensure_cmd()` / `_build_acpx_cancel_cmd()` / `_build_acpx_close_cmd()`
  - 测试退出码到 JobStatus 的映射
  - 测试 NDJSON 解析（ACP JSON-RPC 格式）
  - 测试 `restore_on_startup()` 的 session 状态查询

### 需要修改的测试

- `tests/test_state_machine.py`
  - mock 目标从 `executor.dispatch()` 改为 `executor.start_session()` / `send_followup()` / `close_session()`
  - 新增多轮 tick 测试：模拟评估器返回 `revise` → `accept` 的循环
  - 新增最大轮次上限测试

### 新增测试

- 评估器单元测试：`_evaluate_design()` / `_evaluate_dev()` 各种制品组合
- `turns` 表 CRUD 测试
- 模板渲染测试（Jinja2 变量替换和循环）
