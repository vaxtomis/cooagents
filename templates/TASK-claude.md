# 任务单（Claude 设计阶段）

## 基本信息
- run_id: {{run_id}}
- ticket: {{ticket}}
- stage: DESIGN_RUNNING
- repo: {{repo_path}}
- worktree: {{worktree}}

## 输入资料（必须先阅读）
1. {{req_path}}
2. docs/design/DES-template.md
3. docs/design/ADR-template.md

## 你的目标
基于需求文档完成功能设计，明确架构、接口、数据结构、异常处理、测试策略与发布回滚。

## 输出要求
1. 设计文档：`docs/design/DES-{{ticket}}.md`
2. 架构决策：`docs/design/ADR-{{ticket}}-*.md`（如有）
3. 设计说明应可直接指导开发实现。

## 约束
- 不要直接改业务代码（本阶段只做设计）。
- 设计必须覆盖验收标准与边界条件。
- 输出使用 Markdown。

## 完成判定（DoD）
- 设计文档存在且结构完整。
- 至少覆盖：模块设计、接口设计、测试设计。
- 如有关键取舍，补充 ADR。

## 回执（ACK）
开始执行后，请创建：`tasks/{{run_id}}/design.ack.json`
内容示例：
```json
{"run_id":"{{run_id}}","stage":"design","agent":"claude","status":"accepted"}
```
