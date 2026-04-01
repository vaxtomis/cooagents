# 设计阶段派发流程（Design Dispatch Flow）

当用户审批通过需求文档后，cooagents 如何通过 acpx 启动设计 agent 的完整交互流程。

## 阶段总览

```
approve(gate="req")
    └─ REQ_REVIEW → DESIGN_QUEUED              （仅状态变更，0 次 acpx 调用）

scheduler tick (≤30s 后)
    └─ _tick_design_queued
        ├─ 选主机 + 建 worktree + 渲染任务文件
        └─ start_session
            └─ _bootstrap_session_start         （3 次 acpx 调用，顺序执行）
                ├─ ① sessions ensure            （创建会话，短命令）
                ├─ ② status                     （验证存活，最多重试 3 次）
                └─ ③ prompt --file TASK.md      （发送任务，长时间流式监听）
```

---

## 第一步：用户审批触发阶段切换

**入口：** `POST /api/v1/runs/{run_id}/approve`，body 为 `{"gate": "req", "by": "..."}`

**代码路径：** `routes/runs.py:124` → `state_machine.py:235 approve()`

```python
# state_machine.py:267
next_stages = {"req": "DESIGN_QUEUED", "design": "DEV_QUEUED", "dev": "MERGE_QUEUED"}
await self._update_stage(run_id, run["current_stage"], next_stages[gate])
```

`approve` 方法只做两件事：
1. 写入审批记录到 `approvals` 表
2. 将 run 阶段从 `REQ_REVIEW` 推进到 `DESIGN_QUEUED`

**此时没有调用任何 acpx 命令。** 实际派发由 Scheduler 异步触发。

---

## 第二步：Scheduler 轮询触发 tick

**代码路径：** `scheduler.py:64 _timeout_enforcement_loop` → `scheduler.py:149 _tick_runnable_runs()`

Scheduler 的超时巡检循环每 **30 秒** 运行一次，其中会调用 `_tick_runnable_runs()`，查询所有处于可推进阶段的 run：

```sql
SELECT id FROM runs WHERE status='running' AND current_stage IN
    ('DESIGN_QUEUED','DESIGN_DISPATCHED','DESIGN_RUNNING',
     'DEV_QUEUED','DEV_DISPATCHED','DEV_RUNNING')
```

发现 run 处于 `DESIGN_QUEUED` 后，调用 `sm.tick(run_id)`，路由到 `_tick_design_queued`。

---

## 第三步：`_tick_design_queued` 准备并派发

**代码路径：** `state_machine.py:448`

这个方法依次完成四项准备工作：

### 3.1 选择主机

```python
host = await self.hosts.select_host("claude")
# 返回如 {"id": "local", "host": "local"} 或 SSH 主机信息
```

如果没有可用主机，发出 `host.unavailable` 事件后直接返回，等下一轮 tick 再试。

### 3.2 创建 Git Worktree

```python
branch, wt = await self._resolve_worktree(run["repo_path"], run["ticket"], "design")
# branch 例: "design/FEAT-42"
# wt     例: "/path/to/repo/.coop/worktrees/design-FEAT-42"
```

将 worktree 和 branch 写入 runs 表的 `design_worktree` / `design_branch` 字段。

### 3.3 渲染任务文件

```python
task_path = os.path.join(self.coop_dir, "runs", run["id"], "TASK-design.md")
template = "templates/INIT-design.md"
await self.artifacts.render_task(template, {
    "run_id": run["id"],
    "ticket": run["ticket"],
    "repo_path": run["repo_path"],
    "worktree": wt,
    "req_path": req_path,          # 需求文档在 worktree 中的路径
}, task_path)
```

模板 `templates/INIT-design.md` 生成的任务文件包含：ticket 信息、需要阅读的上下文文件列表（README.md / CLAUDE.md / AGENTS.md）、输入资料路径、设计目标与输出要求。

### 3.4 调用 executor 派发

