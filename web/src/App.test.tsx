import { render, screen } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { vi } from "vitest";
import { createAppRouter } from "./router";

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

vi.mock("./pages/EventLogPage", () => ({
  EventLogPage: () => <div>event log page</div>,
}));

describe("App shell", () => {
  it("renders the sidebar navigation and phase 2 routes", () => {
    const overviewLabel = "\u6982\u89c8";
    const hostsLabel = "Agent \u4e3b\u673a";
    const queueLabel = "Merge \u961f\u5217";
    const eventsLabel = "\u4e8b\u4ef6\u65e5\u5fd7";

    const overview = render(<RouterProvider router={createAppRouter(["/"])} />);

    expect(screen.getByText("Cooagents")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: overviewLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Runs" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: hostsLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: queueLabel }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: eventsLabel }).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: overviewLabel })).toBeInTheDocument();
    overview.unmount();

    const runs = render(<RouterProvider router={createAppRouter(["/runs"])} />);
    expect(screen.getByRole("heading", { name: "Runs" })).toBeInTheDocument();
    runs.unmount();

    const detail = render(<RouterProvider router={createAppRouter(["/runs/run-123"])} />);
    expect(screen.getByRole("heading", { name: "Run Detail" })).toBeInTheDocument();
    detail.unmount();

    const hosts = render(<RouterProvider router={createAppRouter(["/agent-hosts"])} />);
    expect(screen.getByRole("heading", { name: hostsLabel })).toBeInTheDocument();
    hosts.unmount();

    const queue = render(<RouterProvider router={createAppRouter(["/merge-queue"])} />);
    expect(screen.getByRole("heading", { name: queueLabel })).toBeInTheDocument();
    queue.unmount();

    render(<RouterProvider router={createAppRouter(["/events"])} />);
    expect(screen.getByRole("heading", { name: eventsLabel })).toBeInTheDocument();
  });
});
