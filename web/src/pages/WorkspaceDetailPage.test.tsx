import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  DesignDoc,
  DesignWork,
  DesignWorkPage,
  DevWork,
  DevWorkPage,
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
  listDesignWorkPage: vi.fn(),
  createDesignWork: vi.fn(),
}));
vi.mock("../api/designDocs", () => ({
  listDesignDocs: vi.fn(),
}));
vi.mock("../api/devWorks", () => ({
  listDevWorks: vi.fn(),
  listDevWorkPage: vi.fn(),
  createDevWork: vi.fn(),
}));
vi.mock("../api/workspaceEvents", () => ({
  listWorkspaceEvents: vi.fn(),
}));
vi.mock("../api/repos", () => ({
  listRepos: vi.fn(),
  listRepoPage: vi.fn(),
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
  repoLogPage: vi.fn(),
}));

import { getWorkspace } from "../api/workspaces";
import { createDesignWork, listDesignWorkPage } from "../api/designWorks";
import { listDesignDocs } from "../api/designDocs";
import { createDevWork, listDevWorkPage } from "../api/devWorks";
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
  max_loops: 3,
  missing_sections: null,
  output_design_doc_id: null,
  escalated_at: null,
  escalation_reason: null,
  title: "T",
  sub_slug: "t",
  version: null,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
  is_running: false,
  repo_refs: [],
};

const devWork: DevWork = {
  id: "dv-1",
  workspace_id: "ws-1",
  design_doc_id: "doc-1",
  current_step: "STEP2_ITERATION",
  iteration_rounds: 1,
  max_rounds: 5,
  first_pass_success: null,
  last_score: 80,
  last_problem_category: null,
  escalated_at: null,
  completed_at: null,
  worktree_path: null,
  worktree_branch: null,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
  is_running: false,
  progress: null,
  repo_refs: [],
  repos: [],
};

