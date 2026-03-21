# 异常处理策略

本文件定义了 Agent 的自主异常处理规则。Agent 应优先尝试自动恢复，仅在超出处理能力或达到重试上限时，才将问题升级并通知用户。

## 异常决策表

| 事件 | 自动响应 | 升级条件 |
|------|----------|----------|
| `job.timeout` | `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/recover -H "Content-Type: application/json" -d '{"action":"resume"}'`，最多 3 次 | 连续 3 次 → 回复通知用户 |
| `job.failed` | `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/retry -H "Content-Type: application/json" -d '{"by":"agent","note":"自动重试"}'`，最多 2 次 | 重试仍失败 → 回复通知用户 |
| `job.interrupted` | 同 `job.failed` | 同上 |
| `merge.conflict` | exec `curl GET /conflicts` 获取冲突文件列表 → 回复通知用户 → 用户解决后执行 `curl POST /resolve-conflict` | — |
| `host.offline` | 等待 `host.online` 事件后执行 `curl POST .../tick` | >30 分钟 → 回复通知用户 |
| curl 4xx 响应 | 记录错误，不重试 | 回复通知用户 |
| curl 5xx / 网络错误 | 等 10s 重试 1 次 | 仍失败 → 回复通知用户 |

## 重试计数追踪

Agent 在对话上下文中按 run_id 追踪重试次数。每次自动恢复操作后递增计数器，达到上限时停止自动处理并通知用户。

## 升级通知格式

使用 `feishu-interaction.md` 中的异常升级模板：

```
⚠️ 任务 {ticket} 需要人工介入
原因：{reason}
当前阶段：{stage}
建议：{suggestion}
```

## curl 错误检测

```bash
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick)
if [ "$HTTP_CODE" -ge 400 ] && [ "$HTTP_CODE" -lt 500 ]; then
  # 4xx: 客户端错误，不重试
elif [ "$HTTP_CODE" -ge 500 ]; then
  # 5xx: 服务端错误，等 10s 重试 1 次
fi
```
