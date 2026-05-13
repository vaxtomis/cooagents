# 任务：为 DevWork $dev_work_id 产出 Round $round 的迭代设计

$step_wall

## 必读路径

1. **设计文档**：`$design_doc_path` —— 用 Read 工具自行查阅；不要复述全文，只引用与本轮相关的章节
2. **上一轮迭代设计**：$previous_iteration_note_path —— Round 1 时此项为占位符；Round ≥ 2 必须 Read 此文件并继承其中 `## 开发计划`
3. **上一轮 Step5 反馈**：$previous_review_path —— Round 1 时此项为占位符；Round ≥ 2 必须 Read 此文件并以「上一轮 issues」为本轮规划的输入
4. **用户 prompt**：

   $user_prompt
$recommended_tech_stack_read_item

## 产出要求

在 `$output_path` 现有文件末尾**追加**以下 $h2_count 个 H2 章节（保留 front-matter 与 H1）：

1. `## 本轮目标` —— 一段话阐明本轮要覆盖的设计范围与变更意图
$recommended_tech_stack_output_requirement$development_plan_requirement_number. `## 开发计划` —— Markdown checkbox checklist，每条使用稳定任务 ID，格式为 `- [ ] DW-01: <可落到单次 edit 的具体步骤>`；ID 在本文件内唯一且递增。Round ≥ 2 时，所有历史 PLAN 都必须保留，旧 ID 不得重编号；本轮只能追加新的主 PLAN，编号从上一轮最大 ID 之后继续递增；也可在历史 PLAN 下追加缩进子 PLAN，例如 `  - [ ] DW-02.1: <子步骤>`。历史项不再执行时不要删除或改写，用删除线标记取消，例如 `- [ ] ~~DW-02: 已取消的旧计划~~`
$use_case_requirement_number. `## 用例清单` —— 表格 `| 用例 | 输入 | 预期 | 对应设计章节 |`，覆盖设计文档验收标准的每一条

完成后不要写入其它文件。
