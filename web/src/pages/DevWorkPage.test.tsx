import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { DevWork, GateInfo } from "../types";
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
});
