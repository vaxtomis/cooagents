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

1. `## 用户故事`
2. `## 场景案例`
3. `## 详细操作流程`
4. `## 验收标准`
5. `## 打分 rubric`

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
