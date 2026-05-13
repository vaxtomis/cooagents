import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type {
  DevIterationNote,
  DevWork,
  GateInfo,
  Review,
  WorkerRepoHandoff,
  WorkspaceEventsEnvelope,
} from "../types";
import { DevWorkPage } from "./DevWorkPage";

vi.mock("../api/devWorks", () => ({
  getDevWork: vi.fn(),
  cancelDevWork: vi.fn(),
  continueDevWork: vi.fn(),
  resumeDevWorkStep: vi.fn(),
  pushDevWorkBranches: vi.fn(),
}));
vi.mock("../api/devIterationNotes", () => ({
  listIterationNotes: vi.fn(),
  getIterationNoteContent: vi.fn(),
}));
vi.mock("../api/reviews", () => ({
  listReviews: vi.fn(),
}));
vi.mock("../api/gates", () => ({
  getGate: vi.fn(),
}));
vi.mock("../api/workspaceEvents", () => ({
  listWorkspaceEvents: vi.fn(),
}));

import {
  cancelDevWork,
  continueDevWork,
  getDevWork,
  pushDevWorkBranches,
  resumeDevWorkStep,
} from "../api/devWorks";
import { getIterationNoteContent, listIterationNotes } from "../api/devIterationNotes";
import { listReviews } from "../api/reviews";
import { getGate } from "../api/gates";
import { listWorkspaceEvents } from "../api/workspaceEvents";

beforeEach(() => {
  vi.mocked(listWorkspaceEvents).mockResolvedValue(emptyEvents);
  vi.mocked(getIterationNoteContent).mockResolvedValue("");
});

afterEach(() => {
  vi.clearAllMocks();
});

const devWork: DevWork = {
  id: "dv-1",
  workspace_id: "ws-1",
  design_doc_id: "doc-1",
  recommended_tech_stack: null,
  current_step: "STEP5_REVIEW",
  iteration_rounds: 2,
  max_rounds: 5,
  first_pass_success: null,
  last_score: 85,
  last_problem_category: null,
  escalated_at: null,
  completed_at: null,
  worktree_path: "/tmp/worktree",
  worktree_branch: "feat/dv-1",
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
  is_running: false,
  continue_available: false,
  resume_available: false,
  resume_step: null,
  progress: null,
  repo_refs: [],
  repos: [],
};

const waitingGate: GateInfo = {
  gate_id: "dev:dv-1:exit",
  status: "waiting",
  gate_key: "exit",
};

const emptyEvents: WorkspaceEventsEnvelope = {
  events: [],
  pagination: { limit: 20, offset: 0, total: 0, has_more: false },
};

function renderPage() {
  return render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <MemoryRouter initialEntries={["/workspaces/ws-1/dev-works/dv-1"]}>
        <Routes>
          <Route path="/workspaces/:wsId/dev-works/:dvId" element={<DevWorkPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  );
}

