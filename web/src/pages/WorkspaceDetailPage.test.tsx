import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DesignDoc, DesignWork, DevWork, Workspace, WorkspaceEventsEnvelope } from "../types";
import { WorkspaceDetailPage } from "./WorkspaceDetailPage";

vi.mock("../api/workspaces", () => ({
  getWorkspace: vi.fn(),
  archiveWorkspace: vi.fn(),
}));
vi.mock("../api/designWorks", () => ({
  listDesignWorks: vi.fn(),
  createDesignWork: vi.fn(),
}));
vi.mock("../api/designDocs", () => ({
  listDesignDocs: vi.fn(),
}));
vi.mock("../api/devWorks", () => ({
  listDevWorks: vi.fn(),
  createDevWork: vi.fn(),
}));
vi.mock("../api/workspaceEvents", () => ({
  listWorkspaceEvents: vi.fn(),
}));

import { getWorkspace } from "../api/workspaces";
import { listDesignWorks } from "../api/designWorks";
import { listDesignDocs } from "../api/designDocs";
import { listDevWorks } from "../api/devWorks";
import { listWorkspaceEvents } from "../api/workspaceEvents";

afterEach(() => {
  vi.clearAllMocks();
});

const workspace: Workspace = {
  id: "ws-1",
  title: "WS",
  slug: "ws",
  status: "active",
  root_path: "/root/ws",
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
};

const designWork: DesignWork = {
  id: "dw-1",
  workspace_id: "ws-1",
  mode: "new",
  current_state: "LLM_GENERATE",
  loop: 1,
  missing_sections: null,
  output_design_doc_id: null,
  escalated_at: null,
  title: "T",
  sub_slug: "t",
  version: null,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
};

const devWork: DevWork = {
  id: "dv-1",
  workspace_id: "ws-1",
  design_doc_id: "doc-1",
  current_step: "STEP2_ITERATION",
  iteration_rounds: 1,
  first_pass_success: null,
  last_score: 80,
  last_problem_category: null,
  escalated_at: null,
  completed_at: null,
  worktree_path: null,
  worktree_branch: null,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
};

const designDoc: DesignDoc = {
  id: "doc-1",
  workspace_id: "ws-1",
  slug: "feature",
  version: "1.0.0",
  path: "/workspaces/ws/docs/feature.md",
  parent_version: null,
  needs_frontend_mockup: false,
  rubric_threshold: 80,
  status: "published",
  content_hash: null,
  byte_size: 1024,
  created_at: "2026-04-01T00:00:00Z",
  published_at: "2026-04-02T00:00:00Z",
};

const eventsEnvelope: WorkspaceEventsEnvelope = {
  events: [],
  pagination: { limit: 50, offset: 0, has_more: false },
};

function renderPage() {
  render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <MemoryRouter initialEntries={["/workspaces/ws-1"]}>
        <Routes>
          <Route path="/workspaces/:wsId" element={<WorkspaceDetailPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  );
}

describe("WorkspaceDetailPage", () => {
  it("renders workspace header and switchable tabs", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorks).mockResolvedValue([designWork]);
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorks).mockResolvedValue([devWork]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    expect(await screen.findByText("WS")).toBeInTheDocument();
    // DesignDoc "feature@1.0.0" renders as three sibling text nodes (slug, '@', version).
    // Use findAllByText with a regex to tolerate multiple matches (path + slug).
    const matches = await screen.findAllByText(/feature/);
    expect(matches.length).toBeGreaterThan(0);
    // All three tab buttons exist.
    expect(screen.getByRole("tab", { name: "设计工作" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "开发工作" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "事件" })).toBeInTheDocument();
  });
});
