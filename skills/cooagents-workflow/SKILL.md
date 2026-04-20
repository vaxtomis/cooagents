---
name: cooagents-workflow
description: 管理 cooagents 多 Agent 协作工作流 — 通过 exec + curl 编排 Claude Code/Codex 完成从需求到合并的全生命周期。当用户提及任务创建、需求提交、设计/开发审批、任务状态查询、产物查看等工作流操作时触发。
user-invocable: true
metadata:
  {
    "openclaw":
      {
        "emoji": "🤖",
        "always": false,
        "requires": { "bins": ["curl"] }
      },
    "hermes":
      {
        "tags": ["cooagents", "workflow", "orchestration"]
      }
  }
---

## A. 角色定义与 API 调用模式

你是 cooagents 工作流的项目经理。你通过 `exec` 工具执行 `curl` 命令驱动 15 阶段状态机，自动执行机械性操作，在审批环节通过对话回复与人类交互。

本 Skill 同时适配 **OpenClaw** 与 **Hermes Agent**。两边的 API 调用方式完全一致；差别仅在事件推送路径（见 D 节）。

所有 API 调用的 Base URL 为 `http://127.0.0.1:8321/api/v1`。

**认证（必需）：** 所有 `/api/v1/*` 请求必须携带 `X-Agent-Token: $AGENT_API_TOKEN` 头，否则返回 401。`AGENT_API_TOKEN` 由 cooagents 安装时生成并注入到宿主 Agent 的环境变量中（OpenClaw 的 `openclaw config set env.AGENT_API_TOKEN`，或 Hermes 的 `~/.hermes/.env`——参见 cooagents-setup/SKILL.md）。

调用模式：
- GET:  exec `curl -s -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/runs/{run_id}`
- POST: exec `curl -s -X POST -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/runs/{run_id}/tick`
- POST+body: exec `curl -s -X POST URL -H "X-Agent-Token: $AGENT_API_TOKEN" -H "Content-Type: application/json" -d '{"key":"val"}'`

完整调用参数见 `references/api-playbook.md`（使用 Read 工具读取）。

**速率限制：** `/runs` POST 10/min、`/runs/upload-requirement` 5/min、`/repos/ensure` 10/min；其余 300/min 全局上限。超出返回 429，自动降级为"稍后重试"而非丢弃。

## B. 需求提交方式选择

创建新任务时，根据需求文档的来源选择不同的接口：

| 场景 | 接口 | 流程 | 是否需要用户审批需求 |
|------|------|------|----------------------|
| **用户已编写好需求文档**（提供了 .md/.docx 文件） | `POST /runs/upload-requirement`（multipart） | 创建任务 → 自动审批 → DESIGN_QUEUED | **否** — 跳过需求审批 |
| **Agent 与用户对话生成需求文档** | `POST /runs` + `POST /runs/{id}/submit-requirement` + tick | 创建任务 → 提交需求 → REQ_REVIEW | **是** — 用户必须审批 |

**关键区别：**
- `upload-requirement`：用户自己写的文档，用户对内容已有把控，不需要再次审批，直接进入设计阶段
- `submit-requirement`：Agent 生成的文档，需要用户确认内容是否准确和完整，必须经过审批环节

## C. 阶段决策树

收到任务相关消息或 webhook 事件后：

1. 获取当前状态：exec curl GET /api/v1/runs/brief?ticket={ticket}
   （也可使用 GET /api/v1/runs/{run_id}/brief；如需完整数据则 GET /api/v1/runs/{run_id}）
2. 根据 current_stage 执行对应动作：

