# 任务：为 DevWork $dev_work_id 产出 Round $round 的迭代设计

$step_wall

## 必读路径

1. **设计文档**：`$design_doc_path` —— 用 Read 工具自行查阅
2. **上一轮迭代设计**：$previous_iteration_note_path —— Round 1 时此项为占位符；Round ≥ 2 必须 Read 此文件并继承其中 `## 开发计划`
3. **上一轮 Step5 反馈**：$previous_review_path —— Round 1 时此项为占位符；Round ≥ 2 必须 Read 此文件并以「上一轮 issues」为本轮规划的输入
$user_prompt_read_item
$recommended_tech_stack_read_item

## 代码工作树

默认只读探查 worktree：`$worktree_path`

## 多仓改动表

$mount_table

$workspace_file_refs

## 产出要求

在 `$output_path` 现有文件末尾**追加**以下 $h2_count 个 H2 章节（保留 front-matter 与 H1）：

1. `## 本轮目标` —— 一段话阐明本轮要覆盖的设计范围与变更意图
$recommended_tech_stack_output_requirement$context_discovery_requirement_number. `## 上下文发现` —— 先只读探查 worktree、仓库文档、接口/类型、配置、相似实现、相邻测试和验证命令候选；用 bullet list 记录实现前必须知道的证据，尽量附 `path/to/file.py:123-145`，不得粘贴大段源码。至少覆盖：相关入口/数据流、接口或类型约束、可镜像的既有模式、测试/验证入口、可能影响本轮实现的配置或依赖。
$development_plan_requirement_number. `## 开发计划` —— Markdown checkbox checklist，每条使用稳定任务 ID，格式为 `- [ ] DW-01: [P0|P1|P2] <可落到单次 edit 的具体步骤>`；ID 在本文件内唯一且递增。可在每个 DW 项下追加缩进子弹说明 `ACTION / MIRROR / VALIDATE / GOTCHA`，但不要破坏主行 checkbox 与 ID 格式。P0=准出必需（核心验收/安全/数据/授权/阻断流程），P1=常规交付，P2=可延期优化或非关键补充。Round 1 优先按设计文档与上下文发现拆粗粒度主 PLAN，只写顶层 DW-xx，覆盖需求/流程/验收面，默认不展开大量子 PLAN。Round ≥ 2 必须保留所有历史 PLAN，旧 ID 不得重编号；新增前对照上轮计划/Step5 反馈/本轮目标，确认是**必要、未重复、有交付价值**的缺口：相同/近似目标不要新增主 PLAN，优先沿用原 ID；细化只在既有 PLAN 下追加不重复的细粒度子 PLAN，例如 `  - [ ] DW-02.1: [P1] <子步骤>`；仅历史 PLAN 未覆盖的独立需求可追加遗漏主 PLAN。不得用不同措辞重复同一验收点、修复动作或测试补充。若上轮反馈含 `PLAN 扩展限制` 或 `plan_score_a >= 90`，视为已高度贴合设计文档，本轮**谨慎新增和细化计划**：不得新增主 PLAN，只能追加缩进子 PLAN（需确有必要）或补验证/验收映射细节。若含 `plan_score_a <= 70`，视为不太贴合，本轮**鼓励新增和细化计划**：主动补齐遗漏主 PLAN，并细化 P0/P1 主流程、验收、边界、测试。历史项不再执行时不要删除或改写，用删除线标记取消，例如 `- [ ] ~~DW-02: [P2] 已取消的旧计划~~`
$acceptance_mapping_requirement_number. `## 验收映射` —— 表格 `| AC ID | 场景/输入 | 预期 | 本轮 DW ID | 验证方式 |`，只映射本轮计划如何覆盖 DesignDoc 的 `AC-xx` 验收标准，不复述用户故事。每个 active `DW-xx` 至少映射一个 `AC-xx`；非验收型技术任务需在 `AC ID` 写 `N/A` 并说明原因。

完成后不要写入其它文件。
