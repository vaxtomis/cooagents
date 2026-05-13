import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlanChecklistPanel, extractPlanChecklist } from "./PlanChecklistPanel";

const content = `---
round: 2
---

# 迭代设计

## 本轮目标

继续登录闭环。

## 开发计划

- [x] DW-01: 登录表单
- [ ] DW-02: 错误提示
  - [ ] DW-02.1: 补充空邮箱提示
- [ ] ~~DW-03: 已取消的验证码入口~~

## 用例清单

| 用例 | 输入 | 预期 |
|---|---|---|
`;

describe("PlanChecklistPanel", () => {
  it("extracts top-level plans, subplans, checked state, and cancellations", () => {
    const plan = extractPlanChecklist(content);

    expect(plan).not.toBeNull();
    expect(plan?.total).toBe(4);
    expect(plan?.completed).toBe(1);
    expect(plan?.cancelled).toBe(1);
    expect(plan?.items).toHaveLength(3);
    expect(plan?.items[1].id).toBe("DW-02");
    expect(plan?.items[1].children).toHaveLength(1);
    expect(plan?.items[1].children[0]).toMatchObject({
      id: "DW-02.1",
      label: "补充空邮箱提示",
      checked: false,
      cancelled: false,
    });
    expect(plan?.items[2]).toMatchObject({
      id: "DW-03",
      label: "已取消的验证码入口",
      cancelled: true,
    });
  });

  it("renders a structured plan panel", () => {
    render(<PlanChecklistPanel content={content} />);

    const panel = screen.getByRole("region", { name: "开发计划结构化视图" });
    expect(within(panel).getByText("开发计划")).toBeInTheDocument();
    expect(within(panel).getByText("1/3 完成")).toBeInTheDocument();
    expect(within(panel).getByText("1 取消")).toBeInTheDocument();
    expect(within(panel).getByText("DW-02.1")).toBeInTheDocument();
    expect(within(panel).getByText("补充空邮箱提示")).toBeInTheDocument();
    expect(within(panel).getByText("已取消的验证码入口")).toHaveClass("line-through");
  });

  it("does not render when the note has no parseable plan checklist", () => {
    const { container } = render(<PlanChecklistPanel content="## 开发计划\n\n普通段落" />);
    expect(container).toBeEmptyDOMElement();
  });
});
