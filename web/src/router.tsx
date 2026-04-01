import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Bot,
  CirclePlay,
  GitMerge,
  LayoutDashboard,
  Server,
} from "lucide-react";
import {
  NavLink,
  Outlet,
  createBrowserRouter,
  createMemoryRouter,
  useLocation,
} from "react-router-dom";
import { AgentHostsPage } from "./pages/AgentHostsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { MergeQueuePage } from "./pages/MergeQueuePage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsListPage } from "./pages/RunsListPage";

type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
};

type PageMeta = {
  title: string;
  eyebrow: string;
  description: string;
};

const navItems: NavItem[] = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/runs", label: "Runs", icon: CirclePlay },
  { to: "/agent-hosts", label: "Agent 主机", icon: Server },
  { to: "/merge-queue", label: "Merge 队列", icon: GitMerge },
];

function resolvePageMeta(pathname: string): PageMeta {
  if (pathname === "/") {
    return {
      title: "概览",
      eyebrow: "仪表盘总览",
      description:
        "审批状态、活跃运行、主机健康度与首阶段运维摘要的实时总览。",
    };
  }

  if (pathname === "/runs") {
    return {
      title: "Runs",
      eyebrow: "服务端查询",
      description:
        "搜索、筛选、排序并翻页浏览运行数据，点击即可进入运行详情。",
    };
  }

  if (pathname.startsWith("/runs/")) {
    return {
      title: "运行详情",
      eyebrow: "实时运行时间线",
      description:
        "查看当前运行状态：任务、产物、事件追踪、审批操作、取消控制，以及基于 SSE 的实时刷新。",
    };
  }

  if (pathname === "/agent-hosts") {
    return {
      title: "Agent 主机",
      eyebrow: "集群运维",
      description:
        "管理主机清单、调整并发容量、执行健康检查、移除失效 Agent。",
    };
  }

  if (pathname === "/merge-queue") {
    return {
      title: "Merge 队列",
      eyebrow: "队列管理",
      description:
        "查看待合并项、跟踪冲突状态、关联运行数据，执行合并或跳过操作。",
    };
  }

  return {
    title: "",
    eyebrow: "",
    description: "",
  };
}

function ShellNavLink({ item, compact = false }: { item: NavItem; compact?: boolean }) {
  const Icon = item.icon;

  return (
    <NavLink
      end={item.end}
      to={item.to}
      className={({ isActive }) => {
        const base = compact
          ? "inline-flex min-w-fit items-center gap-2 rounded-full border px-3 py-2 text-sm transition"
          : "flex items-center gap-3 rounded-2xl border px-3 py-3 text-sm transition";
        const state = isActive
          ? "border-accent/30 bg-accent/12 text-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
          : "border-transparent text-muted hover:border-white/8 hover:bg-white/4 hover:text-white";
        return `${base} ${state}`;
      }}
    >
      <Icon className="size-4.5 shrink-0" strokeWidth={1.8} />
      <span>{item.label}</span>
    </NavLink>
  );
}

function ShellLayout() {
  const { pathname } = useLocation();
  const meta = resolvePageMeta(pathname);

  return (
    <div className="min-h-screen bg-void text-copy">
      <div className="mx-auto flex min-h-screen w-full max-w-[1600px] gap-4 px-3 py-3 md:px-5 md:py-5">
        <aside className="hidden w-[240px] shrink-0 rounded-[30px] border border-white/6 bg-black/55 p-4 shadow-shell backdrop-blur md:flex md:flex-col">
          <div className="flex items-center gap-3 px-2 py-2">
            <div className="flex size-10 items-center justify-center rounded-2xl bg-accent/14 text-accent">
              <Bot className="size-5" strokeWidth={1.9} />
            </div>
            <div>
              <p className="text-lg font-semibold tracking-tight text-white">Cooagents</p>
              <p className="text-xs text-muted">运维控制台</p>
            </div>
          </div>

          <nav className="mt-8 flex flex-col gap-1.5">
            {navItems.map((item) => (
              <ShellNavLink key={item.to} item={item} />
            ))}
          </nav>

          <div className="mt-auto rounded-[24px] border border-white/6 bg-panel p-4 text-sm text-muted">
            <p className="text-white">运维控制台已上线</p>
            <p className="mt-2">
              概览、Runs、主机管理与合并控制共享同一实时终端。
            </p>
          </div>
        </aside>

        <div className="flex min-h-[calc(100vh-1.5rem)] flex-1 flex-col gap-4">
          <header className="overflow-hidden rounded-[30px] border border-white/6 bg-[radial-gradient(circle_at_top_left,rgba(168,85,247,0.18),transparent_38%),linear-gradient(180deg,rgba(24,24,27,0.96),rgba(15,15,18,0.92))] p-5 shadow-shell md:p-7">
            <div className="flex flex-col gap-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-[11px] uppercase tracking-[0.32em] text-accent/85">
                    {meta.eyebrow}
                  </p>
                  <h1 className="mt-3 text-3xl font-semibold tracking-tight text-white md:text-[2.1rem]">
                    {meta.title}
                  </h1>
                  <p className="mt-3 max-w-3xl text-sm leading-6 text-muted md:text-[15px]">
                    {meta.description}
                  </p>
                </div>

                <div className="hidden rounded-full border border-white/8 bg-white/4 px-4 py-2 text-xs uppercase tracking-[0.24em] text-muted md:flex md:items-center md:gap-2">
                  <span className="size-2 rounded-full bg-success" />
                  在线
                </div>
              </div>

              <nav className="flex gap-2 overflow-x-auto pb-1 md:hidden">
                {navItems.map((item) => (
                  <ShellNavLink key={item.to} item={item} compact />
                ))}
              </nav>
            </div>
          </header>

          <main className="flex-1">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}

const routes = [
  {
    path: "/",
    element: <ShellLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "runs", element: <RunsListPage /> },
      { path: "runs/:runId", element: <RunDetailPage /> },
      { path: "agent-hosts", element: <AgentHostsPage /> },
      { path: "merge-queue", element: <MergeQueuePage /> },
    ],
  },
];

export function createAppRouter(initialEntries?: string[]) {
  return initialEntries
    ? createMemoryRouter(routes, { initialEntries })
    : createBrowserRouter(routes);
}

export const appRouter = createAppRouter();