describe("DevWorkPage", () => {
  it("renders approve and reject buttons when exit gate is waiting", async () => {
    vi.mocked(getDevWork).mockResolvedValue(devWork);
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockResolvedValue(waitingGate);

    renderPage();

    await waitFor(() => expect(getDevWork).toHaveBeenCalled());
    expect(
      await screen.findByRole("img", { name: /DevWork 实际执行轮次 3\/5/ }),
    ).toBeInTheDocument();

    // Switch to the gate tab.
    fireEvent.click(screen.getByRole("tab", { name: "闸门" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "批准" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "驳回" })).toBeEnabled();
    });
  });

  it("shows the actual executed round count from iteration notes after completion", async () => {
    const notes: DevIterationNote[] = [
      {
        id: "note-1",
        dev_work_id: "dv-1",
        round: 1,
        markdown_path: "devworks/dv-1/iteration-round-1.md",
        score_history: [74],
        created_at: "2026-04-23T00:00:01Z",
      },
      {
        id: "note-2",
        dev_work_id: "dv-1",
        round: 2,
        markdown_path: "devworks/dv-1/iteration-round-2.md",
        score_history: [90],
        created_at: "2026-04-23T00:00:02Z",
      },
    ];
    vi.mocked(getDevWork).mockResolvedValue({
      ...devWork,
      current_step: "COMPLETED",
      iteration_rounds: 1,
      first_pass_success: false,
      last_score: 90,
      completed_at: "2026-04-23T00:00:03Z",
    });
    vi.mocked(listIterationNotes).mockResolvedValue(notes);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    expect(
      await screen.findByRole("img", { name: /DevWork 实际执行轮次 2\/5/ }),
    ).toBeInTheDocument();
  });

  it("renders iteration plans as a structured checklist", async () => {
    vi.mocked(getDevWork).mockResolvedValue(devWork);
    vi.mocked(listIterationNotes).mockResolvedValue([
      {
        id: "note-1",
        dev_work_id: "dv-1",
        round: 1,
        markdown_path: "devworks/dv-1/iteration-round-1.md",
        score_history: [85],
        created_at: "2026-04-23T00:00:01Z",
      },
    ]);
    vi.mocked(getIterationNoteContent).mockResolvedValue(
      [
        "# 迭代设计",
        "",
        "## 开发计划",
        "",
        "- [x] DW-01: 登录表单",
        "- [ ] DW-02: 错误提示",
        "  - [ ] DW-02.1: 空邮箱提示",
        "- [ ] ~~DW-03: 已取消入口~~",
        "",
        "## 用例清单",
      ].join("\n"),
    );
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "迭代设计文件" }));

    const panel = await screen.findByRole("region", { name: "开发计划结构化视图" });
    expect(within(panel).getByText("1/3 完成")).toBeInTheDocument();
    expect(within(panel).getByText("DW-02.1")).toBeInTheDocument();
    expect(within(panel).getByText("空邮箱提示")).toBeInTheDocument();
    expect(within(panel).getByText("已取消入口")).toHaveClass("line-through");
  });

  it("requires confirmation before cancelling a DevWork", async () => {
    vi.mocked(getDevWork).mockResolvedValue(devWork);
    vi.mocked(cancelDevWork).mockResolvedValue(undefined);
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "取消" }));
    expect(cancelDevWork).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("确认取消 DevWork")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    await waitFor(() => {
      expect(cancelDevWork).toHaveBeenCalledWith("dv-1");
    });
  });

  it("renders the per-mount push status grid with err tooltip", async () => {
    const repos: WorkerRepoHandoff[] = [
      {
        repo_id: "repo-aaa111",
        mount_name: "frontend",
        base_branch: "main",
        base_rev: "a1b2c3d4e5f6",
        devwork_branch: "devwork/ws-1/dv-1/frontend",
        push_state: "pushed",
        is_primary: true,
        worktree_path: "/tmp/frontend",
        url: "git@github.com:org/frontend.git",
        ssh_key_path: null,
        push_err: null,
      },
      {
        repo_id: "repo-bbb222",
        mount_name: "backend",
        base_branch: "main",
        base_rev: null,
        devwork_branch: "devwork/ws-1/dv-1/backend",
        push_state: "failed",
        is_primary: false,
        worktree_path: "/tmp/backend",
        url: "git@github.com:org/backend.git",
        ssh_key_path: null,
        push_err: "remote rejected: protected branch",
      },
    ];
    vi.mocked(getDevWork).mockResolvedValue({ ...devWork, repos });
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    await waitFor(() => expect(getDevWork).toHaveBeenCalled());
    expect(await screen.findByText("frontend")).toBeInTheDocument();
    expect(screen.getByText("backend")).toBeInTheDocument();
    // Failed row exposes the err message both inline and via title=.
    const failedMsg = await screen.findByText(
      "remote rejected: protected branch",
    );
    expect(failedMsg).toBeInTheDocument();
    expect(failedMsg.getAttribute("title")).toBe(
      "remote rejected: protected branch",
    );
  });

  it("shows manual push only after completion and calls the API", async () => {
    const repos: WorkerRepoHandoff[] = [
      {
        repo_id: "repo-aaa111",
        mount_name: "frontend",
        base_branch: "main",
        base_rev: null,
        devwork_branch: "devwork/ws-1/dv-1/frontend",
        push_state: "pending",
        is_primary: true,
        worktree_path: "/tmp/frontend",
        url: "git@github.com:org/frontend.git",
        ssh_key_path: null,
        push_err: null,
      },
    ];
    const completed = { ...devWork, current_step: "COMPLETED" as const, repos };
    vi.mocked(getDevWork).mockResolvedValue(completed);
    vi.mocked(pushDevWorkBranches).mockResolvedValue({
      ...completed,
      repos: [{ ...repos[0], push_state: "pushed" }],
    });
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    const button = await screen.findByRole("button", { name: "推送分支" });
    expect(button).toHaveClass("devwork-push-attention");
    fireEvent.click(button);

    await waitFor(() => {
      expect(pushDevWorkBranches).toHaveBeenCalledWith("dv-1");
    });
    expect(await screen.findByRole("button", { name: "已推送" })).toBeDisabled();
  });

  it("hides manual push before completion", async () => {
    vi.mocked(getDevWork).mockResolvedValue({
      ...devWork,
      repos: [
        {
          repo_id: "repo-aaa111",
          mount_name: "frontend",
          base_branch: "main",
          base_rev: null,
          devwork_branch: "devwork/ws-1/dv-1/frontend",
          push_state: "pending",
          is_primary: true,
          worktree_path: "/tmp/frontend",
          url: "git@github.com:org/frontend.git",
          ssh_key_path: null,
          push_err: null,
        },
      ],
    });
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    await waitFor(() => expect(getDevWork).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "推送分支" })).not.toBeInTheDocument();
  });

  it("renders retry push label and inline error", async () => {
    vi.mocked(getDevWork).mockResolvedValue({
      ...devWork,
      current_step: "COMPLETED",
      repos: [
        {
          repo_id: "repo-aaa111",
          mount_name: "frontend",
          base_branch: "main",
          base_rev: null,
          devwork_branch: "devwork/ws-1/dv-1/frontend",
          push_state: "failed",
          is_primary: true,
          worktree_path: "/tmp/frontend",
          url: "git@github.com:org/frontend.git",
          ssh_key_path: null,
          push_err: "remote rejected",
        },
      ],
    });
    vi.mocked(pushDevWorkBranches).mockRejectedValue(
      new ApiError(502, "push denied", null),
    );
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "重试推送" }));

    expect(await screen.findByText("push denied")).toBeInTheDocument();
  });

  it("continues an escalated max-round DevWork with the requested round count", async () => {
    const escalated = {
      ...devWork,
      current_step: "ESCALATED" as const,
      iteration_rounds: 10,
      max_rounds: 10,
      escalated_at: "2026-04-23T00:00:05Z",
      continue_available: true,
    };
    vi.mocked(getDevWork).mockResolvedValue(escalated);
    vi.mocked(continueDevWork).mockResolvedValue({
      ...escalated,
      current_step: "STEP2_ITERATION",
      iteration_rounds: 10,
      max_rounds: 13,
      escalated_at: null,
      continue_available: false,
      is_running: true,
    });
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    fireEvent.change(await screen.findByLabelText("继续循环次数"), {
      target: { value: "3" },
    });
    fireEvent.change(screen.getByLabelText("继续循环准出阈值"), {
      target: { value: "90" },
    });
    fireEvent.click(screen.getByRole("button", { name: "继续循环" }));

    await waitFor(() => {
      expect(continueDevWork).toHaveBeenCalledWith("dv-1", 3, 90);
    });
  });

  it("resumes an artifact-failure escalation from the recorded step", async () => {
    vi.mocked(getDevWork).mockResolvedValue({
      ...devWork,
      current_step: "ESCALATED",
      escalated_at: "2026-04-23T00:00:01Z",
      resume_available: true,
      resume_step: "STEP5_REVIEW",
    });
    vi.mocked(resumeDevWorkStep).mockResolvedValue({
      ...devWork,
      current_step: "STEP5_REVIEW",
      resume_available: false,
      resume_step: null,
      is_running: true,
    });
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Step/ }));

    await waitFor(() => {
      expect(resumeDevWorkStep).toHaveBeenCalledWith("dv-1");
    });
  });

  it("treats 404 from getGate as no-gate state (not an error)", async () => {
    vi.mocked(getDevWork).mockResolvedValue(devWork);
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    await waitFor(() => expect(getDevWork).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "闸门" }));

    await waitFor(() => {
      expect(screen.getByText("当前未进入闸门。")).toBeInTheDocument();
    });
  });

  it("renders running banner and shows heartbeat progress", async () => {
    vi.mocked(getDevWork).mockResolvedValue({
      ...devWork,
      is_running: true,
      progress: {
        step: "STEP4_DEVELOP",
        round: 2,
        elapsed_s: 45,
        last_heartbeat_at: "2026-04-23T00:00:01Z",
        dispatch_id: "ad-1",
      },
    });
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    expect(await screen.findByText("自动推进中")).toBeInTheDocument();
    expect(screen.getByText("后台驱动正在推进此 DevWork，心跳进度会随轮询刷新。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "推进" })).not.toBeInTheDocument();
    expect(screen.getByText("STEP4_DEVELOP")).toBeInTheDocument();
    expect(screen.getByText("45s")).toBeInTheDocument();
  });

  it("renders a bounded scoped activity feed", async () => {
    vi.mocked(getDevWork).mockResolvedValue(devWork);
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));
    vi.mocked(listWorkspaceEvents).mockResolvedValue({
      events: [
        {
          id: 1,
          event_id: "evt-1",
          event_name: "dev_work.progress",
          workspace_id: "ws-1",
          correlation_id: "dv-1",
          payload: { step: "STEP4_DEVELOP", round: 2 },
          ts: "2026-04-23T00:00:01Z",
        },
      ],
      pagination: { limit: 20, offset: 0, total: 1, has_more: false },
    });

    renderPage();

    await waitFor(() => expect(getDevWork).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));

    expect(await screen.findByText("dev_work.progress")).toBeInTheDocument();
    expect(screen.getByTestId("devwork-activity-feed")).toBeInTheDocument();
    expect(listWorkspaceEvents).toHaveBeenCalledWith(
      "ws-1",
      expect.objectContaining({
        correlation_id: "dv-1",
        event_name: expect.arrayContaining(["dev_work.progress"]),
      }),
    );
  });

  it("renders review history as structured summary cards", async () => {
    const reviews: Review[] = [
      {
        id: "rev-1",
        dev_work_id: "dv-1",
        design_work_id: null,
        dev_iteration_note_id: "note-1",
        round: 2,
        score: 72,
        score_breakdown: {
          plan_score_a: 80,
          actual_score_b: 90,
          final_score: 72,
        },
        issues: [
          {
            message: "Login button does not submit",
            severity: "high",
            file: "src/Login.tsx",
            line: 42,
            expected: "submit request",
            context: { nested: true },
          },
        ],
        findings: [
          {
            id: "DW-01",
            status: "done",
            verified: true,
          },
          {
            title: "Validation guard is present",
            kind: "positive",
            mount: "frontend",
            note: "schema guard exists",
          },
        ],
        next_round_hints: [
          {
            kind: "missing_feature",
            message: "Add logout route",
            mount: "backend",
            path: "routes/auth.py",
          },
        ],
        problem_category: "impl_gap",
        reviewer: "codex",
        created_at: "2026-04-23T00:00:02Z",
      },
    ];
    vi.mocked(getDevWork).mockResolvedValue(devWork);
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue(reviews);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    const view = renderPage();

    await waitFor(() => expect(getDevWork).toHaveBeenCalled());
    const reviewsTab = view.container.querySelector<HTMLButtonElement>("#devwork-tab-reviews");
    expect(reviewsTab).not.toBeNull();
    fireEvent.click(reviewsTab!);

    expect(await screen.findByText("Login button does not submit")).toBeInTheDocument();
    const findingsTable = screen.getByRole("table", { name: "发现项" });
    expect(within(findingsTable).getByText("DW-01")).toBeInTheDocument();
    expect(within(findingsTable).getByText("done")).toBeInTheDocument();
    expect(within(findingsTable).getByText("true")).toBeInTheDocument();
    expect(screen.getByText("Validation guard is present")).toBeInTheDocument();
    expect(within(findingsTable).getByText("schema guard exists")).toBeInTheDocument();
    expect(screen.getByText("Add logout route")).toBeInTheDocument();
    expect(screen.getByText("src/Login.tsx:42")).toBeInTheDocument();
    expect(screen.getByText("routes/auth.py")).toBeInTheDocument();
    expect(screen.getByText("submit request")).toBeInTheDocument();
    expect(screen.queryByText(/"message"/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"nested"/)).not.toBeInTheDocument();
  });

  it("switches review history by selected round and shows a/b/final scores", async () => {
    const reviews: Review[] = [
      {
        id: "rev-1",
        dev_work_id: "dv-1",
        design_work_id: null,
        dev_iteration_note_id: "note-1",
        round: 1,
        score: 56,
        score_breakdown: {
          plan_score_a: 80,
          actual_score_b: 70,
          final_score: 56,
        },
        issues: [{ message: "Round one issue" }],
        findings: null,
        next_round_hints: null,
        problem_category: "impl_gap",
        reviewer: "codex",
        created_at: "2026-04-23T00:00:01Z",
      },
      {
        id: "rev-2",
        dev_work_id: "dv-1",
        design_work_id: null,
        dev_iteration_note_id: "note-2",
        round: 2,
        score: 81,
        score_breakdown: {
          plan_score_a: 90,
          actual_score_b: 90,
          final_score: 81,
        },
        issues: [{ message: "Round two issue" }],
        findings: null,
        next_round_hints: null,
        problem_category: null,
        reviewer: "codex",
        created_at: "2026-04-23T00:00:02Z",
      },
    ];
    vi.mocked(getDevWork).mockResolvedValue(devWork);
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue(reviews);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "审核历史" }));

    expect(await screen.findByText("Round two issue")).toBeInTheDocument();
    expect(screen.queryByText("Round one issue")).not.toBeInTheDocument();
    expect(screen.getByText("开发计划分 a")).toBeInTheDocument();
    expect(screen.getByText("实施分 b")).toBeInTheDocument();
    expect(screen.getAllByText("最终评分").length).toBeGreaterThan(0);
    expect(screen.getByText("round(a*b / 100)")).toBeInTheDocument();
    expect(screen.getAllByText("81").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /第 1 轮/ }));

    expect(await screen.findByText("Round one issue")).toBeInTheDocument();
    expect(screen.queryByText("Round two issue")).not.toBeInTheDocument();
    expect(screen.getAllByText("56").length).toBeGreaterThan(0);
  });

  it("supports keyboard navigation across detail tabs", async () => {
    vi.mocked(getDevWork).mockResolvedValue(devWork);
    vi.mocked(listIterationNotes).mockResolvedValue([]);
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getGate).mockRejectedValue(new ApiError(404, "gate not found", null));

    renderPage();

    const overviewTab = await screen.findByRole("tab", { name: "总览" });
    overviewTab.focus();
    fireEvent.keyDown(overviewTab, { key: "End" });

    const activityTab = screen.getByRole("tab", { name: "Activity" });
    await waitFor(() => {
      expect(activityTab).toHaveAttribute("aria-selected", "true");
      expect(document.activeElement).toBe(activityTab);
    });
  });
});
