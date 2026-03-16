# cooagents Workflow API 重新设计

## 1. 背景与问题

### 现有系统问题

当前 cooagents 是一套基于 CLI + cron + tmux 的多 agent 协作工具，存在以下核心问题：

1. **OpenClaw 无法通过飞书正确发起任务** — 系统是纯 CLI 驱动，飞书只做单向 webhook 推送，没有"接收消息 -> 解析意图 -> 触发操作"的路径。
2. **tmux send-keys 注入不可靠** — 中文 prompt 通过 `tmux send-keys` 发送到 Claude/Codex 终端，在不同环境下编码、换行符、终端缓冲区大小都会导致 prompt 被截断或乱码。
3. **cron 轮询驱动，无主动推进** — 依赖 cron 每 2 分钟轮询，如果 cron 未配好或环境不支持（如 Windows），流程卡住。
4. **飞书只推不拉** — 无法通过飞书回复来 approve 或做其他操作，阻塞确认只能通过命令行完成。
5. **ACK 机制路径问题** — Agent 执行环境（worktree）和 ACK 文件路径（主仓库 `tasks/`）不在同一目录，agent 可能找不到正确路径。

### 目标

将系统重构为 HTTP API 驱动的架构，OpenClaw 通过调用 API 操作流程，实现：

- 飞书双向交互（自然语言 + 指令）
- 可靠的 agent 调度（非交互模式替代 tmux）
- 实时状态推进（进程结束即推进，不依赖 cron）
- 阶段门控审阅（产物发飞书，阻塞等待回复）
- 多任务并发与冲突控制

## 2. 整体架构

### 四层架构

```
┌─────────────────────────────────────────────────────┐
│                    用户 (飞书)                        │
└──────────────────────┬──────────────────────────────┘
                       │ 双向消息
┌──────────────────────▼──────────────────────────────┐
│              OpenClaw (AI Agent)                     │
│  - 接收用户自然语言 → 解析意图 → 调用 API            │
│  - 接收 webhook 回调 → 格式化 → 发飞书通知           │
│  - 阶段门控时：把产物发飞书 → 等用户回复 → 调 approve │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP API
┌──────────────────────▼──────────────────────────────┐
│           Workflow API Server (FastAPI)               │
│                                                       │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │ State Machine│  │ Agent Executor│  │ Webhook     │ │
│  │ (SQLite)     │  │ (SSH+非交互)  │  │ Notifier    │ │
│  └─────────────┘  └──────────────┘  └─────────────┘ │
└──────────────────────┬──────────────────────────────┘
                       │ SSH / local subprocess
┌──────────────────────▼──────────────────────────────┐
│          Agent Hosts (本机 / 远程服务器)               │
│  - claude -p "..." --output-format json              │
│  - codex -q "..."                                    │
└─────────────────────────────────────────────────────┘
```

### 组件职责

| 组件 | 职责 | 不负责 |
|------|------|--------|
| OpenClaw | 飞书交互、意图解析、调 API、发通知 | 状态管理、agent 调度 |
| Workflow API | 状态机推进、agent 调度、事件记录、webhook 回调 | 飞书交互 |
| Agent Executor | SSH 执行 claude/codex、收集输出 | 状态判断 |
| Agent Hosts | 运行 Claude Code / Codex | 流程控制 |

### 关键设计决策

- Workflow API 和 OpenClaw 部署在同一台 Linux 服务器上，API 只需监听 `127.0.0.1`
- Agent 执行通过 SSH（即使目标是本机也可走 SSH，保持一致性），也支持本地直接执行
- 状态机推进是**同步的** — API 调用时立即推进，不再依赖 cron 轮询
- Agent 执行是**异步的** — 调度后通过后台任务监控完成，完成后 webhook 通知 OpenClaw

## 3. API 接口设计

### Base URL

```
http://127.0.0.1:8321/api/v1
```