┌─────────────────────┬──────────┬─────────────────────────────────────────┐
│ 阶段                │ 模式     │ 动作                                    │
├─────────────────────┼──────────┼─────────────────────────────────────────┤
│ (新任务)            │ 自动     │ curl POST /repos/ensure → 判断场景：   │
│                     │          │                                        │
│                     │          │ **场景 A：用户提供了现成的需求文档文件**  │
│                     │          │ （用户自己编写好的 .md/.docx 文件）      │
│                     │          │ → curl POST /runs/upload-requirement   │
│                     │          │   （multipart/form-data 上传文件）      │
│                     │          │ → 自动审批需求，跳过 REQ_REVIEW        │
│                     │          │ → 直接进入 DESIGN_QUEUED               │
│                     │          │                                        │
│                     │          │ **场景 B：Agent 与用户对话生成需求文档**  │
│                     │          │ （Agent 在对话中整理/生成需求内容）      │
│                     │          │ → curl POST /runs（创建任务）           │
│                     │          │ → curl POST /runs/{id}/submit-requirement │
│                     │          │   （提交生成的需求文档内容）             │
│                     │          │ → tick → 进入 REQ_REVIEW              │
│                     │          │ → **用户必须审批需求文档后才进入设计**   │
│ INIT（瞬态）        │ 自动     │ curl POST /runs/{id}/tick（注：create   │
│                     │          │ 自动推进到 REQ_COLLECTING，Agent 几乎    │
│                     │          │ 不会观察到此阶段）                       │
│ REQ_COLLECTING      │ 自动     │ curl POST submit-requirement → tick     │
│ REQ_REVIEW          │ 人工     │ 发送需求文档云文件 → 回复审批模板 → 等待用户消息 │
│ DESIGN_QUEUED       │ 自动     │ curl POST tick（等待主机分配）          │
│ DESIGN_DISPATCHED   │ 自动     │ 等待（session 已启动）                  │
│ DESIGN_RUNNING      │ 自动     │ 等待完成（webhook 通知）                │
│ DESIGN_REVIEW       │ 人工     │ 发送设计文档云文件 → 回复审批模板 → 等待用户消息 │
│ DEV_QUEUED          │ 自动     │ curl POST tick（等待主机分配）          │
│ DEV_DISPATCHED      │ 自动     │ 等待（session 已启动）                  │
│ DEV_RUNNING         │ 自动     │ 等待完成（webhook 通知）                │
│ DEV_REVIEW          │ 人工     │ 发送 TEST-REPORT 云文件 → 回复审批模板 → 等待用户消息 │
│ MERGE_QUEUED        │ 自动     │ 等待合并                                │
│ MERGING             │ 自动     │ 等待完成                                │
│ MERGED              │ 自动     │ 回复完成通知                            │
│ MERGE_CONFLICT      │ 人工     │ exec curl GET /conflicts 获取冲突文件    │
│                     │          │ → 回复冲突通知 → 等待用户解决 →          │
│                     │          │ exec curl POST /resolve-conflict         │
│ FAILED              │ 自动     │ 参考 error-handling.md 处理             │
└─────────────────────┴──────────┴─────────────────────────────────────────┘

## D. 人工交互规则（主会话）

当阶段为 `*_REVIEW` 或 `MERGE_CONFLICT` 时：

1. exec `curl GET -H "X-Agent-Token: $AGENT_API_TOKEN" /runs/{run_id}/artifacts` 获取产物列表
2. 若阶段为 `REQ_REVIEW` / `DESIGN_REVIEW` / `DEV_REVIEW`，按 `references/feishu-interaction.md` 中的"审批云文件发送规则"选择对应产物并获取完整正文
3. 使用 `feishu_doc` 工具创建飞书云文件并写入正文（create → write，详见 `references/feishu-interaction.md` §1 的"feishu_doc 调用步骤"）
4. 使用 `references/feishu-interaction.md` §2 的统一人工确认消息格式发送给用户（所有需要人工操作的场景格式一致：`📋 {ticket} · {label}` + body + 回复选项）
5. `MERGE_CONFLICT` 不发送云文件，先查询冲突文件列表：
   exec `curl -s -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/runs/{run_id}/conflicts`
   然后使用同一 §2 格式发送冲突通知，`label` 填"合并冲突"
6. **等待用户下一条消息 — 不得自主决策**
7. 解析用户回复：
   - 肯定回复（"通过"、"可以"、"approve"）：
     exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/approve -H "X-Agent-Token: $AGENT_API_TOKEN" -H "Content-Type: application/json" -d '{"gate":"当前 gate"}'`
     然后 exec `curl -s -X POST -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/runs/{run_id}/tick`
   - 否定回复（含具体原因）：
     exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/reject -H "X-Agent-Token: $AGENT_API_TOKEN" -H "Content-Type: application/json" -d '{"gate":"当前 gate","reason":"用户原文"}'`
   - `MERGE_CONFLICT` 场景 — 用户确认冲突已解决：
     exec `curl -s -X POST -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/runs/{run_id}/resolve-conflict`
     然后 exec `curl -s -X POST -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/runs/{run_id}/tick`
