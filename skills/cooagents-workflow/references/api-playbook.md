# API 操作手册

本手册提供 cooagents 工作流 API 的 curl 命令参考，按常见操作场景组织。所有命令均可直接复制使用，只需将 `<run_id>`、`<artifact_id>` 等占位符替换为实际值。

---

## 1. 创建并启动任务（场景 B：Agent 对话生成需求）

**适用场景：** Agent 与用户在对话中整理、生成需求文档。提交后进入 `REQ_REVIEW` 阶段，**用户必须审批需求内容后才能进入设计阶段**。这是需要用户确认需求文档质量的标准流程。

**前置条件：** 服务已在 `http://127.0.0.1:8321` 运行。

```bash
# 0. 确保仓库存在（创建任务前必须调用）
curl -s -X POST http://127.0.0.1:8321/api/v1/repos/ensure \
  -H "Content-Type: application/json" \
  -d '{"repo_path":"/path/to/repo","repo_url":"git@github.com:user/project.git"}'
# repo_url 可选：提供时 clone，不提供时 git init
# Response: {"status":"exists"} / {"status":"cloned"} / {"status":"initialized"}

# 1. 创建任务
curl -s -X POST http://127.0.0.1:8321/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"ticket":"PROJ-123","repo_path":"/path/to/repo","repo_url":"git@github.com:user/project.git","description":"任务描述"}'
# repo_url 可选，仅做记录；repo_path 必须是已有的 git 仓库，否则返回 400
# 可选字段：preferences（dict）、notify_channel（通知渠道）、notify_to（通知目标）
# 可选字段：design_agent（"claude"/"codex"，设计阶段 Agent）、dev_agent（"claude"/"codex"，开发阶段 Agent）
# 不指定时使用 config 中的 preferred_design_agent / preferred_dev_agent 默认值
# Response: {"id":"<run_id>","current_stage":"REQ_COLLECTING",...}
# 注：create 会自动推进 INIT → REQ_COLLECTING，响应中 current_stage 已为 REQ_COLLECTING

# 2. 提交需求
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/submit-requirement \
  -H "Content-Type: application/json" \
  -d '{"content":"# 需求文档\n详细需求内容..."}'

# 3. 推进到下一阶段
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/tick
# Response: {"id":"<run_id>","current_stage":"REQ_REVIEW",...}
```

### 1b. 上传需求文档创建任务（场景 A：用户已有需求文档）

**适用场景：** 用户已经自行编写好完整的需求文档（.md 或 .docx 文件）。上传后**自动审批需求，跳过 REQ_COLLECTING 和 REQ_REVIEW**，直接进入 `DESIGN_QUEUED`。适用于用户对需求内容已有明确把控、无需再次审批的情况。

**前置条件：** 已有需求文档文件（.md 或 .docx），服务已运行。

```bash
# 上传需求文档并创建任务（跳过 REQ_COLLECTING 和 REQ_REVIEW）
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/upload-requirement \
  -F "file=@/path/to/REQ-PROJ-123.md" \
  -F "ticket=PROJ-123" \
  -F "repo_path=/path/to/repo"
# 可选字段（均通过 -F 传递）：description、notify_channel、notify_to、repo_url、design_agent、dev_agent
# .docx 文件会自动通过 pandoc 转换为 markdown（需要主机安装 pandoc）
# Response: {"id":"<run_id>","current_stage":"DESIGN_QUEUED",...}
```

---

## 2. 查询状态

**前置条件：** 任务已创建，持有有效的 `<run_id>`。

```bash
# 查询单个任务
curl -s http://127.0.0.1:8321/api/v1/runs/<run_id>
# Response: 完整的 run 对象，包含 current_stage、created_at 等字段

# 列出所有任务
curl -s http://127.0.0.1:8321/api/v1/runs
# 可选过滤参数：?status=running&limit=20
```

**预期响应（单个任务）：**
```json
{
  "id": "<run_id>",
  "ticket": "PROJ-123",
  "current_stage": "REQ_REVIEW",
  "status": "running",
  "created_at": "2026-03-17T10:00:00Z",
  "updated_at": "2026-03-17T10:05:00Z"
}
```

### 2b. 查询任务简要（推荐）

当只需要了解当前进展和上一步结果时，使用 brief 接口代替完整查询：

```bash
# 按 ticket 查询（推荐 — 无需记住 run_id）
curl -s "http://127.0.0.1:8321/api/v1/runs/brief?ticket=PROJ-123"

# 按 run_id 查询
curl -s http://127.0.0.1:8321/api/v1/runs/<run_id>/brief
```

两个接口返回相同的响应结构。按 ticket 查询时自动选择最近的活跃 run（优先 running > failed > completed > cancelled）。

