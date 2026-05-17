---
title: $title
goal: $goal
version: $version
parent_version: $parent_version
rubric_threshold: $rubric_threshold
needs_frontend_mockup: $needs_frontend_mockup
created_at: $created_at
---

# $title

## 问题与目标

- 问题: $problem_statement
- 证据: $evidence
- 关键假设: $key_hypothesis
- 成功信号: $success_signal

## 用户故事

$user_story

## 场景案例

### SC-01 主场景

- Actor: $scenario_actor
- Trigger: $scenario_trigger
- Preconditions: $scenario_preconditions
- Main Flow:
  1. $scenario_step_1
  2. $scenario_step_2
- Expected Result: $scenario_expected_result

## 范围与非目标

| 优先级 | 范围项 | 说明 |
|---|---|---|
| Must | $must_scope | $must_scope_reason |
| Won't | $wont_scope | $wont_scope_reason |

非目标:
- $non_goal

## 详细操作流程

$operation_flow

## 验收标准

- [ ] AC-01: $acceptance_criterion_1
- [ ] AC-02: $acceptance_criterion_2

## 技术约束与集成边界

- 依赖系统: $dependent_systems
- 权限/数据/兼容约束: $permission_data_compat_constraints
- 不可破坏行为: $non_breaking_behaviors
- 建议验证入口: $verification_entrypoints

## 交付切片

| PH ID | 能力 | 依赖 | 可并行性 | 完成信号 |
|---|---|---|---|---|
| PH-01 | $phase_capability | $phase_dependency | $phase_parallelism | $phase_done_signal |

## 决策记录

| 决策 | 选择 | 备选 | 理由 |
|---|---|---|---|
| $decision | $choice | $alternatives | $rationale |

## 打分 rubric

| 维度 | 权重 | 判定标准 |
|---|---:|---|
| 完整性 | 20 | $rubric_completeness |
| 对齐度 | 30 | $rubric_alignment |
| 可实现性 | 30 | $rubric_implementability |
| 边界覆盖 | 20 | $rubric_edge_coverage |

$mockup_section