### 任务生命周期

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/runs` | 创建新任务 |
| `GET` | `/runs` | 列出所有任务 |
| `GET` | `/runs/{run_id}` | 查询任务详情（含状态、审批、事件、产物） |
| `POST` | `/runs/{run_id}/tick` | 手动推进一步（通常不需要，API 会自动推进） |
| `POST` | `/runs/{run_id}/approve` | 审批门控（req / design / dev） |
| `POST` | `/runs/{run_id}/reject` | 驳回并附修改意见，回退到上一阶段重做 |
| `POST` | `/runs/{run_id}/retry` | 重试失败的任务 |
| `POST` | `/runs/{run_id}/recover` | 中断恢复（resume/redo/manual） |
| `DELETE` | `/runs/{run_id}` | 取消/终止任务 |

### 产物管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/runs/{run_id}/artifacts` | 列出产物（支持 `?kind=design&status=submitted` 过滤） |
| `GET` | `/runs/{run_id}/artifacts/{id}` | 获取单个产物元数据 |
| `GET` | `/runs/{run_id}/artifacts/{id}/content` | 获取产物文件原文 |
| `GET` | `/runs/{run_id}/artifacts/{id}/diff` | 获取与上一版本的 diff |

### Agent Host 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/agent-hosts` | 列出所有 host 及负载 |
| `POST` | `/agent-hosts` | 注册新 host |
| `PUT` | `/agent-hosts/{id}` | 更新 host 配置 |
| `DELETE` | `/agent-hosts/{id}` | 下线 host |
| `POST` | `/agent-hosts/{id}/check` | 手动触发健康检查 |

### Webhook 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/webhooks` | 注册 webhook 回调地址 |
| `DELETE` | `/webhooks/{id}` | 删除 webhook |

### 仓库与合并队列

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/repos/{repo_path}/runs` | 查看同一仓库下所有进行中的任务 |
| `GET` | `/runs/{run_id}/conflicts` | 获取冲突检测报告 |
| `POST` | `/runs/{run_id}/merge` | 请求合并到 main（进入合并队列） |
| `GET` | `/repos/{repo_path}/merge-queue` | 查看合并队列 |

### Job 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/runs/{run_id}/jobs` | 查看该任务的所有 job 历史 |
| `GET` | `/runs/{run_id}/jobs/{job_id}/output` | 获取 agent 完整输出日志 |

### 关键请求/响应示例

**创建任务：**
```json
// POST /runs
// Request:
{
  "ticket": "FEAT-123",
  "repo_path": "/path/to/repo",
  "description": "实现用户登录功能",
  "preferences": {
    "design_host": "my-pc",
    "dev_host": "server-a"
  }
}

// Response: 201
{
  "run_id": "run-20260316-120000-abc123",
  "status": "running",
  "current_stage": "REQ_COLLECTING"
}
```

**审批：**
```json
// POST /runs/{run_id}/approve
// Request:
{"gate": "req", "by": "user", "comment": "需求确认，可以开始设计"}

// Response: 200
{"run_id": "...", "status": "running", "current_stage": "DESIGN_QUEUED"}
```

**驳回：**
```json
// POST /runs/{run_id}/reject
// Request:
{"gate": "design", "by": "user", "reason": "接口缺少鉴权方案，请补充"}

// Response: 200
{"run_id": "...", "status": "running", "current_stage": "DESIGN_QUEUED", "note": "已回退，修改意见已注入任务"}
```

**获取产物内容：**
```json
// GET /runs/{run_id}/artifacts/{id}/content
// Response: 200
{"kind": "design", "path": "docs/design/DES-FEAT-123.md", "content": "# 设计文档\n...全文...", "version": 2}
```

### Webhook 回调格式

门控等待的 webhook 回调会**自动携带产物内容**：

```json
// POST → OpenClaw 的回调地址
{
  "event": "gate.waiting",
  "run_id": "run-20260316-120000-abc123",
  "ticket": "FEAT-123",
  "stage": "DESIGN_REVIEW",
  "gate": "design",
  "artifacts": [
    {
      "id": 3,
      "kind": "design",
      "version": 2,
      "status": "submitted",
      "path": "docs/design/DES-FEAT-123.md",
      "content": "# 设计文档\n...全文...",
      "byte_size": 4523,
      "diff_from_prev": "- 旧接口定义\n+ 新增鉴权方案..."
    }
  ],
  "message": "设计文档（第2版）已就绪，请审阅",
  "timestamp": "2026-03-16T12:00:00Z"
}
```

## 4. 产物管理体系