**预期响应：**
```json
{
  "run_id": "<run_id>",
  "ticket": "PROJ-123",
  "status": "running",
  "created_at": "2026-03-22T10:00:00Z",
  "current": {
    "stage": "DEV_RUNNING",
    "description": "开发 Agent 执行中",
    "action_type": "automatic",
    "since": "2026-03-22T10:30:00Z",
    "elapsed_sec": 1200,
    "summary": "codex 正在 host-2.local 上执行，已完成 3/5 轮",
    "job_id": "job-xxx",
    "job_status": "running",
    "agent_type": "codex",
    "turn_count": 3,
    "host": "host-2.local"
  },
  "previous": {
    "stage": "DEV_REVIEW",
    "result": "rejected",
    "reason": "测试覆盖率不足",
    "by": "reviewer",
    "at": "2026-03-22T10:29:00Z"
  },
  "progress": {
    "gates_passed": ["req", "design"],
    "gates_remaining": ["dev"],
    "artifacts_count": 4
  }
}
```

**字段说明：**
- `current.description` — 阶段的中文描述
- `current.action_type` — `automatic`（自动推进）/ `gate`（需审批）/ `manual`（需人工操作）/ `terminal`（终态）
- `current.summary` — 一句话概括当前正在发生什么
- `previous` — 上一个有意义的阶段（审批门/人工操作/终态），含审批结果与原因（无历史时为 null）
- `progress.gates_passed` — 已通过的审批门
- `progress.gates_remaining` — 尚未通过的审批门

---

## 3. 审批通过

**前置条件：** 任务处于审批等待阶段（`REQ_REVIEW`、`DESIGN_REVIEW` 或 `DEV_REVIEW`）。

```bash
# 审批通过
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/approve \
  -H "Content-Type: application/json" \
  -d '{"gate":"req","by":"reviewer_name","comment":"LGTM"}'
# gate 可选值："req"、"design"、"dev"
# comment 为可选字段

# 推进到下一阶段
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/tick
```

**预期响应：**
```json
{"id":"<run_id>","current_stage":"DESIGN_QUEUED","status":"running"}
```

---

## 4. 驳回重做

**前置条件：** 任务处于审批等待阶段（`REQ_REVIEW`、`DESIGN_REVIEW` 或 `DEV_REVIEW`）。

```bash
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/reject \
  -H "Content-Type: application/json" \
  -d '{"gate":"design","by":"reviewer_name","reason":"需要补充错误处理设计"}'
```

**驳回后目标阶段：**

| gate 值   | 驳回后跳转阶段     |
|-----------|--------------------|
| `req`     | `REQ_COLLECTING`   |
| `design`  | `DESIGN_QUEUED`    |
| `dev`     | `DEV_QUEUED`       |

**预期响应：**
```json
{"id":"<run_id>","current_stage":"DESIGN_QUEUED","status":"running"}
```

---

## 5. 查看产物

**前置条件：** 任务已产生产物（通常在各阶段完成后）。

```bash
# 列出产物
curl -s http://127.0.0.1:8321/api/v1/runs/<run_id>/artifacts
# 可选过滤参数：?kind=design&status=approved

# 获取产物内容
curl -s http://127.0.0.1:8321/api/v1/runs/<run_id>/artifacts/<artifact_id>/content

# 获取产物与上一版本的 diff
curl -s http://127.0.0.1:8321/api/v1/runs/<run_id>/artifacts/<artifact_id>/diff
```

**预期响应（列出产物）：**
```json
[
  {
    "id": "<artifact_id>",
    "kind": "design",
    "status": "approved",
    "created_at": "2026-03-17T10:10:00Z"
  }
]
```

---

## 6. 处理失败

根据失败类型选择不同命令：

- **`retry`**（POST /retry）：用于 `FAILED` 状态的任务，将任务恢复到 `failed_at_stage` 或 `INIT` 阶段重新执行。
- **`recover`**（POST /recover）：用于中断或卡住的 job，支持 `resume`（继续）、`redo`（重做）、`manual`（标记为人工处理）三种动作。

```bash
# retry：用于 FAILED 状态
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/retry \
  -H "Content-Type: application/json" \
  -d '{"by":"operator","note":"修复了配置问题"}'

# recover：用于中断的 job
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/recover \
  -H "Content-Type: application/json" \
  -d '{"action":"resume"}'
# action 可选值："resume"（继续）、"redo"（重做）、"manual"（标记为人工处理）
```

**预期响应：**
```json
{"id":"<run_id>","current_stage":"DEV_QUEUED","status":"running"}
```

---

## 7. 取消任务

**前置条件：** 任务处于可取消状态（非已完成状态）。

