import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  DesignDoc,
  DesignWork,
  DevWork,
  Repo,
  RepoBranches,
  Workspace,
  WorkspaceEventsEnvelope,
} from "../types";
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
vi.mock("../api/repos", () => ({
  listRepos: vi.fn(),
  getRepo: vi.fn(),
  createRepo: vi.fn(),
  updateRepo: vi.fn(),
  deleteRepo: vi.fn(),
  syncRepos: vi.fn(),
  fetchRepo: vi.fn(),
  repoBranches: vi.fn(),
  repoTree: vi.fn(),
  repoBlob: vi.fn(),
  repoLog: vi.fn(),
}));

import { getWorkspace } from "../api/workspaces";
import { createDesignWork, listDesignWorks } from "../api/designWorks";
import { listDesignDocs } from "../api/designDocs";
import { createDevWork, listDevWorks } from "../api/devWorks";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { listRepos, repoBranches } from "../api/repos";

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
  repo_refs: [],
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
  repo_refs: [],
  repos: [],
};

const repoFrontend: Repo = {
  id: "repo-aaa111",
  name: "frontend",
  url: "git@github.com:org/frontend.git",
  default_branch: "main",
  ssh_key_path: null,
  bare_clone_path: null,
  role: "frontend",
  fetch_status: "healthy",
  last_fetched_at: "2026-04-26T12:00:14Z",
  last_fetch_err: null,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-26T12:00:14Z",
};

const repoBackend: Repo = {
  ...repoFrontend,
  id: "repo-bbb222",
  name: "backend",
  role: "backend",
};

const branchesMain: RepoBranches = {
  default_branch: "main",
  branches: ["main", "develop"],
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

  it("DevWork form rejects empty repo_refs and never calls createDevWork", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorks).mockResolvedValue([]);
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorks).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "开发工作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新建 DevWork" }));

    // Pick the DesignDoc.
    const docSelect = await screen.findByDisplayValue("请选择");
    fireEvent.change(docSelect, { target: { value: "doc-1" } });

    // Submit without picking any repo — the editor is rendered with one
    // empty placeholder row that stays excluded from `value` because it has
    // no real fields, so the parent form should see `repo_refs.length === 0`.
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    expect(await screen.findByText(/请至少添加一个仓库/)).toBeInTheDocument();
    expect(createDevWork).not.toHaveBeenCalled();
  });

  it("DevWork form posts repo_refs[] when a repo + branch is selected", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorks).mockResolvedValue([]);
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorks).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);
    vi.mocked(createDevWork).mockResolvedValue(devWork);

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "开发工作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新建 DevWork" }));

    const docSelect = await screen.findByDisplayValue("请选择");
    fireEvent.change(docSelect, { target: { value: "doc-1" } });

    // Pick a repo (auto-seeds mount_name).
    const repoSelect = await screen.findByLabelText("仓库选择 #1");
    fireEvent.change(repoSelect, { target: { value: "repo-aaa111" } });

    // Pick a branch (lazy-loaded after repo selection).
    const branchSelect = await screen.findByLabelText("base_branch #1");
    await waitFor(() => expect(repoBranches).toHaveBeenCalled());
    fireEvent.change(branchSelect, { target: { value: "main" } });

    // Fill prompt.
    const promptArea = screen.getByLabelText("DevWork prompt");
    fireEvent.change(promptArea, { target: { value: "ship feature x" } });

    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => {
      expect(createDevWork).toHaveBeenCalledWith(
        expect.objectContaining({
          workspace_id: "ws-1",
          design_doc_id: "doc-1",
          prompt: "ship feature x",
          repo_refs: [
            expect.objectContaining({
              repo_id: "repo-aaa111",
              base_branch: "main",
              mount_name: "frontend",
            }),
          ],
        }),
      );
    });
  });

  it("DevWork form blocks submit when two rows share a mount_name", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorks).mockResolvedValue([]);
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorks).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend, repoBackend]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "开发工作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新建 DevWork" }));

    const docSelect = await screen.findByDisplayValue("请选择");
    fireEvent.change(docSelect, { target: { value: "doc-1" } });

    const repoSelect1 = await screen.findByLabelText("仓库选择 #1");
    fireEvent.change(repoSelect1, { target: { value: "repo-aaa111" } });

    fireEvent.click(screen.getByRole("button", { name: /添加仓库/ }));
    const repoSelect2 = await screen.findByLabelText("仓库选择 #2");
    fireEvent.change(repoSelect2, { target: { value: "repo-bbb222" } });

    // Force both rows to use the same mount_name.
    const mount2 = await screen.findByLabelText("mount_name #2");
    fireEvent.change(mount2, { target: { value: "frontend" } });

    // Both branches set so the only blocker is the duplicate.
    await waitFor(() => expect(repoBranches).toHaveBeenCalled());
    const branch1 = screen.getByLabelText("base_branch #1");
    fireEvent.change(branch1, { target: { value: "main" } });
    const branch2 = screen.getByLabelText("base_branch #2");
    fireEvent.change(branch2, { target: { value: "main" } });

    const promptArea = screen.getByLabelText("DevWork prompt");
    fireEvent.change(promptArea, { target: { value: "x" } });

    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    expect(
      await screen.findByText(/mount_name "frontend" 重复/),
    ).toBeInTheDocument();
    expect(createDevWork).not.toHaveBeenCalled();
  });

  it("DesignWork repo binding starts collapsed", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorks).mockResolvedValue([]);
    vi.mocked(listDesignDocs).mockResolvedValue([]);
    vi.mocked(listDevWorks).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "新建 DesignWork" }));
    const toggle = await screen.findByRole("button", {
      name: /添加仓库绑定（可选）/,
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByLabelText("仓库选择 #1")).toBeNull();
  });

  it("DesignWork form omits repo_refs when disclosure stays closed", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorks).mockResolvedValue([]);
    vi.mocked(listDesignDocs).mockResolvedValue([]);
    vi.mocked(listDevWorks).mockResolvedValue([]);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(createDesignWork).mockResolvedValue(designWork);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "新建 DesignWork" }));

    fireEvent.change(screen.getByLabelText("标题"), {
      target: { value: "Hello" },
    });
    fireEvent.change(screen.getByLabelText("Slug"), {
      target: { value: "feature-x" },
    });
    fireEvent.change(screen.getByLabelText("用户输入"), {
      target: { value: "do something" },
    });

    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => {
      expect(createDesignWork).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Hello",
          slug: "feature-x",
          user_input: "do something",
        }),
      );
      const args = vi.mocked(createDesignWork).mock.calls[0][0];
      expect(args.repo_refs).toBeUndefined();
    });
  });
});