### 产物分类

| 类别 | 说明 | 产出阶段 | 示例 |
|------|------|----------|------|
| `req` | 需求文档 | REQ_COLLECTING | `REQ-FEAT-123.md` |
| `design` | 设计文档 | DESIGN_RUNNING | `DES-FEAT-123.md` |
| `adr` | 架构决策记录 | DESIGN_RUNNING | `ADR-FEAT-123-001.md` |
| `code` | 代码变更 | DEV_RUNNING | commit list / diff |
| `test-report` | 测试报告 | DEV_RUNNING | `TEST-REPORT-FEAT-123.md` |

### 产物数据模型

```sql
artifacts (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         TEXT NOT NULL,
  kind           TEXT NOT NULL,          -- req / design / adr / code / test-report
  path           TEXT NOT NULL,          -- 相对于 repo 或 worktree 的路径
  git_ref        TEXT,                   -- commit hash（代码类产物）
  stage          TEXT NOT NULL,          -- 产出时所在阶段
  version        INTEGER DEFAULT 1,     -- 版本号（驳回重做后递增）
  status         TEXT DEFAULT 'draft',  -- draft / submitted / approved / rejected
  review_comment TEXT,                   -- 审批意见
  content_hash   TEXT,                   -- 文件内容 SHA256
  byte_size      INTEGER,               -- 文件大小
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
)
```

### 产物状态流转

```
draft → submitted → approved
                  ↘ rejected → (新版本 draft) → submitted → ...
```

### 代码类产物

代码变更不是单个文件，通过 git 管理：

```json
// GET /runs/{run_id}/artifacts?kind=code
{
  "kind": "code",
  "branch": "feat/FEAT-123-dev",
  "worktree": "/path/to/wt-FEAT-123-dev",
  "commits": [
    {"hash": "a1b2c3d", "message": "feat: add login endpoint", "files_changed": 5, "insertions": 120, "deletions": 15}
  ],
  "summary": {"total_commits": 3, "total_files_changed": 12, "total_insertions": 350, "total_deletions": 40},
  "diff_stat": "src/auth.py | 80 ++++\nsrc/routes.py | 25 +++\n..."
}
```

### 驳回重做流程

以设计文档被驳回为例：

1. 用户在飞书回复"接口缺少鉴权方案"
2. OpenClaw 调用 `POST /runs/{run_id}/reject`，附带 reason
3. API 将当前 design artifact 标记为 `rejected`，记录 `review_comment`
4. 状态机回退到 `DESIGN_QUEUED`
5. API 生成修订任务单（原设计文档 + 驳回意见注入新 prompt）
6. 重新调度 Claude Code 执行
7. Claude 产出新版本（`version=2`），标记为 `submitted`
8. Webhook 再次通知 OpenClaw → 发飞书给用户审阅

## 5. 状态机设计

### 完整状态流转

```
INIT
  │
  ▼
REQ_COLLECTING
  │ [需求文档产出]
  ▼
REQ_REVIEW ◄── rejected ──┐
  │                        │
  ├── approved             │
  ▼                        │
DESIGN_QUEUED              │  (无可用 host 时等待)
  │                        │
  ▼                        │
DESIGN_DISPATCHED          │  (已调度，等待 agent 启动)
  │                        │
  ▼                        │
DESIGN_RUNNING             │
  │ [设计产物产出]          │
  ▼                        │
DESIGN_REVIEW ─────────────┘
  │
  ├── approved
  ▼
DEV_QUEUED                    (无可用 host 时等待)
  │
  ▼
DEV_DISPATCHED                (已调度，等待 agent 启动)
  │
  ▼
DEV_RUNNING
  │ [代码 + 测试报告产出]
  ▼
DEV_REVIEW ── rejected ──► DEV_QUEUED
  │
  ├── approved
  ▼
MERGE_QUEUED
  │
  ▼
MERGING
  │       │
success  conflict
  │       │
  ▼       ▼
MERGED  MERGE_CONFLICT ── (人工处理后 retry) ──► MERGE_QUEUED

任何阶段异常 ──► FAILED ── retry ──► 回到异常前阶段
```

### 与旧设计对比

