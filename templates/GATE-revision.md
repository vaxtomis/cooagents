# 审批修订指令

## 基本信息
- ticket: {{ ticket }}
- revision: v{{ revision_version }}

## 修订原因
{{ reject_reason }}

## 你的目标
根据审阅反馈修订产出，确保覆盖所有修改意见。

{% if agent_type == "claude" %}
## 输出要求
1. 更新后的设计文档：`docs/design/DES-{{ ticket }}.md`
2. 如有新的架构决策：`docs/design/ADR-{{ ticket }}-*.md`
{% else %}
## 输出要求
1. 代码改动（在当前 worktree）
2. 更新测试报告：`docs/dev/TEST-REPORT-{{ ticket }}.md`
{% endif %}

## 约束
- 修订必须回应所有审阅意见。
- 保持与原始任务要求一致的输出格式。
