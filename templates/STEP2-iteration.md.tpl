# 任务：为 DevWork $dev_work_id 产出 Round $round 的迭代设计

## 设计文档（只读参考）

$design_doc_text

## 用户 prompt

$user_prompt

## 上一轮 Step5 反馈（issues）

$previous_feedback

## 产出要求

在 `$output_path` 现有文件末尾**追加**以下三个 H2 章节（保留文件已有的 front-matter 与 H1 标题）：

1. `## 本轮目标` —— 一段话阐明本轮要覆盖的设计范围与变更意图
2. `## 开发计划` —— 有序列表，每条是一个可落到单次 edit 的具体步骤
3. `## 用例清单` —— 表格 `| 用例 | 输入 | 预期 | 对应设计章节 |`，覆盖设计文档验收标准的每一条

不要修改 front-matter；不要产出其它章节；不要写代码实现（那是 Step4 的职责）。

完成后不要写入其它文件。
