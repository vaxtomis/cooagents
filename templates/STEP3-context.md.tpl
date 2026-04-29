# 任务：为 DevWork Step3 产出浓缩上下文

$step_wall

## 必读路径

1. **代码工作树**：`$worktree_path`
2. **设计文档**：`$design_doc_path`
3. **本轮迭代设计**：`$iteration_note_path`

## 多仓改动表

$mount_table

## 产出要求

使用 file 工具读取上述设计文档与迭代设计，并在 worktree 内扫描与本轮「开发计划/用例清单」相关的源文件（import 链、相似命名、相邻测试等）。

在 `$output_path` 写入一个 markdown 文件，**只包含以下两个 H2 章节**：

1. `## 浓缩上下文` —— bullet list，列出与本轮相关的文件路径（worktree 相对路径）和摘要
2. `## 疑点与风险` —— bullet list，列出执行开发计划时可能的冲突、缺失依赖、或与现有代码的冲突
