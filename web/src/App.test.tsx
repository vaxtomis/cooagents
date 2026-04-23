import { render, screen, waitFor } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "./auth/AuthContext";
import { createAppRouter } from "./router";

vi.mock("./api/auth", () => ({
  fetchMe: vi.fn(async () => ({ username: "tester" })),
  login: vi.fn(async () => ({ username: "tester" })),
  logout: vi.fn(async () => {}),
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

function renderAt(path: string) {
  return render(
    <AuthProvider>
      <RouterProvider router={createAppRouter([path])} />
    </AuthProvider>,
  );
}

describe("App shell", () => {
  it("renders sidebar navigation and workspace-centric routes", async () => {
    const overviewLabel = "概览";
    const workspacesLabel = "工作区域";
    const crossLabel = "跨区域 DevWorks";
    const hostsLabel = "Agent 主机";

    const overview = renderAt("/");
    await waitFor(() => expect(screen.getByText("Cooagents")).toBeInTheDocument());
    expect(screen.getAllByRole("link", { name: overviewLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: workspacesLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: crossLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: hostsLabel }).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: overviewLabel })).toBeInTheDocument();
    overview.unmount();

    const workspaces = renderAt("/workspaces");
    await waitFor(() => expect(screen.getByRole("heading", { name: workspacesLabel })).toBeInTheDocument());
    workspaces.unmount();

    const detail = renderAt("/workspaces/ws-123");
    await waitFor(() => expect(screen.getByRole("heading", { name: "Workspace 详情" })).toBeInTheDocument());
    detail.unmount();

    const dwDetail = renderAt("/workspaces/ws-1/design-works/dw-1");
    await waitFor(() => expect(screen.getByRole("heading", { name: "DesignWork 详情" })).toBeInTheDocument());
    dwDetail.unmount();

    const dvDetail = renderAt("/workspaces/ws-1/dev-works/dv-1");
    await waitFor(() => expect(screen.getByRole("heading", { name: "DevWork 详情" })).toBeInTheDocument());
    dvDetail.unmount();

    const cross = renderAt("/dev-works");
    await waitFor(() => expect(screen.getByRole("heading", { name: crossLabel })).toBeInTheDocument());
    cross.unmount();

    const hosts = renderAt("/agent-hosts");
    await waitFor(() => expect(screen.getByRole("heading", { name: hostsLabel })).toBeInTheDocument());
    hosts.unmount();
  });
});
