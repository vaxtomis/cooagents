# 任务：DevWork Step5 多仓审核打分

$step_wall

$retry_feedback

你是 reviewer，使用 Read / Bash 工具自行查阅以下文件并完成评分。

## 必读顺序

1. **设计文档**：`$design_doc_path` —— 抽取 `## 打分 rubric` 段
2. **本轮迭代设计**：`$iteration_note_path` —— Step2 写的「本轮目标 / 开发计划 / 用例清单」，其中「开发计划」应是带稳定 ID 的 checkbox checklist
3. **Step4 自审 findings**：`$step4_findings_path` —— Step4 写的本轮 lint/typecheck/unittest 结果与 `plan_execution`
4. **Step3 上下文与疑点**：`$context_path` —— 校验 Step4 是否处理了 Step3 raised 的疑点/风险

## 多仓改动表

$mount_table

## 必做的诊断（Bash 工具）

对**每个** mount（含 primary `$primary_worktree_path`），进入其 `worktree_path` 后跑 `cd <wt> && git diff HEAD`，并把每个 mount 的代码改动纳入评分；同时交叉验证 Step4 findings 中带该 `mount_name` 的条目与 git diff 一致。

## 打分聚合规则（多仓）

$aggregation_rule

$scoring_rule

$boundary_check

$plan_verification_guide

$plan_audit_targets

$next_round_hints_guide

## 输出要求

评分阈值：`$rubric_threshold`。

**必须**将结果写入 `$output_json_path`，内容为一个 ```json``` 围栏包裹的对象：

```json
{
  "score": 0,
  "score_breakdown": {"plan_score_a": 0, "actual_score_b": 0, "final_score": 0, "plan_coverage": 0.0, "execution_coverage": 0.0, "previous_actual_score_b": null},
  "issues": [
    {"mount": "<mount_name>", "dimension": "<rubric维度>", "severity": "<info|warn|error>", "message": "<具体问题>"},
    {"kind": "boundary_violation", "step": "step4", "severity": "<info|warn|error>", "message": "<越界事实>"}
  ],
  "plan_verification": [{"id": "DW-01", "status": "done", "importance": "P0", "required_for_exit": true, "implemented": true, "verified": true, "confidence": "high", "evidence": ["path/to/file.ts:10"], "missing_evidence": []}],
  "next_round_hints": [
    {"kind": "missing_feature", "mount": "<mount_name>", "severity": "info", "message": "<未实现的功能 + 可选代码引用>"},
    {"kind": "optimization", "mount": "<mount_name>", "severity": "info", "message": "<可优化位置 + 代码引用>"}
  ],
  "problem_category": "req_gap"
}
```

字段规则（**严格遵守**）：

- `score`：整数 0-100；多仓时为整体准出分（不是各仓加权平均），必须等于 `round(plan_score_a * actual_score_b / 100)`。
- `score_breakdown`：对象；解释 `score` 如何由 a/b 模型得到，必须包含 `plan_score_a`（计划若完美实现对设计文档的满足分 a）与 `actual_score_b`（当前实现相对开发计划的完成分 b）。
- `issues`：数组；常规 rubric 问题带 `mount` / `dimension`（`mount` 可选）；越界类问题带 `kind="boundary_violation"` 与 `step` 字段；`score >= $rubric_threshold` 且未发现越界时可以为空数组 `[]`。
- `plan_verification`：数组；核验 Step4 `plan_execution` 与 checklist、git diff、测试是否一致。每项必须带 `id/status/implemented/verified`，并尽量带 `importance`（`"P0"`/`"P1"`/`"P2"`）与 `required_for_exit`；`status` 仅可为 `"done"`、`"partial"`、`"deferred"`、`"blocked"`、`"failed"`、`"unverified"`。`implemented` 表示 Step4 是否已交付该计划项；`verified` 只表示 Step5 是否有充分 diff/测试/运行证据。已交付但证据不足时写 `{"status":"done","implemented":true,"verified":false}` 并补 `missing_evidence`；不要改迭代设计文件。
- `next_round_hints`：数组；面向**下一轮**的提示，每条带 `kind` 与 `message`；详见上方「下一轮提示」段；本轮无缺失功能且无优化项时为空数组 `[]`。**不要**与 `issues` 重复内容。
- `problem_category`：枚举，**仅可取** `"req_gap"`、`"impl_gap"`、`"design_hollow"` 之一，或 `null`。按上面「打分聚合规则」选取；`null` 仅当 `score >= $rubric_threshold` 时使用。

不要写入其它文件。


## 退出前检查

结束 Step5 前，必须重新读取 `$output_json_path` 并确认文件存在、非空、可被 JSON 解析，且顶层字段至少包含 `score`、`issues`、`problem_category`。只把 JSON 打印到 stdout 不算完成。
