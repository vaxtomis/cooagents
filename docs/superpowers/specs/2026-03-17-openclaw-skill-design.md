# OpenClaw cooagents-workflow SKILL 设计文档

> 日期：2026-03-17
> 状态：待评审（v2 — 修正 OpenClaw 集成机制）

## 1. 目标

为 OpenClaw 创建一个全局 SKILL，使其 Agent 具备"项目经理"能力——自主编排 cooagents 15 阶段工作流，自动完成机械性操作，在需要人工判断的审批环节通过对话回复拉人介入。

## 2. 约束与决策

### 部署位置
- OpenClaw 全局 `skills/` 目录：`C:\Work\github\openclaw\skills\cooagents-workflow\`
- 非项目级 skill，任何 workspace 下都可用

### 使用者
- OpenClaw Agent 自身（非终端用户直接使用的工具）
- Agent 通过 skill description 判断何时遵循本 skill 的指令

### API 调用方式

> **关键设计决策**：OpenClaw Agent 不支持直接的 function calling 到外部 HTTP API。Agent 通过 `exec` 工具执行 `curl` 命令调用 cooagents REST API。这与 OpenClaw 现有 skill 的模式一致（如 `weather` skill 用 `curl` 调 wttr.in，`github` skill 用 `gh` CLI）。

`openclaw-tools.json` 是项目内部的 API 参考文档，**不是 OpenClaw 的工具注册机制**。OpenClaw 的工具来源仅有三种：内置工具（TypeScript）、Plugin（`api.registerTool()`）、MCP Server。本设计不涉及后两种。

### 飞书交互方式

> **关键设计决策**：Agent 不需要调用飞书 API。当用户通过飞书与 OpenClaw 对话时，Agent 的普通文本回复会自动通过飞书渠道返回给用户。审批请求 = Agent 按模板格式化回复文本；等待审批 = 等待用户的下一条消息。

### 自动化边界

| 环节 | 模式 | 说明 |
|------|------|------|
| 创建任务 | 自动 | Agent 从对话上下文提取 ticket + repo_path |
| 提交需求文档 | 自动 | Agent 整理需求内容，exec curl 调 submit_requirement |
| tick 推进 | 自动 | 幂等操作，安全重复调用 |
| 监控 RUNNING 状态 | 自动 | 通过 webhook 事件或 exec curl 轮询 |
| **需求审批 (req gate)** | **人工** | 回复审批模板，等待用户消息 |
| **设计审批 (design gate)** | **人工** | 回复设计产物摘要，等待用户消息 |
| **开发审批 (dev gate)** | **人工** | 回复代码/测试报告摘要，等待用户消息 |
| **驳回反馈** | **人工** | 收集人工描述的驳回原因 |
| 超时/失败重试 | 自动 | 按策略自动恢复，超限通知用户 |
| **合并冲突** | **人工** | 通知用户，附冲突文件列表 |
| 查询状态/产物 | 自动 | exec curl 查询 |

## 3. 方案选择

**选定方案 B：SKILL + references 子目录**

- 主 `SKILL.md`（~150 行）：核心决策逻辑，注入 Agent prompt
- `references/` 子目录：详细参考文档，Agent 用 `Read` 工具按需读取
- `openclaw-tools.json` 保留为项目 API 参考文档（非 OpenClaw 集成点）

淘汰方案：
- A（单一大文件）：400+ 行 prompt 注入，token 浪费
- C（TypeScript 插件）：开发量大，纯 Markdown 即可满足需求

## 4. 文件结构

```
C:\Work\github\openclaw\skills\cooagents-workflow\
├── SKILL.md                        # 核心决策逻辑
└── references/
    ├── api-playbook.md             # curl 命令手册（按场景组织）
    ├── error-handling.md           # 异常处理策略
    └── feishu-interaction.md       # 回复消息模板
```

另需更新：
```
C:\Work\codex\cooagents\docs\
├── PROCESS.md                      # 重写：对齐 acpx + 15 阶段架构
└── openclaw-tools.json             # 新增 tick 端点定义（API 参考文档）
```

## 5. SKILL.md 设计

### 5.1 Frontmatter

```yaml
---
name: cooagents-workflow
description: 管理 cooagents 多 Agent 协作工作流 — 通过 exec + curl 编排 Claude Code/Codex 完成从需求到合并的全生命周期。当用户提及任务创建、需求提交、设计/开发审批、任务状态查询、产物查看等工作流操作时触发。
user-invocable: true
metadata:
  {
    "openclaw":
      {
        "emoji": "🤖",
        "always": false,
        "requires": { "bins": ["curl"] }
      }
  }
