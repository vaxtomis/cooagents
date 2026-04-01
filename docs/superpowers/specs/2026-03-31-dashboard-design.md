# Cooagents Dashboard 当前实现规格

> 更新时间：2026-04-01
> 本文档描述 `main` 分支当前已经落地的 Dashboard 实现，而不是最初的目标草案。

## 概述

Cooagents Dashboard 是 cooagents 多 Agent 协作编排系统的 Web 控制台，面向项目管理者、运维和开发者。
当前实现已经覆盖 5 个真实页面：概览、Runs、Run 详情、Agent Hosts、Merge Queue，并由 FastAPI 提供同源 API 与静态资源挂载。

## 技术栈

| 层 | 当前实现 |
|---|---|
| 前端框架 | React 18 |
| 语言 | TypeScript |
| 路由 | React Router DOM 6.30 |
| 数据获取 | 原生 `fetch` 封装 + SWR |
| 样式 | Tailwind CSS v4 |
| 主题 tokens | `web/src/index.css` 中的 `@theme` 变量 |
| UI 辅助库 | Headless UI、Lucide React |
| 构建工具 | Vite 6 |
| 实时通信 | SSE |
| 后端 | FastAPI |
| 部署 | 前端打包到 `web/dist`，由 FastAPI 提供静态文件 |

## 项目结构

```text
cooagents/
├── web/
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── index.css
│       ├── router.tsx
│       ├── api/
│       │   ├── client.ts
│       │   ├── runs.ts
│       │   ├── agents.ts
│       │   ├── repos.ts
│       │   └── diagnostics.ts
│       ├── hooks/
│       │   ├── usePolling.ts
│       │   └── useSSE.ts
│       ├── components/
│       │   ├── ApprovalAction.tsx
│       │   ├── RunCard.tsx
│       │   ├── StageProgress.tsx
│       │   ├── StatCard.tsx
│       │   └── StatusBadge.tsx
│       ├── pages/
│       │   ├── DashboardPage.tsx
│       │   ├── RunsListPage.tsx
│       │   ├── RunDetailPage.tsx
│       │   ├── AgentHostsPage.tsx
│       │   └── MergeQueuePage.tsx
│       └── types/
│           └── index.ts
├── routes/
│   ├── agent_hosts.py
│   ├── artifacts.py
│   ├── diagnostics.py
│   ├── events.py
│   ├── repos.py
│   ├── runs.py
│   └── sse.py
└── src/
    ├── app.py
    └── sse.py
```

## 前端壳层

### 路由与布局

- 路由定义在 `web/src/router.tsx`
- 页面壳层不是单独的 `MainLayout.tsx`，而是 `router.tsx` 内的 `ShellLayout`
- 当前路由：
  - `/`
  - `/runs`
  - `/runs/:runId`
  - `/agent-hosts`
  - `/merge-queue`

### 主题与样式

主题变量定义在 `web/src/index.css` 的 `@theme` 中，当前核心 tokens 包括：

- 字体
  - `--font-sans: Geist, Segoe UI, sans-serif`
  - `--font-mono: Geist Mono, SFMono-Regular, monospace`
- 颜色
  - `--color-void`
  - `--color-panel`
  - `--color-panel-strong`
  - `--color-copy`
  - `--color-muted`
  - `--color-accent`
  - `--color-success`
  - `--color-warning`
  - `--color-danger`
- 阴影
  - `--shadow-shell`
  - `--shadow-panel`

当前实现没有 `web/tailwind.config.ts`；主题通过 Tailwind v4 的 CSS-first 方式管理。

## 数据层

### `web/src/api/client.ts`

- 统一前缀：`/api/v1`
- 使用 `apiRequest` / `apiFetch` 封装请求
- 查询参数由 `apiPath()` 统一拼接
- 非 2xx 响应抛出 `ApiError(status, message, data)`

### `web/src/hooks/usePolling.ts`

- 基于 SWR 配置对象返回轮询策略
- 默认轮询间隔：15 秒
- `revalidateOnFocus` 关闭
- `revalidateOnReconnect` 开启
- `keepPreviousData` 开启

### `web/src/hooks/useSSE.ts`

