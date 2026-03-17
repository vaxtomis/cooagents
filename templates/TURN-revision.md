# 修订指令（Turn {{ turn }}）

## 评估反馈
> {{ feedback }}

## 你的目标
根据上述反馈修订本阶段产出，确保满足完成判定。

{% if missing_artifacts %}
## 缺失制品
请补充以下文件：
{% for artifact in missing_artifacts %}
- {{ artifact }}
{% endfor %}
{% endif %}

## 约束
- 回应所有反馈要点。
- 不要重复已有的正确内容。
- 保持输出格式与之前一致。
