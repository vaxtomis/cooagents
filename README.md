# cooagents

**Workspace 驱动的多 Agent 协作编排系统** —— 用 HTTP API 驱动 Claude Code / Codex 完成「设计 → 开发 → 准出」全闭环，由打分驱动的状态机自动收敛，人工只在四个关键点介入。

```mermaid
flowchart LR
    HOST(["🦉 OpenClaw / Hermes"]):::client -->|HTTP + Token| API["⚙️ cooagents API"]:::core
    DASH(["🖥️ Web Dashboard"]):::client -->|Session Cookie| API
    API --> WS[("📂 Workspace FS")]:::fs
    API --> DB[("🗄️ SQLite (WAL)")]:::db
    API -->|acpx / SSH| Agent["🤖 Claude Code / Codex"]:::agent
    Agent -.->|产物| WS
    API -.->|Webhook + HMAC| HOST

    classDef client fill:#1e1b4b,stroke:#818cf8,color:#e0e7ff
    classDef core   fill:#1a1a2e,stroke:#a855f7,color:#f5f5f5
    classDef agent  fill:#14532d,stroke:#4ade80,color:#dcfce7
    classDef fs     fill:#3f3f46,stroke:#a1a1aa,color:#fafafa
    classDef db     fill:#1e293b,stroke:#60a5fa,color:#e0f2fe
```

## 目录