- 基于 `EventSource`
- 返回 `{ state, isLive }`
- 当前状态枚举：`idle / connecting / live / reconnecting / offline`
- 默认监听事件类型：
  - `stage.changed`
  - `approval.changed`
  - `artifact.created`
  - `artifact.updated`
  - `job.updated`
  - `job.completed`
  - `job.failed`
  - `run.completed`
  - `run.failed`
  - `run.cancelled`

## 页面规格

### 1. Dashboard

**路由：** `/`

**当前实现：**
- 顶部 5 个 KPI 卡片：
  - 运行中
  - 待审批
  - 合并中
  - 失败
  - 已完成
- 左侧主区展示活跃 Run 列表
- 右侧展示：
  - 待审批列表，可直接 approve / reject
  - Agent 主机摘要
- 页面以 15 秒轮询刷新 Runs 与 Hosts 数据

**主要数据来源：**
- `GET /api/v1/runs`
- `GET /api/v1/runs?status=running`
- `GET /api/v1/agent-hosts`
- `POST /api/v1/runs/{run_id}/approve`
- `POST /api/v1/runs/{run_id}/reject`

### 2. Runs List

**路由：** `/runs`

**当前实现：**
- 服务端搜索、筛选、排序、分页
- 支持筛选参数：
  - `status`
  - `ticket`
  - `current_stage`
- 支持排序参数：
  - `created_at`
  - `updated_at`
  - `ticket`
  - `status`
  - `current_stage`
- 列表行可跳转到 Run 详情页
- 通过响应头 `X-Total-Count` 计算分页总数

**主要数据来源：**
- `GET /api/v1/runs`

### 3. Run Detail

**路由：** `/runs/:runId`

**当前实现：**
- 顶部展示：
  - Ticket
  - Current stage
  - Status
  - Repo
  - 14 阶段进度条
- 执行上下文卡片：
  - 当前 action
  - elapsed
  - artifacts 数量
  - 当前描述
  - 上一阶段结果
- 右侧展示：
  - SSE 连接状态
  - Approval history
  - Operator controls
- 详情主体使用真实 tab，而不是纵向堆叠：
  - `Artifacts`
  - `Agent输出`
  - `事件追踪`
  - `Stage历史`
- 支持动作：
  - 审批通过 / 驳回
  - Cancel run
  - 查看 artifact content / diff
  - 加载 job output
- 收到 SSE 事件后会节流刷新 Run 详情数据

**主要数据来源：**
- `GET /api/v1/runs/{run_id}`
- `GET /api/v1/runs/{run_id}/brief`
- `GET /api/v1/runs/{run_id}/artifacts`
- `GET /api/v1/runs/{run_id}/artifacts/{artifact_id}/content`
- `GET /api/v1/runs/{run_id}/artifacts/{artifact_id}/diff`
- `GET /api/v1/runs/{run_id}/jobs`
- `GET /api/v1/runs/{run_id}/jobs/{job_id}/output`
- `GET /api/v1/runs/{run_id}/trace`
- `GET /api/v1/runs/{run_id}/events/stream`
- `POST /api/v1/runs/{run_id}/approve`
- `POST /api/v1/runs/{run_id}/reject`
- `DELETE /api/v1/runs/{run_id}`

### 4. Agent Hosts

**路由：** `/agent-hosts`

**当前实现：**
- 页面采用“配置优先”卡片布局，而不是纯运维面板
- 每张卡片展示：
  - host id
  - host address
  - agent type
  - max concurrent
  - SSH key 是否配置
  - labels
  - status
  - current load
- 右侧保留表单区，支持：
  - create
  - edit
- 卡片动作保留：
  - check
  - delete

**主要数据来源：**
- `GET /api/v1/agent-hosts`
- `POST /api/v1/agent-hosts`
- `PUT /api/v1/agent-hosts/{host_id}`
- `DELETE /api/v1/agent-hosts/{host_id}`
- `POST /api/v1/agent-hosts/{host_id}/check`

### 5. Merge Queue

**路由：** `/merge-queue`

**当前实现：**
- 左侧展示 merge queue 列表
- 前端会对每个 queue item 的 `run_id` 再拉 `GET /runs/{id}` 做 enrich
- 行级操作：
  - merge
  - skip
- 右侧详情区展示：
  - selected queue item
  - run id / repo / stage / updated
  - merge priority