| 旧状态 | 新状态 | 变化 |
|--------|--------|------|
| `REQ_READY` | `REQ_REVIEW` | 语义更清晰 |
| `DESIGN_ASSIGNED` | `DESIGN_QUEUED` → `DESIGN_DISPATCHED` | 拆分：等待资源 vs 已调度 |
| `DESIGN_DONE` | `DESIGN_REVIEW` | 明确审阅阶段 |
| `DEV_ASSIGNED` | `DEV_QUEUED` → `DEV_DISPATCHED` | 同上拆分 |
| — | `DEV_REVIEW` | 新增开发审阅 |
| — | `MERGE_QUEUED` / `MERGING` / `MERGED` / `MERGE_CONFLICT` | 新增合并流程 |
| `COMPLETED` | `MERGED` | 合并成功才算完成 |

### 推进条件与动作

| 状态 | 推进条件 | 动作 | 下一状态 |
|------|----------|------|----------|
| INIT | 自动 | 创建 step | REQ_COLLECTING |
| REQ_COLLECTING | 需求文档存在 | 注册 artifact(submitted) | REQ_REVIEW |
| REQ_REVIEW | approve | artifact→approved, 查找 host | DESIGN_QUEUED |
| REQ_REVIEW | reject | artifact→rejected, 记录意见 | REQ_COLLECTING |
| DESIGN_QUEUED | 有可用 claude host | 分配 host, 创建 worktree, 生成任务单, 调度 | DESIGN_DISPATCHED |
| DESIGN_DISPATCHED | agent 进程已启动 | 记录 step | DESIGN_RUNNING |
| DESIGN_RUNNING | 设计文档产出 | 注册 artifact(submitted) | DESIGN_REVIEW |
| DESIGN_REVIEW | approve | artifact→approved, 查找 host | DEV_QUEUED |
| DESIGN_REVIEW | reject | artifact→rejected, 注入修改意见 | DESIGN_QUEUED |
| DEV_QUEUED | 有可用 codex host | 分配 host, 创建 worktree, 生成任务单, 调度 | DEV_DISPATCHED |
| DEV_DISPATCHED | agent 进程已启动 | 记录 step | DEV_RUNNING |
| DEV_RUNNING | 测试报告产出 | 注册 artifact(submitted), 冲突检测 | DEV_REVIEW |
| DEV_REVIEW | approve | artifact→approved | MERGE_QUEUED |
| DEV_REVIEW | reject | artifact→rejected, 注入修改意见 | DEV_QUEUED |
| MERGE_QUEUED | 轮到本任务 + 无冲突 | rebase main, 合并 | MERGING |
| MERGING | 合并成功 | 记录 merge commit | MERGED |
| MERGING | 合并冲突 | 记录冲突文件, 通知 | MERGE_CONFLICT |
| MERGE_CONFLICT | 人工处理后 retry | — | MERGE_QUEUED |
| MERGED | 终态 | 清理 worktree, 释放 host, 通知 | — |
| FAILED | retry | — | 恢复到失败前阶段 |

### 超时配置

| 阶段 | 超时 | 处理 |
|------|------|------|
| DISPATCHED | 5 分钟 | agent 未启动 → FAILED |
| RUNNING (design) | 30 分钟 | 可配置，超时 → FAILED + 通知 |
| RUNNING (dev) | 60 分钟 | 可配置，超时 → FAILED + 通知 |
| REVIEW | 无超时 | 阻塞等待人工，每 24h 提醒一次 |
| QUEUED | 无超时 | 等待资源，每 10min 检查 + 通知 |

## 6. 多任务并发与冲突控制

### 并发能力

每个 run 是独立的状态机实例，有自己的数据库记录、Git worktree、Git branch、产物目录。多个任务可同时处于不同阶段。

### 三层防线

**第一层：分支隔离**

每个任务有独立的 worktree 和分支：

```
main
 ├── feat/FEAT-1-design    ← wt-FEAT-1-design/
 ├── feat/FEAT-1-dev       ← wt-FEAT-1-dev/
 ├── feat/FEAT-2-design    ← wt-FEAT-2-design/
 └── feat/FEAT-2-dev       ← wt-FEAT-2-dev/
```

**第二层：冲突检测**

在关键节点主动检测潜在冲突：

