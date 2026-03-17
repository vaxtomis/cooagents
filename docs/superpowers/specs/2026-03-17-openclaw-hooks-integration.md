# OpenClaw Hooks 集成设计文档

> 日期：2026-03-17
> 状态：待评审

## 1. 目标

让 cooagents 的 webhook 事件实时推送到 OpenClaw `/hooks/agent` 端点，由 OpenClaw Agent（已加载 cooagents-workflow Skill）自动处理并投递到指定用户的消息渠道。实现"用户发起任务后坐等通知"的完整自动化。

## 2. 当前状态

cooagents 的 `WebhookNotifier` 当前是通用 webhook 推送：
- 注册一个 URL + 可选 event filter + 可选 HMAC secret
- 事件触发时 POST 标准 JSON `{"event": "...", "payload": {...}}`
- 21 种事件类型，Jinja2 模板渲染消息

**问题**：OpenClaw 没有通用 webhook receiver。它有专用的 `/hooks/agent` 端点，协议格式不同。

## 3. OpenClaw `/hooks/agent` 协议

```
POST /hooks/agent
Authorization: Bearer <token>
Content-Type: application/json

{
  "message": "string (必填，Agent 收到的消息内容)",
  "name": "string (可选，来源名称)",
  "deliver": boolean (可选，默认 true，是否投递到渠道)",
  "channel": "feishu" | "telegram" | "slack" | ... (可选，目标渠道)",
  "to": "string (可选，目标用户/群组 ID)",
  "wakeMode": "now" | "next-heartbeat" (可选，默认 now)",
  "idempotencyKey": "string (可选，最长 256 字符)"
}

Response: {"ok": true, "runId": "..."}
```

关键行为：
- Agent 在**隔离会话**中处理消息（非用户主会话）
- `deliver: true` 时，Agent 回复自动投递到指定 `channel` + `to`
- 认证：Bearer token 或 `x-openclaw-token` header
- 限制：body 最大 256KB，认证失败限速 20次/60秒

### 3.1 会话模型与审批流程

> **关键架构约束**：`/hooks/agent` 创建的是**一次性隔离会话**（`sessionTarget: "isolated"`）。Agent 运行完毕后会话即销毁，无法接收后续消息。用户在飞书中的回复始终路由到**主会话**（由 `resolveRoute()` 决定）。

这意味着**通知**和**审批处理**天然分在两个会话中完成：

```
cooagents 事件
    │
    ▼
隔离会话（一次性）
├─ Agent 加载 cooagents-workflow Skill
├─ exec curl GET /artifacts（获取产物内容）
├─ 格式化审批模板
├─ deliver: true → 投递到飞书用户
└─ 会话销毁
                                    飞书聊天窗口
                                    ├─ [Bot] 📋 任务 PROJ-42 等待审批 (design)
                                    │        【设计文档摘要】...
                                    │        请回复"通过"或驳回原因
                                    │
                                    ├─ [用户] 通过
                                    │
                                    ▼
                                主会话（持久）
                                ├─ Agent 同样加载 cooagents-workflow Skill
                                ├─ 看到聊天记录中的审批请求 + 用户回复
                                ├─ 识别为审批操作
                                ├─ exec curl POST /approve
                                └─ exec curl POST /tick
```

**此模型可行的原因：**

1. **Skill 全局加载**：cooagents-workflow 是全局 Skill（非项目级），隔离会话和主会话的 Agent 都会加载，具备相同的决策能力。
2. **聊天记录共享**：隔离 Agent 通过 `deliver` 投递的消息出现在飞书聊天记录中，主会话 Agent 处理用户回复时能看到完整上下文（审批请求 + 用户回复），足以判断用户意图。
3. **单向依赖**：隔离会话只负责"通知"（一次性），不需要维持状态；主会话负责"交互"（持久），天然适合处理用户回复。

**通知类事件**（`run.completed`、`merge.conflict` 等）不涉及后续交互，隔离会话投递后即完成，无需主会话参与。

