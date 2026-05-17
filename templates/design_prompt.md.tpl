# 任务：为 Workspace `$workspace_slug` 产出设计文档 v$version

## 用户诉求

$user_input

$supplemental_materials

## 产出要求

输出一份 Markdown 设计文档，必须以 YAML front-matter 开头（`---` 包裹），包含以下字段：

- `title`: $title
- `goal`: 一句话描述设计目标
- `version`: $version
- `parent_version`: $parent_version_or_empty
- `rubric_threshold`: 默认 80，可根据重要程度上调（整数，1-100）
- `needs_frontend_mockup`: $needs_frontend_mockup

主体必须按顺序包含以下 H2 章节，缺一不可：

1. `## 问题与目标`
2. `## 用户故事`
3. `## 场景案例`
4. `## 范围与非目标`
5. `## 详细操作流程`
6. `## 验收标准`
7. `## 技术约束与集成边界`
8. `## 交付切片`
9. `## 决策记录`
10. `## 打分 rubric`

## 职责边界

DesignWork 的产物是“需求与验收契约”，供后续 DevWork 拆解执行计划。

- 必须描述问题、目标、用户、场景、范围、验收标准、稳定技术边界和能力级交付切片。
- 不得输出 `DW-xx`、不得输出 checkbox 开发任务、不得写文件级修改计划或单次 edit 步骤。
- 未知信息不要臆造；写 `TBD - needs research` 或 `Assumption - needs validation: <验证方式>`。
- `PH-xx` 是能力级交付切片，不是开发任务；DevWork Step2 会再把 `PH-xx + AC-xx` 拆成 `DW-xx`。

### `## 问题与目标` 格式要求

必须包含以下标签行：

- `问题:` 谁在什么场景下面临什么可观察问题
- `证据:` 用户输入、附件、代码/业务事实；缺证据时写 `Assumption - needs validation: ...`
- `关键假设:` 可被后续验证的假设
- `成功信号:` 可观察、可测试的成功状态

### `## 场景案例` 格式要求

- 至少包含一个子案例
- 每个子案例必须以 `### SC-xx <标题>` 开头
- 每个子案例必须包含以下字段：
  - `Actor:`
  - `Main Flow:`
  - `Expected Result:`
- 推荐补充：
  - `Trigger:`
  - `Preconditions:`

### `## 验收标准` 格式要求

- 使用 checklist
- 每条使用 `- [ ] AC-xx: ...`
- 每条必须可测试、可观察

### `## 范围与非目标` 格式要求

- 先写 MoSCoW 表格，列为：`优先级 | 范围项 | 说明`
- 再写 `非目标:` 列表，明确本版不做什么以及原因

### `## 技术约束与集成边界` 格式要求

只写稳定工程边界，必须包含以下标签行或列表：

- `依赖系统:`
- `权限/数据/兼容约束:`
- `不可破坏行为:`
- `建议验证入口:`

不要写具体文件级改动、不要写 `DW-xx`、不要写开发 checklist。

### `## 交付切片` 格式要求

- 使用 markdown table
- 至少包含列：`PH ID | 能力 | 依赖 | 可并行性 | 完成信号`
- `PH ID` 使用 `PH-xx`，例如 `PH-01`
- 每行描述能力级里程碑，不描述单次 edit 步骤

### `## 决策记录` 格式要求

- 使用 markdown table
- 至少包含列：`决策 | 选择 | 备选 | 理由`

### `## 打分 rubric` 格式要求

- 使用 markdown table
- 至少包含列：`维度 | 权重 | 判定标准`
- `权重` 使用整数
- 推荐总权重为 100

$mockup_instruction

## Repository inspection guidance

If this DesignWork requires inspecting a code repository, focus on business
source code and configuration that affects the requested behavior. Do not scan
or summarize generated/vendor/runtime folders such as `node_modules/`,
`devworks/`, `.git/`, `.coop/`, build outputs, caches, coverage reports, or
other paths excluded by the repository's `.gitignore`. Prefer targeted file
reads and bounded searches over broad recursive scans.

## 本轮补齐项（若非首轮）

$missing_sections_hint

## 产出路径

将最终 markdown 写入：`$output_path`

不要写入任何其他文件。完整 markdown 内容即为本次产出。