```bash
curl -s -X DELETE http://127.0.0.1:8321/api/v1/runs/<run_id>
# 可选参数：?cleanup=true 同时清理 worktree
```

**预期响应：**
```json
{"id":"<run_id>","status":"cancelled"}
```

---

## 8. 推进阶段（tick — 最常用）

**前置条件：** 任务处于可推进的阶段（已审批或满足推进条件）。

```bash
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/tick
# 幂等操作 — 可安全地重复调用
# 返回更新后的 run 对象，包含新的 current_stage
```

**预期响应：**
```json
{
  "id": "<run_id>",
  "current_stage": "DESIGN_QUEUED",
  "status": "running",
  "updated_at": "2026-03-17T10:15:00Z"
}
```

**注意：** tick 是幂等的，若当前阶段无法推进（如等待审批），调用不会报错，而是返回当前状态。

---

## 9. 解决合并冲突

**前置条件：** 任务处于 `MERGE_CONFLICT` 阶段，用户已在 worktree 中手动解决冲突。

```bash
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/resolve-conflict \
  -H "Content-Type: application/json" \
  -d '{"by":"operator"}'
# 将任务从 MERGE_CONFLICT 重新入队到 MERGE_QUEUED
```

**查看冲突文件列表：**

```bash
curl -s http://127.0.0.1:8321/api/v1/runs/<run_id>/conflicts
# Response: {"conflicts":["path/to/file1.py","path/to/file2.py"]}
```

**预期响应：**
```json
{"id":"<run_id>","current_stage":"MERGE_QUEUED","status":"running"}
```

---

## 10. 查看 Job 执行信息

**前置条件：** 任务已分派 Agent 执行。

```bash
# 列出任务的所有 job
curl -s http://127.0.0.1:8321/api/v1/runs/<run_id>/jobs

# 获取 job 输出
curl -s http://127.0.0.1:8321/api/v1/runs/<run_id>/jobs/<job_id>/output
# Response: {"job_id":"<job_id>","output":...}
```

---

## 11. 合并队列管理

```bash
# 手动入队合并（通常由状态机自动处理）
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/merge \
  -H "Content-Type: application/json" \
  -d '{"priority":0}'
# priority 越大优先级越高，默认 0

# 跳过合并
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/merge-skip

# 查看合并队列
curl -s http://127.0.0.1:8321/api/v1/repos/merge-queue
```

---

## 12. 仓库管理

```bash
# 列出所有关联仓库
curl -s http://127.0.0.1:8321/api/v1/repos

# 按路径查看仓库的所有任务
curl -s "http://127.0.0.1:8321/api/v1/repos?path=/path/to/repo"
```

---

## 13. 诊断与链路追踪

**前置条件：** 任务出现异常，需要排查失败原因。

```bash
# 查看 run 的完整事件链路
curl -s http://127.0.0.1:8321/api/v1/runs/<run_id>/trace
# 可选参数：?level=error（只看 error 级别）、?span_type=job、?limit=50&offset=0
# Response: {"run_id":"<run_id>","summary":{"total_events":42,"errors":1,...},"events":[...]}

# 查看 job 的故障诊断
curl -s http://127.0.0.1:8321/api/v1/jobs/<job_id>/diagnosis
# Response: {"job_id":"<job_id>","diagnosis":{"error_summary":"TimeoutError: ...","error_detail":"...","duration_ms":120000,...}}

# 通过 trace_id 追踪完整请求链路
curl -s http://127.0.0.1:8321/api/v1/traces/<trace_id>
# trace_id 可从 HTTP 响应头 X-Trace-Id 获取
# Response: {"trace_id":"<trace_id>","affected_runs":["run-1"],"affected_jobs":["job-1"],"events":[...]}
```

**典型排查流程：**
```bash
# 1. 查看 run 的错误事件
curl -s "http://127.0.0.1:8321/api/v1/runs/<run_id>/trace?level=error"

# 2. 从 summary.jobs 找到失败的 job_id，查看诊断
curl -s http://127.0.0.1:8321/api/v1/jobs/<job_id>/diagnosis

# 3. 根据 diagnosis.error_summary 决定处理方式
```

---

## 14. Webhook 管理

```bash
# 注册 webhook
curl -s -X POST http://127.0.0.1:8321/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/hook","events":["job.completed","run.completed"],"secret":"optional-hmac-secret"}'
# events 可选：不指定则接收所有事件；secret 可选：提供时使用 HMAC-SHA256 签名

# 列出所有 webhook
curl -s http://127.0.0.1:8321/api/v1/webhooks

# 删除 webhook
curl -s -X DELETE http://127.0.0.1:8321/api/v1/webhooks/<webhook_id>

# 查看投递记录
curl -s http://127.0.0.1:8321/api/v1/webhooks/<webhook_id>/deliveries
```
