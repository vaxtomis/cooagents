# 中断恢复

## 基本信息
- ticket: {{ ticket }}
- 恢复次数: {{ resume_count }}

## 中断原因
{{ interrupt_reason }}

## 已完成工作
{% if commits_made %}
### 提交记录
{{ commits_made }}
{% endif %}

{% if diff_stat %}
### 变更统计
{{ diff_stat }}
{% endif %}

## 原始任务
{{ original_task_content }}

## 你的目标
继续完成原始任务中未完成的部分。已有的提交记录和代码变更已保留。

## 约束
- 先检查当前代码状态再继续。
- 不要重复已完成的工作。
- 确保最终输出与原始任务要求一致。
