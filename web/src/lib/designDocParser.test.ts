import { describe, expect, it } from "vitest";
import { parseDesignDocV2 } from "./designDocParser";

const VALID_DOC = `---
title: Checkout flow
goal: Let buyers complete payment
version: 2.0.0
rubric_threshold: 90
needs_frontend_mockup: true
---

# Checkout flow

## 问题与目标

- 问题: Buyers cannot complete payment from the cart.
- 证据: Support tickets mention abandoned checkout.
- 关键假设: Assumption - needs validation: Card payment is the first supported method.
- 成功信号: Paid orders are created and visible in order history.

## 用户故事

As a buyer, I want to pay for my cart so that I can receive my order.

## 场景案例

### SC-01 Successful payment

- Actor: Buyer
- Trigger: Buyer submits a valid card
- Preconditions: Cart has items
- Main Flow:
  1. Buyer reviews cart.
  2. Buyer pays.
- Expected Result: The order is paid.

### SC-02 Card decline

- Actor: Buyer
- Expected Result: The buyer sees a recoverable payment error.

## 范围与非目标

| 优先级 | 范围项 | 说明 |
|---|---|---|
| Must | Card checkout | Complete the primary payment path |
| Won't | Wallet checkout | Not included in this release |

## 详细操作流程

1. Buyer opens checkout.
2. Buyer submits payment.

## 验收标准

- [ ] AC-01: Valid payment creates a paid order.
- [x] AC-02: Declined payment shows a recoverable error.

## 技术约束与集成边界

- 依赖系统: Payment API, order service.
- 权限/数据/兼容约束: Do not expose card data.
- 不可破坏行为: Existing order history remains readable.
- 建议验证入口: Payment integration tests.

## 交付切片

| PH ID | 能力 | 依赖 | 可并行性 | 完成信号 |
|---|---|---|---|---|
| PH-01 | Successful card payment | Payment API | - | AC-01 passes |
| PH-02 | Decline recovery | PH-01 | with PH-03 | AC-02 passes |

## 决策记录

| 决策 | 选择 | 备选 | 理由 |
|---|---|---|---|
| Payment method | Card first | Wallet, bank transfer | Lowest launch risk |

## 打分 rubric

| 维度 | 权重 | 判定标准 |
|---|---:|---|
| 完整性 | 20 | Required sections are present |
| 对齐度 | 30 | Scenarios map to acceptance criteria |
| 可实现性 | 30 | Boundaries are stable |
| 边界覆盖 | 20 | Failure paths are covered |
`;

describe("parseDesignDocV2", () => {
  it("extracts front matter, sections, scenarios, AC, PH, decisions, and rubric", () => {
    const parsed = parseDesignDocV2(VALID_DOC);

    expect(parsed.title).toBe("Checkout flow");
    expect(parsed.frontMatter.goal).toBe("Let buyers complete payment");
    expect(parsed.frontMatter.needs_frontend_mockup).toBe("true");
    expect(parsed.problemSummary.problem).toBe("Buyers cannot complete payment from the cart.");
    expect(parsed.problemSummary.keyHypothesis).toContain("Assumption - needs validation");
    expect(parsed.scenarios).toHaveLength(2);
    expect(parsed.scenarios[0]).toMatchObject({
      id: "SC-01",
      title: "Successful payment",
      actor: "Buyer",
    });
    expect(parsed.acceptanceItems).toEqual([
      {
        id: "AC-01",
        text: "Valid payment creates a paid order.",
        checked: false,
        markers: [],
      },
      {
        id: "AC-02",
        text: "Declined payment shows a recoverable error.",
        checked: true,
        markers: [],
      },
    ]);
    expect(parsed.scopeRows[0]).toMatchObject({ priority: "Must", item: "Card checkout" });
    expect(parsed.deliverySlices[0]).toMatchObject({
      phId: "PH-01",
      capability: "Successful card payment",
      doneSignal: "AC-01 passes",
    });
    expect(parsed.decisionRows[0]).toMatchObject({ decision: "Payment method", choice: "Card first" });
    expect(parsed.rubricRows).toHaveLength(4);
    expect(parsed.rubricWeightTotal).toBe(100);
    expect(parsed.counts).toMatchObject({
      scenarios: 2,
      acceptance: 2,
      deliverySlices: 2,
      warnings: 0,
    });
    expect(parsed.markers.map((marker) => marker.kind)).toEqual(["assumption"]);
  });

  it("accepts optional page structure without requiring it", () => {
    const withoutPage = parseDesignDocV2(VALID_DOC);
    const withPage = parseDesignDocV2(
      `${VALID_DOC}\n## 页面结构\n\n- Checkout form\n- Payment status\n`,
    );

    expect(withoutPage.sections["页面结构"]).toBeUndefined();
    expect(withoutPage.warnings).not.toContain("缺少章节: 页面结构");
    expect(withPage.sections["页面结构"]?.body).toContain("Checkout form");
  });

  it("reports missing required sections and missing AC items", () => {
    const parsed = parseDesignDocV2(
      VALID_DOC
        .replace(/## 问题与目标[\s\S]*?(?=\n## 用户故事)/, "")
        .replace("- [ ] AC-01: Valid payment creates a paid order.", "- Valid payment creates a paid order.")
        .replace("- [x] AC-02: Declined payment shows a recoverable error.", ""),
    );

    expect(parsed.warnings).toContain("缺少章节: 问题与目标");
    expect(parsed.warnings).toContain("验收标准 未找到 AC-xx checklist 项");
  });

  it("reports invalid PH IDs and rubric weight problems", () => {
    const parsed = parseDesignDocV2(
      VALID_DOC
        .replace("| PH-01 | Successful card payment |", "| P1 | Successful card payment |")
        .replace("| 完整性 | 20 |", "| 完整性 | high |"),
    );

    expect(parsed.warnings).toContain("交付切片 PH ID 必须匹配 PH-xx: P1");
    expect(parsed.warnings).toContain("打分 rubric 权重必须是整数: high");
    expect(parsed.rubricWeightTotal).toBe(80);
  });

  it("marks TBD and assumption text wherever they appear", () => {
    const parsed = parseDesignDocV2(
      VALID_DOC.replace(
        "Valid payment creates a paid order.",
        "TBD - needs research: payment gateway callback behavior.",
      ),
    );

    expect(parsed.acceptanceItems[0].markers).toEqual(["tbd"]);
    expect(parsed.markers.map((marker) => marker.kind).sort()).toEqual(["assumption", "tbd"]);
  });
});