---
```

**`requires: { "bins": ["curl"] }`** — 确保 skill 仅在 `curl` 可用时加载，避免无效注入。

### 5.2 核心内容结构

SKILL.md 包含以下部分（总计约 150 行）：

**A. 角色定义**
> 你是 cooagents 工作流的项目经理。你通过 `exec` 工具执行 `curl` 命令驱动 15 阶段状态机，自动执行机械性操作，在审批环节通过对话回复与人类交互。

**B. API 调用方式**
- Base URL: `http://127.0.0.1:8321/api/v1`
- 所有 API 操作通过 `exec` 工具执行 `curl` 命令完成
- 示例模式：
```bash
# GET 请求
curl -s http://127.0.0.1:8321/api/v1/runs/{run_id}

# POST 请求（带 JSON body）
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick

# POST 请求（带参数）
curl -s -X POST http://127.0.0.1:8321/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"ticket":"PROJ-123","repo_path":"/path/to/repo"}'
```
- 完整调用参数见 `references/api-playbook.md`（使用 Read 工具读取）

**C. 阶段决策树**（核心）

```
收到任务相关消息或 webhook 事件后：

1. 获取当前状态：exec curl GET /api/v1/runs/{run_id}
2. 根据 current_stage 执行对应动作：

┌─────────────────────┬──────────┬─────────────────────────────────────────┐
│ 阶段                │ 模式     │ 动作                                    │
├─────────────────────┼──────────┼─────────────────────────────────────────┤
│ (新任务)            │ 自动     │ curl POST /runs → curl POST             │
│                     │          │ /runs/{id}/submit-requirement → tick    │
│ INIT（瞬态）        │ 自动     │ curl POST /runs/{id}/tick（注：create   │
│                     │          │ 自动推进到 REQ_COLLECTING，Agent 几乎    │
│                     │          │ 不会观察到此阶段）                       │
│ REQ_COLLECTING      │ 自动     │ curl POST submit-requirement → tick     │
│ REQ_REVIEW          │ 人工     │ 回复审批模板 → 等待用户消息             │
│ DESIGN_QUEUED       │ 自动     │ curl POST tick（等待主机分配）          │
│ DESIGN_DISPATCHED   │ 自动     │ 等待（session 已启动）                  │
│ DESIGN_RUNNING      │ 自动     │ 等待完成（webhook 通知）                │
│ DESIGN_REVIEW       │ 人工     │ 回复设计产物摘要 → 等待用户消息         │
│ DEV_QUEUED          │ 自动     │ curl POST tick（等待主机分配）          │
│ DEV_DISPATCHED      │ 自动     │ 等待（session 已启动）                  │
│ DEV_RUNNING         │ 自动     │ 等待完成（webhook 通知）                │
│ DEV_REVIEW          │ 人工     │ 回复代码/测试报告摘要 → 等待用户消息    │
│ MERGE_QUEUED        │ 自动     │ 等待合并                                │
│ MERGING             │ 自动     │ 等待完成                                │
│ MERGED              │ 自动     │ 回复完成通知                            │
│ MERGE_CONFLICT      │ 人工     │ 回复冲突通知，附冲突文件列表            │
│ FAILED              │ 自动     │ 参考 error-handling.md 处理             │
└─────────────────────┴──────────┴─────────────────────────────────────────┘
```

**D. 人工交互规则**

当阶段为 `*_REVIEW` 或 `MERGE_CONFLICT` 时：
1. 执行 `curl GET /runs/{run_id}/artifacts` 获取产物列表
2. 执行 `curl GET /runs/{run_id}/artifacts/{artifact_id}/content` 获取关键产物内容
3. 按模板格式化回复文本（参考 `references/feishu-interaction.md`）
4. **等待用户的下一条消息，不自行决定**
5. 解析用户回复：
   - 肯定回复（"通过"、"可以"、"approve"）→ `curl POST /approve` (body: `{"gate":"...","by":"用户标识"}`) → `curl POST /tick`
   - 否定回复（含具体原因）→ `curl POST /reject` (body: `{"gate":"...","by":"用户标识","reason":"用户原文"}`)
6. 回复操作结果

**`by` 字段**：标识审批人。使用消息发送者的用户名或 ID，用于审批记录追溯。

**驳回后目标阶段**：
- `req` gate 驳回 → 回退到 `REQ_COLLECTING`（重新收集需求）
- `design` gate 驳回 → 回退到 `DESIGN_QUEUED`（重新排队设计）
- `dev` gate 驳回 → 回退到 `DEV_QUEUED`（重新排队开发）

**E. Webhook 事件处理**

列出 Agent 需要关注的核心事件及响应：
- `stage.changed`（`to` 为 `*_REVIEW` 或 `MERGE_CONFLICT`）→ 触发人工交互流程
- `stage.changed`（其他阶段变更）→ 可选通知
- `job.completed` → curl POST tick
- `job.failed` / `job.timeout` → 参考 error-handling.md
- `job.interrupted` / `job.error` → 与 `job.failed` 同策略处理（参考 error-handling.md）
- `merge.conflict` → 回复冲突通知，附冲突文件列表
- `merge.completed` → 合并成功确认（之后 `run.completed` 会紧随触发）
- `run.completed` → 回复完成通知
- `run.cancelled` → 回复已取消通知
- `host.online` → 对之前因主机离线等待的任务执行 curl POST tick
- `turn.started` / `turn.completed` → 多轮评估进度追踪
- `gate.approved` / `gate.rejected` → 确认审批结果已生效

**F. References 指引**

