# 任务单（Claude 设计修订）

## 基本信息
- run_id: {{run_id}}
- ticket: {{ticket}}
- stage: DESIGN_RUNNING (revision)
- worktree: {{worktree}}
- revision: v{{revision_version}}

## 修订原因
{{reject_reason}}

## 输入资料
1. 原设计文档：{{original_design_path}}
2. docs/design/DES-template.md

## 你的目标
根据审阅反馈修订设计文档，确保覆盖所有修改意见。

## 输出要求
1. 更新后的设计文档：`docs/design/DES-{{ticket}}.md`
2. 如有新的架构决策：`docs/design/ADR-{{ticket}}-*.md`

## 约束
- 不要直接改业务代码。
- 修订必须回应所有审阅意见。
- 输出使用 Markdown。
