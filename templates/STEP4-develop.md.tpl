# 任务：为 DevWork Step4 按迭代设计完成开发与自审

$step_wall

$retry_feedback

## 必读路径

1. **本轮迭代设计**：`$iteration_note_path`
2. **本轮浓缩上下文**：`$context_path`

## 工作树

默认落地 worktree：`$worktree_path`

## 多仓改动表

$mount_table

如本轮需要修改非 primary mount，请直接 `cd <mount.worktree_path>` 后落盘；所有 mount 均为本机 git worktree，可独立 `git status` / `git diff`。

## 产出要求

1. 按迭代设计的「开发计划」在相应 worktree 内实现代码变更，保证已执行计划项有对应测试覆盖。
2. 如果本轮任务过大，可以不要求完成所有开发计划，未完成项在 `plan_execution` 中标为 `deferred` 或 `blocked`。
3. 运行项目既有的 lint / typecheck / 单元测试命令（自行探测 `Makefile`、`pyproject.toml`、`package.json` 等）。
4. 第一轮 lint/typecheck/测试若失败，**最多就地修复一次**再跑一次；之后失败必须如实记录。
5. **不要 `git commit` / `git push`** —— 保留 worktree 未提交变更供 Step5 审 diff。
6. 将自审结果写入 `$findings_output_path`，必须是合法 JSON：
   ```json
   {"pass": true, "plan_execution": [{"id": "DW-01", "status": "done", "evidence": ["path/to/file.ts:10"]}, {"id": "DW-02", "status": "deferred", "reason": "本轮容量不足"}], "findings": [{"kind": "lint|typecheck|unittest|diff", "message": "...", "path": "..."}]}
   ```
   `pass=true` 表示已交付代码的 lint / typecheck / 测试全部通过；任何一项失败即 `pass=false` 并在 `findings` 里列出至少一条。`plan_execution[].status` 仅可为 `done`、`deferred`、`blocked`。

## 退出前检查

在结束 Step4 前，必须确认 `$findings_output_path` 已经存在且可读取，并且内容能被 JSON 解析。不要只把 JSON 打印到 stdout，也不要写到其它路径。
