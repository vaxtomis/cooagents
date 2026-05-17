import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { DesignWork, WorkspaceEventsEnvelope } from "../types";
import { DesignWorkPage } from "./DesignWorkPage";

vi.mock("../api/designWorks", () => ({
  getDesignWork: vi.fn(),
  getDesignWorkRetrySource: vi.fn(),
  retryDesignWork: vi.fn(),
  cancelDesignWork: vi.fn(),
  rerunDesignWork: vi.fn(),
  deleteDesignWork: vi.fn(),
}));
vi.mock("../api/designDocs", () => ({
  getDesignDocContent: vi.fn(),
}));
vi.mock("../api/reviews", () => ({
  listReviews: vi.fn(),
}));
vi.mock("../api/workspaceEvents", () => ({
  listWorkspaceEvents: vi.fn(),
}));
vi.mock("../api/workspaces", () => ({
  listWorkspaceFiles: vi.fn(),
  uploadWorkspaceFile: vi.fn(),
}));
vi.mock("../api/repos", () => ({
  listRepos: vi.fn(),
  repoBranches: vi.fn(),
}));

import {
  cancelDesignWork,
  deleteDesignWork,
  getDesignWork,
  getDesignWorkRetrySource,
  rerunDesignWork,
  retryDesignWork,
} from "../api/designWorks";
import { getDesignDocContent } from "../api/designDocs";
import { listRepos } from "../api/repos";
import { listReviews } from "../api/reviews";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { listWorkspaceFiles } from "../api/workspaces";

afterEach(() => {
  vi.clearAllMocks();
});

beforeEach(() => {
  vi.mocked(listWorkspaceFiles).mockResolvedValue({
    workspace_id: "ws-1",
    slug: "ws",
    status: "active",
    files: [],
    pagination: { limit: 50, offset: 0, total: 0, has_more: false },
  });
});

function renderPage() {
  function LocationProbe() {
    const location = useLocation();
    return <div data-testid="location-probe">{location.pathname}</div>;
  }

  return render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <MemoryRouter initialEntries={["/workspaces/ws-1/design-works/dw-1"]}>
        <LocationProbe />
        <Routes>
          <Route path="/workspaces/:wsId/design-works/:dwId" element={<DesignWorkPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  );
}

const baseDesignWork: DesignWork = {
  id: "dw-1",
  workspace_id: "ws-1",
  mode: "new",
  current_state: "LLM_GENERATE",
  loop: 2,
  max_loops: 4,
  missing_sections: ["architecture", "data-flow"],
  output_design_doc_id: null,
  escalated_at: null,
  escalation_reason: null,
  title: "Feature",
  sub_slug: "feature",
  version: null,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
  is_running: false,
  repo_refs: [],
  workspace_file_refs: [],
  attachment_paths: [],
};

const eventsEnvelope: WorkspaceEventsEnvelope = {
  events: [],
  pagination: { limit: 8, offset: 0, total: 0, has_more: false },
};

const designDocV2 = `---
title: Checkout flow
goal: Let buyers complete payment
version: 2.0.0
rubric_threshold: 90
needs_frontend_mockup: false
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
- Expected Result: The order is paid.

## 范围与非目标

| 优先级 | 范围项 | 说明 |
|---|---|---|
| Must | Card checkout | Complete the primary payment path |

## 详细操作流程

1. Buyer submits payment.

## 验收标准

- [ ] AC-01: Valid payment creates a paid order.

## 技术约束与集成边界

- 依赖系统: Payment API.

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
| 完整性 | 40 | Required sections are present |
| 对齐度 | 60 | Acceptance maps to delivery |
`;

