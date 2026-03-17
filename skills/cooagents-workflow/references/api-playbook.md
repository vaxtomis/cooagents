# API 操作手册

本手册提供 cooagents 工作流 API 的 curl 命令参考，按常见操作场景组织。所有命令均可直接复制使用，只需将 `<run_id>`、`<artifact_id>` 等占位符替换为实际值。

---

## 1. 创建并启动任务

**前置条件：** 服务已在 `http://127.0.0.1:8321` 运行。

```bash
# 1. 创建任务
curl -s -X POST http://127.0.0.1:8321/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"ticket":"PROJ-123","repo_path":"/path/to/repo","description":"任务描述"}'
# Response: {"id":"<run_id>","current_stage":"INIT",...}

# 2. 提交需求
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/submit-requirement \
  -H "Content-Type: application/json" \
  -d '{"content":"# 需求文档\n详细需求内容..."}'

# 3. 推进到下一阶段
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/tick
# Response: {"id":"<run_id>","current_stage":"REQ_REVIEW",...}
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
  "status": "active",
  "created_at": "2026-03-17T10:00:00Z",
  "updated_at": "2026-03-17T10:05:00Z"
}
```

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
{"id":"<run_id>","current_stage":"DESIGN_QUEUED","status":"active"}
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
{"id":"<run_id>","current_stage":"DESIGN_QUEUED","status":"active"}
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
{"id":"<run_id>","current_stage":"DEV_QUEUED","status":"active"}
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
  "status": "active",
  "updated_at": "2026-03-17T10:15:00Z"
}
```

**注意：** tick 是幂等的，若当前阶段无法推进（如等待审批），调用不会报错，而是返回当前状态。
