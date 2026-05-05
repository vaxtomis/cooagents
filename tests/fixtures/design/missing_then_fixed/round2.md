---
title: Demo2
goal: Ship the full design after a correction
version: 1.0.0
parent_version:
rubric_threshold: 85
needs_frontend_mockup: false
---

# Demo2

## 用户故事

作为用户，我希望完成基础登录。

## 场景案例

### SC-01 基础登录

- Actor: User
- Main Flow:
  1. 用户打开登录页。
  2. 用户输入账号密码并提交。
  3. 系统完成认证并返回结果。
- Expected Result: 用户成功进入首页。

## 详细操作流程

1. 打开登录页。
2. 输入账号密码。
3. 提交认证请求。
4. 返回认证结果。

## 验收标准

- [ ] AC-01: 当账号密码正确时，应跳转首页。
- [ ] AC-02: 当账号密码错误时，应展示可观察的错误提示。

## 打分 rubric

| 维度 | 权重 | 判定标准 |
|---|---:|---|
| 完整性 | 20 | 章节齐全且 front-matter 完整。 |
| 对齐度 | 30 | 用户故事、场景案例和验收标准一致。 |
| 可实现性 | 30 | 实现路径明确，无歧义步骤。 |
| 边界覆盖 | 20 | 覆盖失败提示等关键边界。 |
