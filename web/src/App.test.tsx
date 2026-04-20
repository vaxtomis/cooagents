import { render, screen, waitFor } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { vi } from "vitest";
import { AuthProvider } from "./auth/AuthContext";
import { createAppRouter } from "./router";

vi.mock("./api/auth", () => ({
  fetchMe: vi.fn(async () => ({ username: "tester" })),
  login: vi.fn(async () => ({ username: "tester" })),
  logout: vi.fn(async () => {}),
}));

vi.mock("./pages/DashboardPage", () => ({
  DashboardPage: () => <div>dashboard page</div>,
}));

vi.mock("./pages/RunsListPage", () => ({
  RunsListPage: () => <div>runs page</div>,
}));

vi.mock("./pages/RunDetailPage", () => ({
  RunDetailPage: () => <div>run detail page</div>,
}));

vi.mock("./pages/AgentHostsPage", () => ({
  AgentHostsPage: () => <div>agent hosts page</div>,
}));

vi.mock("./pages/MergeQueuePage", () => ({
  MergeQueuePage: () => <div>merge queue page</div>,
}));

function renderAt(path: string) {
  return render(
    <AuthProvider>
      <RouterProvider router={createAppRouter([path])} />
    </AuthProvider>,
  );
}

describe("App shell", () => {
  it("renders the sidebar navigation and phase 2 routes", async () => {
    const overviewLabel = "\u6982\u89c8";
    const hostsLabel = "Agent \u4e3b\u673a";
    const queueLabel = "Merge \u961f\u5217";

    const overview = renderAt("/");
    await waitFor(() => expect(screen.getByText("Cooagents")).toBeInTheDocument());
    expect(screen.getAllByRole("link", { name: overviewLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Runs" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: hostsLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: queueLabel }).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: overviewLabel })).toBeInTheDocument();
    overview.unmount();

    const runs = renderAt("/runs");
    await waitFor(() => expect(screen.getByRole("heading", { name: "Runs" })).toBeInTheDocument());
    runs.unmount();

    const detail = renderAt("/runs/run-123");
    await waitFor(() => expect(screen.getByRole("heading", { name: "运行详情" })).toBeInTheDocument());
    detail.unmount();

    const hosts = renderAt("/agent-hosts");
    await waitFor(() => expect(screen.getByRole("heading", { name: hostsLabel })).toBeInTheDocument());
    hosts.unmount();

    const queue = renderAt("/merge-queue");
    await waitFor(() => expect(screen.getByRole("heading", { name: queueLabel })).toBeInTheDocument());
    queue.unmount();
  });
});
