# OpenClaw 平台参考文档

> 本文档总结 OpenClaw 的核心特性，供 cooagents 集成开发参考。
> 源码位置：`C:\Work\github\openclaw`

## 一、平台定位

OpenClaw 是一个**本地运行的个人 AI 助手平台**，充当多渠道消息网关，将用户消息路由到 AI Agent 处理。支持 20+ 消息渠道（飞书、Telegram、Discord、Slack 等），通过 WebSocket 控制面统一管理。

核心架构：

```
用户 ──(飞书/Telegram/...)──▶ Channel Plugin ──▶ Gateway ──▶ Agent ──▶ LLM
                                                    │
                                              Skills / Tools
```

## 二、Skill 系统

### 格式规范

Skills 是 **Markdown 文件 + YAML frontmatter**，位于 `skills/<name>/SKILL.md`：

```markdown
---
name: my-skill
description: 一行描述，用于判断是否加载该 skill
user-invocable: true                    # 可选：用户是否可直接调用（顶层字段）
disable-model-invocation: false         # 可选：禁止模型主动调用（顶层字段）
metadata:
  {
    "openclaw":
      {
        "emoji": "🔧",
        "always": false,
        "primaryEnv": "python",
        "requires": { "bins": ["git", "python3"], "env": ["API_KEY"] },
        "install": [
          { "id": "brew", "kind": "brew", "formula": "tool-name", "bins": ["tool-name"] }
        ]
      }
  }
---

# Skill 标题

详细的 skill 内容，指导 Agent 如何完成任务。
支持完整的 Markdown 格式。
```

> **注意**：`emoji`、`always`、`requires`、`install` 等 OpenClaw 特有字段必须放在 `metadata` 的 `"openclaw"` JSON5 块内，而非顶层 frontmatter。`user-invocable` 和 `disable-model-invocation` 是顶层字段。详见 `frontmatter.ts` 中 `resolveOpenClawManifestBlock()` 的解析逻辑。

### 加载机制

1. **来源优先级**：项目目录 `skills/` → 插件目录 → 用户全局目录
2. **过滤逻辑**：根据 `requires` 检查依赖可用性，根据配置过滤
3. **注入方式**：`formatSkillsForPrompt()` 将 skill 内容注入 Agent 的系统提示
4. **容量限制**：可通过 `skills.limits.maxSkillsInPrompt` 和 `maxSkillsPromptChars` 控制

### 目录结构

```
skills/
└── my-skill/
    ├── SKILL.md              # 必须：主 skill 文件
    ├── README.md             # 可选：说明文档
    └── references/           # 可选：参考资料子目录
        ├── guide-a.md
        └── guide-b.md
```

### 关键特性

- **`user-invocable: true`** — 用户可通过 `/skill-name` 直接调用
- **`always: true`** — 每次对话都会加载，不依赖模型判断
- **`references/`** — skill 可指向参考文档，Agent 按需读取，不全部加载到 prompt

## 三、Tool / Function Calling 系统

### 工具定义方式

OpenClaw 有两种工具来源：

1. **内置工具**（TypeScript 实现）：browser、canvas、cron、gateway 等
2. **外部工具定义**（JSON）：如 `openclaw-tools.json`，供 Agent function calling

### 工具接口

```typescript
type AgentTool = {
  name: string;
  description: string;
  parameters: JSONSchema;
  execute: (params) => AgentToolResult;
};

type AgentToolResult = {
  content: string;
  images?: Image[];
  details?: object;
};
```

### 工具调用流程

```
Agent 决定调用工具 → Gateway 接收 function call → 路由到对应 handler → 返回结果 → Agent 继续推理
```

## 四、飞书（Feishu）集成

### 插件位置

`extensions/feishu/` — 作为 Channel Plugin 实现。

### 核心能力

| 能力 | 说明 |
|------|------|
| 消息收发 | 文本、富文本、图片、文件 |
| 卡片交互 | 构建交互式卡片，处理按钮回调 |
| Bitable | 读写飞书多维表格 |
| Wiki | 查询知识库 |
| Drive | 文件上传下载 |
| 通讯录 | 群组和用户信息查询 |
| 消息反应 | Emoji 反应 |