```
详细参考（使用 Read 工具按需读取）：
- curl 命令详情 → references/api-playbook.md
- 异常处理策略 → references/error-handling.md
- 回复消息模板 → references/feishu-interaction.md
```

## 6. references 设计

### 6.1 api-playbook.md

**按操作场景组织**，每个场景包含：前置条件、完整 curl 命令、预期响应 JSON。

场景列表：
1. 创建并启动任务
```bash
# 1. 创建任务
curl -s -X POST http://127.0.0.1:8321/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"ticket":"PROJ-123","repo_path":"/path/to/repo","description":"任务描述"}'
# Response: {"id":"<run_id>","current_stage":"INIT",...}

# 2. 提交需求
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/submit-requirement \
  -H "Content-Type: application/json" \
  -d '{"content":"# 需求文档\n..."}'

# 3. 推进
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick
```

2. 查询状态
```bash
curl -s http://127.0.0.1:8321/api/v1/runs/{run_id}
```

3. 审批通过
```bash
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/approve \
  -H "Content-Type: application/json" \
  -d '{"gate":"req","by":"reviewer_name","comment":"LGTM"}'
# 审批后推进
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick
```

4. 驳回重做
```bash
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/reject \
  -H "Content-Type: application/json" \
  -d '{"gate":"design","by":"reviewer_name","reason":"需要补充错误处理设计"}'
```

5. 查看产物
```bash
curl -s http://127.0.0.1:8321/api/v1/runs/{run_id}/artifacts
curl -s http://127.0.0.1:8321/api/v1/runs/{run_id}/artifacts/{artifact_id}/content
```

6. 处理失败（retry vs recover）
```bash
# retry_task：用于 FAILED 状态（恢复到 failed_at_stage 或 INIT）
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/retry \
  -H "Content-Type: application/json" \
  -d '{"by":"operator","note":"修复了问题"}'

# recover_task：用于中断的 job（action: resume/redo/manual）
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/recover \
  -H "Content-Type: application/json" \
  -d '{"action":"resume"}'
```

7. 取消任务
```bash
curl -s -X DELETE http://127.0.0.1:8321/api/v1/runs/{run_id}
```

8. 推进阶段（tick — 最常用）
```bash
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick
```

### 6.2 error-handling.md

**按异常类型组织**，定义自治决策规则：

| 事件 | 自动响应 | 升级条件 |
|------|----------|----------|
| `job.timeout` | exec curl recover（action=resume），最多 3 次 | 连续 3 次 → 回复通知用户 |
| `job.failed` | exec curl retry，最多 2 次 | 重试仍失败 → 回复通知用户 |
| `job.interrupted` / `job.error` | 同 `job.failed` | 同上 |
| `merge.conflict` | 立即回复通知用户，附冲突文件列表 | — |
| `host.offline` | 等待 `host.online` 后 exec curl tick | >30 分钟 → 回复通知用户 |
| curl 4xx 响应 | 记录错误，不重试 | 回复通知用户 |
| curl 5xx / 网络错误 | 等 10s 重试 1 次 | 仍失败 → 回复通知用户 |

### 6.3 feishu-interaction.md

三类回复消息模板（Agent 直接将格式化文本作为对话回复发送，无需调用飞书 API）：

**审批请求**（REQ_REVIEW / DESIGN_REVIEW / DEV_REVIEW）：
```
📋 任务 {ticket} 等待审批 ({gate_name})

【{artifact_type} 摘要】
{artifact_summary_or_first_500_chars}

请回复：
- "通过" — 审批通过，推进到下一阶段
- 具体的驳回原因 — 将驳回并附上你的反馈给 Agent 修订
```

**状态通知**（阶段变更、完成）：
```
🔄 任务 {ticket}: {from_stage} → {to_stage}
{contextual_message}
```

**异常升级**（超限、冲突）：
```
⚠️ 任务 {ticket} 需要人工介入
原因：{reason}
当前阶段：{stage}
建议：{suggestion}
```

## 7. PROCESS.md 重写方案

完全重写 `docs/PROCESS.md`，删除所有 tmux/cron/flock 引用。新结构：

1. **总览** — OpenClaw（需求管理）+ Claude Code（设计）+ Codex（开发）三角色协作
2. **15 阶段流程** — 引用 README 中 mermaid 状态图，简述每阶段输入/输出
3. **分支规范** — `feat/{ticket}-design`、`feat/{ticket}-dev`
4. **产物规范** — `docs/design/DES-{ticket}.md`、`docs/design/ADR-{ticket}.md`、`docs/dev/TEST-REPORT-{ticket}.md`
5. **审批流程** — 三个 Gate 的触发条件、审批方式、驳回后行为
6. **API 驱动** — 说明所有操作通过 HTTP API 完成，不再有 CLI 脚本

## 8. 不在范围内

- 不修改 cooagents 的 Python 源码
- 不开发 OpenClaw TypeScript 插件
- 不修改 OpenClaw 源码
- 不实现 MCP Server

**注意**：`openclaw-tools.json` 需新增 `tick` 端点定义（`POST /api/v1/runs/{run_id}/tick`），作为 API 参考文档使用。