**审批类事件**（`gate.waiting`）涉及双向交互，分两步完成：
- **Step 1**（隔离会话）：格式化审批请求，投递到飞书用户
- **Step 2**（主会话）：用户回复 → Agent 解析 → 调用 approve/reject API

## 4. 设计方案

### 4.1 通知目标配置

**两层配置**：per-run 优先 → 全局默认兜底。

**创建任务时指定**（per-run）：
```bash
curl -X POST http://127.0.0.1:8321/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{
    "ticket": "PROJ-42",
    "repo_path": "/path/to/repo",
    "notify_channel": "feishu",
    "notify_to": "ou_abc123"
  }'
```

`notify_channel` 和 `notify_to` 存入 `runs` 表，webhook 投递时优先使用。

**全局默认**（settings.yaml）：
```yaml
openclaw:
  hooks:
    url: "http://127.0.0.1:18789/hooks/agent"
    token: "${OPENCLAW_HOOKS_TOKEN}"
    default_channel: "feishu"
    default_to: "ou_default_user"
```

### 4.2 WebhookNotifier 改造

当前 `notify()` 对所有订阅者统一发送标准 JSON。改为：

1. **保留通用 webhook 订阅机制**（向后兼容，其他消费方仍可注册）
2. **新增 OpenClaw hooks 投递路径**：如果 `openclaw.hooks.url` 已配置且 `openclaw.hooks.enabled` 为 true，额外向 OpenClaw `/hooks/agent` 发送

投递逻辑：

```python
async def notify(self, event_type, payload):
    # 1. 现有通用 webhook 投递（不变）
    await self._deliver_to_subscribers(event_type, payload)

    # 2. OpenClaw hooks 投递（新增）
    if self.openclaw_hooks_enabled:
        await self._deliver_to_openclaw(event_type, payload)
```

### 4.3 消息格式

OpenClaw Agent 需要从 `message` 字段解析事件信息。格式设计为**结构化纯文本**，Agent 易于理解：

```
[cooagents:{event_type}] {rendered_message}
run_id: {run_id}
ticket: {ticket}
stage: {current_stage}
```

示例：
```
[cooagents:gate.waiting] 任务 PROJ-42 等待审阅 (design)
run_id: run-abc123
ticket: PROJ-42
stage: DESIGN_REVIEW
```

Agent 收到后按 SKILL.md 决策树处理：看到 `DESIGN_REVIEW` → fetch 产物 → 格式化审批模板 → 通过 `deliver` 投递到用户飞书。

### 4.4 幂等键

格式：`cooagents:{run_id}:{event_type}:{timestamp_s}`

- `timestamp_s` 取秒级精度，同一秒内的重复事件被去重
- 最长 256 字符（OpenClaw 限制）

### 4.5 事件过滤

并非所有 21 种事件都需要推送到 OpenClaw。只推需要 Agent 响应的事件：

| 事件 | 推送 | Agent 响应 |
|------|------|-----------|
| `gate.waiting` | 是 | 发送审批请求模板 |
| `job.completed` | 是 | exec curl tick |
| `job.failed` | 是 | exec curl retry |
| `job.timeout` | 是 | exec curl recover |
| `job.interrupted` | 是 | exec curl retry |
| `merge.conflict` | 是 | 通知用户冲突 |
| `merge.completed` | 是 | 通知用户完成 |
| `run.completed` | 是 | 通知用户完成 |
| `run.cancelled` | 是 | 通知用户取消 |
| `host.online` | 是 | tick 等待中的任务 |
| `stage.changed` | 否 | 由上述具体事件覆盖 |
| `gate.approved` / `gate.rejected` | 否 | Agent 自己触发的，无需回推 |
| `turn.*` / `session.*` / `review.reminder` | 否 | 内部追踪，不需要 Agent 响应 |

## 5. 文件变更

### 数据库

`db/schema.sql` — `runs` 表新增两列：
```sql
ALTER TABLE runs ADD COLUMN notify_channel TEXT;
ALTER TABLE runs ADD COLUMN notify_to TEXT;
```

### 配置

