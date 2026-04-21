import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Bot,
  CirclePlay,
  GitMerge,
  LayoutDashboard,
  LogOut,
  Server,
} from "lucide-react";
import {
  NavLink,
  Navigate,
  Outlet,
  createBrowserRouter,
  createMemoryRouter,
  useLocation,
} from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { AgentHostsPage } from "./pages/AgentHostsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
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
          : "flex items-center gap-3 rounded-xl border px-3 py-2.5 text-sm transition";
        const state = isActive
          ? "border-[color:var(--color-ring-warm)] bg-panel text-copy shadow-[0_0_0_1px_var(--color-ring-warm)]"
          : "border-transparent text-muted hover:border-border hover:bg-panel-strong/60 hover:text-copy";
        return `${base} ${state}`;
      }}
    >
      <Icon className="size-4 shrink-0" strokeWidth={1.8} />
      <span>{item.label}</span>
    </NavLink>
  );
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-void text-muted">
        <span className="text-sm">加载会话...</span>
      </div>
    );
  }
  if (status === "unauthenticated") {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <>{children}</>;
}

function ShellLayout() {
  const { pathname } = useLocation();
  const meta = resolvePageMeta(pathname);
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-void text-copy">
      <div className="mx-auto flex min-h-screen w-full max-w-[1500px] gap-6 px-4 py-5 md:px-8 md:py-8">
        <aside className="hidden w-[256px] shrink-0 flex-col rounded-[24px] border border-border bg-panel p-5 shadow-whisper md:flex">
          <div className="flex items-center gap-3 px-1 pb-2">
            <div className="flex size-10 items-center justify-center rounded-2xl bg-accent/10 text-accent">
              <Bot className="size-5" strokeWidth={1.9} />
            </div>
            <div>
              <p className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">
                Cooagents
              </p>
              <p className="text-[11px] uppercase tracking-[0.18em] text-muted-soft">
                运维控制台
              </p>
            </div>
          </div>

          <nav className="mt-8 flex flex-col gap-1">
            {navItems.map((item) => (
              <ShellNavLink key={item.to} item={item} />
            ))}
          </nav>

          <div className="mt-auto space-y-3">
            <div className="rounded-2xl border border-border-strong bg-panel-strong/40 p-4 text-sm text-muted">
              <p className="font-serif text-base font-medium leading-snug text-copy">
                章节般的运维节奏
              </p>
              <p className="mt-2 leading-relaxed">
                概览、Runs、主机管理与合并控制共享同一实时终端。
              </p>
            </div>
            <div className="flex items-center justify-between gap-3 rounded-2xl border border-border bg-panel px-4 py-3 text-xs text-muted">
              <div className="min-w-0 truncate">
                <p className="text-[10px] uppercase tracking-[0.22em] text-muted-soft">已登录</p>
                <p className="truncate text-sm text-copy">{user?.username ?? "-"}</p>
              </div>
              <button
                type="button"
                onClick={() => void logout()}
                className="inline-flex items-center gap-1 rounded-full border border-border-strong px-3 py-1.5 text-xs text-muted transition hover:border-accent/40 hover:text-accent"
              >
                <LogOut className="size-3.5" strokeWidth={1.8} />
                退出
              </button>
            </div>
          </div>
        </aside>

        <div className="flex min-h-[calc(100vh-2rem)] flex-1 flex-col gap-6">
          <header className="overflow-hidden rounded-[24px] border border-border bg-panel px-6 py-8 shadow-whisper md:px-10 md:py-10">
            <div className="flex flex-col gap-6">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-accent">
                    {meta.eyebrow}
                  </p>
                  <h1 className="mt-3 font-serif text-[2.25rem] font-medium leading-[1.15] tracking-tight text-copy md:text-[2.75rem]">
                    {meta.title}
                  </h1>
                  <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-muted md:text-base">
                    {meta.description}
                  </p>
                </div>

                <div className="hidden shrink-0 items-center gap-2 rounded-full border border-border-strong bg-panel-strong/50 px-4 py-2 text-[11px] uppercase tracking-[0.22em] text-muted md:flex">
                  <span className="size-1.5 rounded-full bg-success" />
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
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <ShellLayout />
      </RequireAuth>
    ),
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