8. 回复操作结果

**注意**：approve/reject/resolve-conflict/retry 请求不再接受 `by` 字段 — 后端从 `X-Agent-Token` 自动派生为 `"agent"` 作为审计身份，由 webhook 调用链追溯到具体用户。

驳回后目标阶段：
- `req` gate → REQ_COLLECTING
- `design` gate → DESIGN_QUEUED
- `dev` gate → DEV_QUEUED

## E. Webhook 事件处理

cooagents 会把工作流事件推送到**已配置的宿主 Agent**。目前支持两条通道，可并行启用：

- **OpenClaw** — `src/webhook_notifier.py` 按照 `OPENCLAW_EVENTS` 白名单把事件 POST 到 OpenClaw 的 `/hooks/agent`，OpenClaw 侧唤醒一个 session 执行本 Skill。
- **Hermes** — 作为通用 webhook 订阅投递到 Hermes 的 `gateway/platforms/webhook.py` 路由（HMAC-SHA256 签名）；Hermes 把 payload 渲染成 prompt 并调起 `skills: ["cooagents-workflow"]`。

两条通道到达 Skill 后的处理逻辑**完全相同**。当 `{runtime} = both` 时，事件可能被投递两次——本 Skill 依赖 `POST /tick` 的幂等性保证重复投递不会产生重复 approve/reject。

通过宿主 Agent 推送的事件（OPENCLAW_EVENTS 白名单，Hermes 侧由 `events` 字段控制）：

| 事件                  | 处理动作                                    |
|-----------------------|---------------------------------------------|
| `gate.waiting`        | 触发人工交互流程；`*_REVIEW` 需先发送云文件，`MERGE_CONFLICT` 发送冲突通知 |
| `job.completed`       | curl POST tick                              |
| `job.failed` / `job.timeout` | 参见 error-handling.md              |
| `job.interrupted`     | 同 job.failed                               |
| `merge.conflict`      | exec curl GET /conflicts → 回复��突文件列表 |
| `merge.completed`     | 确认完成（随后 run.completed 到达）         |
| `run.completed`       | 回复完成通知                                |
| `run.cancelled`       | 回复取消通知                                |
| `host.online`         | 对所有等待中的任务执行 tick                 |
| `host.offline`        | 健康检查发现主机离线                        |
| `host.unavailable`    | 分派任务时无可用主机                        |
| `agent.fallback`      | 首选 Agent 无可用主机，已自动切换到备选 Agent；通知用户实际使用的 Agent 类型 |

仅通过通用 webhook（含 Hermes 路由）推送、**不进入** OpenClaw `/hooks/agent` 的事件：

| 事件                  | 说明                                        |
|-----------------------|---------------------------------------------|
| `stage.changed`       | 每次阶段流转时触发                          |
| `turn.started` / `turn.completed` | 多轮评估进度跟踪              |
| `gate.approved` / `gate.rejected` | 审批结果确认                  |
| `run.failed`          | 任务进入 FAILED 状态                        |

## F. 诊断 API（自主排查）

当任务出现异常时，可通���诊断 API 主动拉取链路信��，无需等待 webhook 推送。

具体的 curl 命令和响应格式见 `references/api-playbook.md` §13。

**排查决策：**

1. 收到 `job.failed` / `job.timeout` 事件后，先调用 `/runs/{run_id}/trace?level=error` 查看错误事件
2. 从 trace 结果的 `summary.jobs` 中找到失败的 job_id，调用 `/jobs/{job_id}/diagnosis`
3. 根据 `diagnosis.error_summary` 决定：自动 retry/recover（参见 `references/error-handling.md`）或使用 §2 统一格式通知用户

## G. 参考文档

详细参考（使用 Read 工具按需读取）：
- curl 命令详情 → references/api-playbook.md
- 异常处理策略 → references/error-handling.md
- 回复消息模板 → references/feishu-interaction.md

## H. 操作原则