`config/settings.yaml` — `openclaw` 节新增 `hooks` 子节：
```yaml
openclaw:
  deploy_skills: true
  targets:
    - type: local
      skills_dir: "~/.openclaw/skills"
  hooks:
    enabled: true
    url: "http://127.0.0.1:18789/hooks/agent"
    token: "${OPENCLAW_HOOKS_TOKEN}"
    default_channel: "feishu"
    default_to: ""
```

`src/config.py` — 新增 `OpenclawHooksConfig`：
```python
class OpenclawHooksConfig(BaseModel):
    enabled: bool = False
    url: str = "http://127.0.0.1:18789/hooks/agent"
    token: str = ""
    default_channel: str = "feishu"
    default_to: str = ""
```

### API 模型

`src/models.py` — `CreateRunRequest` 新增可选字段：
```python
class CreateRunRequest(BaseModel):
    ticket: str
    repo_path: str
    description: str | None = None
    preferences: dict | None = None
    notify_channel: str | None = None
    notify_to: str | None = None
```

### 状态机

`src/state_machine.py` — `create_run()` 存储 `notify_channel` 和 `notify_to` 到 `runs` 表。

### Webhook 通知

`src/webhook_notifier.py` — 新增 `_deliver_to_openclaw()` 方法：
- 接收 `event_type` 和 `payload`（含 `run_id`）
- 查 `runs` 表获取 `notify_channel`、`notify_to`、`ticket`、`current_stage`
- 如果 per-run 未配置则用全局默认
- 渲染 message 文本
- POST 到 `/hooks/agent`
- 失败重试（同现有逻辑）

### Skill 更新

`skills/cooagents-workflow/SKILL.md` — 新增两节：

**Webhook 事件消息格式**（Agent 在隔离会话中收到）：
```markdown
## Webhook 事件消息

你会通过 hooks 收到格式如下的事件通知：

[cooagents:{event_type}] {消息}
run_id: {run_id}
ticket: {ticket}
stage: {current_stage}

收到后按上方决策树中对应阶段的动作执行。

注意：你在隔离会话中运行，处理完即结束。你的回复会通过 deliver 机制自动投递到用户的消息渠道。
对于审批类事件（gate.waiting），你需要：
1. 获取产物内容并格式化审批模板
2. 回复审批模板（会自动投递到用户）
3. 你不需要等待用户回复 — 用户的回复会由主会话 Agent 处理
```

**审批回复处理**（Agent 在主会话中收到用户回复）：
```markdown
## 审批回复处理

当用户在对话中回复审批相关内容时（如"通过"、"驳回：原因..."），参考聊天记录中的审批请求消息，识别对应的 ticket 和 gate，然后执行审批操作。

示例场景：
- 聊天记录中有 "📋 任务 PROJ-42 等待审批 (design)"
- 用户回复 "通过"
- 你应执行：
  1. exec curl POST /approve (gate=design, by=用户标识)
  2. exec curl POST /tick
```

## 6. 会话模型总结

| 场景 | 会话类型 | Agent 行为 |
|------|----------|-----------|
| 收到 `[cooagents:*]` 前缀消息 | 隔离会话 | 按决策树处理事件，回复自动投递到用户 |
| 用户主动询问任务状态 | 主会话 | exec curl 查询状态，回复用户 |
| 用户回复审批（"通过"/"驳回"） | 主会话 | 从聊天上下文识别 ticket 和 gate，执行 approve/reject |
| 用户发起新任务 | 主会话 | 从对话提取 ticket + repo_path，exec curl 创建任务 |

## 7. OpenClaw 侧配置

在 OpenClaw 的 `openclaw.json` 中启用 hooks：

```json5
{
  "hooks": {
    "enabled": true,
    "token": "your-secret-token"    // 与 cooagents settings.yaml 中一致
  }
}
```

不需要配置 `mappings`，cooagents 直接 POST 到 `/hooks/agent`。

## 8. 不在范围内

- 不修改 OpenClaw 源码
- 不实现 OpenClaw 插件
- 不修改现有通用 webhook 订阅机制（保留向后兼容）
- 不实现 per-event 的 channel/to 路由（统一用 per-run 配置）
