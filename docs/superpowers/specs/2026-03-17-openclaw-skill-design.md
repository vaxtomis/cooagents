# OpenClaw cooagents-workflow SKILL 设计文档

> 日期：2026-03-17
> 状态：待评审

## 1. 目标

为 OpenClaw 创建一个全局 SKILL，使其 Agent 具备"项目经理"能力——自主编排 cooagents 15 阶段工作流，自动完成机械性操作，在需要人工判断的审批环节通过飞书拉人介入。

## 2. 约束与决策

### 部署位置
- OpenClaw 全局 `skills/` 目录：`C:\Work\github\openclaw\skills\cooagents-workflow\`
- 非项目级 skill，任何 workspace 下都可用

### 使用者
- OpenClaw Agent 自身（非终端用户直接使用的工具）
- Agent 根据 skill description 自动判断何时激活

### 自动化边界

| 环节 | 模式 | 说明 |
|------|------|------|
| 创建任务 | 自动 | Agent 从对话上下文提取 ticket + repo_path |
| 提交需求文档 | 自动 | Agent 整理需求内容调 submit_requirement |
| tick 推进 | 自动 | 幂等操作，安全重复调用 |
| 监控 RUNNING 状态 | 自动 | 通过 webhook 事件或轮询 get_task_status |
| **需求审批 (req gate)** | **人工** | 飞书展示需求摘要，请求确认 |
| **设计审批 (design gate)** | **人工** | 飞书展示设计产物，请求审批 |
| **开发审批 (dev gate)** | **人工** | 飞书展示代码/测试报告，请求审批 |
| **驳回反馈** | **人工** | 收集人工描述的驳回原因 |
| 超时/失败重试 | 自动 | 按策略自动恢复，超限升级 |
| **合并冲突** | **人工** | 飞书通知人工介入，附冲突文件列表 |
| 查询状态/产物 | 自动 | 信息查询 |

## 3. 方案选择

**选定方案 B：SKILL + references 子目录**

- 主 `SKILL.md`（~150 行）：核心决策逻辑，注入 Agent prompt
- `references/` 子目录：详细参考文档，Agent 按需读取
- 与现有 `openclaw-tools.json`（11 个函数定义）互补——SKILL 提供"何时调什么"，tools.json 提供"怎么调"

淘汰方案：
- A（单一大文件）：400+ 行 prompt 注入，token 浪费
- C（TypeScript 插件）：开发量大，纯 Markdown 即可满足需求

## 4. 文件结构

```
C:\Work\github\openclaw\skills\cooagents-workflow\
├── SKILL.md                        # 核心决策逻辑
└── references/
    ├── api-playbook.md             # API 操作手册（按场景组织）
    ├── error-handling.md           # 异常处理策略
    └── feishu-interaction.md       # 飞书交互模板
```

另需更新：
```
C:\Work\codex\cooagents\docs\
├── PROCESS.md                      # 重写：对齐 acpx + 15 阶段架构
└── openclaw-tools.json             # 校验通过，无需改动
```

## 5. SKILL.md 设计

### 5.1 Frontmatter

```yaml
---
name: cooagents-workflow
description: 管理 cooagents 多 Agent 协作工作流 — 通过 HTTP API 编排 Claude Code/Codex 完成从需求到合并的全生命周期。当用户提及任务创建、需求提交、设计/开发审批、任务状态查询、产物查看等工作流操作时触发。
emoji: 🤖
always: false
user-invocable: true
---
```

### 5.2 核心内容结构

SKILL.md 包含以下部分（总计约 150 行）：

**A. 角色定义**
> 你是 cooagents 工作流的项目经理。你通过 HTTP API 驱动 15 阶段状态机，自动执行机械性操作，在审批环节通过飞书消息与人类交互。

**B. API 基础信息**
- Base URL: `http://127.0.0.1:8321/api/v1`
- 所有调用使用 JSON content-type
- 指向 `references/api-playbook.md` 获取详细参数

**C. 阶段决策树**（核心）

