# Cooagents Dashboard 设计文档

## 概述

为 cooagents 多 Agent 协作编排系统构建一个全功能 Web Dashboard，以**项目管理者**为主视角，兼顾运维与开发者需求。用户可通过 Dashboard 一览全局状态、跟踪任务进度、执行审批操作、查看 Agent 输出、监控主机健康、排查错误。

## 技术栈

| 层 | 选型 |
|---|------|
| 前端框架 | Vue 3 (Composition API + `<script setup>`) |
| 语言 | TypeScript |
| 组件库 | Naive UI |
| 构建工具 | Vite |
| HTTP 客户端 | axios |
| 路由 | Vue Router 4 |
| 实时通信 | SSE (Server-Sent Events) |
| 后端 | FastAPI (已有) |
| 部署 | 生产时打包嵌入 FastAPI 静态文件服务 |

## 项目结构

```
cooagents/
├── web/                          # Vue 3 前端项目
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.ts               # 入口，挂载 NaiveUI
│       ├── App.vue               # 根组件 + NaiveUI Provider + Router
│       ├── router/
│       │   └── index.ts          # Vue Router 路由定义
│       ├── api/                   # API 调用层
│       │   ├── client.ts         # axios 实例 + 基础配置
│       │   ├── runs.ts           # /runs 相关接口
│       │   ├── agents.ts         # /agent-hosts 相关接口
│       │   ├── merge.ts          # merge-queue 相关接口
│       │   └── diagnostics.ts    # /trace /events 相关接口
│       ├── composables/           # 可复用逻辑
│       │   ├── usePolling.ts     # 轮询 hook (列表页，默认 15s)
│       │   └── useSSE.ts         # SSE 连接 hook (详情页，自动重连)
│       ├── layouts/
│       │   └── MainLayout.vue    # 左侧导航 + 内容区
│       ├── views/                 # 页面组件
│       │   ├── DashboardView.vue # 概览页
│       │   ├── RunsListView.vue  # Runs 列表
│       │   ├── RunDetailView.vue # Run 详情
│       │   ├── AgentHostsView.vue# Agent 主机
│       │   ├── MergeQueueView.vue# Merge 队列
│       │   └── EventLogView.vue  # 事件日志
│       ├── components/            # 共享组件
│       │   ├── StageProgress.vue # 15 阶段进度条
│       │   ├── RunCard.vue       # Run 摘要卡片
│       │   ├── ApprovalAction.vue# 审批操作按钮
│       │   └── StatusBadge.vue   # 状态标签
│       └── types/
│           └── index.ts          # TypeScript 类型定义
├── src/
│   ├── app.py                    # 修改: 挂载 Vue dist 静态文件
│   └── sse.py                    # 新增: SSE 广播器
└── routes/
    └── sse.py                    # 新增: SSE 路由
```

## 页面设计

### 1. 概览页 (DashboardView)

**路由:** `/`

**布局:**
- 顶部：5 个统计卡片 — 运行中、待审批、合并中、失败、已完成
- 左下：活跃任务列表 — 每个 Run 显示 ticket、描述、当前阶段、Agent/轮次、迷你进度条
- 右下上：待审批列表 — 可直接操作通过/驳回
- 右下下：Agent 主机状态摘要 — 在线状态 + 负载

**数据来源:**
- 统计卡片 → `GET /api/v1/runs` 按 status 聚合计数
- 活跃任务 → `GET /api/v1/runs?status=running`，结合 `GET /api/v1/runs/{id}/brief` 获取摘要
- 待审批 → 过滤 `current_stage` 为 `REQ_REVIEW` / `DESIGN_REVIEW` / `DEV_REVIEW` 的 run
- Agent 主机 → `GET /api/v1/agent-hosts`

**刷新策略:** 轮询，15 秒间隔

### 2. Runs 列表页 (RunsListView)

**路由:** `/runs`

**功能:**
- 表格展示所有 Run，列：Ticket、描述、当前阶段（彩色标签）、状态、轮次、创建时间
- 筛选：按 status 下拉筛选（running / completed / failed / cancelled / 全部）
- 搜索：按 ticket 关键字搜索
- 排序：按创建时间倒序（默认），可按列排序
- 分页：Naive UI 分页组件
- 点击行跳转到 Run 详情页

**数据来源:** `GET /api/v1/runs`（支持 status 查询参数）

**刷新策略:** 轮询，15 秒间隔

### 3. Run 详情页 (RunDetailView)

**路由:** `/runs/:runId`

**布局:**
- 面包屑导航：Runs / {ticket}
- 标题区：ticket + 描述 + repo + 创建时间 + 操作按钮（取消 Run）
- **15 阶段进度条** — 已完成阶段绿色，当前阶段发光高亮，未到达灰色。鼠标悬停显示阶段名
- 左列：当前状态卡片 — 阶段（中文描述）、Agent 类型 + 主机、轮次、耗时、Job ID
- 右列：审批记录卡片 — req / design / dev 三个 Gate 的状态（approved / rejected / 未到达）
- 底部 Tab 切换：
  - **Artifacts** — 产物表格（类型标签、路径、版本、状态），可查看内容和 diff
  - **Agent 输出** — 当前/历史 Job 的执行输出文本（`GET /runs/{run_id}/jobs/{job_id}/output`）
  - **事件追踪** — 该 Run 的事件流，SSE 实时推送（`GET /runs/{run_id}/trace`）
  - **Stage 历史** — 阶段转换时间线，展示 steps 表数据