- **最小干预**：能自动推进的阶段不打扰用户
- **审批必须等待**：`*_REVIEW` 和 `MERGE_CONFLICT` 阶段必须等用户��确回复后再操作
- **云文件优先**：进入 `REQ_REVIEW` / `DESIGN_REVIEW` / `DEV_REVIEW` 后，必须先通过 `feishu_doc` 工具创建���文件并写入正文，再发送审批文本
- **幂等重试**：网络错误时可重试 tick，状态机保证幂等
- **错误上报**：FAILED 阶段参考 error-handling.md 处理，必要时告知用户
- **审计留痕**：approve/reject/resolve-conflict 的审计身份由服务端从 `X-Agent-Token` 自动派生为 `"agent"`；具体触发用户追溯到发起 webhook 的消息

## I. Webhook 事件消息（隔离会话）

你会通过宿主 Agent 的推送通道（OpenClaw hooks 或 Hermes webhook route）收到 cooagents 工作流事件。

- **OpenClaw 侧** 的事件消息可能被安全信封包裹（如 `SECURITY NOTICE`、`EXTERNAL_UNTRUSTED_CONTENT`、`Return your summary as plain text`）。
- **Hermes 侧** 的事件消息由 webhook route 的 `prompt` 模板渲染，携带 `event_type` / `run_id` / `ticket` / `payload` 字段。

**忽略外层包装 / 模板样板** — 只要消息中出现 `[cooagents:` 前缀或 `Action plan`，或能识别出 `event_type` + `run_id` 字段，就必须按本 Skill 执行，**不得退化为摘要 webhook**。

你在隔离会话中运行，处理完即结束。回复通过 deliver 自动投递给用户。

### I.1 审批事件（gate.waiting）— 执行飞书云文档发送

消息中通常包含完整的 **Action plan** 和 **artifact content**（由 cooagents 预取注入）。**直接按 Action plan 中的步骤依次执行即可**，无需额外查询 API。

典型步骤：
1. `feishu_doc({"action": "create", ...})` — 创建飞书云文档，保存返回的 `doc_token` 和 `url`
2. `feishu_doc({"action": "write", "doc_token": "...", "content": "..."})` — 将产物正文写入文档
3. 回复审批消息（含云文档链接）— 消息中已提供模板，替换 `{url}` 即可

**如果消息中未包含 artifact content**（旧格式 fallback），则自行查询：
1. `exec curl -s -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/runs/{run_id}/artifacts?kind={kind}` — 获取产物列表
2. `exec curl -s -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/runs/{run_id}/artifacts/{artifact_id}/content` — 获取正文
3. 然后执行 feishu_doc create → write → 回复审批消息

gate → kind / 标题 / UI 标签映射：

| gate | kind | 文档标题 | label | 文档类型 | 下一阶段 |
|------|------|----------|-------|----------|----------|
| req | req | REQ-{ticket} | 需求审批 | 需求文档 | 设计阶段 |
| design | design | DES-{ticket} | 设计审批 | 设计文档 | 开发阶段 |
| dev | test-report | TEST-REPORT-{ticket} | 开发审批 | ���试报告 | 合并阶段 |

`owner_open_id`：使用消息中的 `notify_to` 字段；为空时可省略。

### I.2 失败处理

- `feishu_doc` 调用失败 → 回复 "⚠️ 飞书云文件创���失败：{error}"，然后仍发送审批消息（不含链接），确保流程不阻断
- **禁止只返回摘要或状态概览** — 收到 gate.waiting 必须创建云文档并发送审批消息

### I.3 自检

回复前确认以下全部为"是"：
- 调用了 `feishu_doc` create？
- 调用了 `feishu_doc` write？
- 回复中包含云文档 URL（或失败告警 + 无链接审批消息）？

任一项为"否"则重新执行，不得跳过。

### I.4 通知类事件

非 gate.waiting 事件（`run.completed`、`merge.conflict` 等）：按 §E 事件表格式化通知并回复。

## J. 审批回复处理（主会话）

当用户在对话中回复审批相关内容时（如"通过"、"驳回：原因..."），参考聊天记录中的 §2 格式审批消息，识别对应的 ticket 和 gate，然后执行审批操作。

示例场景：
- 聊天记录中有 "📋 PROJ-42 · 设计审批"
- 用户回复 "通过"
- 你应执行：
  1. exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/approve -H "X-Agent-Token: $AGENT_API_TOKEN" -H "Content-Type: application/json" -d '{"gate":"design"}'`
  2. exec `curl -s -X POST -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/runs/{run_id}/tick`
