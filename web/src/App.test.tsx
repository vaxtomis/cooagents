import { render, screen } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { createAppRouter } from "./router";

describe("App shell", () => {
  it("renders the sidebar navigation and phase 1 routes", () => {
    const overview = render(<RouterProvider router={createAppRouter(["/"])} />);

    expect(screen.getByText("Cooagents")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "概览" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Runs" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Agent 主机" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "Merge 队列" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: "事件日志" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "概览" })).toBeInTheDocument();
    overview.unmount();

    const runs = render(<RouterProvider router={createAppRouter(["/runs"])} />);
    expect(screen.getByRole("heading", { name: "Runs" })).toBeInTheDocument();
    runs.unmount();

    const detail = render(<RouterProvider router={createAppRouter(["/runs/run-123"])} />);
    expect(screen.getByRole("heading", { name: "Run Detail" })).toBeInTheDocument();
    detail.unmount();

    const hosts = render(<RouterProvider router={createAppRouter(["/agent-hosts"])} />);
    expect(screen.getByRole("heading", { name: "Agent 主机" })).toBeInTheDocument();
    hosts.unmount();

    const queue = render(<RouterProvider router={createAppRouter(["/merge-queue"])} />);
    expect(screen.getByRole("heading", { name: "Merge 队列" })).toBeInTheDocument();
    queue.unmount();

    render(<RouterProvider router={createAppRouter(["/events"])} />);
    expect(screen.getByRole("heading", { name: "事件日志" })).toBeInTheDocument();
  });
});