**审批操作:**
- 当 `current_stage` 为 `*_REVIEW` 时，显示「通过」和「驳回」按钮
- 通过 → `POST /api/v1/runs/{run_id}/approve`
- 驳回 → `POST /api/v1/runs/{run_id}/reject`（带 reason 输入框）

**数据来源:**
- 基础信息 → `GET /api/v1/runs/{run_id}`（返回 steps、approvals、events、artifacts）
- 摘要 → `GET /api/v1/runs/{run_id}/brief`
- Artifacts → `GET /api/v1/runs/{run_id}/artifacts`
- Artifact 内容 → `GET /api/v1/runs/{run_id}/artifacts/{id}/content`
- Artifact Diff → `GET /api/v1/runs/{run_id}/artifacts/{id}/diff`
- Job 输出 → `GET /api/v1/runs/{run_id}/jobs/{job_id}/output`
- 事件 → `GET /api/v1/runs/{run_id}/trace`

**刷新策略:** SSE 实时推送（`GET /api/v1/runs/{run_id}/events/stream`），自动重连

### 4. Agent 主机页 (AgentHostsView)

**路由:** `/agent-hosts`

**布局:**
- 右上角「+ 注册主机」按钮，弹出表单
- 卡片网格（2 列），每个主机一张卡片：
  - 标题 + 状态标签（active 绿 / draining 黄 / offline 红）
  - 属性：类型（claude / codex / both）、地址、并发负载（当前/最大）
  - 负载进度条
  - 操作按钮：健康检查、编辑、停用/删除

**数据来源:**
- 列表 → `GET /api/v1/agent-hosts`
- 注册 → `POST /api/v1/agent-hosts`
- 编辑 → `PUT /api/v1/agent-hosts/{host_id}`
- 删除 → `DELETE /api/v1/agent-hosts/{host_id}`
- 健康检查 → `POST /api/v1/agent-hosts/{host_id}/check`

**刷新策略:** 轮询，30 秒间隔

### 5. Merge 队列页 (MergeQueueView)

**路由:** `/merge-queue`

**功能:**
- 表格展示队列，列：优先级、Ticket、分支名、状态、冲突文件数、操作
- 状态：merging（蓝）、waiting（灰）、merged（绿）、conflict（红）、skipped（灰）
- conflict 项显示冲突文件数，可点击查看冲突详情
- 操作：跳过合并（`POST /runs/{run_id}/merge-skip`）

**数据来源:**
- 队列 → `GET /api/v1/repos/merge-queue`
- 冲突详情 → `GET /api/v1/runs/{run_id}/conflicts`
- 跳过 → `POST /api/v1/runs/{run_id}/merge-skip`

**刷新策略:** 轮询，15 秒间隔

### 6. 事件日志页 (EventLogView)

**路由:** `/events`

**布局:**
- 顶部过滤栏：Level 下拉、Span type 下拉、Run 下拉、trace_id 搜索框
- 终端风格日志列表（等宽字体），每行：时间、Level（颜色编码）、Run ticket、Span type、事件描述
- Level 颜色：INFO 绿、WARN 黄、ERROR 红、DEBUG 灰
- 点击 Run ticket 跳转到 Run 详情

**数据来源:**
- 全局事件 → `GET /api/v1/events`（新增端点，支持 level、span_type、run_id 查询参数，按时间倒序分页）
- Trace 查找 → `GET /api/v1/traces/{trace_id}`

**刷新策略:** 轮询，10 秒间隔

## 后端新增

### SSE 端点

**路由:** `GET /api/v1/runs/{run_id}/events/stream`

**实现:**
- 新增 `src/sse.py` — `SSEBroadcaster` 类
  - 维护 `dict[str, list[asyncio.Queue]]`，key 为 run_id
  - `subscribe(run_id)` → 创建 Queue 并注册
  - `unsubscribe(run_id, queue)` → 移除 Queue
  - `broadcast(run_id, event_type, data)` → 向该 run 的所有 Queue 推送
- 在 `TraceEmitter.emit()` 中调用 `SSEBroadcaster.broadcast()`
- 新增 `routes/sse.py` — SSE 路由
  - 使用 `StreamingResponse` + `text/event-stream`
  - 连接时注册 Queue，断开时清理

### 全局事件查询端点

**路由:** `GET /api/v1/events`

**查询参数:**
- `level` — 过滤 level（debug / info / warning / error）
- `span_type` — 过滤 span_type（system / user）
- `run_id` — 过滤特定 run
- `limit` — 返回条数（默认 100）
- `offset` — 分页偏移

