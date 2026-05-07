import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { DevWork, GateInfo, WorkerRepoHandoff } from "../types";
import { DevWorkPage } from "./DevWorkPage";

vi.mock("../api/devWorks", () => ({
  getDevWork: vi.fn(),
  tickDevWork: vi.fn(),
  cancelDevWork: vi.fn(),
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

import { getDevWork } from "../api/devWorks";
import { listIterationNotes } from "../api/devIterationNotes";
import { listReviews } from "../api/reviews";
import { getGate } from "../api/gates";

afterEach(() => {
  vi.clearAllMocks();
});

const devWork: DevWork = {
  id: "dv-1",
  workspace_id: "ws-1",
  design_doc_id: "doc-1",
  current_step: "STEP5_REVIEW",
  iteration_rounds: 2,
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
  progress: null,
  repo_refs: [],
  repos: [],
};

const waitingGate: GateInfo = {
  gate_id: "dev:dv-1:exit",
  status: "waiting",
  gate_key: "exit",
};

function renderPage() {
  render(
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

    // Switch to the gate tab.
    fireEvent.click(screen.getByRole("tab", { name: "闸门" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "批准" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "驳回" })).toBeEnabled();
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

  it("renders running banner, disables tick, and shows heartbeat progress", async () => {
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
    expect(screen.getByText(/后台驱动正在推进此 DevWork/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "推进" })).toBeDisabled();
    expect(screen.getByText("STEP4_DEVELOP")).toBeInTheDocument();
    expect(screen.getByText("45s")).toBeInTheDocument();
  });
});