- **创建任务时**：扫描同仓库 running 任务，webhook 通知并发提醒（不阻塞）
- **DEV_RUNNING 完成时**：`git merge --no-commit main` 检测冲突 + 与其他进行中分支对比
- **审批通过、准备合并前**：再次检测

**第三层：合并队列**

```sql
merge_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL,
  repo_path  TEXT NOT NULL,
  branch     TEXT NOT NULL,
  priority   INTEGER DEFAULT 0,
  status     TEXT DEFAULT 'queued',  -- queued / merging / merged / conflict
  queued_at  TEXT NOT NULL,
  merged_at  TEXT
)
```

- 同一仓库同时只允许一个任务执行合并
- 合并前 rebase 到最新 main
- rebase 冲突 → 标记 conflict，通知人工处理
- 合并成功 → 通知队列中下一个任务 rebase

### 场景处理总结

| 场景 | 处理方式 |
|------|---------|
| 多任务修改不同文件 | 完全并行，无干预 |
| 多任务修改同一文件但不冲突 | 并行开发，合并队列顺序合入 |
| 多任务修改同一文件且冲突 | 冲突检测提前预警，合并时阻塞通知人工 |
| 语义冲突 | 依赖人工审阅 |

## 7. Agent 调度策略

### Agent Host 池

```sql
agent_hosts (
  id             TEXT PRIMARY KEY,
  host           TEXT NOT NULL,           -- SSH 地址或 "local"
  agent_type     TEXT NOT NULL,           -- "claude" / "codex" / "both"
  max_concurrent INTEGER DEFAULT 1,
  status         TEXT DEFAULT 'online',   -- online / offline / busy
  current_load   INTEGER DEFAULT 0,
  ssh_key        TEXT,
  labels         TEXT,
  last_heartbeat TEXT,
  created_at     TEXT NOT NULL
)
```

### 自动调度算法

1. 过滤：agent_type 匹配
2. 过滤：status == online
3. 过滤：current_load < max_concurrent
4. 排序：current_load 最少优先
5. 选中 → current_load += 1 → 调度

无可用 host 时：状态停留在 QUEUED，webhook 通知，有 host 空闲时自动重试。

### 手动覆盖

创建任务时可指定目标 host：

```json
{
  "ticket": "FEAT-123",
  "preferences": {
    "design_host": "my-pc",
    "dev_host": "server-a"
  }
}
```

### 健康检查

每 60 秒检查 host 可达性：
- local: 检查 claude/codex 命令是否存在
- remote: `ssh user@host "echo ok"` 超时 5 秒
- 不可达 → 标记 offline，恢复后自动标记 online

## 8. Agent 执行与监控

### 执行模式

放弃 tmux send-keys，改用非交互进程模式：

```bash
# Claude Code
claude -p "$(cat task.md)" --output-format json --max-turns 50

# Codex
codex -q --prompt "$(cat task.md)" --json
```

### 执行器架构

```python
class AgentExecutor:
    async def dispatch(self, run_id, host, task_file, agent_type) -> Job:
        cmd = self._build_command(agent_type, task_file)
        if host.host == "local":
            proc = await asyncio.create_subprocess_exec(*cmd, cwd=worktree, ...)
        else:
            proc = await asyncio.create_subprocess_exec("ssh", host.host, f"cd {worktree} && {cmd}", ...)
        job = Job(run_id=run_id, host_id=host.id, pid=proc.pid, process=proc)
        asyncio.create_task(self._watch(job))
        return job

    async def _watch(self, job):
        try:
            stdout, stderr = await asyncio.wait_for(job.process.communicate(), timeout=job.timeout)
            job.exit_code = job.process.returncode
            await self._on_complete(job)
        except asyncio.TimeoutError:
            job.process.kill()
            await self._on_timeout(job)
```

### Job 数据模型

```sql
jobs (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL,
  host_id       TEXT NOT NULL,
  agent_type    TEXT NOT NULL,
  stage         TEXT NOT NULL,
  task_file     TEXT NOT NULL,
  worktree      TEXT NOT NULL,
  pid           INTEGER,
  status        TEXT DEFAULT 'starting',  -- starting / running / completed / failed / timeout / cancelled / interrupted
  exit_code     INTEGER,
  output_log    TEXT,
  base_commit   TEXT,
  snapshot_json TEXT,
  resume_count  INTEGER DEFAULT 0,
  started_at    TEXT,
  ended_at      TEXT,
  timeout_sec   INTEGER DEFAULT 3600
)
```

