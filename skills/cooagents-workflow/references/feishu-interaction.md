# 回复消息模板

Agent 的审批与通知消息会通过当前对话渠道回给用户。对于飞书场景，`*_REVIEW` 阶段必须优先使用 `feishu_doc` 工具创建云文件，再发送审批说明文本。

> **隔离会话注意**：通过 webhook 触发的隔离会话应优先按 `SKILL.md` §H 和消息中的 Action plan 执行（产物内容已预取注入，无需额外 curl 调用）。仅当消息中未包含 artifact content 时，才参考本文件 §1 的完整流程。

---

## 1. 审批云文件发送规则

适用阶段：`REQ_REVIEW` / `DESIGN_REVIEW` / `DEV_REVIEW`

进入人工审核阶段后，先做下面的动作，再发送审批消息：

1. `exec curl GET /api/v1/runs/{run_id}/artifacts` 获取产物列表。
2. 根据 gate 选择需要发送的文档正文：
   - `REQ_REVIEW`：最新 `kind=req`
   - `DESIGN_REVIEW`：最新 `kind=design`
   - `DEV_REVIEW`：最新 `kind=test-report`
3. `exec curl GET /api/v1/runs/{run_id}/artifacts/{artifact_id}/content` 获取完整正文。
4. 使用 `feishu_doc` 工具创建飞书云文件并写入正文（详见下方”feishu_doc 调用步骤”），将返回的 `url` 填入 §2 统一人工确认消息的 `{doc_url}` 占位符。
5. 若 `DESIGN_REVIEW` 同时存在 `kind=adr`，可追加发送 ADR 云文件，但主设计文档必须先发送。

### feishu_doc 调用步骤

**Step A — 创建空文档：**

```json
feishu_doc({“action”: “create”, “title”: “{doc_title}”, “owner_open_id”: “{sender_open_id}”})
```

- `doc_title` 按阶段选择：`REQ-{ticket}` / `DES-{ticket}` / `TEST-REPORT-{ticket}`
- `owner_open_id`：使用消息发送方的 `open_id`（来自 OpenClaw 注入的 inbound metadata 中的 `sender_id`），确保用户自动获得文档完全访问权限
- 返回值中包含 `doc_token` 和 `url`，保存这两个值

**Step B — 写入正文：**

```json
feishu_doc({“action”: “write”, “doc_token”: “{doc_token}”, “content”: “{artifact_content}”})
```

- `doc_token`：Step A 返回的值
- `content`：步骤 3 获取的 Markdown 正文原文

**Step C — 发送链接：**

将 Step A 返回的 `url` 填入 §2 统一人工确认消息的 `{doc_url}` 占位符。创建和写入是两步操作，必须都成功后再发送消息。

### 隔离会话中的 owner_open_id

在 webhook 触发的隔离会话中，`sender_id` 可能不是目标用户。此时：
- 优先使用 run 数据中的 `notify_to` 字段作为 `owner_open_id`
- 如果 `notify_to` 为空，可省略 `owner_open_id`（文档仅 bot 可访问，需在审批消息中提醒用户手动打开链接后申请权限）

### 硬性要求

- **必须使用 `feishu_doc` 工具创建云文件** — 不得只发送摘要代替。
- **不得把完整正文直接粘贴进聊天消息** — 长文档在聊天中不可读，必须走云文件。
- **创建失败时必须明确告警** — 回复 “⚠️ 飞书云文件创建失败：{error}” 并附上失败原因，然后仍发送审批文本（不含云文件链接），确保审批流程不被阻断。

---

## 2. 人工确认消息（统一格式）

**所有需要用户确认/操作的场景，必须使用以下固定格式：**

```
📋 {ticket} · {label}

{body}

请回复：
{options}
```

字段填充规则：

| 场景 | `label` | `body` | `options` |
|------|---------|--------|-----------|
| `REQ_REVIEW` | 需求审批 | 📄 需求文档：{doc_url} | - “通过” — 推进到设计阶段 <br> - 驳回原因 — 打回给 Agent 修订 |
| `DESIGN_REVIEW` | 设计审批 | 📄 设计文档：{doc_url} | - “通过” — 推进到开发阶段 <br> - 驳回原因 — 打回给 Agent 修订 |
| `DEV_REVIEW` | 开发审批 | 📄 测试报告：{doc_url} | - “通过” — 推进到合并阶段 <br> - 驳回原因 — 打回给 Agent 修订 |
| `MERGE_CONFLICT` | 合并冲突 | 冲突文件：<br>- {file_1}<br>- {file_2}<br>Worktree：{worktree_path} | - “已解决” — 确认后重新入队合并 |
| 异常升级 | 需要介入 | 原因：{reason}<br>阶段：{stage}<br>建议：{suggestion} | - “重试” — 再次尝试<br>- 其他处理方案 — 人工介入 |

### 示例

**审批（DESIGN_REVIEW）：**

```
📋 PROJ-42 · 设计审批

📄 设计文档：https://xxx.feishu.cn/docx/ABC123

请回复：
- “通过” — 推进到开发阶段
- 驳回原因 — 打回给 Agent 修订
```

**合并冲突（MERGE_CONFLICT）：**

```
📋 PROJ-42 · 合并冲突

冲突文件：
- src/state_machine.py
- tests/test_state_machine.py
Worktree：/tmp/cooagents-wt/PROJ-42-dev

请回复：
- “已解决” — 确认后重新入队合并
```

**异常升级：**

```
📋 PROJ-42 · 需要介入

原因：Agent 连续 3 次超时
阶段：DEV_RUNNING
建议：检查主机资源或手动恢复

请回复：
- “重试” — 再次尝试
- 其他处理方案 — 人工介入
```

---

## 3. 状态通知模板（无需用户操作）

适用场景：阶段流转、完成事件等纯通知，不需要用户回复。

```
🔄 任务 {ticket}: {from_stage} → {to_stage}
{contextual_message}
```

特殊情况：

- `MERGED` / `run.completed`：使用 `✅ 任务 {ticket} 已完成合并`
- `run.cancelled`：使用 `❌ 任务 {ticket} 已取消`

---

## 使用指引

| 场景 | 使用模板 | 说明 |
|------|----------|------|
| `*_REVIEW` 阶段 | §1 云文件发送 + §2 人工确认消息 | 先创建云文件，再发统一格式的确认消息 |
| `MERGE_CONFLICT` | §2 人工确认消息 | 填入冲突文件列表 |
| 重试/恢复超限 | §2 人工确认消息 | 填入异常原因和建议 |
| `MERGED` / `run.completed` | §3 状态通知 | 纯通知，不需用户回复 |
| `gate.approved` / `gate.rejected` | §3 状态通知 | 纯通知，不需用户回复 |
