# 回复消息模板

Agent 的审批与通知消息会通过当前对话渠道回给用户。对于飞书场景，`*_REVIEW` 阶段必须优先使用飞书技能发送云文件，再发送审批说明文本。

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
4. 将正文写入临时 Markdown 文件，文件名使用：
   - `REQ-{ticket}.md`
   - `DES-{ticket}.md`
   - `TEST-REPORT-{ticket}.md`
5. 使用 OpenClaw 中已安装的飞书技能上传该文件为云文件，并直接发送给用户。
6. 若 `DESIGN_REVIEW` 同时存在 `kind=adr`，可追加发送 ADR 云文件，但主设计文档必须先发送。

硬性要求：

- 不得只发送摘要代替云文件。
- 不得把完整正文直接粘贴进聊天消息代替云文件。
- 云文件发送失败时，必须明确告警用户“文档云文件发送失败”，并附上失败原因。

---

## 2. 审批请求模板

在云文件发送成功后，再发送下面的审批文本：

```
📋 任务 {ticket} 等待审批 ({gate_name})

已通过飞书云文件发送：{artifact_type}

请回复：
- "通过" — 审批通过，推进到下一阶段
- 具体的驳回原因 — 将驳回并附上你的反馈给 Agent 修订
```

字段说明：

- `gate_name`：取值为“需求审批”/“设计审批”/“开发审批”
- `artifact_type`：取值为“需求文档”/“设计文档”/“测试报告”

---

## 3. 状态通知模板

适用场景：阶段流转、完成事件

```
🔄 任务 {ticket}: {from_stage} → {to_stage}
{contextual_message}
```

特殊情况：

- `MERGED` / `run.completed`：使用 `✅ 任务 {ticket} 已完成合并`
- `run.cancelled`：使用 `❌ 任务 {ticket} 已取消`

---

## 4. 异常升级模板

适用场景：超出重试上限、冲突、需要人工介入的错误

```
⚠️ 任务 {ticket} 需要人工介入
原因：{reason}
当前阶段：{stage}
建议：{suggestion}
```

---

## 使用指引

| 场景 | 使用模板 | 说明 |
|------|----------|------|
| `*_REVIEW` 阶段 | 审批云文件发送规则 + 审批请求模板 | 先发云文件，再发审批文本 |
| `MERGE_CONFLICT` | 异常升级模板 | 附冲突文件列表 |
| `MERGED` / `run.completed` | 状态通知模板 | 完成确认 |
| `gate.approved` / `gate.rejected` | 状态通知模板 | 确认审批结果 |
| 重试/恢复超限 | 异常升级模板 | 说明失败原因和建议 |