```python
timeout_sec = self._execution_timeout("design")  # config.timeouts.design_execution，默认 1800
await self.executor.start_session(
    run_id     = "run-abc123",
    host       = {"id": "local", "host": "local"},
    agent_type = "claude",
    task_file  = ".coop/runs/run-abc123/TASK-design.md",
    worktree   = "/path/to/worktree",
    timeout_sec = 1800,
)
```

派发完成后，将阶段从 `DESIGN_QUEUED` 推进到 `DESIGN_DISPATCHED`。

---

## 第四步：`start_session` 创建 Job 并启动 Bootstrap

**代码路径：** `acpx_executor.py:340`

```python
session_name = self._make_session_name(run_id, "design")
# 结果: "run-abc123-design"
# 如果是第 2 次修订: "run-abc123-design-r2"

job_id = await self.jobs.create_job(
    run_id, host["id"], "claude", "DESIGN_DISPATCHED",
    task_file, worktree, base_commit, timeout_sec,
    session_name=session_name,
)

bootstrap_task = asyncio.create_task(
    self._bootstrap_session_start(job_id, run_id, host, "claude",
        task_file, worktree, timeout_sec, session_name)
)
```

`start_session` **立即返回** `job_id`，bootstrap 在后台异步运行。

---

## 第五步：`_bootstrap_session_start` — 实际 acpx 调用

**代码路径：** `acpx_executor.py:374`

这个方法对 acpx 发起 **3 次调用**，顺序执行：

### 调用 1：`sessions ensure`（创建/确认会话）

```bash
acpx --cwd /path/to/worktree \
     --format json \
     --approve-all \
     --non-interactive-permissions deny \
     claude sessions ensure --name run-abc123-design
```

| 参数 | 来源 | 说明 |
|------|------|------|
| `--cwd` | `start_session` 的 `worktree` 参数 | 设计分支的 git worktree 路径 |
| `--format json` | 硬编码 | 输出 JSON 格式，便于解析 |
| `--approve-all` | `config.acpx.permission_mode` 映射 | 自动批准所有工具调用权限 |
| `--non-interactive-permissions deny` | 硬编码 | 非 TTY 下自动拒绝权限提示，防止阻塞 |
| `claude` | `agent_type = "claude"` | 使用 Claude 作为设计 agent |
| `--name` | `_make_session_name()` 生成 | 会话名格式：`{run_id}-{phase}` |

- **超时控制：** `config.timeouts.dispatch_ensure`（默认 60 秒）
- **成功判定：** `returncode == 0`
- **失败处理：** 标记 job 为 `failed`/`timeout`，通知状态机，流程终止

### 调用 2：`status`（验证 queue owner 存活）

```bash
acpx --cwd /path/to/worktree \
     --format json \
     claude status -s run-abc123-design
```

- **目的：** ensure 返回 0 不代表 queue owner 真的存活（issue #17 场景：headless 环境下 queue owner 因 raw-mode stdin 问题立即崩溃）
- **重试机制：** 最多 `_session_reconcile_attempts` 次（默认 3），间隔 `_session_reconcile_delay` 秒（默认 0.5）
- **成功判定：** 响应 JSON 中 `status` 为 `"running"` 或 `"alive"`
- **失败处理：** 发出 `session.ensure_unhealthy` 事件，标记 job 为 `failed`，流程终止

### 调用 3：`prompt`（发送设计任务）

```bash
acpx --cwd /path/to/worktree \
     --format json \
     --approve-all \
     --non-interactive-permissions deny \
     --timeout 1800 \
     --ttl 600 \
     --json-strict \
     claude -s run-abc123-design \
     --file /absolute/path/to/.coop/runs/run-abc123/TASK-design.md
```

