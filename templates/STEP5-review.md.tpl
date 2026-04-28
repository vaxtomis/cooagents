# 任务：DevWork Step5 多仓审核打分

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

## 输出要求

评分阈值：`$rubric_threshold`。

**必须**将结果写入 `$output_json_path`，内容为一个 ```json``` 围栏包裹的对象：

```json
{
  "score": 0,
  "issues": [
    {"mount": "<mount_name>", "dimension": "<rubric维度>", "severity": "<info|warn|error>", "message": "<具体问题>"}
  ],
  "problem_category": "req_gap"
}
```

字段规则（**严格遵守**）：

- `score`：整数 0-100；多仓时为整体打分（不是各仓加权平均）。
- `issues`：数组；每条建议带 `mount` 字段以指明问题所在仓；`score >= $rubric_threshold` 时可以为空数组 `[]`。`mount` 字段为可选诊断辅助，遗漏不会导致解析失败。
- `problem_category`：枚举，**仅可取** `"req_gap"`、`"impl_gap"`、`"design_hollow"` 之一，或 `null`。按上面「打分聚合规则」选取；`null` 仅当 `score >= $rubric_threshold` 时使用。

不要写入其它文件。