const repoFrontend: Repo = {
  id: "repo-aaa111",
  name: "frontend",
  url: "git@github.com:org/frontend.git",
  local_path: null,
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

const designPage: DesignWorkPage = {
  items: [designWork],
  pagination: { limit: 6, offset: 0, total: 1, has_more: false },
};

const devPage: DevWorkPage = {
  items: [devWork],
  pagination: { limit: 6, offset: 0, total: 1, has_more: false },
};

const eventsEnvelope: WorkspaceEventsEnvelope = {
  events: [],
  pagination: { limit: 20, offset: 0, total: 0, has_more: false },
};

function renderPage() {
  function LocationProbe() {
    const location = useLocation();
    return <div data-testid="location-probe">{location.pathname}</div>;
  }

  render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <MemoryRouter initialEntries={["/workspaces/ws-1"]}>
        <Routes>
          <Route path="/workspaces/:wsId" element={<WorkspaceDetailPage />} />
          <Route path="/workspaces/:wsId/design-works/:dwId" element={<LocationProbe />} />
          <Route path="/workspaces/:wsId/dev-works/:dvId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  );
}

describe("WorkspaceDetailPage", () => {
  it("renders workspace header and switchable tabs", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue(designPage);
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue(devPage);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    expect(await screen.findByText("WS")).toBeInTheDocument();
    const matches = await screen.findAllByText(/feature/);
    expect(matches.length).toBeGreaterThan(0);
    expect(screen.getByRole("tab", { name: "设计工作" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "开发工作" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "事件流" })).toBeInTheDocument();
  });

  it("renders a retry state when design work loading fails", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage)
      .mockRejectedValueOnce(new Error("设计工作接口失败"))
      .mockResolvedValueOnce(designPage);
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue(devPage);
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    expect(await screen.findByText("设计工作接口失败")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(listDesignWorkPage).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("T")).toBeInTheDocument();
  });

  it("bounds the workspace event stream while keeping pagination visible", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue(designPage);
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue(devPage);
    vi.mocked(listWorkspaceEvents).mockResolvedValue({
      events: [
        {
          id: 1,
          event_id: "evt-1",
          event_name: "design_work.escalated",
          workspace_id: "ws-1",
          correlation_id: "dw-1",
          payload: { reason: "post-validate failed" },
          ts: "2026-04-23T00:00:01Z",
        },
      ],
      pagination: { limit: 20, offset: 0, total: 21, has_more: true },
    });

    renderPage();

    fireEvent.click((await screen.findAllByRole("tab"))[2]);

    expect(await screen.findByText("design_work.escalated")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-events-feed")).toBeInTheDocument();
    expect(document.querySelector('[data-pagination-tone="console"]')).not.toBeNull();
  });

  it("DevWork form rejects empty repo_refs and never calls createDevWork", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "开发工作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新建开发工作" }));
    expect(document.querySelector('[data-dialog-size="wide"]')).not.toBeNull();

    const docSelect = await screen.findByDisplayValue("请选择");
    fireEvent.change(docSelect, { target: { value: "doc-1" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    expect(await screen.findByText(/至少添加一个仓库/)).toBeInTheDocument();
    expect(createDevWork).not.toHaveBeenCalled();
  });

  it("DevWork form posts repo_refs[] when a repo + branch is selected", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);
    vi.mocked(createDevWork).mockResolvedValue(devWork);

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "开发工作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新建开发工作" }));

    const docSelect = await screen.findByDisplayValue("请选择");
    fireEvent.change(docSelect, { target: { value: "doc-1" } });

    const selects = await screen.findAllByRole("combobox");
    const repoSelect = selects[1];
    fireEvent.change(repoSelect, { target: { value: "repo-aaa111" } });

    await waitFor(() => expect(repoBranches).toHaveBeenCalled());
    const branchSelect = (await screen.findAllByRole("combobox"))[2];
    fireEvent.change(branchSelect, { target: { value: "main" } });

    const promptArea = screen.getByLabelText("DevWork 执行提示");
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
      const args = vi.mocked(createDevWork).mock.calls[0][0];
      expect(args.agent).toBeUndefined();
    });
  });

  it("DevWork form sends explicitly selected agent", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);
    vi.mocked(createDevWork).mockResolvedValue(devWork);

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "开发工作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新建开发工作" }));

    const docSelect = await screen.findByDisplayValue("请选择");
    fireEvent.change(docSelect, { target: { value: "doc-1" } });

    const selects = await screen.findAllByRole("combobox");
    fireEvent.change(selects[1], { target: { value: "repo-aaa111" } });

    await waitFor(() => expect(repoBranches).toHaveBeenCalled());
    const branchSelect = (await screen.findAllByRole("combobox"))[2];
    fireEvent.change(branchSelect, { target: { value: "main" } });

    fireEvent.change(screen.getByLabelText("DevWork 执行提示"), { target: { value: "ship feature x" } });
    fireEvent.change(screen.getByLabelText("执行 Agent"), { target: { value: "codex" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => {
      const args = vi.mocked(createDevWork).mock.calls[0][0];
      expect(args.agent).toBe("codex");
    });
  });

  it("DevWork form sends policy overrides when provided", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);
    vi.mocked(createDevWork).mockResolvedValue(devWork);

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "开发工作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新建开发工作" }));

    fireEvent.change(await screen.findByDisplayValue("请选择"), { target: { value: "doc-1" } });
    const selects = await screen.findAllByRole("combobox");
    fireEvent.change(selects[1], { target: { value: "repo-aaa111" } });
    await waitFor(() => expect(repoBranches).toHaveBeenCalled());
    fireEvent.change((await screen.findAllByRole("combobox"))[2], { target: { value: "main" } });

    fireEvent.change(screen.getByLabelText("DevWork 执行提示"), { target: { value: "ship feature x" } });
    fireEvent.change(screen.getByLabelText("DevWork max rounds"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("DevWork rubric threshold"), { target: { value: "92" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => {
      const args = vi.mocked(createDevWork).mock.calls[0][0];
      expect(args.max_rounds).toBe(2);
      expect(args.rubric_threshold).toBe(92);
    });
  });

  it("DevWork form blocks submit when two rows share a mount_name", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend, repoBackend]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "开发工作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新建开发工作" }));

    const docSelect = await screen.findByDisplayValue("请选择");
    fireEvent.change(docSelect, { target: { value: "doc-1" } });

    let selects = await screen.findAllByRole("combobox");
    fireEvent.change(selects[1], { target: { value: "repo-aaa111" } });

    fireEvent.click(screen.getByRole("button", { name: /添加仓库/ }));
    selects = await screen.findAllByRole("combobox");
    fireEvent.change(selects[3], { target: { value: "repo-bbb222" } });

    const mount2 = await screen.findByLabelText("mount_name #2");
    fireEvent.change(mount2, { target: { value: "frontend" } });

    await waitFor(() => expect(repoBranches).toHaveBeenCalled());
    selects = await screen.findAllByRole("combobox");
    fireEvent.change(selects[2], { target: { value: "main" } });
    fireEvent.change(selects[4], { target: { value: "main" } });

    fireEvent.change(screen.getByLabelText("DevWork 执行提示"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    expect(await screen.findByText(/mount_name "frontend" 重复/)).toBeInTheDocument();
    expect(createDevWork).not.toHaveBeenCalled();
  });

  it("DesignWork repo binding starts collapsed", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "新建设计工作" }));
    expect(document.querySelector('[data-dialog-size="wide"]')).not.toBeNull();
    const toggle = await screen.findByRole("button", { name: /关联仓库/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByLabelText("仓库选择 #1")).toBeNull();
  });

  it("DesignWork form omits repo_refs when disclosure stays closed", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(createDesignWork).mockResolvedValue(designWork);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "新建设计工作" }));

    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "Hello" } });
    fireEvent.change(screen.getByLabelText("Slug 标识"), { target: { value: "feature-x" } });
    fireEvent.change(screen.getByLabelText("需求说明"), { target: { value: "do something" } });

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
      expect(args.agent).toBeUndefined();
    });
  });

  it("DesignWork form sends explicitly selected agent", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(createDesignWork).mockResolvedValue(designWork);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "新建设计工作" }));
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "Hello" } });
    fireEvent.change(screen.getByLabelText("Slug 标识"), { target: { value: "feature-x" } });
    fireEvent.change(screen.getByLabelText("需求说明"), { target: { value: "do something" } });
    fireEvent.change(screen.getByLabelText("执行 Agent"), { target: { value: "codex" } });

    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => {
      const args = vi.mocked(createDesignWork).mock.calls[0][0];
      expect(args.agent).toBe("codex");
    });
  });

  it("DesignWork form sends policy overrides when provided", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(createDesignWork).mockResolvedValue(designWork);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "新建设计工作" }));
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "Hello" } });
    fireEvent.change(screen.getByLabelText("Slug 标识"), { target: { value: "feature-x" } });
    fireEvent.change(screen.getByLabelText("需求说明"), { target: { value: "do something" } });
    fireEvent.change(screen.getByLabelText("DesignWork max loops"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("DesignWork rubric threshold"), { target: { value: "88" } });

    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => {
      const args = vi.mocked(createDesignWork).mock.calls[0][0];
      expect(args.max_loops).toBe(1);
      expect(args.rubric_threshold).toBe(88);
    });
  });

  it("redirects to the created DesignWork detail page after submit", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(createDesignWork).mockResolvedValue({ ...designWork, id: "dw-created" });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "新建设计工作" }));
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "Hello" } });
    fireEvent.change(screen.getByLabelText("Slug 标识"), { target: { value: "feature-x" } });
    fireEvent.change(screen.getByLabelText("需求说明"), { target: { value: "do something" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent(
        "/workspaces/ws-1/design-works/dw-created",
      );
    });
  });

  it("redirects to the created DevWork detail page after submit", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue({ items: [], pagination: { limit: 6, offset: 0, total: 0, has_more: false } });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);
    vi.mocked(listRepos).mockResolvedValue([repoFrontend]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);
    vi.mocked(createDevWork).mockResolvedValue({ ...devWork, id: "dv-created" });

    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "开发工作" }));
    fireEvent.click(await screen.findByRole("button", { name: "新建开发工作" }));

    const docSelect = await screen.findByDisplayValue("请选择");
    fireEvent.change(docSelect, { target: { value: "doc-1" } });

    const selects = await screen.findAllByRole("combobox");
    fireEvent.change(selects[1], { target: { value: "repo-aaa111" } });
    await waitFor(() => expect(repoBranches).toHaveBeenCalled());
    fireEvent.change((await screen.findAllByRole("combobox"))[2], { target: { value: "main" } });
    fireEvent.change(screen.getByLabelText("DevWork 执行提示"), { target: { value: "ship feature x" } });
    fireEvent.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => {
      expect(screen.getByTestId("location-probe")).toHaveTextContent(
        "/workspaces/ws-1/dev-works/dv-created",
      );
    });
  });

  it("shows running badges in design and dev work rows", async () => {
    vi.mocked(getWorkspace).mockResolvedValue(workspace);
    vi.mocked(listDesignWorkPage).mockResolvedValue({
      items: [{ ...designWork, is_running: true }],
      pagination: { limit: 6, offset: 0, total: 1, has_more: false },
    });
    vi.mocked(listDesignDocs).mockResolvedValue([designDoc]);
    vi.mocked(listDevWorkPage).mockResolvedValue({
      items: [{ ...devWork, is_running: true }],
      pagination: { limit: 6, offset: 0, total: 1, has_more: false },
    });
    vi.mocked(listWorkspaceEvents).mockResolvedValue(eventsEnvelope);

    renderPage();

    expect(await screen.findByText("自动推进中")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "开发工作" }));
    expect(await screen.findByText("自动推进中")).toBeInTheDocument();
  });
});