- 当状态为 `conflict` 时：
  - 拉取冲突文件列表
  - 失败时回退到 queue item 自带的 `conflict_files`
  - 支持 `Resolve and requeue`

**主要数据来源：**
- `GET /api/v1/repos/merge-queue`
- `GET /api/v1/runs/{run_id}`
- `POST /api/v1/runs/{run_id}/merge`
- `POST /api/v1/runs/{run_id}/merge-skip`
- `GET /api/v1/runs/{run_id}/conflicts`
- `POST /api/v1/runs/{run_id}/resolve-conflict`

## 后端接口规格

### Runs
- `POST /api/v1/runs`
- `GET /api/v1/runs`
- `GET /api/v1/runs/brief?ticket=...`
- `GET /api/v1/runs/{run_id}`
- `GET /api/v1/runs/{run_id}/brief`
- `POST /api/v1/runs/{run_id}/approve`
- `POST /api/v1/runs/{run_id}/reject`
- `POST /api/v1/runs/{run_id}/retry`
- `POST /api/v1/runs/{run_id}/recover`
- `POST /api/v1/runs/{run_id}/submit-requirement`
- `POST /api/v1/runs/{run_id}/resolve-conflict`
- `DELETE /api/v1/runs/{run_id}`

### Jobs / Artifacts / Merge
- `GET /api/v1/runs/{run_id}/jobs`
- `GET /api/v1/runs/{run_id}/jobs/{job_id}/output`
- `GET /api/v1/runs/{run_id}/artifacts`
- `GET /api/v1/runs/{run_id}/artifacts/{artifact_id}/content`
- `GET /api/v1/runs/{run_id}/artifacts/{artifact_id}/diff`
- `GET /api/v1/runs/{run_id}/conflicts`
- `POST /api/v1/runs/{run_id}/merge`
- `POST /api/v1/runs/{run_id}/merge-skip`
- `GET /api/v1/repos/merge-queue`
- `POST /api/v1/repos/ensure`

### Hosts
- `GET /api/v1/agent-hosts`
- `POST /api/v1/agent-hosts`
- `PUT /api/v1/agent-hosts/{host_id}`
- `DELETE /api/v1/agent-hosts/{host_id}`
- `POST /api/v1/agent-hosts/{host_id}/check`

### Diagnostics / Events / SSE
- `GET /api/v1/events`
- `GET /api/v1/runs/{run_id}/trace`
- `GET /api/v1/jobs/{job_id}/diagnosis`
- `GET /api/v1/traces/{trace_id}`
- `GET /api/v1/runs/{run_id}/events/stream`

## SSE 实现

- 广播器实现位于 `src/sse.py`
- SSE 路由位于 `routes/sse.py`
- `GET /api/v1/runs/{run_id}/events/stream` 会：
  - 校验 run 是否存在
  - 为该 run 建立订阅队列
  - 返回 `text/event-stream`
  - 连接断开时自动取消订阅

## 静态资源挂载

`src/app.py` 中的 `mount_dashboard_spa(app)` 当前行为如下：

- 检测 `web/dist/index.html` 是否存在
- 若存在：
  - `/` 返回 `index.html`
  - `/{full_path}` 优先返回真实静态资源文件
  - 非 `api/` 且不是静态资源的路径 fallback 到 `index.html`
- 若不存在：
  - 不挂载 SPA

## 开发与构建

### 当前前端脚本

```bash
cd web
npm run dev
npm run test
npm run build
```

### 当前 Vite 配置

- 使用 `@vitejs/plugin-react`
- 使用 `@tailwindcss/vite`
- dev server 监听 `127.0.0.1:4173`
- 当前没有配置 `/api` 代理

### 后端启动

```bash
python -m uvicorn src.app:app --reload --port 8321
```

## 当前约束

以下内容是当前实现的真实边界，文档需要明确保留：

1. 当前路由库是 React Router 6，不是 React Router 7。
2. 当前没有 `web/tailwind.config.ts`，主题定义在 `web/src/index.css`。
3. 当前没有独立的 `layouts/MainLayout.tsx`，页面壳层在 `web/src/router.tsx` 中实现。
4. `Agent Hosts` 页面当前是”配置展示优先 + 运维操作保留”的实现，而不是单独的重运维控制台。
