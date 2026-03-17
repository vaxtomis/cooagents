# 多 Agent 协作流程

## 1. 总览

三角色协作：OpenClaw（需求管理）+ Claude Code（设计）+ Codex（开发）。

通过 HTTP API 驱动 15 阶段状态机，完成从需求到代码合并的全生命周期。

架构图参见 README.md。

## 2. 15 阶段流程

完整状态转移图参见 README.md 中的 mermaid 状态图。

| 阶段 | 输入 | 输出 | 模式 |
|------|------|------|------|
| INIT | create_task 调用 | run 记录 | 自动 |
| REQ_COLLECTING | 需求内容 | 需求文档 | 自动 |
| REQ_REVIEW | 需求文档 | 审批/驳回 | 人工 |
| DESIGN_QUEUED | 审批通过 | 主机分配 | 自动 |
| DESIGN_DISPATCHED | 主机分配 | acpx session | 自动 |
| DESIGN_RUNNING | session | 设计文档 + ADR | 自动 |
| DESIGN_REVIEW | 设计文档 | 审批/驳回 | 人工 |
| DEV_QUEUED | 审批通过 | 主机分配 | 自动 |
| DEV_DISPATCHED | 主机分配 | acpx session | 自动 |
| DEV_RUNNING | session | 代码 + 测试 | 自动 |
| DEV_REVIEW | 代码 + 测试报告 | 审批/驳回 | 人工 |
| MERGE_QUEUED | 审批通过 | 合并队列位置 | 自动 |
| MERGING | 队列位置 | 合并结果 | 自动 |
| MERGED | 合并成功 | 完成通知 | 自动 |
| MERGE_CONFLICT | 合并失败 | 冲突文件列表 | 人工 |
| FAILED | 错误 | 重试/恢复决策 | 自动 |

## 3. 分支规范

- 设计分支：`feat/{ticket}-design`
- 开发分支：`feat/{ticket}-dev`

## 4. 产物规范

- 设计文档：`docs/design/DES-{ticket}.md`
- 架构决策：`docs/design/ADR-{ticket}.md`
- 测试报告：`docs/dev/TEST-REPORT-{ticket}.md`

## 5. 审批流程

流程中包含三个人工审批门控：req、design、dev。

### req 门控

- 触发阶段：REQ_REVIEW
- 审批通过：`POST /runs/{id}/approve`，body 传 `gate=req`、`by`、可选 `comment`；然后 `POST /runs/{id}/tick` 推进至 DESIGN_QUEUED
- 驳回：`POST /runs/{id}/reject`，body 传 `gate=req`、`by`、`reason`；回退至 REQ_COLLECTING

### design 门控

- 触发阶段：DESIGN_REVIEW
- 审批通过：`POST /runs/{id}/approve`，body 传 `gate=design`、`by`、可选 `comment`；然后 `POST /runs/{id}/tick` 推进至 DEV_QUEUED
- 驳回：`POST /runs/{id}/reject`，body 传 `gate=design`、`by`、`reason`；回退至 DESIGN_QUEUED

### dev 门控

- 触发阶段：DEV_REVIEW
- 审批通过：`POST /runs/{id}/approve`，body 传 `gate=dev`、`by`、可选 `comment`；然后 `POST /runs/{id}/tick` 推进至 MERGE_QUEUED
- 驳回：`POST /runs/{id}/reject`，body 传 `gate=dev`、`by`、`reason`；回退至 DEV_QUEUED

## 6. API 驱动

所有操作均通过 HTTP API 完成，基础地址：`http://127.0.0.1:8321/api/v1`。

所有操作均通过 HTTP 请求完成，不依赖任何外部脚本或后台进程。`tick` 端点幂等，可安全重复调用。

关键端点（完整文档参见 openclaw-tools.json）：

- `POST /runs` — 创建任务
- `POST /runs/{id}/tick` — 推进阶段
- `POST /runs/{id}/approve` — 审批通过
- `POST /runs/{id}/reject` — 驳回
- `GET /runs/{id}` — 查询状态
