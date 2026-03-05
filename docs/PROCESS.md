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
