import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { DesignWork, WorkspaceEventsEnvelope } from "../types";
import { DesignWorkPage } from "./DesignWorkPage";

vi.mock("../api/designWorks", () => ({
  getDesignWork: vi.fn(),
  getDesignWorkRetrySource: vi.fn(),
  retryDesignWork: vi.fn(),
  cancelDesignWork: vi.fn(),
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
vi.mock("../api/repos", () => ({
  listRepos: vi.fn(),
  repoBranches: vi.fn(),
}));

import { getDesignWork, getDesignWorkRetrySource, retryDesignWork } from "../api/designWorks";
import { getDesignDocContent } from "../api/designDocs";
import { listRepos } from "../api/repos";
import { listReviews } from "../api/reviews";
import { listWorkspaceEvents } from "../api/workspaceEvents";

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  function LocationProbe() {
    const location = useLocation();
    return <div data-testid="location-probe">{location.pathname}</div>;
  }

  render(
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
};

const eventsEnvelope: WorkspaceEventsEnvelope = {
  events: [],
  pagination: { limit: 8, offset: 0, total: 0, has_more: false },
};

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
