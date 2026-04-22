---
title: Demo
goal: Ship a minimal login flow
version: 1.0.0
parent_version:
rubric_threshold: 85
needs_frontend_mockup: false
---

# Demo

## 用户故事

作为用户，我希望通过邮箱登录。

## 用户案例

- 新用户首次登录
- 老用户切换设备登录

## 详细操作流程

1. 输入邮箱与密码
2. 校验通过后跳转首页

## 验收标准

- 邮箱格式错误报错
- 密码错误 5 次锁账户

## 打分 rubric

| 评分项 | 权重 | 说明 |
|---|---|---|
| 设计文档完整度 | 20 | 章节全 |
| 用户故事映射 | 30 | 一一对应 |
| 流程可实现性 | 30 | 可直接指导开发 |
| 边界异常 | 20 | 覆盖主要异常 |