```
收到任务相关消息或 webhook 事件后：

1. 获取当前状态：调用 get_task_status(run_id)
2. 根据 current_stage 执行对应动作：

┌─────────────────────┬──────────┬────────────────────────────────┐
│ 阶段                │ 模式     │ 动作                           │
├─────────────────────┼──────────┼────────────────────────────────┤
│ (新任务)            │ 自动     │ create_task → submit_requirement│
│                     │          │ → tick                         │
│ REQ_COLLECTING      │ 自动     │ submit_requirement → tick      │
│ REQ_REVIEW          │ 人工     │ 展示需求 → 请求审批 → 等待回复 │
│ DESIGN_QUEUED       │ 自动     │ tick（等待主机分配）           │
│ DESIGN_DISPATCHED   │ 自动     │ 等待（session 已启动）         │
│ DESIGN_RUNNING      │ 自动     │ 等待完成（webhook 通知）       │
│ DESIGN_REVIEW       │ 人工     │ 展示设计产物 → 请求审批        │
│ DEV_QUEUED          │ 自动     │ tick（等待主机分配）           │
│ DEV_DISPATCHED      │ 自动     │ 等待（session 已启动）         │
│ DEV_RUNNING         │ 自动     │ 等待完成（webhook 通知）       │
│ DEV_REVIEW          │ 人工     │ 展示代码/测试报告 → 请求审批   │
│ MERGE_QUEUED        │ 自动     │ 等待合并                       │
│ MERGING             │ 自动     │ 等待完成                       │
│ MERGED              │ 自动     │ 通知完成                       │
│ FAILED              │ 自动     │ 参考 error-handling.md 处理    │
└─────────────────────┴──────────┴────────────────────────────────┘
```

**D. 人工交互规则**

当阶段为 `*_REVIEW` 时：
1. 调 `list_artifacts(run_id)` 获取产物列表
2. 调 `get_artifact_content(run_id, artifact_id)` 获取关键产物内容
3. 向飞书发送审批请求（参考 `references/feishu-interaction.md` 模板）
4. **等待用户回复，不自行决定**
5. 解析回复：
   - 肯定回复（"通过"、"可以"、"approve"）→ `approve_gate` → `tick`
   - 否定回复（含具体原因）→ `reject_gate(reason=用户原文)`
6. 回复操作结果

**E. Webhook 事件处理**

列出 Agent 需要关注的核心事件及响应：
- `gate.waiting` → 触发人工审批流程
- `job.completed` → tick 推进
- `job.failed` / `job.timeout` → 参考 error-handling.md
- `merge.conflict` → 飞书通知人工
- `run.completed` → 通知完成

**F. References 指引**

```
详细参考（按需读取）：
- API 调用详情 → references/api-playbook.md
- 异常处理策略 → references/error-handling.md
- 飞书消息模板 → references/feishu-interaction.md
```

## 6. references 设计

### 6.1 api-playbook.md

**按操作场景组织**，每个场景包含：前置条件、调用序列、参数示例、预期响应。

场景列表：
1. 创建并启动任务（create → submit → tick）
2. 推进阶段（get_status → tick）
3. 审批通过（approve_gate → tick）
4. 驳回重做（reject_gate + reason）
5. 查看产物（list_artifacts → get_artifact_content）
6. 处理失败（retry_task / recover_task）
7. 取消任务（cancel_task）

### 6.2 error-handling.md

**按异常类型组织**，定义自治决策规则：

| 事件 | 自动响应 | 升级条件 |
|------|----------|----------|
| `job.timeout` | `recover_task(action=resume)`，最多 3 次 | 连续 3 次 → 飞书通知 |
| `job.failed` | `retry_task`，最多 2 次 | 重试仍失败 → 飞书通知 |
| `merge.conflict` | 立即飞书通知，附冲突文件列表 | — |
| `host.offline` | 等待 `host.online` 后 tick | >30 分钟 → 飞书通知 |
| API 调用 4xx | 记录错误，不重试 | 通知人 |
| API 调用 5xx/网络错误 | 等 10s 重试 1 次 | 仍失败 → 通知人 |

### 6.3 feishu-interaction.md

三类消息模板：

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
- 不修改 `openclaw-tools.json`（已验证端点正确）
- 不开发 OpenClaw TypeScript 插件
- 不修改 OpenClaw 源码
