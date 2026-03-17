# cooagents

多 Agent 协作流程管理系统 —— 通过 HTTP API 编排 Claude Code / Codex 完成从需求到合并的全生命周期。

```mermaid
flowchart LR
    OC(["OpenClaw (Feishu)"]) -->|HTTP| API["cooagents API"]
    API -->|"acpx / SSH"| Agent["Claude Code / Codex"]
    Agent -.->|artifacts| API
    API -.->|webhook| OC
```

## 目录

- [核心特性](#核心特性)
- [架构概览](#架构概览)
- [快速启动](#快速启动)
- [配置说明](#配置说明)
- [工作流阶段](#工作流阶段)
- [API 参考](#api-参考)
- [模板系统](#模板系统)
- [数据库设计](#数据库设计)
- [OpenClaw 集成](#openclaw-集成)
- [测试](#测试)
- [项目结构](#项目结构)

## 核心特性

- **15 阶段状态机** — 从需求收集到代码合并，每个阶段可观测、可控制
- **多轮评估循环** — RUNNING 阶段自动评估产物质量，不达标则向 Agent 发送修订指令（设计最多 3 轮，开发最多 5 轮）
- **acpx Session 管理** — 基于 acpx CLI 的持久化会话，支持多轮交互、断点恢复、超时控制
- **三级审批 Gate** — 需求 / 设计 / 开发各设独立审批节点，支持驳回重做
- **产物版本管理** — 需求文档、设计文档、ADR、测试报告等产物自动扫描、哈希校验、版本追踪
- **多主机 Agent 池** — 支持本地 + SSH 远程主机，按负载自动选择，独立健康检查
- **优先级合并队列** — FIFO + 优先级排序，冲突检测，自动 rebase
- **Webhook 事件通知** — HMAC 签名、事件过滤、失败重试，21 种事件类型
- **Jinja2 模板引擎** — 灵活的任务指令模板，支持条件逻辑和循环

## 架构概览

```mermaid
flowchart TB
    OC(["OpenClaw (Feishu)"]) <-->|"HTTP / Webhook"| APP

    subgraph APP["cooagents API"]
        direction TB
        SM["State Machine"] --- AE["Acpx Executor"]
        SM --- AM["Artifact Manager"]
        SM --- MM["Merge Manager"]
        HM["Host Manager"] --- AE
        JM["Job Manager"] --- AE
        WH["Webhook Notifier"]
        SCH["Scheduler"]
        DB[("SQLite (aiosqlite)")]
    end

    AE -->|"acpx session"| CC["Claude Code\n(设计阶段)"]
    AE -->|"acpx session"| CX["Codex\n(开发阶段)"]
```

**技术栈：** FastAPI + aiosqlite + asyncssh + Jinja2 + Pydantic v2

**角色分工：**

| 角色 | 职责 | 执行方式 |
|------|------|----------|
| **OpenClaw** | 需求沟通确认、任务分配、Gate 审批 | 飞书对话 → HTTP API |
| **Claude Code** | 需求理解、功能设计、ADR 编写 | acpx session（`claude` 后端） |
| **Codex** | 编码实现、测试编写、代码提交 | acpx session（`codex` 后端） |

## 快速启动

### 环境要求

- Python 3.11+
- git
- `acpx` CLI（acpx 可自动适配 `claude` / `codex` 后端）

### 安装

```bash
git clone git@github.com:vaxtomis/cooagents.git
cd cooagents
scripts/bootstrap.sh
```

bootstrap 脚本会自动完成：检查 Python/git → 安装依赖 → 创建运行目录 → 初始化数据库。

### 启动服务

```bash
uvicorn src.app:app --host 127.0.0.1 --port 8321
```

启动后访问 API 文档：

| 地址 | 说明 |
|------|------|
| `http://127.0.0.1:8321/docs` | Swagger UI（交互式） |
| `http://127.0.0.1:8321/redoc` | ReDoc（阅读式） |
| `http://127.0.0.1:8321/health` | 健康检查 |

## 配置说明

### 服务配置 (`config/settings.yaml`)

```yaml
server:
  host: 127.0.0.1
  port: 8321

database:
  path: .coop/state.db

timeouts:
  dispatch_startup: 300      # Agent 启动超时（秒）
  design_execution: 1800     # 设计阶段超时
  dev_execution: 3600        # 开发阶段超时
  review_reminder: 86400     # 审批提醒间隔

health_check:
  interval: 60               # 健康检查间隔
  ssh_timeout: 5

merge:
  auto_rebase: true
  max_resume_count: 3

acpx:
  permission_mode: approve-all
  default_format: json
  ttl: 600                   # Session 空闲超时

turns:
  design_max_turns: 3        # 设计阶段最大轮次
  dev_max_turns: 5           # 开发阶段最大轮次
```

### Agent 主机配置 (`config/agents.yaml`)

```yaml
hosts:
  - id: local-pc
    host: local              # 本地执行
    agent_type: both         # claude + codex
    max_concurrent: 2

  - id: dev-server
    host: dev@10.0.0.5       # SSH 远程执行
    agent_type: codex
    max_concurrent: 4
    ssh_key: ~/.ssh/id_rsa
    labels:
      - gpu
      - high-memory
```

### 环境变量 (`.env`)

```bash
FEISHU_WEBHOOK_URL=          # 飞书 Webhook 回调地址（可选）
COOAGENTS_CONFIG_DIR=config  # 配置目录
COOAGENTS_COOP_DIR=.coop     # 运行时状态目录
```

## 工作流阶段

```mermaid
flowchart LR
    INIT:::auto --> RC["REQ_COLLECTING"]
    RC --> RR{"REQ_REVIEW 🚦"}
    RR -->|approve| DQ["DESIGN_QUEUED"]
    RR -->|reject| RC

    DQ --> DD["DESIGN_DISPATCHED"]:::auto --> DR["DESIGN_RUNNING"]
    DR -->|"revise ≤3轮"| DR
    DR --> DRV{"DESIGN_REVIEW 🚦"}
    DRV -->|approve| VQ["DEV_QUEUED"]
    DRV -->|reject| DQ

    VQ --> VD["DEV_DISPATCHED"]:::auto --> VR["DEV_RUNNING"]
    VR -->|"revise ≤5轮"| VR
    VR --> VRV{"DEV_REVIEW 🚦"}
    VRV -->|approve| MQ["MERGE_QUEUED"]:::auto
    VRV -->|reject| VQ

    MQ --> MG["MERGING"]:::auto --> MD(["MERGED"])

    classDef auto fill:#e8f5e9,stroke:#4caf50
```

- **🚦 审批 Gate** — 需要调用 `approve` 或 `reject` 端点通过
- **revise** — 自动检查产物完整性，不通过则发送修订指令继续

### 各阶段详细说明

| 阶段 | 触发方式 | 说明 |
|------|----------|------|
| `INIT` | 系统自动 | 创建 run 后立即转入 REQ_COLLECTING |
| `REQ_COLLECTING` | `submit-requirement` | 等待提交需求文档 |
| `REQ_REVIEW` | `approve` / `reject` | 人工审批需求 |
| `DESIGN_QUEUED` | `tick` | 等待可用 Claude 主机 |
| `DESIGN_DISPATCHED` | 自动 | acpx session 已启动 |
| `DESIGN_RUNNING` | 自动 | Agent 工作中，完成后自动评估产物 |
| `DESIGN_REVIEW` | `approve` / `reject` | 人工审批设计方案 |
| `DEV_QUEUED` | `tick` | 等待可用 Codex 主机 |
| `DEV_DISPATCHED` | 自动 | acpx session 已启动 |
| `DEV_RUNNING` | 自动 | Agent 工作中，完成后自动评估产物 |
| `DEV_REVIEW` | `approve` / `reject` | 人工审批代码 |
| `MERGE_QUEUED` | 自动 | 进入合并队列 |
| `MERGING` | 自动 | 执行合并 |
| `MERGED` | 自动 | 完成 |

### 多轮评估机制

在 `DESIGN_RUNNING` 和 `DEV_RUNNING` 阶段，Agent 完成工作后状态机会自动评估产物：

**设计阶段检查项：**
- `DES-{ticket}.md` — 设计文档
- `ADR-{ticket}.md` — 架构决策记录

**开发阶段检查项：**
- `TEST-REPORT-{ticket}.md` — 测试报告

如果缺少必需产物，系统会通过 `send_followup` 向 Agent 发送修订指令（TURN-revision / TURN-dev-fix 模板），并保持在 RUNNING 阶段。每轮修订记录在 `turns` 表中，达到上限后强制推进到 REVIEW 阶段。

## API 参考

### 工作流 (`/api/v1/runs`)

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/runs` | 创建任务 |
| `GET` | `/runs` | 列出任务（支持 `status`/`limit`/`offset` 过滤） |
| `GET` | `/runs/{id}` | 任务详情（含 steps、approvals、events、artifacts） |
| `POST` | `/runs/{id}/tick` | 推进一步 |
| `POST` | `/runs/{id}/submit-requirement` | 提交需求文档 |
| `POST` | `/runs/{id}/approve` | 审批通过（`gate`: req/design/dev） |
| `POST` | `/runs/{id}/reject` | 驳回（`gate` + `reason`） |
| `POST` | `/runs/{id}/retry` | 重试失败任务 |
| `POST` | `/runs/{id}/recover` | 恢复中断任务（`action`: resume/redo/manual） |
| `DELETE` | `/runs/{id}` | 取消任务 |

### 产物 (`/api/v1/runs/{id}/artifacts`)

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/artifacts` | 列出产物（支持 `kind`/`status` 过滤） |
| `GET` | `/artifacts/{aid}` | 产物元数据 |
| `GET` | `/artifacts/{aid}/content` | 产物内容 |
| `GET` | `/artifacts/{aid}/diff` | 与上一版本的 diff |

### Job 与合并 (`/api/v1/runs/{id}`)

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/jobs` | 列出 Agent 任务 |
| `GET` | `/jobs/{jid}/output` | Agent 输出内容 |
| `GET` | `/conflicts` | 合并冲突详情 |
| `POST` | `/merge` | 入队合并（支持 `priority`） |
| `POST` | `/merge-skip` | 跳过合并 |

### Agent 主机 (`/api/v1/agent-hosts`)

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/agent-hosts` | 列出所有主机 |
| `POST` | `/agent-hosts` | 注册新主机 |
| `PUT` | `/agent-hosts/{id}` | 更新主机配置 |
| `DELETE` | `/agent-hosts/{id}` | 移除主机 |
| `POST` | `/agent-hosts/{id}/check` | 健康检查 |

### Webhook (`/api/v1/webhooks`)

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/webhooks` | 创建订阅 |
| `GET` | `/webhooks` | 列出所有订阅 |
| `DELETE` | `/webhooks/{id}` | 移除订阅 |
| `GET` | `/webhooks/{id}/deliveries` | 投递记录 |

### 仓库视图 (`/api/v1/repos`)

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/repos` | 列出仓库或按仓库查询 run |
| `GET` | `/repos/merge-queue` | 合并队列状态 |

### 使用示例

```bash
# 创建任务
curl -X POST http://127.0.0.1:8321/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"ticket": "PROJ-42", "repo_path": "/path/to/repo"}'

# 提交需求
curl -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/submit-requirement \
  -H "Content-Type: application/json" \
  -d '{"content": "# 需求标题\n## 背景\n..."}'

# 审批通过设计
curl -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/approve \
  -H "Content-Type: application/json" \
  -d '{"gate": "design", "by": "reviewer-name"}'

# 驳回并说明原因
curl -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/reject \
  -H "Content-Type: application/json" \
  -d '{"gate": "design", "by": "reviewer-name", "reason": "缺少错误处理方案"}'

# 推进状态
curl -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick

# 查看详情
curl http://127.0.0.1:8321/api/v1/runs/{run_id}
```

## 模板系统

使用 Jinja2 引擎渲染 Agent 任务指令，模板位于 `templates/` 目录：

| 模板 | 用途 | 触发时机 |
|------|------|----------|
| `INIT-design.md` | 设计阶段初始任务 | DESIGN_QUEUED → DISPATCHED |
| `INIT-dev.md` | 开发阶段初始任务 | DEV_QUEUED → DISPATCHED |
| `TURN-revision.md` | 设计轮次修订指令 | 设计评估不通过时 |
| `TURN-dev-fix.md` | 开发轮次修复指令 | 开发评估不通过时 |
| `GATE-revision.md` | Gate 驳回后修订指令 | 审批驳回后重新调度 |
| `RESUME.md` | 中断恢复指令 | `recover` 操作 |
| `WEBHOOK-messages.yaml` | 事件通知消息模板 | Webhook 投递时 |

模板变量示例：`{{ ticket }}`、`{{ worktree }}`、`{{ feedback }}`、`{{ turn }}`

## 数据库设计

SQLite 数据库包含 10 张表：

```mermaid
erDiagram
    RUNS ||--o{ STEPS : has
    RUNS ||--o{ EVENTS : logs
    RUNS ||--o{ APPROVALS : has
    RUNS ||--o{ ARTIFACTS : produces
    RUNS ||--o{ JOBS : dispatches
    RUNS ||--o{ MERGE_QUEUE : enqueues
    JOBS ||--o{ TURNS : tracks

    RUNS {
        text id PK
        text ticket
        text repo_path
        text status
        text current_stage
    }
    JOBS {
        text id PK
        text run_id FK
        text host_id FK
        text agent_type
        text session_name
        int turn_count
    }
    ARTIFACTS {
        int id PK
        text run_id FK
        text kind
        int version
        text status
    }
    AGENT_HOSTS {
        text id PK
        text host
        text agent_type
        int max_concurrent
    }
    WEBHOOKS {
        int id PK
        text url
        text events_json
        text status
    }
```

| 表 | 说明 |
|----|------|
| `runs` | 工作流 run 记录，含状态、阶段、worktree 路径 |
| `steps` | 阶段转换历史（from → to） |
| `events` | 事件审计日志 |
| `approvals` | Gate 审批决策（approved / rejected） |
| `artifacts` | 产物版本，含 SHA256 哈希、字节大小、审批状态 |
| `jobs` | Agent 任务记录，含 session name、轮次计数 |
| `turns` | 每轮修订的提示文件、评估结果 |
| `agent_hosts` | 主机池，含负载、状态、SSH 配置 |
| `merge_queue` | 合并队列，含优先级和冲突文件 |
| `webhooks` | Webhook 订阅配置 |

## OpenClaw 集成

`docs/openclaw-tools.json` 提供了 11 个函数调用定义，可直接导入 OpenClaw 实现飞书对话式任务管理：

| 函数 | 对应 API |
|------|----------|
| `create_task` | `POST /api/v1/runs` |
| `list_tasks` | `GET /api/v1/runs` |
| `get_task_status` | `GET /api/v1/runs/{id}` |
| `submit_requirement` | `POST /api/v1/runs/{id}/submit-requirement` |
| `approve_gate` | `POST /api/v1/runs/{id}/approve` |
| `reject_gate` | `POST /api/v1/runs/{id}/reject` |
| `retry_task` | `POST /api/v1/runs/{id}/retry` |
| `recover_task` | `POST /api/v1/runs/{id}/recover` |
| `cancel_task` | `DELETE /api/v1/runs/{id}` |
| `list_artifacts` | `GET /api/v1/runs/{id}/artifacts` |
| `get_artifact_content` | `GET /api/v1/runs/{id}/artifacts/{aid}/content` |

### 配置 Webhook 接收通知

```bash
curl -X POST http://127.0.0.1:8321/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-openclaw-callback/webhook",
    "secret": "your-hmac-secret",
    "events": ["gate.waiting", "run.completed", "merge.conflict"]
  }'
```

### 支持的事件类型

| 类别 | 事件 |
|------|------|
| 阶段 | `stage.changed` |
| Gate | `gate.waiting` `gate.approved` `gate.rejected` |
| Job | `job.completed` `job.failed` `job.interrupted` `job.timeout` |
| Run | `run.completed` `run.cancelled` `run.retried` |
| 合并 | `merge.completed` `merge.conflict` |
| 主机 | `host.online` `host.offline` |
| 轮次 | `turn.started` `turn.completed` |
| Session | `session.created` `session.closed` |
| 其他 | `review.reminder` `requirement.submitted` |

## 测试

```bash
# 运行全部测试
pytest tests/ -v

# 运行特定模块
pytest tests/test_state_machine.py -v
pytest tests/test_e2e.py -v
pytest tests/test_acpx_executor.py -v
```

测试覆盖（97 个测试）：

| 模块 | 测试数 | 说明 |
|------|--------|------|
| `test_acpx_executor.py` | 24 | 命令构建、session 管理、exit code |
| `test_state_machine.py` | 16 | 状态转换、Gate、多轮评估 |
| `test_host_manager.py` | 8 | 选择、负载、健康检查 |
| `test_artifact_manager.py` | 7 | 注册、版本、Jinja2 渲染 |
| `test_merge_manager.py` | 7 | 队列、优先级、冲突 |
| `test_api.py` | 7 | HTTP 端点集成测试 |
| `test_git_utils.py` | 6 | worktree、冲突检测 |
| `test_webhook_notifier.py` | 6 | 订阅、过滤、投递 |
| `test_config.py` | 5 | 配置加载、默认值 |
| `test_e2e.py` | 4 | 完整流程、驳回重做、取消、重试 |
| `test_job_manager.py` | 3 | session、轮次追踪 |
| `test_database.py` | 3 | 连接、事务 |
| `test_scheduler.py` | 1 | 启动、停止 |

## 项目结构

```
cooagents/
├── config/
│   ├── agents.yaml            # Agent 主机配置
│   └── settings.yaml          # 服务配置
├── db/
│   └── schema.sql             # 数据库 Schema（10 表）
├── docs/
│   ├── PROCESS.md             # 流程说明
│   ├── openclaw-tools.json    # OpenClaw 函数定义
│   ├── design/                # 设计文档模板
│   └── dev/                   # 开发文档模板
├── routes/                    # FastAPI 路由
│   ├── runs.py                # 工作流端点
│   ├── artifacts.py           # 产物端点
│   ├── repos.py               # Job/合并端点
│   ├── agent_hosts.py         # 主机管理端点
│   └── webhooks.py            # Webhook 端点
├── src/                       # 核心模块
│   ├── app.py                 # FastAPI 应用入口
│   ├── state_machine.py       # 15 阶段状态机
│   ├── acpx_executor.py       # acpx session 执行器（唯一执行器）
│   ├── artifact_manager.py    # 产物版本管理
│   ├── host_manager.py        # 多主机管理
│   ├── job_manager.py         # Job/轮次追踪
│   ├── merge_manager.py       # 合并队列
│   ├── webhook_notifier.py    # Webhook 通知
│   ├── scheduler.py           # 后台调度器
│   ├── database.py            # 异步 SQLite
│   ├── config.py              # 配置加载
│   ├── models.py              # Pydantic 模型
│   ├── git_utils.py           # Git 操作工具
│   └── exceptions.py          # 自定义异常
├── templates/                 # Jinja2 任务模板
├── tests/                     # 测试套件（97 tests）
├── scripts/
│   └── bootstrap.sh           # 初始化脚本
├── requirements.txt
└── pyproject.toml
```

## 依赖

| 包 | 版本 | 用途 |
|----|------|------|
| fastapi | >=0.110 | HTTP API 框架 |
| uvicorn | >=0.29 | ASGI 服务器 |
| aiosqlite | >=0.20 | 异步 SQLite |
| asyncssh | >=2.14 | SSH 远程执行 |
| pydantic | >=2.0 | 数据验证 |
| jinja2 | >=3.1 | 模板渲染 |
| pyyaml | >=6.0 | YAML 配置 |
| httpx | >=0.27 | HTTP 客户端（Webhook） |

## License

MIT
