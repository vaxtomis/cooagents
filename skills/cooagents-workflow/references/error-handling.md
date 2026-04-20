# 异常处理策略

本文件定义了 Agent 的自主异常处理规则。Agent 应优先尝试自动恢复，仅在超出处理能力或达到重试上限时，才将问题升级并通知用户。

## 异常决策表

升级通知统一使用 `feishu-interaction.md` §2 的人工确认消息格式（`📋 {ticket} · 需要介入`）。

| 事件 | 自动响应 | 升级条件 |
|------|----------|----------|
| `job.timeout` | `curl -s -X POST -H "X-Agent-Token: $AGENT_API_TOKEN" .../recover -H "Content-Type: application/json" -d '{"action":"resume"}'`，最多 3 次 | 连续 3 次 → §2 格式通知用户 |
| `job.failed` | `curl -s -X POST -H "X-Agent-Token: $AGENT_API_TOKEN" .../retry -H "Content-Type: application/json" -d '{"note":"自动重试"}'`，最多 2 次 | 重试仍失败 → §2 格式通知用户 |
| `job.interrupted` | 同 `job.failed` | 同上 |
| `merge.conflict` | exec `curl GET -H "X-Agent-Token: $AGENT_API_TOKEN" /conflicts` 获取冲突文件列表 | §2 格式通知用户（`label` 填"合并冲突"） |
| `host.offline` | 等待 `host.online` 事件后执行 `curl POST -H "X-Agent-Token: $AGENT_API_TOKEN" .../tick` | >30 分钟 → §2 格式通知用户 |
| 401 响应 | 环境变量 `AGENT_API_TOKEN` 未正确注入，停止重试 | 立即 §2 格式通知用户并提示检查安装 |
| 429 响应 | 等 60s 再重试 1 次 | 仍 429 → §2 格式通知用户（限流由 cooagents 服务侧管控） |
| 4xx 其他响应 | 记录错误，不重试 | §2 格式通知用户 |
| 5xx / 网络错误 | 等 10s 重试 1 次 | 仍失败 → §2 格式通知用户 |

## 诊断 API 排查

在执行自动恢复之前，建议先通过诊断 API 获取详细信息，避免盲目重试：

```bash
# 查看错误事件链路
curl -s -H "X-Agent-Token: $AGENT_API_TOKEN" "http://127.0.0.1:8321/api/v1/runs/{run_id}/trace?level=error"

# 获取 job 诊断（含错误摘要、堆栈、主机状态；敏感字段已脱敏）
curl -s -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/jobs/{job_id}/diagnosis
```

诊断结果中的关键字段：
- `diagnosis.error_summary` — 错误类型（如 `TimeoutError`、`SSHError`）
- `diagnosis.failure_context.host_status_at_failure` — 失败时主机状态（`offline` 说明主机问题）
- `diagnosis.failure_context.retry_count` — 已重试次数
- `diagnosis.duration_ms` — Job 耗时

根据诊断结果调整处理策略：
- 主机 `offline` → 等待 `host.online` 再重试
- `retry_count` ≥ 3 → 不再自动重试，通知用户
- `TimeoutError` → 考虑使用 `recover` 而非 `retry`

## 重试计数追踪

Agent 在对话上下文中按 run_id 追踪重试次数。每次自动恢复操作后递增计数器，达到上限时停止自动处理并通知用户。

## 升级通知格式

使用 `feishu-interaction.md` §2 的统一人工确认消息格式，`label` 填 "需要介入"：

```
📋 {ticket} · 需要介入

原因：{reason}
阶段：{stage}
建议：{suggestion}

请回复：
- "重试" — 再次尝试
- 其他处理方案 — 人工介入
```

## curl 错误检测

```bash
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  -H "X-Agent-Token: $AGENT_API_TOKEN" \
  http://127.0.0.1:8321/api/v1/runs/{run_id}/tick)
if [ "$HTTP_CODE" -eq 401 ]; then
  # 认证失败，检查 $AGENT_API_TOKEN 是否已注入
elif [ "$HTTP_CODE" -eq 429 ]; then
  # 触发限流，等 60s 后单次重试
elif [ "$HTTP_CODE" -ge 400 ] && [ "$HTTP_CODE" -lt 500 ]; then
  # 4xx: 客户端错误，不重试
elif [ "$HTTP_CODE" -ge 500 ]; then
  # 5xx: 服务端错误，等 10s 重试 1 次
fi
```
