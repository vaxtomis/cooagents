# cooagents

多 Agent 协作流程管理系统 —— 通过 HTTP API 编排 Claude Code / Codex 完成从需求到合并的全生命周期。支持 **OpenClaw** 与 **Hermes** 两种宿主 Agent。

```mermaid
flowchart LR
    HOST(["🦉 OpenClaw / Hermes"]):::client -->|HTTP| API["⚙️ cooagents API"]:::core
    DASH(["🖥️ Dashboard"]):::client -->|HTTP| API
    API -->|"acpx / SSH"| Agent["🤖 Claude Code / Codex"]:::agent
    Agent -.->|artifacts| API
    API -.->|webhook| HOST

    classDef client fill:#1e1b4b,stroke:#818cf8,color:#e0e7ff
    classDef core fill:#1a1a2e,stroke:#a855f7,color:#f5f5f5
    classDef agent fill:#14532d,stroke:#4ade80,color:#dcfce7
```

## 目录

- [核心特性](#核心特性)
- [快速启动](#快速启动)
- [宿主集成](#宿主集成)
  - [OpenClaw](#openclaw)
  - [Hermes](#hermes)
- [配置](#配置)
- [工作流阶段](#工作流阶段)
- [API 参考](#api-参考)
- [事件与 Webhook](#事件与-webhook)
- [测试与项目结构](#测试与项目结构)

## 核心特性

- **16 阶段状态机** — 需求 → 设计 → 开发 → 合并，每一步可观测、可控制
- **多轮评估循环** — 产物不达标时自动发送修订指令（设计≤3 轮，开发≤5 轮）
- **三级审批 Gate** — 需求 / 设计 / 开发各独立审批
- **多主机 Agent 池** — 本地 + SSH 远程，按负载自动选择
- **产物版本管理** — SHA256 校验、diff、`.md` / `.docx` 下载（pandoc）
- **三层链路追踪** — Request → Run → Job 全链路 `trace_id`，响应头 `X-Trace-Id`
- **Webhook 通知** — HMAC 签名、事件过滤、失败重试
- **Dashboard** — React + TypeScript，任务列表、详情、产物、事件追踪
- **双宿主适配** — OpenClaw（私有 `/hooks/agent`）与 Hermes（通用 webhook route）

**技术栈：** FastAPI + aiosqlite + asyncssh + Jinja2 + Pydantic v2 + React + TypeScript + Tailwind CSS

## 快速启动

### 环境要求

- Python 3.11+
- git、Node.js（用于安装 `acpx`）
- （可选）pandoc —— `.docx` ↔ `.md` 转换
- （可选）Nginx / Caddy 反向代理 —— 公网部署需终止 HTTPS

### 安装

```bash
git clone git@github.com:vaxtomis/cooagents.git
cd cooagents
bash scripts/bootstrap.sh
```

`bootstrap.sh` 自动完成：Python 校验 → git/node/npm 检查 → acpx 安装 → venv + pip → `web/` 构建 → 校验 `web/dist/index.html` → 目录与 DB 初始化。

如果你想手动复现前端构建步骤，等价命令是：

```bash
cd web
npm ci
npm run build
test -f dist/index.html
```

### 生成启动凭据

公网部署要求以下环境变量，缺一即拒绝启动：`ADMIN_USERNAME`、`ADMIN_PASSWORD_HASH`、`JWT_SECRET`、`AGENT_API_TOKEN`。用内置脚本一次生成：

```bash
.venv/bin/python scripts/generate_password_hash.py --username admin --password '<YOUR_STRONG_PW>'
# 把输出的 4 行写入 .env
umask 077 && .venv/bin/python scripts/generate_password_hash.py \
  --username admin --password '<YOUR_STRONG_PW>' > .env
chmod 600 .env
```

### 启动服务

```bash
set -a && . ./.env && set +a
.venv/bin/uvicorn src.app:app --host 127.0.0.1 --port 8321
```

验证：

| 地址 | 说明 |
|------|------|
| `http://127.0.0.1:8321/` | Dashboard（需登录） |
| `http://127.0.0.1:8321/health` | 健康检查，返回 `{"status":"ok"}` |
| `http://127.0.0.1:8321/docs` | Swagger UI |
| `http://127.0.0.1:8321/redoc` | ReDoc |

> 推荐用 `/cooagents-setup` Skill 代替手工安装 —— 见下节。

## 宿主集成

cooagents 会把 16 阶段工作流事件推送给宿主 Agent；宿主 Agent 通过 Skill 调用 cooagents API（所有请求需带 `X-Agent-Token: $AGENT_API_TOKEN`）。两种宿主对比：

| 维度 | OpenClaw | Hermes |
|------|----------|--------|
| 推送协议 | 私有 `/hooks/agent` + Bearer | 通用 webhook + HMAC-SHA256 |
| Skill 路径 | `~/.openclaw/skills/<name>/` | `~/.hermes/skills/<name>/` |
| CLI | `openclaw` | `hermes` |
| env 写入 | `openclaw config set env.KEY VAL` | 追加到 `$(hermes config env-path)` |
| gateway 重启 | `openclaw restart` | `hermes gateway restart` |

cooagents 启动时由 `src/skill_deployer.py` 自动把仓库内 `skills/` 下三个 Skill（`cooagents-setup`、`cooagents-upgrade`、`cooagents-workflow`）同步到宿主的 skills 目录。详见 [skills/cooagents-setup/references/hermes-integration.md](skills/cooagents-setup/references/hermes-integration.md)。

### OpenClaw

#### 一键安装（推荐）

1. 从仓库复制 `skills/cooagents-setup/` 到 `~/.openclaw/skills/cooagents-setup/`（首次无 cooagents 运行时的引导方式）。
2. 在 OpenClaw 对话中调用 `/cooagents-setup`，按提示填写 `repo_path`、`admin_password`。Skill 会完成安装 + 启动 + 注册 Agent 主机 + 回写 `openclaw.hooks`、`env.AGENT_API_TOKEN`。

#### 手动配置（安装已完成，只补 hooks）

```bash
# 生成 hooks 专用 token（严禁复用 gateway.auth.token）
HOOKS_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')

openclaw config set hooks.enabled true --strict-json
openclaw config set hooks.token "$HOOKS_TOKEN"
openclaw config set hooks.defaultSessionKey "hook:ingress"
openclaw config set hooks.allowRequestSessionKey false --strict-json
openclaw config set hooks.allowedSessionKeyPrefixes '["hook:"]' --strict-json

# 注入 AGENT_API_TOKEN 到 OpenClaw 环境
openclaw config set env.AGENT_API_TOKEN "$AGENT_API_TOKEN"

# 在 config/settings.yaml 中把相同 token 配到 openclaw.hooks
```

示例 `settings.yaml`：

```yaml
openclaw:
  deploy_skills: true
  targets:
    - type: local
      skills_dir: "~/.openclaw/skills"
  hooks:
    enabled: true
    url: "http://127.0.0.1:18789/hooks/agent"
    token: "$ENV:OPENCLAW_HOOKS_TOKEN"   # 或直接字面值
```

### Hermes

#### 一键安装（推荐）

1. 从仓库复制 `skills/cooagents-setup/` 到 `~/.hermes/skills/cooagents-setup/`。
2. 在 Hermes 中调用 `/cooagents-setup`，Skill 识别到当前宿主为 `hermes` 后会执行 **C-6B Hermes 分支**：生成 HMAC secret、写入 `~/.hermes/.env`、注册 webhook route、订阅 cooagents webhook、注入 `AGENT_API_TOKEN`。

#### 手动配置

```bash
# 1. 生成 HMAC secret
HERMES_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')

# 2. 写入 Hermes 环境（供 webhook route 引用）
printf "HERMES_WEBHOOK_SECRET=%s\n" "$HERMES_SECRET" >> "$(hermes config env-path)"
printf "AGENT_API_TOKEN=%s\n"       "$AGENT_API_TOKEN" >> "$(hermes config env-path)"
chmod 600 "$(hermes config env-path)"

# 3. 把 secret 同步到 cooagents .env
printf "\nHERMES_WEBHOOK_SECRET=%s\n" "$HERMES_SECRET" >> /path/to/cooagents/.env
```

在 Hermes `config.yaml` 的 `platforms.webhook.extra.routes` 下追加一条 `cooagents` 路由：

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
          prompt: |
            cooagents 推送事件：{event_type}
            run_id: {run_id}
            ticket: {ticket}

            payload: {payload}
          deliver: "log"
```

重启 Hermes gateway 并在 cooagents `settings.yaml` 启用：

```yaml
hermes:
  enabled: true
  skills_dir: "~/.hermes/skills"
  deploy_skills: true
  webhook:
    enabled: true
    url: "http://127.0.0.1:8644/webhooks/cooagents"
    secret: "$ENV:HERMES_WEBHOOK_SECRET"
```

最后向 cooagents 注册一条 webhook 订阅（HMAC 与 Hermes route 的 secret 相同）：

```bash
curl -X POST http://127.0.0.1:8321/api/v1/webhooks \
  -H "X-Agent-Token: $AGENT_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"http://127.0.0.1:8644/webhooks/cooagents\",
       \"events\":[\"gate.waiting\",\"run.completed\",\"run.failed\",\"merge.conflict\"],
       \"secret\":\"$HERMES_SECRET\"}"
```

#### 验证

```bash
# 无签名时应返回 401，签名正确时返回 202
curl -s -X POST http://127.0.0.1:8644/webhooks/cooagents \
  -H "Content-Type: application/json" -d '{"ping":"1"}' -o /dev/null -w "%{http_code}\n"

# Hermes 拿到 AGENT_API_TOKEN
hermes exec 'echo $AGENT_API_TOKEN'
```

> `openclaw` 与 `hermes` 两个分支可以同时启用（`{runtime}=both`）；状态机 `POST /tick` 本身幂等，重复投递不会产生重复 approve。

## 配置

### `config/settings.yaml`（关键字段）

```yaml
server: { host: 0.0.0.0, port: 8321 }
database: { path: .coop/state.db }
timeouts: { dispatch_startup: 300, design_execution: 1800, dev_execution: 3600 }
acpx: { permission_mode: approve-all, ttl: 600 }
turns: { design_max_turns: 3, dev_max_turns: 5 }
tracing: { enabled: true, retention_days: 7 }

# 二选一或两者并存
openclaw: { hooks: { enabled: false, url: "...", token: "..." } }
hermes:   { webhook: { enabled: false, url: "...", secret: "..." } }
```

### `config/agents.yaml`

```yaml
hosts:
  - id: local-pc
    host: local
    agent_type: both        # claude + codex
    max_concurrent: 2
  - id: dev-server
    host: dev@10.0.0.5      # SSH 远程
    agent_type: codex
    max_concurrent: 4
    ssh_key: ~/.ssh/id_rsa
```

### `.env`（安装时生成，权限 600）

```
ADMIN_USERNAME=...
ADMIN_PASSWORD_HASH=...
JWT_SECRET=...
AGENT_API_TOKEN=...
HERMES_WEBHOOK_SECRET=...     # 仅 Hermes 启用时
```

## 工作流模型

v1 采用 **Workspace 驱动** 模型（完整设计见 `.claude/PRPs/prds/workspace-driven-task-refactor.prd.md`）：

- **Workspace** 是并行工作的容器，对应磁盘上的一个独立文件夹 + `workspace.md` 索引；Workspace 本身不进 Git。
- **DesignWork**（D0→D7 状态机）产出 SemVer 版本化设计文档 `DES-<slug>-<ver>.md`。
- **DevWork**（5 步状态机：校验 → 迭代设计 → 上下文检索 → 开发+自审 → 审核打分）在指定代码仓库的 git worktree 里执行，由打分驱动的闭环自动收敛；回跳路由按 `problem_category` (`req_gap` / `impl_gap` / `design_hollow`) 分流，累计轮次超阈值触发 `devwork.escalated`。
- 人工介入点：创建 Workspace、写首份设计、挑设计版本、准入/准出四处；其余全自动。

## API 参考

核心端点（完整 Swagger 见 `/docs`）：

| 分组 | 端点 | 说明 |
|------|------|------|
| Workspace | `POST/GET/DELETE /api/v1/workspaces[/{id}]` | Workspace 生命周期 |
| DesignWork | `POST /api/v1/design-works` / `GET /api/v1/design-works?workspace_id=X` | 设计工作状态机 |
| DesignDoc | `GET /api/v1/design-docs[/{id}][/content]` | 设计文档只读投影 |
| DevWork | `POST /api/v1/dev-works` / `GET /api/v1/dev-works?workspace_id=X` | 开发工作状态机 |
| IterationNote | `GET /api/v1/dev-works/{id}/iteration-notes` / `GET /api/v1/dev-iteration-notes/{id}/content` | 迭代设计文件 |
| Review | `GET /api/v1/reviews?dev_work_id=X` | Step5 / D5 评分记录 |
| Event | `GET /api/v1/workspaces/{id}/events` | Workspace 事件流（只读） |
| Gate | `POST /api/v1/gates/{gate_id}/{approve|reject}` | 闸门双通道审批 |
| Webhook | `POST/GET/DELETE /api/v1/webhooks` | 订阅（新契约） |

所有 `/api/v1/*`（除 `/auth/*`）需 Session 认证（登录后携带 cookie）。

## 事件与 Webhook

新契约统一信封：

```json
{"event":"<event_name>","event_id":"<uuid>","ts":"<ISO8601>","correlation_id":"<id>","payload":{...}}
```

| 分类 | 事件（节选） |
|------|----------|
| Workspace | `workspace.created` `workspace.archived` `workspace.human_intervention` |
| DesignWork | `design_work.started` `design_work.round_completed` `design_work.escalated` |
| DesignDoc | `design_doc.published` |
| DevWork | `dev_work.started` `dev_work.step_started` `dev_work.step_completed` `dev_work.round_completed` `dev_work.score_passed` `dev_work.escalated` `dev_work.completed` |
| Gate | `dev_work.gate.entry_waiting` `dev_work.gate.exit_waiting` |

签名：`X-Cooagents-Signature: sha256=<hmac>`。事件自带 `event_id` 用于消费方去重。

订阅示例：

```bash
curl -X POST http://127.0.0.1:8321/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{"url":"https://your-callback","secret":"hmac-secret",
       "events":["dev_work.gate.entry_waiting","dev_work.completed"]}'
```

## 测试与项目结构

```bash
pytest tests/ -v                         # full suite
pytest tests/test_dev_work_sm.py         # 单模块
```

目录速览：

```
cooagents/
├── config/          # settings.yaml
├── db/schema.sql    # 8 表（workspace 模型）
├── docs/            # docs & openclaw-tools.json
├── scripts/         # bootstrap.sh、generate_password_hash.py
├── skills/          # cooagents-{setup,upgrade}/ —— 启动时部署到宿主
├── src/             # FastAPI、workspace / design / dev 状态机、webhook notifier
├── routes/          # HTTP 路由
├── templates/       # Jinja2 任务指令模板
├── tests/           # pytest 套件
└── web/             # React + TS + Tailwind Dashboard
```

## License

MIT
