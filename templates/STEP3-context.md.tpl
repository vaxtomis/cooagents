# 任务：为 DevWork Step3 产出浓缩上下文

$step_wall

## 必读路径

1. **代码工作树**：`$worktree_path`
2. **设计文档**：`$design_doc_path`
3. **本轮迭代设计**：`$iteration_note_path`

## 多仓改动表

$mount_table

## 产出要求

使用 file 工具读取上述设计文档与迭代设计，复核 Step2 `## 上下文发现`，并在 worktree 内扫描与本轮「开发计划/用例清单」相关的源文件（import 链、相似命名、相邻测试等）。

在 `$output_path` 写入一个 markdown 文件，**只包含以下三个 H2 章节**：

1. `## 浓缩上下文` —— bullet list，列出与本轮相关的文件路径（worktree 相对路径）和摘要
2. `## 模式镜像` —— bullet list，提炼本轮必须镜像的命名、错误处理、接口/类型、配置、测试或目录结构模式；每条尽量带来源 `path:line`
3. `## 执行地图` —— markdown table `| DW ID | 目标文件 | 动作 | 模式来源 | 验证命令 |`，列出预期修改文件、动作、原因、关联 DW ID 和建议验证命令；只做定位和映射，不重新规划

不要输出独立 `## 疑点与风险` 章节；必要注意事项并入 `## 执行地图` 的具体行。
