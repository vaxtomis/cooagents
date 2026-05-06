import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "./auth/AuthContext";
import { createAppRouter } from "./router";

vi.mock("./api/auth", () => ({
  fetchMe: vi.fn(async () => ({ username: "tester" })),
  login: vi.fn(async () => ({ username: "tester" })),
  logout: vi.fn(async () => {}),
}));

vi.mock("./api/workspaces", () => ({
  listWorkspaces: vi.fn(async () => []),
}));

vi.mock("./pages/WorkspaceDashboardPage", () => ({
  WorkspaceDashboardPage: () => <div>workspace dashboard page</div>,
}));

vi.mock("./pages/WorkspacesPage", () => ({
  WorkspacesPage: () => <div>workspaces page</div>,
}));

vi.mock("./pages/WorkspaceDetailPage", () => ({
  WorkspaceDetailPage: () => <div>workspace detail page</div>,
}));

vi.mock("./pages/DesignWorkPage", () => ({
  DesignWorkPage: () => <div>design work page</div>,
}));

vi.mock("./pages/DevWorkPage", () => ({
  DevWorkPage: () => <div>dev work page</div>,
}));

vi.mock("./pages/CrossWorkspaceDevWorkPage", () => ({
  CrossWorkspaceDevWorkPage: () => <div>cross workspace dev works page</div>,
}));

vi.mock("./pages/AgentHostsPage", () => ({
  AgentHostsPage: () => <div>agent hosts page</div>,
}));

vi.mock("./pages/ReposPage", () => ({
  ReposPage: () => <div>repos page</div>,
}));

vi.mock("./pages/RepoDetailPage", () => ({
  RepoDetailPage: () => <div>repo detail page</div>,
}));

function renderAt(path: string) {
  return render(
    <AuthProvider>
      <RouterProvider router={createAppRouter([path])} />
    </AuthProvider>,
  );
}

describe("App shell", () => {
  it("renders sidebar navigation and workspace-centric routes", async () => {
    const overviewLabel = "总览";
    const workspacesLabel = "Workspace";
    const agentHostsLabel = "Agent Host 管理";
    const repoRegistryLabel = "仓库注册表";

    const overview = renderAt("/");
    await waitFor(() => expect(screen.getByText("Cooagents")).toBeInTheDocument());
    expect(screen.getAllByRole("link", { name: overviewLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: workspacesLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: agentHostsLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: repoRegistryLabel }).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "运行总览" })).toBeInTheDocument();
    overview.unmount();

    const workspaces = renderAt("/workspaces");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Workspace 目录" })).toBeInTheDocument(),
    );
    workspaces.unmount();

    const detail = renderAt("/workspaces/ws-123");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Workspace 工作台" })).toBeInTheDocument(),
    );
    detail.unmount();

    const dwDetail = renderAt("/workspaces/ws-1/design-works/dw-1");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "DesignWork 详情" })).toBeInTheDocument(),
    );
    dwDetail.unmount();

    const dvDetail = renderAt("/workspaces/ws-1/dev-works/dv-1");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "DevWork 详情" })).toBeInTheDocument(),
    );
    dvDetail.unmount();

    const hosts = renderAt("/agent-hosts");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: agentHostsLabel })).toBeInTheDocument(),
    );
    hosts.unmount();
  });

  it("collapses and expands the desktop sidebar", async () => {
    renderAt("/");
    await waitFor(() => expect(screen.getByText("Cooagents")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "折叠侧栏" }));

    expect(screen.getByRole("button", { name: "展开侧栏" })).toBeInTheDocument();
    expect(screen.queryByText("主导航")).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "总览" }).length).toBeGreaterThan(0);
  });
});
