# 任务：DevWork Step5 多仓审核打分

$step_wall

你是 reviewer，使用本机已有的 Read / Bash 工具自行查阅以下文件并完成评分。

## 必读顺序（请按顺序读完）

1. **设计文档**：`$design_doc_path`
   —— 用于判断设计完整性 & 抽取 `## 打分 rubric` 段
2. **本轮迭代设计**：`$iteration_note_path`
   —— Step2 LLM 写的「本轮目标 / 开发计划 / 用例清单」
3. **Step4 自审 findings**：`$step4_findings_path`
   —— Step4 LLM 写的本轮 lint / typecheck / unittest 自审
4. **Step3 上下文与疑点**：`$context_path`
   —— 校验 Step4 是否处理了 Step3 raised 的疑点/风险

## 多仓改动表

$mount_table

$btrack_limitation

## 必做的诊断（Bash 工具）

- 进 primary worktree 看本轮代码改动：
  ```bash
  cd $primary_worktree_path && git diff HEAD
  ```
- **不要**对非 primary mount 跑 git 命令（worktree 不存在）；
  那些仓只读 Step4 findings 中带该 `mount_name` 的条目。

## 打分聚合规则（多仓）

$aggregation_rule

$boundary_check

$next_round_hints_guide

## 输出要求

评分阈值：`$rubric_threshold`。

**必须**将结果写入 `$output_json_path`，内容为一个 ```json``` 围栏包裹的对象：

```json
{
  "score": 0,
  "issues": [
    {"mount": "<mount_name>", "dimension": "<rubric维度>", "severity": "<info|warn|error>", "message": "<具体问题>"},
    {"kind": "boundary_violation", "step": "step4", "severity": "<info|warn|error>", "message": "<越界事实>"}
  ],
  "next_round_hints": [
    {"kind": "missing_feature", "mount": "<mount_name>", "severity": "info", "message": "<未实现的功能 + 可选代码引用>"},
    {"kind": "optimization", "mount": "<mount_name>", "severity": "info", "message": "<可优化位置 + 代码引用>"}
  ],
  "problem_category": "req_gap"
}
```

字段规则（**严格遵守**）：

- `score`：整数 0-100；多仓时为整体打分（不是各仓加权平均）。
- `issues`：数组；常规 rubric 问题带 `mount` / `dimension` 字段（`mount` 可选）；越界类问题带 `kind="boundary_violation"` 与 `step` 字段；`score >= $rubric_threshold` 且未发现越界时可以为空数组 `[]`。
- `next_round_hints`：数组；面向**下一轮**的提示，每条带 `kind` 与 `message`；详见上方「下一轮提示」段；本轮无缺失功能且无优化项时为空数组 `[]`。**不要**与 `issues` 重复内容。
- `problem_category`：枚举，**仅可取** `"req_gap"`、`"impl_gap"`、`"design_hollow"` 之一，或 `null`。按上面「打分聚合规则」选取；`null` 仅当 `score >= $rubric_threshold` 时使用。

不要写入其它文件。
