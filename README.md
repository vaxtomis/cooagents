# cooagents

OpenClaw / Claude / Codex 协作流程模板仓库（异步事件驱动版）。

## 角色分工
- OpenClaw：需求沟通确认、任务分配、流程 gate
- Claude：需求理解、功能设计
- Codex：编码实现、测试与提交

## 流程概览
1. 需求确认（OpenClaw）→ 输出 `docs/req/REQ-<ticket>.md`
2. 设计阶段（Claude）→ 输出 `docs/design/DES-<ticket>.md` + ADR
3. 开发阶段（Codex）→ 输出代码 + `docs/dev/TEST-REPORT-<ticket>.md`

详见：`docs/PROCESS.md`

## 异步工作流（SQLite + 事件日志）

- 状态数据库：`.coop/state.db`
- 运行快照：`.coop/runs/<run_id>/state.json`
- 锁文件：`.coop/workflow.lock`

常用命令：

```bash
# 1) 创建运行实例
scripts/workflow-start.sh <ticket>

# 2) 推进一次状态机（可手动反复执行，或由 cron 定时执行）
scripts/workflow-tick.sh <run_id>

# 3) Gate 审批
scripts/workflow-approve.sh <run_id> req 小吴 "需求确认通过"
scripts/workflow-approve.sh <run_id> design 小吴 "设计确认通过"

# 4) 查看状态
scripts/workflow-status.sh <run_id>
scripts/workflow-status.sh --list

# 5) 失败重试
scripts/workflow-retry.sh <run_id> <operator> "重试原因"

# 6) 批量推进（cron 调度）
scripts/workflow-tick-cron.sh

# 7) 输出关键事件（可接消息通知）
python3 scripts/workflow-notify.py
```