### 产物检测

Agent 进程结束后立即扫描 worktree 中的产物文件，注册到 artifacts 表，然后自动 tick 推进状态。**秒级响应，不再依赖 cron 轮询。**

### 远程 host 的任务文件传递

通过 git 同步：
1. 本地：生成 task.md → git commit → git push
2. 远程：git pull → agent 执行
3. 远程：agent 产出 → git commit → git push
4. 本地：git pull → 检测产物 → 推进状态

## 9. 中断恢复策略

### 策略：不回滚，保留现场 + 支持续做

Agent 工作在独立 worktree 的独立分支上，不会污染 main 分支。中间产出（半成品代码/文档）有价值，不应直接丢弃。

### 中断时：保存现场快照

```python
async def _on_interrupted(self, job, reason):
    # git stash 保存未提交改动
    # 记录 base_commit、head_commit、commits_made、diff_stat、agent 输出
    # 保存到 jobs.snapshot_json
```

### 三个恢复选项

通过飞书询问用户：

1. **续做 (resume)** — 恢复 stash，生成续做任务单（包含进度上下文），在现有进度上继续
2. **重做 (redo)** — 丢弃 stash，`git reset --hard` 到 base_commit，用原始任务单重新执行
3. **人工介入 (manual)** — 保留现场不动，等待人工检查决定

超过 3 次续做自动建议重做。

### Webhook 通知

```json
{
  "event": "job.interrupted",
  "run_id": "...",
  "reason": "timeout after 3600s",
  "progress": {
    "commits_made": 3,
    "files_changed": 7,
    "diff_stat": "src/auth.py | 80 +++\nsrc/routes.py | 25 +++",
    "last_output": "正在编写单元测试..."
  },
  "options": ["resume", "redo", "manual"],
  "message": "FEAT-123 开发任务超时中断，已完成 3 个 commit（7 个文件）。请选择：\n1. 续做\n2. 重做\n3. 人工介入"
}
```

## 10. OpenClaw 集成协议

### 意图映射

| 用户自然语言 | API 调用 |
|-------------|---------|
| "我需要一个XX功能" / "新需求：XX" | `POST /runs` |
| "XX进展如何" / "查看状态" | `GET /runs/{run_id}` |
| "通过" / "可以" / "approved" | `POST /runs/{run_id}/approve` |
| "需要修改" / "XX有问题，请改成YY" | `POST /runs/{run_id}/reject` |
| "续做" / "继续之前的" | `POST /runs/{run_id}/recover {"action": "resume"}` |
| "重做" | `POST /runs/{run_id}/recover {"action": "redo"}` |
| "取消" | `DELETE /runs/{run_id}` |
| "所有任务" | `GET /runs` |
| "看看设计文档" | `GET /runs/{run_id}/artifacts/{id}/content` |

### Webhook → 飞书消息

OpenClaw 收到 webhook 后根据事件类型生成飞书消息：

- **阶段推进通知**：仅通知，无需回复
- **门控审阅**：阻塞，附带产物全文/摘要，等待用户回复"通过"或修改意见
- **任务中断**：附带进度，等待选择续做/重做/人工介入
- **冲突告警**：附带冲突文件，等待处理方式
- **任务完成**：附带变更摘要和测试结果

### 大文档处理

飞书单条消息约 4000 字符限制，超长文档分段发送。

### 上下文管理

OpenClaw 需要跟踪"当前等待用户回复的是哪个任务的哪个 gate"，以便用户简单回复"通过"时知道调哪个 API。

### 工具描述文件

提供 `docs/openclaw-tools.json`，定义所有 function calling 工具，OpenClaw 加载后即可使用。

## 11. 项目结构

