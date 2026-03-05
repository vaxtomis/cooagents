# 任务单（Codex 开发阶段）

## 基本信息
- run_id: {{run_id}}
- ticket: {{ticket}}
- stage: DEV_RUNNING
- repo: {{repo_path}}
- worktree: {{worktree}}

## 输入资料（必须先阅读）
1. {{design_path}}
2. docs/dev/PLAN-template.md
3. docs/dev/TEST-REPORT-template.md

## 你的目标
根据设计文档完成编码、测试与结果记录，确保可回归与可提交。

## 输出要求
1. 代码改动（在当前 worktree）
2. 测试报告：`docs/dev/TEST-REPORT-{{ticket}}.md`
3. 如有必要：开发计划 `docs/dev/PLAN-{{ticket}}.md`

## 约束
- 必须先读设计文档再动代码。
- 关键变更需有测试或验证步骤。
- 输出使用 Markdown。

## 完成判定（DoD）
- 核心功能实现完成。
- 测试报告已生成，含 PASS/FAIL 结果。
- 代码可提交，且变更说明清晰。

## 回执（ACK）
开始执行后，请创建：`tasks/{{run_id}}/dev.ack.json`
内容示例：
```json
{"run_id":"{{run_id}}","stage":"dev","agent":"codex","status":"accepted"}
```
