# Hermes Agent 集成细节

> 本文档为 `cooagents-setup` 阶段 ⑥ 的 C-6B Hermes 分支提供背景与故障排查。

## 1. 架构概览

```
┌─────────────────────────┐                   ┌────────────────────────────┐
│ cooagents               │  HMAC-SHA256 POST │ Hermes gateway             │
│ (webhook_notifier.py)   │ ───────────────▶ │ gateway/platforms/webhook  │
│                         │  X-Signature-256  │  route: "cooagents"        │
│  POST /api/v1/webhooks  │                   │  secret: HERMES_WEBHOOK_…  │
└────────────▲────────────┘                   │  prompt / skills / deliver │
             │                                └─────────────┬──────────────┘
             │ X-Agent-Token: $AGENT_API_TOKEN              │
             │                                              ▼
             │                                 Hermes 调起 cooagents-workflow skill
             │                                              │
             │  exec curl GET/POST /api/v1/* …              │
             └──────────────────────────────────────────────┘
```

关键点：
- cooagents **不要**自己再实现 Hermes 专用的推送协议。直接用现有 `POST /api/v1/webhooks` 创建订阅即可，HMAC 已内置。
- Hermes 侧只需要在 `config.yaml` 的 `platforms.webhook.extra.routes` 下加一条同名路由，secret 与订阅一致。
- Hermes 的 webhook route 会用 `prompt` 模板把 payload 渲染成 Agent prompt，再把它交给 `skills` 中列出的 Skill（这里是 `cooagents-workflow`）处理；Skill 中的 `exec curl` 需要 `AGENT_API_TOKEN` 环境变量可见。

## 2. 对比 OpenClaw 的差异

| 维度 | OpenClaw | Hermes |
|------|----------|--------|
| 推送协议 | 自定义 `/hooks/agent` + Bearer token | 通用 webhook + HMAC-SHA256 |
| 目录布局 | `~/.openclaw/skills/<name>/SKILL.md` | `~/.hermes/skills/<name>/SKILL.md` |
| CLI 入口 | `openclaw` | `hermes` |
| 环境变量写入 | `openclaw config set env.KEY VAL` | 追加到 `$(hermes config env-path)`（= `~/.hermes/.env`） |
| gateway 重启 | `openclaw restart`（如有） | `hermes gateway restart` |
| 事件投递方向 | cooagents → OpenClaw → 活跃 session | cooagents → Hermes webhook route → Skill |

## 3. 最小可用 route 片段

把下面这段合入 Hermes `config.yaml`：

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

字段说明：
- `events: ["*"]` — 接收所有事件，由 skill 自行过滤；生产建议改为具体列表，例如 `["gate.waiting","run.completed","run.failed"]`。
- `secret` — 必须与 cooagents 订阅里的 `secret` 完全一致；用 `${HERMES_WEBHOOK_SECRET}` 引用 env 便于轮换。
- `skills` — 事件落地后被 Hermes 执行的 skill 列表。保持为 `["cooagents-workflow"]` 即可。
- `deliver` — 审批/通知只需要触发 skill，不必立即回推文本到其他平台；用 `log` 最轻量。需要把结论转发到 Telegram/Discord 时改成对应的 deliver type。

## 4. 事件到 prompt 的字段

`webhook_notifier.py` 投递到订阅时的 JSON 大致形状：

```json
{
  "event_type": "gate.waiting",
  "run_id": "run-abcdef123456",
  "ticket": "TICKET-001",
  "gate": "design",
  "stage": "DESIGN_REVIEW",
  "payload": {...}        // event-specific detail
}
```

Hermes 的 `prompt` 模板支持对 payload 字段的花括号插值；skill 会读到 prompt 文本后调用 `cooagents-workflow` 的决策树。

## 5. 故障排查速查

| 症状 | 排查方向 |
|------|----------|
| `curl` 返回 `000` / `curl: (7)` | Hermes gateway 未启动或 port 8644 被占用 —— `hermes gateway status`；端口冲突改 `platforms.webhook.extra.port` |
| Hermes 日志 `HMAC mismatch` | `HERMES_WEBHOOK_SECRET` 与订阅 secret 不一致；对比 `~/.hermes/.env` 与 cooagents `/api/v1/webhooks` 返回的 secret |
| Skill 被触发但 `exec curl` 401 | `AGENT_API_TOKEN` 未注入 Hermes env；检查 `$(hermes config env-path)` 是否包含该行，重启 gateway |
| Hermes 接收到事件但没触发 skill | route 的 `skills` 字段漏写 / `deliver_only: true` 被误设 |
| Skill 不存在 | 确认 `~/.hermes/skills/cooagents-workflow/SKILL.md` 已由 `src/skill_deployer.py` 部署 |

## 6. 双宿主（OpenClaw + Hermes）并存

当 `{runtime} = both` 时：
- cooagents 的 `openclaw.hooks.enabled=true` 与 `hermes.webhook.enabled=true` 可以同时开启。
- OpenClaw 继续用 `OPENCLAW_EVENTS` 内定事件列表；Hermes 订阅自行指定 `events`。
- 两边会各收到一份事件，确保 skill 执行是幂等的（参考 `cooagents-workflow` 的决策树——状态转换都用 `POST /tick` 推进，重复投递不会产生重复 approve）。
- Skills 会被同时部署到 `~/.openclaw/skills/` 和 `~/.hermes/skills/`，两边的 SKILL.md 内容完全一致。
