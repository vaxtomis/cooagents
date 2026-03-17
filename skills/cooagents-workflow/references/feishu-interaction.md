# 回复消息模板

Agent 的回复会自动通过当前对话渠道（飞书/Telegram/Discord 等）返回给用户。
以下模板用于格式化 Agent 的文本回复，不需要调用任何渠道 API。

---

## 1. 审批请求模板

适用阶段：REQ_REVIEW / DESIGN_REVIEW / DEV_REVIEW

```
📋 任务 {ticket} 等待审批 ({gate_name})

【{artifact_type} 摘要】
{artifact_summary_or_first_500_chars}

请回复：
- "通过" — 审批通过，推进到下一阶段
- 具体的驳回原因 — 将驳回并附上你的反馈给 Agent 修订
```

**字段说明：**

- `gate_name`：取值为 "需求审批" / "设计审批" / "开发审批"
- `artifact_type`：取值为 "需求文档" / "设计文档" / "代码+测试报告"
- `artifact_summary_or_first_500_chars`：通过 `curl GET /artifacts/{id}/content` 获取产物内容，填入摘要或前 500 字符

---

## 2. 状态通知模板

适用场景：阶段流转、完成事件

```
🔄 任务 {ticket}: {from_stage} → {to_stage}
{contextual_message}
```

**特殊情况：**

- MERGED / run.completed → 使用 "✅ 任务 {ticket} 已完成合并"
- run.cancelled → 使用 "❌ 任务 {ticket} 已取消"

---

## 3. 异常升级模板

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
| *_REVIEW 阶段 | 审批请求 | 获取产物内容后格式化 |
| MERGE_CONFLICT | 异常升级 | 附冲突文件列表 |
| MERGED / run.completed | 状态通知 | 完成确认 |
| gate.approved / gate.rejected | 状态通知 | 确认审批结果 |
| 重试/恢复超限 | 异常升级 | 说明失败原因和建议 |
