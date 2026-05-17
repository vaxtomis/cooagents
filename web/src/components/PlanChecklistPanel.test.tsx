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

- [x] DW-01: [P0] 登录表单
- [ ] DW-02: [P1] 错误提示
  - [ ] DW-02.1: [P2] 补充空邮箱提示
- [ ] ~~DW-03: [P2] 已取消的验证码入口~~

## 验收映射

| AC ID | 场景/输入 | 预期 | 本轮 DW ID | 验证方式 |
|---|---|---|---|---|
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
      importance: "P2",
      checked: false,
      cancelled: false,
    });
    expect(plan?.items[2]).toMatchObject({
      id: "DW-03",
      label: "已取消的验证码入口",
      importance: "P2",
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
    expect(within(panel).getByText("P0 准出必需")).toBeInTheDocument();
    expect(within(panel).getAllByText("P2 可延期")).toHaveLength(2);
    expect(within(panel).getByText("补充空邮箱提示")).toBeInTheDocument();
    expect(within(panel).getByText("已取消的验证码入口")).toHaveClass("line-through");
  });

  it("overlays Step5 execution and verification state by plan id", () => {
    render(
      <PlanChecklistPanel
        content={content}
        planVerification={[
          {
            id: "DW-01",
            status: "done",
            implemented: true,
            verified: false,
            required_for_exit: true,
            missing_evidence: ["缺少登录失败用例"],
          },
          {
            id: "DW-02",
            status: "partial",
            implemented: false,
            verified: false,
            required_for_exit: true,
          },
          {
            id: "DW-02.1",
            status: "deferred",
            implemented: false,
            verified: false,
            required_for_exit: false,
          },
        ]}
      />,
    );

    const panel = screen.getByRole("region", { name: "开发计划结构化视图" });
    expect(within(panel).getByText("1 个准出阻断")).toBeInTheDocument();
    expect(within(panel).getByText("已交付 / 证据不足")).toBeInTheDocument();
    expect(within(panel).getByText("阻断准出")).toBeInTheDocument();
    expect(within(panel).getByText("已延期，不阻断")).toBeInTheDocument();
    expect(within(panel).getByText("缺少登录失败用例")).toBeInTheDocument();
  });

  it("does not render when the note has no parseable plan checklist", () => {
    const { container } = render(<PlanChecklistPanel content="## 开发计划\n\n普通段落" />);
    expect(container).toBeEmptyDOMElement();
  });
});
