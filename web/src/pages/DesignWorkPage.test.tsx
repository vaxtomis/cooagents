import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { DesignWork } from "../types";
import { DesignWorkPage } from "./DesignWorkPage";

vi.mock("../api/designWorks", () => ({
  getDesignWork: vi.fn(),
  tickDesignWork: vi.fn(),
  cancelDesignWork: vi.fn(),
}));
vi.mock("../api/designDocs", () => ({
  getDesignDocContent: vi.fn(),
}));
vi.mock("../api/reviews", () => ({
  listReviews: vi.fn(),
}));

import { getDesignWork } from "../api/designWorks";
import { getDesignDocContent } from "../api/designDocs";
import { listReviews } from "../api/reviews";

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <MemoryRouter initialEntries={["/workspaces/ws-1/design-works/dw-1"]}>
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
  missing_sections: ["architecture", "data-flow"],
  output_design_doc_id: null,
  escalated_at: null,
  title: "Feature",
  sub_slug: "feature",
  version: null,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
};

describe("DesignWorkPage", () => {
  it("renders escalated banner and missing_sections chips when state=ESCALATED", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({ ...baseDesignWork, current_state: "ESCALATED" });
    vi.mocked(listReviews).mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText(/DesignWork 已升级/)).toBeInTheDocument();
    expect(screen.getByText("architecture")).toBeInTheDocument();
    expect(screen.getByText("data-flow")).toBeInTheDocument();

    const tickBtn = screen.getByRole("button", { name: "Tick" });
    expect(tickBtn).toBeDisabled();
  });

  it("renders the reconcile hint when design-doc content returns 410", async () => {
    vi.mocked(getDesignWork).mockResolvedValue({
      ...baseDesignWork,
      output_design_doc_id: "doc-1",
    });
    vi.mocked(listReviews).mockResolvedValue([]);
    vi.mocked(getDesignDocContent).mockRejectedValue(
      new ApiError(410, "file missing", null),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/源文件已缺失/)).toBeInTheDocument();
    });
  });
});
