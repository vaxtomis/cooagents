# 任务单（Codex 开发修订）

## 基本信息
- run_id: {{run_id}}
- ticket: {{ticket}}
- stage: DEV_RUNNING (revision)
- worktree: {{worktree}}
- revision: v{{revision_version}}

## 修订原因
{{reject_reason}}

## 输入资料
1. 设计文档：{{design_path}}
2. 原测试报告：{{test_report_path}}

## 你的目标
根据审阅反馈修改代码和测试，确保覆盖所有修改意见。

## 输出要求
1. 代码改动（在当前 worktree）
2. 更新测试报告：`docs/dev/TEST-REPORT-{{ticket}}.md`

## 约束
- 修订必须回应所有审阅意见。
- 关键变更需有测试或验证步骤。
