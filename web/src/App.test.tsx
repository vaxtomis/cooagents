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
    const overviewLabel = "Overview";
    const workspacesLabel = "Workspaces";
    const crossLabel = "Cross-workspace DevWorks";
    const repoRegistryLabel = "Repository Registry";

    const overview = renderAt("/");
    await waitFor(() => expect(screen.getByText("Cooagents")).toBeInTheDocument());
    expect(screen.getAllByRole("link", { name: overviewLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: workspacesLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: crossLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: repoRegistryLabel }).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Operations overview" })).toBeInTheDocument();
    overview.unmount();

    const workspaces = renderAt("/workspaces");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Workspace directory" })).toBeInTheDocument(),
    );
    workspaces.unmount();

    const detail = renderAt("/workspaces/ws-123");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Workspace detail" })).toBeInTheDocument(),
    );
    detail.unmount();

    const dwDetail = renderAt("/workspaces/ws-1/design-works/dw-1");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Design work detail" })).toBeInTheDocument(),
    );
    dwDetail.unmount();

    const dvDetail = renderAt("/workspaces/ws-1/dev-works/dv-1");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Development work detail" })).toBeInTheDocument(),
    );
    dvDetail.unmount();

    const cross = renderAt("/dev-works");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: crossLabel })).toBeInTheDocument(),
    );
    cross.unmount();
  });
});
