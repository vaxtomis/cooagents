# cooagents

OpenClaw / Claude / Codex 多 Agent 协作流程管理系统（HTTP API 版）。

## 架构概览

基于 **FastAPI + SQLite (aiosqlite)** 的异步 HTTP API 服务器，替代原有 CLI+cron+tmux 方案。

```
OpenClaw (Feishu) ──HTTP──> cooagents API ──SSH/subprocess──> Claude Code / Codex
                   <─webhook─             <─artifacts──
```

**核心模块：**
- `src/state_machine.py` — 15 阶段状态机，驱动全流程
- `src/agent_executor.py` — 通过 subprocess 或 asyncssh 调度 Claude/Codex
- `src/artifact_manager.py` — 产物（需求/设计/ADR/代码/测试报告）版本管理
- `src/merge_manager.py` — 优先级合并队列，冲突检测
- `src/webhook_notifier.py` — HMAC 签名 webhook 通知，含重试

## 角色分工

- **OpenClaw** — 需求沟通确认、任务分配、流程 gate 审批（通过飞书交互）
- **Claude Code** — 需求理解、功能设计（非交互模式：`claude -p`）
- **Codex** — 编码实现、测试与提交（非交互模式：`codex -q`）

## 快速启动

```bash
git clone git@github.com:vaxtomis/cooagents.git
cd cooagents
scripts/bootstrap.sh
```

### 配置 Agent 主机

编辑 `config/agents.yaml`：

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
```

### 启动服务

```bash
uvicorn src.app:app --host 127.0.0.1 --port 8321
```

## API 文档

启动服务后访问自动生成的 API 文档：

- Swagger UI: `http://127.0.0.1:8321/docs`
- ReDoc: `http://127.0.0.1:8321/redoc`
- 健康检查: `GET /health`

### 核心端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/runs` | 创建任务 |
| GET | `/api/v1/runs` | 列出任务 |
| GET | `/api/v1/runs/{id}` | 任务详情（含步骤、产物、事件） |
| POST | `/api/v1/runs/{id}/submit-requirement` | 提交需求文档 |
| POST | `/api/v1/runs/{id}/approve` | 审批通过 |
| POST | `/api/v1/runs/{id}/reject` | 驳回 |
| POST | `/api/v1/runs/{id}/retry` | 重试失败任务 |
| POST | `/api/v1/runs/{id}/recover` | 恢复中断任务 |
| DELETE | `/api/v1/runs/{id}` | 取消任务 |

## 工作流阶段

```
INIT → REQ_COLLECTING → REQ_REVIEW → DESIGN_QUEUED → DESIGN_DISPATCHED
→ DESIGN_RUNNING → DESIGN_REVIEW → DEV_QUEUED → DEV_DISPATCHED
→ DEV_RUNNING → DEV_REVIEW → MERGE_QUEUED → MERGING → MERGED
```

每个 `*_REVIEW` 阶段为人工审批 gate，通过 approve/reject 端点控制。

## OpenClaw 集成

`docs/openclaw-tools.json` 提供了 11 个函数调用定义，可直接导入 OpenClaw 实现飞书对话式任务管理。

配置 webhook 接收事件通知：

```bash
curl -X POST http://127.0.0.1:8321/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-openclaw-callback/webhook", "secret": "your-secret"}'
```

## 依赖

- Python 3.11+
- git
- `claude` CLI（设计阶段）
- `codex` CLI（开发阶段）

## 相关文档

- 设计规格：`docs/superpowers/specs/2026-03-16-workflow-api-redesign-design.md`
- 实现计划：`docs/superpowers/plans/2026-03-16-workflow-api-redesign.md`
- 流程说明：`docs/PROCESS.md`
