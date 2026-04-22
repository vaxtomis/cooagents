# 任务：为 Workspace `$workspace_slug` 产出设计文档 v$version

## 用户诉求

$user_input

## 产出要求

输出一份 Markdown 设计文档，必须以 YAML front-matter 开头（`---` 包裹），包含以下字段：

- `title`: $title
- `goal`: 一句话描述设计目标
- `version`: $version
- `parent_version`: $parent_version_or_empty
- `rubric_threshold`: 默认 80，可根据重要程度上调（整数，1-100）
- `needs_frontend_mockup`: $needs_frontend_mockup

主体必须**按顺序**包含以下 H2 章节，缺一不可：

1. `## 用户故事`
2. `## 用户案例`
3. `## 详细操作流程`
4. `## 验收标准`
5. `## 打分 rubric` — 评分项表格，含"设计文档完整度"

$mockup_instruction

## 本轮补齐项（若非首轮）

$missing_sections_hint

## 产出路径

将最终 markdown 写入：`$output_path`

不要写入任何其他文件。完整 markdown 内容即为本次产出。