### 消息处理流程

```
飞书 webhook → bot.ts（验证、解析）→ 提取会话ID/发送者/提及
    → Gateway 路由到 Agent → Agent 处理 → send.ts 回复飞书
```

## 五、Agent 系统

### 支持的 LLM 提供商

OpenAI、Anthropic (Claude)、Google Gemini、OpenRouter、Amazon Bedrock、Minimax 等。

### Agent 配置

```json
{
  "agents": {
    "defaults": {
      "model": "claude-sonnet-4-20250514",
      "provider": "anthropic",
      "thinking": "medium"
    }
  }
}
```

### 会话管理

- **会话隔离**：每个 `agentId:channelId:accountId:peerId` 组合独立会话
- **多 Agent 路由**：根据 workspace 配置将不同渠道/用户路由到不同 Agent
- **子 Agent**：Agent 可 spawn 子 Agent 处理特定任务

### 模型容错

- OAuth / API Key 轮换
- Provider 间自动切换
- 冷却策略
- 配额感知

## 六、Gateway 控制面

### 角色

中央 WebSocket 服务器，协调所有组件：

- 管理 Agent、Channel、Plugin 的生命周期
- 路由消息
- 调度 function call
- 管理会话状态
- 健康监控

### 主要 RPC 方法

| 方法 | 说明 |
|------|------|
| `agent` | 发送消息到 Agent |
| `gateway.status` | 健康状态 |
| `tools.catalog` | 可用工具列表 |
| `models.catalog` | 可用模型列表 |
| `sessions.*` | 会话管理 |
| `config.*` | 配置读写 |

## 七、插件系统

### 插件类型

| 类型 | 说明 | 示例 |
|------|------|------|
| Channel | 消息渠道 | feishu、discord、telegram |
| Provider | LLM 提供商 | openai、anthropic、google |
| Extension | 功能扩展 | memory、media-understanding |
| Memory | 记忆后端 | lancedb |
| Speech | 语音 | elevenlabs、deepgram |

### 插件入口

```typescript
export default defineChannelPluginEntry({
  id: "feishu",
  name: "Feishu",
  description: "Feishu channel plugin",
  plugin: feishuPlugin,
  setRuntime: setFeishuRuntime,
  registerFull: registerFeishuSubagentHooks,
});
```

## 八、配置体系

### 优先级（高→低）

1. 进程环境变量
2. 工作目录 `.env`
3. `~/.openclaw/.env`
4. `openclaw.json` 的 `env` 块
5. `openclaw.json` 直接配置

### 关键环境变量

```bash
# Gateway
OPENCLAW_GATEWAY_TOKEN=<token>

# LLM（至少配一个）
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# 飞书
FEISHU_APP_ID=cli_...
FEISHU_APP_SECRET=...
```

### 主配置文件 (`openclaw.json`)

```json
{
  "gateway": { "port": 3000 },
  "agents": { "defaults": { "model": "...", "thinking": "medium" } },
  "channels": { "feishu": { "enabled": true } },
  "providers": { "anthropic": { "enabled": true } },
  "plugins": { "slots": { "memory": "lancedb" } },
  "skills": { "limits": { "maxSkillsInPrompt": 20 } }
}
```

## 九、与 cooagents 的集成点

### 当前集成方式

| 集成点 | 机制 | 文件 |
|--------|------|------|
| API 工具定义 | `openclaw-tools.json`（11 个函数） | `docs/openclaw-tools.json` |
| 事件通知 | Webhook → 飞书消息 | `templates/WEBHOOK-messages.yaml` |
| 流程文档 | PROCESS.md（**已过时**） | `docs/PROCESS.md` |

### 推荐增强

1. **编写 cooagents-workflow SKILL** — 让 OpenClaw Agent 自动理解 15 阶段流程
2. **更新 PROCESS.md** — 对齐当前 acpx + 状态机架构
3. **利用 `user-invocable` 特性** — 让用户通过 `/cooagents` 直接触发工作流操作