**实现:** 直接查询 `events` 表，按 `created_at` 倒序，JOIN `runs` 表获取 ticket。新增 `routes/events.py`。

**推送事件类型:**
- `stage_changed` — 阶段变化
- `job_updated` — Job 状态/轮次更新
- `approval_changed` — 审批状态变化
- `artifact_created` — 新产物生成
- `run_completed` — Run 完成/失败

**事件格式:**
```
event: stage_changed
data: {"run_id":"...","stage":"DESIGN_RUNNING","previous":"DESIGN_DISPATCHED","timestamp":"..."}

event: job_updated
data: {"run_id":"...","job_id":"...","status":"running","turn_count":2,"agent_type":"claude"}

event: approval_changed
data: {"run_id":"...","gate":"design","decision":"approved","by":"admin","timestamp":"..."}
```

### 静态文件服务

修改 `src/app.py`：
- 检测 `web/dist/` 目录是否存在
- 存在则使用 `StaticFiles(directory="web/dist", html=True)` 挂载到 `/`
- API 路由 `/api/v1/*` 优先级高于静态文件
- 所有非 API、非静态文件请求 fallback 到 `index.html`（SPA 路由支持）
- 开发模式下不挂载，由 Vite dev server 处理

## 前端数据层

### API Client (`api/client.ts`)
- axios 实例，baseURL: 开发时 `http://localhost:8321/api/v1`，生产时 `/api/v1`
- 统一错误处理拦截器

### Composables

**`usePolling(fetchFn, intervalMs)`**
- 定时调用 fetchFn 获取数据
- 返回 `{ data, loading, error, refresh() }`
- 组件卸载时自动清理 interval
- 页面不可见时暂停轮询（`visibilitychange` 事件）

**`useSSE(url)`**
- 建立 SSE 连接
- 返回 `{ events, connected, error }`
- 断开自动重连（指数退避，最大 30 秒）
- 组件卸载时自动关闭连接

## 共享组件

### StageProgress
- 输入：`currentStage: string`，`steps: Step[]`
- 展示 15 阶段的水平进度条
- 已完成阶段绿色，当前阶段发光高亮，未到达灰色
- 鼠标悬停 tooltip 显示阶段中文名和时间

### StatusBadge
- 输入：`status: string`（running / completed / failed / cancelled 等）
- 对应颜色的 Naive UI Tag 组件

### RunCard
- 输入：`run: RunBrief`
- 概览页使用，展示 ticket、描述、阶段、Agent 信息、迷你进度条

### ApprovalAction
- 输入：`runId: string`，`gate: string`
- 「通过」和「驳回」按钮
- 驳回时弹出输入框填写 reason

## 路由配置

```typescript
const routes = [
  {
    path: '/',
    component: MainLayout,
    children: [
      { path: '', name: 'dashboard', component: DashboardView },
      { path: 'runs', name: 'runs', component: RunsListView },
      { path: 'runs/:runId', name: 'run-detail', component: RunDetailView },
      { path: 'agent-hosts', name: 'agent-hosts', component: AgentHostsView },
      { path: 'merge-queue', name: 'merge-queue', component: MergeQueueView },
      { path: 'events', name: 'events', component: EventLogView },
    ]
  }
]
```

## 开发与部署

### 开发模式
```bash
# 终端 1: 启动 FastAPI
python -m uvicorn src.app:app --reload --port 8321

# 终端 2: 启动 Vite dev server
cd web && npm run dev
```

Vite 配置 API 代理：
```typescript
// vite.config.ts
export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8321'
    }
  }
})
```

### 生产构建
```bash
cd web && npm run build
# 输出到 web/dist/，FastAPI 自动挂载
```

## 阶段定义与颜色

进度条展示正常流程的 14 个阶段（INIT → MERGED）。FAILED 和 MERGE_CONFLICT 是异常终态，不在进度条中显示，而是通过状态标签（StatusBadge）体现。

| 阶段 | 中文 | 类型 | 颜色 |
|------|------|------|------|
| INIT | 初始化 | automatic | gray |
| REQ_COLLECTING | 等待需求提交 | manual | blue |
| REQ_REVIEW | 需求审批中 | gate | orange |
| DESIGN_QUEUED | 设计任务排队中 | automatic | blue |
| DESIGN_DISPATCHED | 设计 Agent 启动中 | automatic | blue |
| DESIGN_RUNNING | 设计 Agent 执行中 | automatic | green |
| DESIGN_REVIEW | 设计审批中 | gate | orange |
| DEV_QUEUED | 开发任务排队中 | automatic | blue |
| DEV_DISPATCHED | 开发 Agent 启动中 | automatic | blue |
| DEV_RUNNING | 开发 Agent 执行中 | automatic | green |
| DEV_REVIEW | 开发审批中 | gate | orange |
| MERGE_QUEUED | 合并排队中 | automatic | blue |
| MERGING | 合并执行中 | automatic | blue |
| MERGED | 已合并完成 | terminal | green |
| FAILED | 执行失败 | terminal | red |
| MERGE_CONFLICT | 合并冲突待解决 | manual | red |
