# 多 Agent 协作流程（OpenClaw + Claude + Codex）

## Phase A：需求确认（OpenClaw）
1. 与需求方沟通并确认仓库
2. 形成 `docs/req/REQ-<ticket>.md`
3. Gate A：需求方确认后进入设计

## Phase B：需求设计（Claude）
1. 创建设计 worktree 与分支 `feat/<ticket>-design`
2. 在 tmux 会话中启动 Claude
3. 完成设计文档并提交
4. Gate B：OpenClaw 审核 + 需求方确认

## Phase C：开发实现（Codex）
1. 创建开发 worktree 与分支 `feat/<ticket>-dev`
2. 在 tmux 会话中启动 Codex
3. 按设计实现、测试、提交
4. Gate C：OpenClaw 汇总并准备 PR

## 分支规范
- 设计分支：`feat/<ticket>-design`
- 开发分支：`feat/<ticket>-dev`

## tmux 会话规范
- 设计：`design-<ticket>`
- 开发：`dev-<ticket>`

## 异步稳定模式（事件驱动）

采用状态机推进，避免线性脚本中断导致全流程失败。

状态流转：
- `INIT` -> `REQ_COLLECTING` -> `REQ_READY`(等待 req 审批)
- `DESIGN_RUNNING` -> `DESIGN_DONE`(等待 design 审批)
- `DEV_RUNNING` -> `COMPLETED`

实现要点：
- SQLite 存储运行状态/事件/审批/产物
- `tick` 幂等：可重复执行，不重复创建会话和 worktree
- `flock` 锁避免并发 tick 冲突
- Gate 审批（req/design）显式触发下一阶段
- 状态快照写入 `.coop/runs/<run_id>/state.json`，便于排障
- `tick` 异常自动将 run 标记为 `failed`，可执行 retry

## 调度建议（cron）

示例：每 2 分钟推进运行中的流程、每 2 分钟拉取关键事件

```cron
*/2 * * * * cd /path/to/cooagents && scripts/workflow-tick-cron.sh >> .coop/cron-tick.log 2>&1
*/2 * * * * cd /path/to/cooagents && python3 scripts/workflow-notify.py >> .coop/cron-notify.log 2>&1
```