- [核心概念](#核心概念)
- [核心特性](#核心特性)
- [快速启动](#快速启动)
- [配置](#配置)
- [宿主集成](#宿主集成)
- [API 参考](#api-参考)
- [事件与 Webhook](#事件与-webhook)
- [Success Metrics](#success-metrics)
- [Dashboard](#dashboard)
- [项目结构](#项目结构)
- [测试](#测试)
- [License](#license)

## 核心概念

v1 是**破坏性重构**后的 Workspace 模型。旧的 15 阶段线性 Run、artifact/merge_queue/agent_hosts 等表全部废弃。

| 概念 | 定义 | 存储 |
|------|------|------|
| **Workspace** | 并行工作的容器；磁盘上一个独立文件夹 + `workspace.md` 索引；本身不进 Git。 | `workspaces` 表 + `$WORKSPACES_ROOT/<slug>/` |
| **DesignWork** | 产出 SemVer 版本化设计文档的状态机（D0→D7），打分循环 ≤ `design.max_loops`（默认 3）。 | `design_works` 表 |
| **DesignDoc** | DesignWork 成功产出的 `DES-<slug>-<SemVer>.md`；只增、不改。 | `design_docs` 表 + `workspaces/<slug>/designs/` |
| **DevWork** | 指定代码仓库的 git worktree 内执行的 5 步状态机：校验 → 迭代设计 → 上下文 → 开发+自审 → 审核打分。 | `dev_works` 表 |
| **DevIterationNote** | DevWork Step2 产出的工作簿 markdown；含轮次 / 计划 / 动态用例 / Step5 反馈。 | `dev_iteration_notes` 表 |
| **Review** | Step5 / D5 的评分记录（score、issues、`problem_category`）。 | `reviews` 表 |
| **Gate** | 闸门审批（v1 只有 DevWork 的 `exit` gate）；状态 `waiting/approved/rejected`。 | `dev_works.gates_json` |
| **Workspace Event** | 统一事件总线，Webhook 与 Metrics 共用同一事件 ID。 | `workspace_events` 表 |

**人工介入点只有四个**：创建 Workspace、写首份设计、挑设计版本、准出审批。其余全自动。

**DevWork 回跳路由按 `problem_category` 分流**：`req_gap` / `impl_gap` / `design_hollow`；累计轮次超阈值触发 `dev_work.escalated`。

## 核心特性

- **Workspace 驱动** — 磁盘 + DB 双向投影，启动时 `reconcile()` 校验；单 DevWork/DesignDoc 通过 partial UNIQUE index 强制串行。
- **双状态机** — DesignWork（D0–D7，11 态）+ DevWork（STEP1–STEP5，8 态），单写入者，幂等 `tick`。
- **打分驱动迭代** — Step5 reviewer 输出 `score + problem_category`，SM 自行决定回跳到 Step2/3/4 或收敛。
- **版本化设计产物** — SemVer `1.0.0` + `content_hash` (SHA-256) + `byte_size`；`published` 后不可修改。
- **Exit Gate** — `config.devwork.require_human_exit_confirm=true` 时挂起等待人工审批；`POST /api/v1/gates/.../approve` 释放。
- **Webhook 契约** — `StrEnum` 冻结事件名（21 个）；`X-Cooagents-Signature: sha256=…` HMAC；`event_id` 消费者去重。
- **Metrics 投影** — `GET /api/v1/metrics/workspaces?since=&until=` 返回 PRD 四指标（active / HI per ws / FPS / avg rounds），纯只读聚合。
- **多主机 Agent 池** — 本地 + SSH 远程（asyncssh），`acpx` 适配 Claude/Codex；`allowed_tools_{design,dev}` 可做工具白名单。
- **双宿主适配** — OpenClaw（私有 `/hooks/agent` + Bearer）与 Hermes（通用 webhook + HMAC）同端共存，启动时自动部署 Skill。
- **Dashboard** — React 18 + Vite + Tailwind + SWR 15s 轮询；WorkspaceDashboard / WorkspaceDetail / DesignWork / DevWork / CrossWorkspaceDevWork。
- **E2E 烟测** — `tests/test_smoke_e2e.py` 驱动真实 SM 走三条路径（happy / design-escalated / devwork-escalated）并回查 `/metrics/workspaces`。

**技术栈：** FastAPI · aiosqlite (WAL) · asyncssh · Pydantic v2 · Jinja2 · SlowAPI · argon2-cffi · PyJWT · React 18 · TypeScript · Tailwind · SWR · Vitest

## 快速启动

### 环境要求

- Python 3.11+
- git、Node.js（`acpx` 安装用）
- （可选）pandoc —— `.docx` ↔ `.md`
- （可选）Nginx / Caddy —— 公网部署终止 HTTPS

### 安装

```bash
git clone git@github.com:vaxtomis/cooagents.git
cd cooagents
bash scripts/bootstrap.sh
```

`bootstrap.sh` 做：Python/git/node 校验 → 安装 `acpx` → 创建 venv → `pip install -r requirements.txt` → `cd web && npm ci && npm run build` → 校验 `web/dist/index.html` → 建 `.coop/` 并初始化 SQLite schema。

### 生成启动凭据

四个环境变量缺一即拒绝启动：`ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` / `JWT_SECRET` / `AGENT_API_TOKEN`。

```bash
umask 077 && .venv/bin/python scripts/generate_password_hash.py \
  --username admin --password '<YOUR_STRONG_PW>' > .env
chmod 600 .env
```

### 启动

```bash
set -a && . ./.env && set +a
.venv/bin/uvicorn src.app:app --host 127.0.0.1 --port 8321
```

| 地址 | 说明 |
|------|------|
| `http://127.0.0.1:8321/` | Dashboard（需登录） |
| `http://127.0.0.1:8321/health` | 健康检查 `{"status":"ok"}` |
| `http://127.0.0.1:8321/docs` | Swagger UI |
| `http://127.0.0.1:8321/redoc` | ReDoc |

> 推荐用 `/cooagents-setup` Skill 一键完成安装 + 启动 + 注册 Agent 主机 + 写回宿主 env。

## 配置

### `config/settings.yaml`

关键字段（完整见文件）：

```yaml
server:   { host: 127.0.0.1, port: 8321 }      # 公网部署必走反向代理
database: { path: .coop/state.db }

acpx:
  permission_mode: approve-all
  ttl: 600
  model: null                       # 使用 agent 默认模型
  allowed_tools_design: null        # 工具白名单（逗号分隔）
  allowed_tools_dev: null

turns:  { design_max_turns: 3, dev_max_turns: 3 }
design: { max_loops: 3, execution_timeout: 600 }
scoring:{ default_threshold: 80 }

openclaw:
  deploy_skills: true
  targets: [{ type: local, skills_dir: "~/.openclaw/skills" }]
  hooks:
    enabled: false
    url: "http://127.0.0.1:18789/hooks/agent"
    token: ""                       # 建议 "$ENV:OPENCLAW_HOOK_TOKEN"

hermes:
  enabled: false
  skills_dir: "~/.hermes/skills"
  deploy_skills: true
  webhook:
    enabled: false
    url: "http://127.0.0.1:8644/webhook/cooagents"
    secret: ""                      # 建议 "$ENV:HERMES_WEBHOOK_SECRET"
    events: []
```

### `config/agents.yaml`

```yaml
hosts:
  - id: local-pc
    host: local
    agent_type: both            # claude + codex
    max_concurrent: 2
  - id: dev-server
    host: dev@10.0.0.5          # SSH
    agent_type: codex
    max_concurrent: 4
    ssh_key: ~/.ssh/id_rsa
```

### `.env`（权限 600）

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=$argon2id$v=19$m=...
JWT_SECRET=...
AGENT_API_TOKEN=...
# 可选：
HERMES_WEBHOOK_SECRET=...
OPENCLAW_HOOK_TOKEN=...
FEISHU_WEBHOOK_URL=...
COOAGENTS_CONFIG_DIR=config
COOAGENTS_COOP_DIR=.coop
```

### 开发环境升级（Phase 2 及以后）

Phase 2 引入 `workspace_files` 表并将若干 `*_path` 字段语义改为
工作区相对路径，**不提供历史数据迁移脚本**。开发者升级时：

```bash
rm -rf .coop/state.db
rm -rf "$WORKSPACES_ROOT"/*
```

然后重启服务即可。首次生产部署前再补历史回填脚本（见 PRD
`oss-file-storage-upgrade.prd.md` §Historical Data Migration）。

## 宿主集成

cooagents 同时支持两种宿主；可单启、也可并存（`{runtime}=both`）。启动时 `src/skill_deployer.py` 把 `skills/cooagents-{setup,upgrade}/` 同步到宿主的 `skills/` 目录。

| 维度 | OpenClaw | Hermes |
|------|----------|--------|
| 推送协议 | 私有 `/hooks/agent` + `Authorization: Bearer` | 通用 webhook route + `X-Cooagents-Signature` HMAC-SHA256 |
| Skill 路径 | `~/.openclaw/skills/<name>/` | `~/.hermes/skills/<name>/` |
| CLI | `openclaw` | `hermes` |
| env 注入 | `openclaw config set env.KEY VAL` | 追加到 `$(hermes config env-path)` |
| 重启 | `openclaw restart` | `hermes gateway restart` |

### OpenClaw（一键）

1. 从仓库复制 `skills/cooagents-setup/` → `~/.openclaw/skills/cooagents-setup/`。
2. 在 OpenClaw 对话里 `/cooagents-setup`，按提示填 `repo_path` 和 `admin_password`，Skill 自动：安装 → 启动 → 注册 Agent 主机 → 写回 `openclaw.hooks.*` 与 `env.AGENT_API_TOKEN`。

手动补 hooks：

```bash
HOOKS_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
openclaw config set hooks.enabled true --strict-json
openclaw config set hooks.token "$HOOKS_TOKEN"
openclaw config set hooks.defaultSessionKey "hook:ingress"
openclaw config set hooks.allowedSessionKeyPrefixes '["hook:"]' --strict-json
openclaw config set env.AGENT_API_TOKEN "$AGENT_API_TOKEN"
# 在 cooagents config/settings.yaml 的 openclaw.hooks.token 填同一个值
```

### Hermes（一键）

1. 从仓库复制 `skills/cooagents-setup/` → `~/.hermes/skills/cooagents-setup/`。
2. 在 Hermes 里 `/cooagents-setup`，Skill 自动走 Hermes 分支：生成 HMAC secret、写入 `~/.hermes/.env`、注册 webhook route、订阅事件、注入 `AGENT_API_TOKEN`。

手动配置核心步骤：

```bash
HERMES_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
printf "HERMES_WEBHOOK_SECRET=%s\nAGENT_API_TOKEN=%s\n" \
  "$HERMES_SECRET" "$AGENT_API_TOKEN" >> "$(hermes config env-path)"
chmod 600 "$(hermes config env-path)"
printf "\nHERMES_WEBHOOK_SECRET=%s\n" "$HERMES_SECRET" >> .env
```

Hermes `config.yaml`：

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 8644
      routes:
        cooagents:
          events: ["*"]
          secret: "${HERMES_WEBHOOK_SECRET}"
          skills: ["cooagents-workflow"]
          deliver: "log"
```

向 cooagents 注册订阅：

```bash
curl -X POST http://127.0.0.1:8321/api/v1/webhooks \
  -H "X-Agent-Token: $AGENT_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"http://127.0.0.1:8644/webhook/cooagents\",
       \"events\":[\"dev_work.gate.exit_waiting\",\"dev_work.completed\",
                   \"dev_work.escalated\",\"workspace.human_intervention\"],
       \"secret\":\"$HERMES_SECRET\"}"
```

## API 参考

所有 `/api/v1/*`（`/auth/*` 除外）需 Session Cookie 或 `X-Agent-Token`（等于 `AGENT_API_TOKEN`）。完整 Swagger 见 `/docs`。

### Workspace

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/v1/workspaces` | 创建（同时落盘 scaffold） |
| GET | `/api/v1/workspaces?status=active` | 列表 |
| GET | `/api/v1/workspaces/{id}` | 详情 |
| DELETE | `/api/v1/workspaces/{id}` | 归档（status=archived） |
| POST | `/api/v1/workspaces/sync` | DB ↔ FS 一致性报告 |
| GET | `/api/v1/workspaces/{id}/events` | 只读事件流 |

### DesignWork / DesignDoc

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/v1/design-works` | 创建 DesignWork（进入 D0→D1） |
| GET | `/api/v1/design-works?workspace_id=X` | 列表 |
| GET | `/api/v1/design-works/{id}` | 详情 |
| POST | `/api/v1/design-works/{id}/tick` | 单步推进（幂等） |
| POST | `/api/v1/design-works/{id}/cancel` | 取消 |
| GET | `/api/v1/design-docs?workspace_id=X` | 设计文档索引 |
| GET | `/api/v1/design-docs/{id}` | 元数据 |
| GET | `/api/v1/design-docs/{id}/content` | Markdown 原文 |

### DevWork / IterationNote

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/v1/dev-works` | 创建 DevWork（准入自动完成） |
| GET | `/api/v1/dev-works?workspace_id=X` | 列表 |
| GET | `/api/v1/dev-works/{id}` | 详情 |
| POST | `/api/v1/dev-works/{id}/tick` | 单步推进 |
| POST | `/api/v1/dev-works/{id}/cancel` | 取消 |
| GET | `/api/v1/dev-works/{id}/iteration-notes` | 迭代记录索引 |
| GET | `/api/v1/dev-iteration-notes/{id}/content` | Markdown 原文 |

### Gate / Review / Metrics / Webhook

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/v1/gates/{gate_id}` | gate_id = `dev:<dev_work_id>:exit` |
| POST | `/api/v1/gates/{gate_id}/{approve\|reject}` | 准出审批，60/min 限流 |
| GET | `/api/v1/reviews?dev_work_id=X` | Step5 / D5 评分记录 |
| GET | `/api/v1/metrics/workspaces?since=&until=` | PRD 四指标聚合 |
| POST/GET/DELETE | `/api/v1/webhooks[/{id}]` | 订阅管理（新契约） |
| GET | `/api/v1/webhooks/{id}/deliveries` | 投递历史 |
| POST | `/api/v1/repos/ensure` | 克隆/拉取代码仓到指定路径 |

## 事件与 Webhook

出站信封（统一）：

```json
{
  "event": "<event_name>",
  "event_id": "<uuid>",
  "ts": "<ISO8601>",
  "workspace_id": "ws-...",
  "correlation_id": "<dev_work_id | design_work_id | ...>",
  "payload": { "...": "..." }
}
```

HTTP 头：

- `X-Cooagents-Event: <event_name>`
- `X-Cooagents-Signature: sha256=<hmac_hex>`（`body` 用订阅 `secret` 做 HMAC-SHA256）
- `X-Cooagents-Event-Id: <uuid>`（消费者去重用）

事件清单（冻结；见 [src/webhook_events.py](src/webhook_events.py)；契约快照测试 [test_envelope_contract.py](tests/test_envelope_contract.py)）：

| 分类 | 事件 |
|------|------|
| Workspace | `workspace.created` · `workspace.archived` · `workspace.human_intervention` |
| DesignWork | `design_work.started` · `design_work.llm_completed` · `design_work.mockup_recorded` · `design_work.round_completed` · `design_work.escalated` · `design_work.cancelled` |
| DesignDoc | `design_doc.published` |
| DevWork | `dev_work.started` · `dev_work.step_started` · `dev_work.step_completed` · `dev_work.round_completed` · `dev_work.score_passed` · `dev_work.escalated` · `dev_work.completed` · `dev_work.cancelled` |
| DevWork Gate | `dev_work.gate.exit_waiting` |
| DevWork Merge | `dev_work.merge_conflict`（forward-compat，v1 无 emit） |
| Internal | `webhook.delivery_failed`（投递失败自记录） |

> **注意**：不存在 `dev_work.gate.entry_waiting` —— 「准入」即用户 `POST /dev-works` 的动作本身，不是 SM 等待态。

## Success Metrics

`GET /api/v1/metrics/workspaces` 返回 PRD 四项指标：

```json
{
  "human_intervention_per_workspace": 1.25,
  "active_workspaces": 3,
  "first_pass_success_rate": 0.67,
  "avg_iteration_rounds": 2.1
}
```

| 字段 | 计算 | 说明 |
|------|------|------|
| `active_workspaces` | `COUNT(*) WHERE status='active'` | **不**随 `since/until` 窗口过滤 —— 这是「当前活跃」瞬时值 |
| `human_intervention_per_workspace` | `#HI events / #workspaces`（窗口内） | HI = `workspace.human_intervention` 事件 |
| `first_pass_success_rate` | 终态 `dev_works` 中 `first_pass_success=1` 的比例 | 分母是 `COMPLETED ∪ ESCALATED` |
| `avg_iteration_rounds` | 终态 `dev_works` 的 `iteration_rounds` 均值 | |

窗口参数：`?since=&until=` 接受 ISO8601（`Z` 后缀 / naive / 带偏移都支持，内部归一到 `+00:00`）。分母为 0 时返回 `0.0`（不会除零）。

## Dashboard

React 18 + Vite + Tailwind，SWR 15 秒轮询。

- **WorkspaceDashboard** —— 四块 HeroStat（直接取 `/metrics/workspaces`）+ 活跃 Workspace 清单
- **WorkspaceDetail** —— Workspace 元数据 + 所属 DesignDoc / DesignWork / DevWork 表
- **DesignWorkPage** —— D0→D7 状态机进度条 + LLM 轮次 + Review 打分
- **DevWorkPage** —— STEP1–STEP5 进度条 + IterationNote + Step5 打分 + exit-gate 审批面板
- **CrossWorkspaceDevWorkPage** —— 跨 Workspace 的 DevWork 聚合视图
- **AgentHostsPage** —— Agent 主机池状态
- **LoginPage** —— 基于 Session Cookie 的登录

## 项目结构

```text
cooagents/
├── config/                  settings.yaml · agents.yaml
├── db/schema.sql            9 表：workspaces / design_docs / design_works /
│                             dev_works / dev_iteration_notes / reviews /
│                             workspace_events / workspace_files /
│                             webhook_subscriptions
├── docs/                    design / dev / internals / openclaw-tools.json
├── scripts/                 bootstrap.sh · generate_password_hash.py
├── skills/                  cooagents-{setup,upgrade}/ —— 启动时部署到宿主
├── src/                     FastAPI app + 两个 SM + manager + webhook notifier
├── routes/                  HTTP 路由（每类实体一文件）
├── templates/               Jinja2 任务指令模板（STEP* / TURN* / GATE* / 信封）
├── tests/                   pytest（含 test_smoke_e2e.py 三条端到端路径）
└── web/                     React + TS + Tailwind Dashboard
```

## 测试

```bash
# 全量
pytest tests/ -v

# 关键单测
pytest tests/test_design_work_sm.py tests/test_dev_work_sm.py
pytest tests/test_metrics_route.py tests/test_gates_route.py
pytest tests/test_envelope_contract.py      # 冻结事件契约
pytest tests/test_smoke_e2e.py              # 端到端三路径

# 前端
cd web && npx vitest run
```

覆盖面：SM（DesignWork / DevWork）、路由层（每个实体独立）、manager（workspace / design_doc / dev_iteration_note）、auth / database / git_utils / acpx_executor / reviewer / semver / file_converter / skill_deployer / webhook_notifier / openclaw_hooks。

### Running OSS integration tests

Set the following env vars before invoking `pytest tests/integration -v`:
- `OSS_BUCKET` — bucket name of the test bucket (NOT a prod bucket)
- `OSS_ENDPOINT` — e.g. `https://oss-cn-hangzhou.aliyuncs.com`
- `OSS_REGION` — e.g. `cn-hangzhou`
- `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` — RAM user credentials with `oss:PutObject`, `oss:GetObject`, `oss:DeleteObject`, `oss:HeadObject`, `oss:ListObjects` on the test bucket
- Set `OSS_RUN_SLOW=1` to additionally run the 1010-key pagination test

Tests auto-skip when any required variable is missing.

## License

MIT — 见 [LICENSE](LICENSE)。
