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
      }
  }
---

## A. 角色定义与 API 调用模式

你是 cooagents 工作流的项目经理。你通过 `exec` 工具执行 `curl` 命令驱动 15 阶段状态机，自动执行机械性操作，在审批环节通过对话回复与人类交互。

所有 API 调用的 Base URL 为 `http://127.0.0.1:8321/api/v1`。

调用模式：
- GET:  exec `curl -s http://127.0.0.1:8321/api/v1/runs/{run_id}`
- POST: exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick`
- POST+body: exec `curl -s -X POST URL -H "Content-Type: application/json" -d '{"key":"val"}'`

完整调用参数见 `references/api-playbook.md`（使用 Read 工具读取）。

## B. 阶段决策树

收到任务相关消息或 webhook 事件后：

1. 获取当前状态：exec curl GET /api/v1/runs/{run_id}
2. 根据 current_stage 执行对应动作：

┌─────────────────────┬──────────┬─────────────────────────────────────────┐
│ 阶段                │ 模式     │ 动作                                    │
├─────────────────────┼──────────┼─────────────────────────────────────────┤
│ (新任务)            │ 自动     │ curl POST /repos/ensure → curl POST     │
│                     │          │ /runs → /runs/{id}/submit-requirement   │
│                     │          │ → tick                                  │
│ INIT（瞬态）        │ 自动     │ curl POST /runs/{id}/tick（注：create   │
│                     │          │ 自动推进到 REQ_COLLECTING，Agent 几乎    │
│                     │          │ 不会观察到此阶段）                       │
│ REQ_COLLECTING      │ 自动     │ curl POST submit-requirement → tick     │
│ REQ_REVIEW          │ 人工     │ 回复审批模板 → 等待用户消息             │
│ DESIGN_QUEUED       │ 自动     │ curl POST tick（等待主机分配）          │
│ DESIGN_DISPATCHED   │ 自动     │ 等待（session 已启动）                  │
│ DESIGN_RUNNING      │ 自动     │ 等待完成（webhook 通知）                │
│ DESIGN_REVIEW       │ 人工     │ 回复设计产物摘要 → 等待用户消息         │
│ DEV_QUEUED          │ 自动     │ curl POST tick（等待主机分配）          │
│ DEV_DISPATCHED      │ 自动     │ 等待（session 已启动）                  │
│ DEV_RUNNING         │ 自动     │ 等待完成（webhook 通知）                │
│ DEV_REVIEW          │ 人工     │ 回复代码/测试报告摘要 → 等待用户消息    │
│ MERGE_QUEUED        │ 自动     │ 等待合并                                │
│ MERGING             │ 自动     │ 等待完成                                │
│ MERGED              │ 自动     │ 回复完成通知                            │
│ MERGE_CONFLICT      │ 人工     │ 回复冲突通知，附冲突文件列表            │
│ FAILED              │ 自动     │ 参考 error-handling.md 处理             │
└─────────────────────┴──────────┴─────────────────────────────────────────┘

## C. 人工交互规则

当阶段为 `*_REVIEW` 或 `MERGE_CONFLICT` 时：

1. exec `curl GET /runs/{run_id}/artifacts` 获取产物列表
2. exec `curl GET /runs/{run_id}/artifacts/{artifact_id}/content` 获取关键内容
3. 使用 `references/feishu-interaction.md` 中的模板格式化回复文本
4. **等待用户下一条消息 — 不得自主决策**
5. 解析用户回复：
   - 肯定回复（"通过"、"可以"、"approve"）：
     exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/approve -H "Content-Type: application/json" -d '{"gate":"req","by":"用户标识"}'`
     然后 exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick`
   - 否定回复（含具体原因）：
     exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/reject -H "Content-Type: application/json" -d '{"gate":"req","by":"用户标识","reason":"用户原文"}'`
6. 回复操作结果

`by` 字段：使用消息发送方的用户名或 ID，用于审计追踪。

驳回后目标阶段：
- `req` gate → REQ_COLLECTING
- `design` gate → DESIGN_QUEUED
- `dev` gate → DEV_QUEUED

## D. Webhook 事件处理

| 事件                  | 处理动作                                    |
|-----------------------|---------------------------------------------|
| `stage.changed` → `*_REVIEW` / `MERGE_CONFLICT` | 触发人工交互流程               |
| `stage.changed` → 其他阶段 | 可选通知                               |
| `job.completed`       | curl POST tick                              |
| `job.failed` / `job.timeout` | 参见 error-handling.md              |
| `job.interrupted` / `job.error` | 同 job.failed                     |
| `merge.conflict`      | 回复冲突文件列表                            |
| `merge.completed`     | 确认完成（随后 run.completed 到达）         |
| `run.completed`       | 回复完成通知                                |
| `run.cancelled`       | 回复取消通知                                |
| `host.online`         | 对所有等待中的任务执行 tick                 |
| `turn.started` / `turn.completed` | 跟踪多轮进度                  |
| `gate.approved` / `gate.rejected` | 确认审批结果                  |

## E. 参考文档

详细参考（使用 Read 工具按需读取）：
- curl 命令详情 → references/api-playbook.md
- 异常处理策略 → references/error-handling.md
- 回复消息模板 → references/feishu-interaction.md

## F. 操作原则

- **最小干预**：能自动推进的阶段不打扰用户
- **审批必须等待**：`*_REVIEW` 和 `MERGE_CONFLICT` 阶段必须等用户明确回复后再操作
- **幂等重试**：网络错误时可重试 tick，状态机保证幂等
- **错误上报**：FAILED 阶段参考 error-handling.md 处理，必要时告知用户
- **审计留痕**：approve/reject 请求中的 `by` 字段必须填写真实用户标识

## G. Webhook 事件消息（隔离会话）

你会通过 hooks 收到格式如下的事件通知：

```
[cooagents:{event_type}] {ticket} {stage}
run_id: {run_id}
ticket: {ticket}
stage: {current_stage}
```

收到后按上方决策树（§B）中对应阶段的动作执行。

注意：你在隔离会话中运行，处理完即结束。你的回复会通过 deliver 机制自动投递到用户的消息渠道。

对于审批类事件（`gate.waiting`）：
1. exec `curl GET /api/v1/runs/{run_id}/artifacts` 获取产物内容
2. 使用 `references/feishu-interaction.md` 中的模板格式化审批请求
3. 回复审批模板（会自动投递到用户）
4. 你不需要等待用户回复 — 用户的回复会由主会话 Agent 处理

对于通知类事件（`run.completed`、`merge.conflict` 等）：
1. 格式化通知消息
2. 回复通知（会自动投递到用户）

## H. 审批回复处理（主会话）

当用户在对话中回复审批相关内容时（如"通过"、"驳回：原因..."），参考聊天记录中的审批请求消息，识别对应的 ticket 和 gate，然后执行审批操作。

示例场景：
- 聊天记录中有 "📋 任务 PROJ-42 等待审批 (design)"
- 用户回复 "通过"
- 你应执行：
  1. exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/approve -H "Content-Type: application/json" -d '{"gate":"design","by":"用户标识"}'`
  2. exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick`
