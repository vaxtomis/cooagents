# 开发修复指令（Turn {{ turn }}）

## 问题描述
> {{ feedback }}

## 你的目标
修复上述问题，确保测试通过并生成测试报告。

{% if test_failures %}
## 失败的测试
{% for failure in test_failures %}
- {{ failure }}
{% endfor %}
{% endif %}

## 约束
- 先复现问题再修复。
- 更新测试报告：`docs/dev/TEST-REPORT-{{ ticket }}.md`
- 确保所有测试通过。