### 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| Web 框架 | FastAPI + uvicorn | 原生 async，自动生成 OpenAPI 文档 |
| 数据库 | SQLite + aiosqlite | 轻量无依赖，单机足够 |
| 任务调度 | asyncio 原生 | 不引入重依赖 |
| SSH 执行 | asyncssh | 纯 Python async SSH 客户端 |
| 配置管理 | YAML + 环境变量 | agents.yaml 管理 host，.env 管理密钥 |
| 进程管理 | systemd | 管理 API 服务进程 |

### 目录结构

```
cooagents/
├── config/
│   ├── agents.yaml              # Agent host 配置
│   └── settings.yaml            # 全局配置
├── db/
│   └── schema.sql               # SQLite schema
├── docs/
│   ├── PROCESS.md
│   ├── API.md
│   ├── openclaw-tools.json
│   ├── design/
│   ├── dev/
│   ├── req/
│   └── ops/
├── src/
│   ├── __init__.py
│   ├── app.py                   # FastAPI 入口
│   ├── config.py                # 配置加载
│   ├── database.py              # 数据库连接与查询
│   ├── models.py                # Pydantic 数据模型
│   ├── state_machine.py         # 状态机核心逻辑
│   ├── artifact_manager.py      # 产物管理
│   ├── agent_executor.py        # Agent 调度与执行
│   ├── job_manager.py           # Job 生命周期管理
│   ├── host_manager.py          # Agent host 池管理
│   ├── webhook_notifier.py      # Webhook 回调
│   ├── merge_manager.py         # 合并队列与冲突检测
│   └── git_utils.py             # Git 操作封装
├── routes/
│   ├── __init__.py
│   ├── runs.py
│   ├── artifacts.py
│   ├── agent_hosts.py
│   ├── webhooks.py
│   └── repos.py
├── templates/
│   ├── TASK-claude.md
│   ├── TASK-codex.md
│   ├── TASK-claude-revision.md
│   ├── TASK-codex-revision.md
│   ├── TASK-resume.md
│   └── WEBHOOK-messages.yaml
├── scripts/
│   ├── bootstrap.sh
│   └── migrate.sh
├── tests/
│   ├── test_state_machine.py
│   ├── test_artifact_manager.py
│   ├── test_agent_executor.py
│   └── test_api.py
├── .env.example
├── requirements.txt
├── pyproject.toml
└── README.md
```

### 模块依赖

```
routes/*  →  state_machine  →  agent_executor  →  git_utils
                │                    │
                ▼                    ▼
         artifact_manager      job_manager
                │                    │
                ▼                    ▼
           database            host_manager
                │
                ▼
        webhook_notifier
        merge_manager → git_utils
```

单向依赖，无循环。

### 与旧代码关系

| 旧文件 | 处理 |
|--------|------|
| `scripts/workflow.py` | 拆分到 `src/state_machine.py` + `src/agent_executor.py`，废弃 |
| `scripts/workflow-*.sh` | 废弃，功能由 API 端点替代 |
| `scripts/tmux-dispatch.py` | 废弃，替换为 `src/agent_executor.py` |
| `scripts/workflow-notify-feishu.py` | 废弃，替换为 `src/webhook_notifier.py` |
| `scripts/workflow-assign.py` | 合并到 `src/artifact_manager.py` |
| `db/schema.sql` | 保留并扩展 |
| `templates/*.md` | 保留并扩展 |
| `docs/*.md` | 保留并更新 |

### 配置文件

**config/settings.yaml：**
```yaml
server:
  host: "127.0.0.1"
  port: 8321

database:
  path: ".coop/state.db"

timeouts:
  dispatch_startup: 300
  design_execution: 1800
  dev_execution: 3600
  review_reminder: 86400

health_check:
  interval: 60
  ssh_timeout: 5

merge:
  auto_rebase: true
  max_resume_count: 3
```

**config/agents.yaml：**
```yaml
hosts:
  - id: local-pc
    host: local
    agent_type: both
    max_concurrent: 2
  - id: dev-server
    host: dev@10.0.0.5
    agent_type: codex
    max_concurrent: 4
    ssh_key: ~/.ssh/id_rsa
    labels: [fast, gpu]
```

### Python 依赖

```
fastapi>=0.110
uvicorn[standard]>=0.29
aiosqlite>=0.20
asyncssh>=2.14
pyyaml>=6.0
pydantic>=2.0
httpx>=0.27
```