describe("DesignWorkPage", () => {
  it("renders escalated banner and missing_sections chips when state=ESCALATED", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      current_state: "ESCALATED",
      escalation_reason: "post-validate failed",
      max_loops: 2,
    });
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    renderPage();

    expect(await screen.findByText(/DesignWork 已升级/)).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "DesignWork 实际执行轮次 3/3，已达最大次数" }),
    ).toBeInTheDocument();
    expect(screen.getByText("后校验")).toBeInTheDocument();
    expect(screen.getByText("architecture")).toBeInTheDocument();
    expect(screen.getByText(/post-validate failed/)).toBeInTheDocument();
    expect(screen.getByText("data-flow")).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "推进" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry as new DesignWork" })).toBeEnabled();
  });

  it("shows completed DesignWork actual executed rounds", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      current_state: "COMPLETED",
      loop: 1,
      max_loops: 4,
      output_design_doc_id: "doc-1",
    });
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(getDesignDocContent).mockResolvedValue("# Design doc");

    renderPage();

    expect(
      await screen.findByRole("img", { name: "DesignWork 实际执行轮次 2/4，已记录" }),
    ).toBeInTheDocument();
  });

  it("renders DesignDoc delivery as structured view with Markdown fallback", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      current_state: "COMPLETED",
      output_design_doc_id: "doc-1",
    });
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(getDesignDocContent).mockResolvedValue(designDocV2);

    const view = renderPage();
    let deliveryTab: HTMLButtonElement | null = null;
    await waitFor(() => {
      deliveryTab = view.container.querySelector<HTMLButtonElement>("#designwork-tab-delivery");
      expect(deliveryTab).not.toBeNull();
    });
    fireEvent.click(deliveryTab!);

    expect(await screen.findByRole("heading", { name: "Checkout flow" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "结构化" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("AC-01")).toBeInTheDocument();
    expect(screen.getByText("PH-01")).toBeInTheDocument();
    expect(screen.getByText("Payment method")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Markdown" }));

    expect(screen.getByRole("heading", { name: "问题与目标", level: 2 })).toBeInTheDocument();
    expect(screen.queryByText("验收项")).not.toBeInTheDocument();
  });

  it("requires confirmation before cancelling a DesignWork", async () => {
    vi.mocked(getDesignWork).mockResolvedValue(baseDesignWork);
    vi.mocked(cancelDesignWork).mockResolvedValue(undefined);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "取消" }));
    expect(cancelDesignWork).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("确认取消 DesignWork")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    await waitFor(() => {
      expect(cancelDesignWork).toHaveBeenCalledWith("dw-1");
    });
  });

  it("reruns a cancelled DesignWork", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      current_state: "CANCELLED",
    });
    vi.mocked(rerunDesignWork).mockResolvedValue({
      ...baseDesignWork,
      current_state: "PRE_VALIDATE",
      is_running: true,
    });
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "重新执行" }));

    await waitFor(() => {
      expect(rerunDesignWork).toHaveBeenCalledWith("dw-1");
    });
  });

  it("confirms before deleting an escalated DesignWork", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      current_state: "ESCALATED",
      escalation_reason: "post-validate failed",
    });
    vi.mocked(deleteDesignWork).mockResolvedValue(undefined);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "删除" }));
    expect(deleteDesignWork).not.toHaveBeenCalled();
    expect(screen.getByText("删除并清理 DesignWork")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      expect(deleteDesignWork).toHaveBeenCalledWith("dw-1");
    });
    expect(screen.getByTestId("location-probe")).toHaveTextContent(
      "/workspaces/ws-1",
    );
  });

  it("opens editable retry form before creating the new row", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      current_state: "ESCALATED",
      escalation_reason: "post-validate failed",
      max_loops: 2,
    });
    vi.mocked(getDesignWorkRetrySource).mockResolvedValue({
      title: "Feature",
      slug: "feature",
      user_input: "old requirement text",
      needs_frontend_mockup: false,
      agent: "claude",
      repo_refs: [],
      workspace_file_refs: [],
      attachment_paths: [],
    });
    vi.mocked(listRepos).mockResolvedValue([]);
    vi.mocked(retryDesignWork).mockResolvedValue({ ...baseDesignWork, id: "dw-2" });
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Retry as new DesignWork" }));

    await waitFor(() => expect(getDesignWorkRetrySource).toHaveBeenCalledWith("dw-1"));
    expect(retryDesignWork).not.toHaveBeenCalled();

    fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "Feature retry" } });
    fireEvent.change(screen.getByLabelText("Requirement"), { target: { value: "new requirement text" } });
    fireEvent.change(screen.getByLabelText("Execution Agent"), { target: { value: "codex" } });
    fireEvent.click(screen.getByRole("button", { name: "Create retry" }));

    await waitFor(() => {
      expect(retryDesignWork).toHaveBeenCalledWith(
        "dw-1",
        expect.objectContaining({
          title: "Feature retry",
          slug: "feature",
          user_input: "new requirement text",
          agent: "codex",
          repo_refs: [],
        }),
      );
    });
    expect(screen.getByTestId("location-probe")).toHaveTextContent(
      "/workspaces/ws-1/design-works/dw-2",
    );
  });

  it("lets retry remove inherited attachment paths", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      current_state: "ESCALATED",
      escalation_reason: "post-validate failed",
      max_loops: 2,
    });
    vi.mocked(getDesignWorkRetrySource).mockResolvedValue({
      title: "Feature",
      slug: "feature",
      user_input: "old requirement text",
      needs_frontend_mockup: false,
      agent: "claude",
      repo_refs: [],
      workspace_file_refs: ["attachments/brief.md"],
      attachment_paths: ["attachments/brief.md"],
    });
    vi.mocked(listRepos).mockResolvedValue([]);
    vi.mocked(retryDesignWork).mockResolvedValue({ ...baseDesignWork, id: "dw-2" });
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Retry as new DesignWork" }));
    expect(await screen.findByText("attachments/brief.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove attachments/brief.md" }));
    fireEvent.click(screen.getByRole("button", { name: "Create retry" }));

    await waitFor(() => {
      expect(retryDesignWork).toHaveBeenCalledWith(
        "dw-1",
        expect.objectContaining({
          workspace_file_refs: [],
        }),
      );
    });
  });

  it("renders the reconcile hint when design-doc content returns 410", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      output_design_doc_id: "doc-1",
    });
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(getDesignDocContent).mockRejectedValue(
      new ApiError(410, "file missing", null),
    );

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "最终交付" }));

    await waitFor(() => {
      expect(screen.getByText(/源文件已缺失/)).toBeInTheDocument();
    });
  });

  it("renders running banner and shows scoped activity", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      is_running: true,
    });
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue({
      events: [
        {
          id: 1,
          event_id: "evt-1",
          event_name: "design_work.started",
          workspace_id: "ws-1",
          correlation_id: "dw-1",
          payload: { title: "Feature", mode: "new" },
          ts: "2026-04-23T00:00:01Z",
        },
      ],
      pagination: { limit: 8, offset: 0, total: 1, has_more: false },
    });

    renderPage();

    expect(await screen.findByText("自动推进中")).toBeInTheDocument();
    expect(screen.getByText("后台驱动正在推进此 DesignWork，页面会自动刷新最新状态。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "推进" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "活动" }));
    expect(await screen.findByText("design_work.started")).toBeInTheDocument();
    expect(screen.getByTestId("designwork-activity-feed")).toBeInTheDocument();
    expect(listWorkspaceEvents).toHaveBeenCalledWith(
      "ws-1",
      expect.objectContaining({
        correlation_id: "dw-1",
        event_name: expect.arrayContaining([
          "design_work.started",
          "design_work.completed",
        ]),
      }),
    );
  });

  it("supports keyboard navigation across detail tabs", async () => {
    vi.mocked(getDesignWork).mockResolvedValue(baseDesignWork);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    const overviewTab = await screen.findByRole("tab", { name: "总览" });
    overviewTab.focus();
    fireEvent.keyDown(overviewTab, { key: "ArrowRight" });

    const deliveryTab = screen.getByRole("tab", { name: "最终交付" });
    await waitFor(() => {
      expect(deliveryTab).toHaveAttribute("aria-selected", "true");
      expect(document.activeElement).toBe(deliveryTab);
    });
  });
});