| 参数 | 来源 | 说明 |
|------|------|------|
| `--timeout 1800` | `config.timeouts.design_execution` | 设计阶段最长执行时间 |
| `--ttl 600` | `config.acpx.ttl` | queue owner 空闲多久后自动退出 |
| `--json-strict` | `config.acpx.json_strict` | 输出严格 JSON（仅在 `--format json` 下生效） |
| `--model` | `config.acpx.model`（可选） | 指定模型，如 `claude-sonnet-4-20250514` |
| `--allowed-tools` | `config.acpx.allowed_tools_design`（可选） | 工具白名单，逗号分隔 |
| `--file` | 渲染后的任务文件绝对路径 | 即第三步生成的 `TASK-design.md` |

这是一个**长时间运行的进程**：
- stdout 输出 NDJSON 流，由 `_watch()` → `_parse_ndjson_stream()` 持续读取
- 事件写入 `.coop/jobs/{job_id}/events.jsonl`
- stderr 写入 `.coop/jobs/{job_id}/stderr.log`
- 进程结束后根据 exit code 映射状态：

| Exit Code | 状态 |
|-----------|------|
| 0 | `completed` |
| 1, 2, 4, 5 | `failed` |
| 3 | `timeout` |
| 130 | `interrupted` |

---

## 后续：设计评审与多轮修订

prompt 进程结束后，`_watch` 通过 `_notify_job_status_changed` 通知状态机，触发 `_tick_design_running`（`state_machine.py:538`）。

状态机通过 `_evaluate_design()` 检查产出物：
- **通过：** 设计文档存在 → 推进到 `DESIGN_REVIEW`（等待人工审批）
- **修订（turn < max_turns）：** 渲染修订任务文件，调用 `send_followup()`：

```python
# state_machine.py:581
await self.executor.send_followup(
    run_id, "claude", revision_path, wt, self._execution_timeout("design")
)
```

`send_followup` **复用已有 session**，只发出 1 次 prompt 命令（不需要重新 ensure）：

```bash
acpx --cwd /path/to/worktree \
     --format json \
     --approve-all \
     --non-interactive-permissions deny \
     --timeout 1800 \
     --ttl 600 \
     --json-strict \
     claude -s run-abc123-design \
     --file /path/to/REVISION-design.md
```

多轮修订次数受 `config.turns.design_max_turns`（默认 1）控制。

---

## 配置参数速查

| 配置项 | 默认值 | 影响 |
|--------|--------|------|
| `timeouts.dispatch_ensure` | 60 | ensure 命令超时（秒） |
| `timeouts.design_execution` | 1800 | prompt 命令超时（秒） |
| `acpx.permission_mode` | `approve-all` | 映射为 `--approve-all` / `--approve-reads` / `--deny-all` |
| `acpx.ttl` | 600 | queue owner 空闲 TTL（秒） |
| `acpx.json_strict` | true | 是否追加 `--json-strict` |
| `acpx.model` | null | 指定模型 ID（null 用 agent 默认） |
| `acpx.allowed_tools_design` | null | 设计阶段工具白名单 |
| `turns.design_max_turns` | 1 | 最大设计修订轮次 |
| `health_check.interval` | 60 | Scheduler 巡检间隔（秒） |

---

## 涉及的关键源文件

| 文件 | 职责 |
|------|------|
| `routes/runs.py:124` | approve API 入口 |
| `src/state_machine.py:235` | `approve()` 阶段推进 |
| `src/state_machine.py:448` | `_tick_design_queued()` 准备与派发 |
| `src/state_machine.py:538` | `_tick_design_running()` 评审与修订 |
| `src/acpx_executor.py:340` | `start_session()` 创建 job |
| `src/acpx_executor.py:374` | `_bootstrap_session_start()` 实际 acpx 调用 |
| `src/acpx_executor.py:460` | `send_followup()` 修订 prompt |
| `src/scheduler.py:149` | `_tick_runnable_runs()` 轮询触发 |
| `templates/INIT-design.md` | 设计任务模板 |
| `config/settings.yaml` | 全部配置项 |
