# 任务单（Claude 设计阶段）

## 基本信息
- run_id: {{run_id}}
- ticket: {{ticket}}
- stage: DESIGN_RUNNING
- worktree: {{worktree}}

## 输入资料（必须先阅读）
1. {{worktree}}/{{req_path}}
2. {{worktree}}/docs/design/DES-template.md
3. {{worktree}}/docs/design/ADR-template.md

## 你的目标
基于需求文档完成功能设计，明确架构、接口、数据结构、异常处理、测试策略与发布回滚。

## 输出要求
1. 设计文档：`{{worktree}}/docs/design/DES-{{ticket}}.md`
2. 架构决策：`{{worktree}}/docs/design/ADR-{{ticket}}-*.md`（如有）
3. 设计说明应可直接指导开发实现。

## 约束
- 不要直接改业务代码（本阶段只做设计）。
- 设计必须覆盖验收标准与边界条件。
- 输出使用 Markdown。

## 完成判定（DoD）
- 设计文档存在且结构完整。
- 至少覆盖：模块设计、接口设计、测试设计。
- 如有关键取舍，补充 ADR。

## 输出格式（`claude -p` 模式）
执行完成后，最后一行输出以下 JSON（不含其他内容）：
```json
{"status":"done","artifacts":["docs/design/DES-{{ticket}}.md"]}
```
如执行失败，输出：
```json
{"status":"error","reason":"<简要说明>"}
```
