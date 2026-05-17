import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DesignDocStructuredViewer } from "./DesignDocStructuredViewer";

const DESIGN_DOC = `---
title: Checkout flow
goal: Let buyers complete payment
version: 2.0.0
rubric_threshold: 90
needs_frontend_mockup: true
---

# Checkout flow

## 问题与目标

- 问题: Buyers cannot complete payment.
- 证据: Support tickets mention abandoned checkout.
- 关键假设: Assumption - needs validation: Card payment is first.
- 成功信号: Paid orders are visible.

## 用户故事

As a buyer, I want to pay for my cart.

## 场景案例

### SC-01 Successful payment

- Actor: Buyer
- Trigger: Buyer submits a valid card
- Preconditions: Cart has items
- Expected Result: The order is paid.

## 范围与非目标

| 优先级 | 范围项 | 说明 |
|---|---|---|
| Must | Card checkout | Complete the primary payment path |
| Won't | Wallet checkout | Not included |

## 详细操作流程

1. Buyer opens checkout.
2. Buyer submits payment.

## 验收标准

- [ ] AC-01: Valid payment creates a paid order.
- [ ] AC-02: Declined payment shows a recoverable error.

## 技术约束与集成边界

- 依赖系统: Payment API.
- 建议验证入口: Payment integration tests.

## 交付切片

| PH ID | 能力 | 依赖 | 可并行性 | 完成信号 |
|---|---|---|---|---|
| PH-01 | Successful card payment | Payment API | - | AC-01 passes |

## 决策记录

| 决策 | 选择 | 备选 | 理由 |
|---|---|---|---|
| Payment method | Card first | Wallet | Lowest launch risk |

## 打分 rubric

| 维度 | 权重 | 判定标准 |
|---|---:|---|
| 完整性 | 20 | Required sections are present |
| 对齐度 | 30 | Scenarios map to acceptance criteria |
| 可实现性 | 30 | Boundaries are stable |
| 边界覆盖 | 20 | Failure paths are covered |
`;

describe("DesignDocStructuredViewer", () => {
  it("renders a structured DesignDoc v2 view by default", () => {
    render(<DesignDocStructuredViewer content={DESIGN_DOC} />);

    expect(screen.getByRole("tab", { name: "结构化" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "Checkout flow" })).toBeInTheDocument();
    expect(screen.getByText("Let buyers complete payment")).toBeInTheDocument();
    expect(screen.getByText("需要")).toBeInTheDocument();
    expect(screen.getByText("Assumption")).toBeInTheDocument();
    expect(screen.getByText("验收项")).toBeInTheDocument();
    expect(screen.getByText("AC-01")).toBeInTheDocument();
    expect(screen.getByText("PH-01")).toBeInTheDocument();
    expect(screen.getByText("Payment method")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();

    const nav = screen.getByRole("navigation", { name: "DesignDoc 章节" });
    expect(within(nav).getByRole("link", { name: /验收标准/ })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: /交付切片/ })).toBeInTheDocument();
  });

  it("keeps the raw Markdown view available", () => {
    render(<DesignDocStructuredViewer content={DESIGN_DOC} />);

    fireEvent.click(screen.getByRole("tab", { name: "Markdown" }));

    expect(screen.getByRole("tab", { name: "Markdown" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "问题与目标", level: 2 })).toBeInTheDocument();
    expect(screen.queryByText("验收项")).not.toBeInTheDocument();
  });

  it("shows non-blocking parse warnings for malformed v2 content", () => {
    render(
      <DesignDocStructuredViewer
        content={DESIGN_DOC
          .replace(/## 问题与目标[\s\S]*?(?=\n## 用户故事)/, "")
          .replace("- [ ] AC-01: Valid payment creates a paid order.", "- Valid payment creates a paid order.")
          .replace("- [ ] AC-02: Declined payment shows a recoverable error.", "")
          .replace("| PH-01 | Successful card payment |", "| P1 | Successful card payment |")}
      />,
    );

    expect(screen.getAllByText("解析告警").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("缺少章节: 问题与目标")).toBeInTheDocument();
    expect(screen.getByText("验收标准 未找到 AC-xx checklist 项")).toBeInTheDocument();
    expect(screen.getByText("交付切片 PH ID 必须匹配 PH-xx: P1")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Markdown" })).toBeInTheDocument();
  });
});
