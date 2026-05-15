# 任务：为 DevWork Step4 按迭代设计完成开发与自审

$step_wall

$retry_feedback
$execution_strategy

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
2. 按上一轮 `actual_score_b` 决定推进重心：若 `actual_score_b >= 80`，说明实施情况已经较贴合迭代计划，本轮优先优化 P0/P1 或 `required_for_exit=true` 的高优先级计划，包括补齐测试、边界处理、错误处理、可维护性和质量细节；若 `actual_score_b < 80`，说明仍有明显未实现计划，本轮优先实现未实现的 active 开发计划，先覆盖 P0/P1 主流程和阻断缺口，再考虑局部优化。
3. 开发前对每个 mount 检查 `.gitignore` 与 `git status --porcelain`。若 `node_modules/`、`.vite/`、`coverage/`、缓存目录、`.tsbuildinfo`、build 输出等生成/依赖路径可能进入 diff，优先补 `.gitignore` 或确认已有忽略规则；若生成物已进入 status/index，清理生成物或从 index/status 移除误纳入项。不得删除业务源码、测试或 lockfile。
4. 如果本轮任务过大，可以不要求完成所有开发计划，未完成项在 `plan_execution` 中标为 `deferred` 或 `blocked`。
5. 建议探测包管理器和既有 lint / typecheck / 单元测试脚本（`Makefile`、`pyproject.toml`、`requirements.txt`、`package.json`、lockfile 等）；能运行则记录命令和结果，因环境、成本或依赖缺失无法运行时，在 `findings` 中说明原因。
6. 每个 DW 项或一组紧密相关变更完成后优先运行最小相关验证；若已开展的 lint / typecheck / 测试失败，建议就地修复一次并重跑；仍失败时必须如实记录，不要用 `pass=true` 掩盖失败。
7. 若实现必须偏离 Step2/Step3 的计划或执行地图，或需要按 Step3 `ADAPT` / `AVOID` 判断避开旧模式，继续使用更正确的实现路径，但必须在 findings JSON 的 `deviations` 中记录 WHAT/WHY/边界。
8. **不要 `git commit` / `git push`** —— 保留 worktree 未提交变更供 Step5 审 diff。
9. 将自审结果写入 `$findings_output_path`，必须是合法 JSON：
   ```json
   {"pass": true, "package_manager": "npm|pnpm|yarn|bun|uv|pip|cargo|go|unknown", "plan_execution": [{"id": "DW-01", "status": "done", "mount_name": "primary", "evidence": ["path/to/file.ts:10"], "validation": ["pytest tests/test_x.py"]}, {"id": "DW-02", "status": "deferred", "reason": "本轮容量不足"}], "gitignore_maintenance": [{"mount_name": "primary", "action": "updated|verified|cleaned", "paths": [".gitignore"], "message": "ignored generated outputs"}], "validation_commands": [{"command": "pytest tests/test_x.py", "status": "passed|failed|skipped", "message": "..."}], "deviations": [{"id": "DW-01", "what": "...", "why": "..."}], "findings": [{"kind": "lint|typecheck|unittest|diff|gitignore", "message": "...", "path": "..."}]}
   ```
   `pass=true` 表示已开展的 lint / typecheck / 测试均通过，且没有已知阻断；已运行检查失败即 `pass=false` 并在 `findings` 里列出至少一条。未运行建议测试时，必须在 `findings` 说明原因。`plan_execution[].status` 仅可为 `done`、`deferred`、`blocked`。`deviations` 应记录偏离计划、适配 MVP 旧模式或避开 `AVOID` 反模式的原因和边界。`package_manager`、`gitignore_maintenance`、`validation_commands`、`deviations` 是可选扩展字段，但建议尽量填写。

## 退出前检查

在结束 Step4 前，必须确认 `$findings_output_path` 已经存在且可读取，并且内容能被 JSON 解析。不要只把 JSON 打印到 stdout，也不要写到其它路径。
