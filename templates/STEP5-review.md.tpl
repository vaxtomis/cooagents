# 任务：DevWork Step5 审核打分

## 设计文档（只读）

$design_doc_text

## 打分 rubric（来自设计文档）

$rubric_section_text

## 本轮迭代设计（只读）

$iteration_note_text

## 本轮 diff（只读）

```
$diff_text
```

## Step4 自审 findings（只读）

```json
$step4_findings_json
```

## 打分要求

依据上面 rubric 对本轮 diff + 迭代设计打分（整数 0-100）。评分阈值：`$rubric_threshold`。

**必须**将结果写入 `$output_json_path`，内容为一个 ```json``` 围栏包裹的对象：

```json
{
  "score": 0,
  "issues": [
    {"dimension": "<rubric维度>", "severity": "<info|warn|error>", "message": "<具体问题>"}
  ],
  "problem_category": "req_gap"
}
```

字段规则（**严格遵守**）：

- `score`: 整数 0-100，必需。
- `issues`: 数组；`score >= $rubric_threshold` 时可以为空数组 `[]`。
- `problem_category`: 枚举，**仅可取** `"req_gap"`、`"impl_gap"`、`"design_hollow"` 之一，或 `null`。
  - `req_gap` ——「迭代设计/开发计划/用例清单」与设计文档或用户诉求有缺口（需要 Step2 重写）
  - `impl_gap` —— 计划合理但代码/测试与计划不匹配（需要 Step4 重做）
  - `design_hollow` —— 设计文档本身缺失关键可评估内容，无法做下去（需要新一轮 DesignWork）
  - `null` —— 仅当 `score >= $rubric_threshold` 时使用

不要写入其它文件。
