# 任务：为 DevWork Step4 按迭代设计完成开发与自审

$step_wall

## 工作树

`$worktree_path`

## 多仓改动表

$mount_table

> 如本轮需要修改非 primary mount，请直接 `cd <mount.worktree_path>` 后落盘；
> 所有 mount 均为本机 git worktree，可独立 `git status` / `git diff`。

## 参考输入（只读）

- 本轮迭代设计： `$iteration_note_path`
- 本轮浓缩上下文： `$context_path`

## 产出要求

1. 按迭代设计的「开发计划」在当前 worktree 内实现代码变更，保证「用例清单」的每一条有对应测试覆盖。
2. 运行项目既有的 lint / typecheck / 单元测试命令（自行探测 `Makefile`、`pyproject.toml`、`package.json` 等）。
3. 如果第一轮 lint/typecheck/测试存在失败，**立即修复一次**并再跑一次；**最多修复一次**，之后失败必须如实记录。
4. 将自审结果写入 `$findings_output_path`，必须是合法 JSON：
   ```json
   {
     "pass": true,
     "findings": [
       {"kind": "lint|typecheck|unittest|diff", "message": "...", "path": "..."}
     ]
   }
   ```
   `pass=true` 表示 lint / typecheck / 测试全部通过；任何一项失败即 `pass=false` 并在 `findings` 里列出至少一条。

## 约束

- 不要删除或篡改 `$iteration_note_path`、`$context_path` 这两个文件。
- 不要直接 `git commit`；保留工作树的未提交变更，供 Step5 审核 diff。
- 不要写入 `.coop/` 目录之外的其它诊断文件。
