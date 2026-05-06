import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Bot,
  ChevronsLeft,
  ChevronsRight,
  Database,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  Server,
} from "lucide-react";
import useSWR from "swr";
import {
  NavLink,
  Navigate,
  Outlet,
  createBrowserRouter,
  createMemoryRouter,
  useLocation,
} from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { listWorkspaces } from "./api/workspaces";
import { useWorkspacePolling } from "./hooks/useWorkspacePolling";
import { AgentHostsPage } from "./pages/AgentHostsPage";
import { CrossWorkspaceDevWorkPage } from "./pages/CrossWorkspaceDevWorkPage";
import { DesignWorkPage } from "./pages/DesignWorkPage";
import { DevWorkPage } from "./pages/DevWorkPage";
import { LoginPage } from "./pages/LoginPage";
import { RepoDetailPage } from "./pages/RepoDetailPage";
import { ReposPage } from "./pages/ReposPage";
import { WorkspaceDashboardPage } from "./pages/WorkspaceDashboardPage";
import { WorkspaceDetailPage } from "./pages/WorkspaceDetailPage";
import { WorkspacesPage } from "./pages/WorkspacesPage";

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

const DESKTOP_SIDEBAR_STORAGE_KEY = "cooagents.desktopSidebarCollapsed";

const primaryNavItems: NavItem[] = [
  { to: "/", label: "总览", icon: LayoutDashboard, end: true },
  { to: "/workspaces", label: "Workspace", icon: FolderKanban },
];

const operationsNavItems: NavItem[] = [
  { to: "/agent-hosts", label: "Agent Host 管理", icon: Server },
  { to: "/repos", label: "仓库注册表", icon: Database },
];

function readSidebarCollapsedPreference() {
  if (typeof window === "undefined") {
    return false;
  }
  return window.localStorage.getItem(DESKTOP_SIDEBAR_STORAGE_KEY) === "1";
}

function resolvePageMeta(pathname: string): PageMeta {
  if (pathname === "/") {
    return {
      title: "运行总览",
      eyebrow: "Workspace 脉搏",
      description:
        "集中查看活跃 Workspace、人工介入、一次性准出率和迭代深度。",
    };
  }

  if (pathname === "/workspaces") {
    return {
      title: "Workspace 目录",
      eyebrow: "主工作区",
      description:
        "创建、筛选和重新进入 Workspace。",
    };
  }

  if (/^\/workspaces\/[^/]+\/design-works\/[^/]+$/.test(pathname)) {
    return {
      title: "DesignWork 详情",
      eyebrow: "Workspace 执行",
      description:
        "查看状态推进、设计文档产物、校验缺口和审核历史。",
    };
  }

  if (/^\/workspaces\/[^/]+\/dev-works\/[^/]+$/.test(pathname)) {
    return {
      title: "DevWork 详情",
      eyebrow: "Workspace 执行",
      description:
        "查看开发进度、迭代文档、评审记录和闸门动作。",
    };
  }

  if (/^\/workspaces\/[^/]+$/.test(pathname)) {
    return {
      title: "Workspace 工作台",
      eyebrow: "设计 / 开发 / 事件",
      description:
        "在一个 Workspace 内处理设计工作、开发工作和事件流。",
    };
  }

  if (pathname === "/dev-works") {
    return {
      title: "跨 Workspace DevWork",
      eyebrow: "全局视图",
      description:
        "跨活跃 Workspace 扫描开发工作状态。",
    };
  }

  if (pathname === "/agent-hosts") {
    return {
      title: "Agent Host 管理",
      eyebrow: "执行基础设施",
      description:
        "登记执行节点、查看健康状态，并维护远端 Agent Host 配置。",
    };
  }

  if (pathname === "/repos") {
    return {
      title: "仓库注册表",
      eyebrow: "共享基础设施",
      description:
        "管理仓库登记、fetch 健康度和仓库元数据。",
    };
  }

  if (/^\/repos\/[^/]+$/.test(pathname)) {
    return {
      title: "仓库详情",
      eyebrow: "代码检查器",
      description:
        "浏览分支、目录树、文件内容和提交历史。",
    };
  }

  return { title: "", eyebrow: "", description: "" };
}

function ShellNavLink({
  item,
  compact = false,
  collapsed = false,
}: {
  item: NavItem;
  compact?: boolean;
  collapsed?: boolean;
}) {
  const Icon = item.icon;

  return (
    <NavLink
      aria-label={item.label}
      end={item.end}
      to={item.to}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) => {
        const base = compact
          ? "inline-flex min-w-fit items-center gap-2 rounded-full border px-3 py-2 text-sm transition"
          : collapsed
            ? "flex size-11 items-center justify-center rounded-xl border text-sm transition"
            : "flex items-center gap-3 rounded-xl border px-3 py-2.5 text-sm transition";
        const state = isActive
          ? "border-[color:var(--color-ring-warm)] bg-panel text-copy shadow-[0_0_0_1px_var(--color-ring-warm)]"
          : "border-transparent text-muted hover:border-border hover:bg-panel-strong/60 hover:text-copy";
        return `${base} ${state}`;
      }}
    >
      <Icon className="size-4 shrink-0" strokeWidth={1.8} />
      {!collapsed ? <span>{item.label}</span> : null}
    </NavLink>
  );
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-void text-muted">
        <span className="text-sm">正在加载会话...</span>
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
  const polling = useWorkspacePolling();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsedPreference);
  const workspacesQuery = useSWR(["shell-workspaces", "active"], () => listWorkspaces("active"), polling);
  const recentWorkspaces = (workspacesQuery.data ?? []).slice(0, 5);

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(
        DESKTOP_SIDEBAR_STORAGE_KEY,
        sidebarCollapsed ? "1" : "0",
      );
    }
  }, [sidebarCollapsed]);

  return (
    <div className="min-h-screen bg-void text-copy">
      <div className="flex min-h-screen w-full gap-4 px-3 py-3 md:px-4 md:py-4">
        <aside
          className={`hidden shrink-0 flex-col rounded-2xl border border-border bg-panel shadow-whisper md:flex ${
            sidebarCollapsed
              ? "w-[88px] items-center px-3 py-4"
              : "w-[232px] p-4"
          }`}
        >
          <div
            className={`flex w-full px-1 pb-2 ${
              sidebarCollapsed
                ? "flex-col items-center gap-3"
                : "items-start justify-between gap-3"
            }`}
          >
            <div className={`flex items-center ${sidebarCollapsed ? "justify-center" : "gap-3"}`}>
              <div className="flex size-10 items-center justify-center rounded-2xl bg-accent/10 text-accent">
                <Bot className="size-5" strokeWidth={1.9} />
              </div>
              {!sidebarCollapsed ? (
                <div>
                  <p className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">
                    Cooagents
                  </p>
                  <p className="text-[11px] uppercase tracking-[0.18em] text-muted-soft">
                    Workspace 控制台
                  </p>
                </div>
              ) : null}
            </div>
            <button
              aria-expanded={!sidebarCollapsed}
              aria-label={sidebarCollapsed ? "展开侧栏" : "折叠侧栏"}
              className="inline-flex size-9 items-center justify-center rounded-lg border border-border-strong bg-panel-strong/50 text-muted transition hover:border-accent/40 hover:text-accent"
              onClick={() => setSidebarCollapsed((current) => !current)}
              title={sidebarCollapsed ? "展开侧栏" : "折叠侧栏"}
              type="button"
            >
              {sidebarCollapsed ? (
                <ChevronsRight className="size-4" strokeWidth={1.8} />
              ) : (
                <ChevronsLeft className="size-4" strokeWidth={1.8} />
              )}
            </button>
          </div>

          <div className={`w-full ${sidebarCollapsed ? "mt-6 space-y-4" : "mt-8 space-y-6"}`}>
            <div>
              {!sidebarCollapsed ? (
                <p className="mb-2 px-1 text-[11px] uppercase tracking-[0.22em] text-muted-soft">
                  主导航
                </p>
              ) : null}
              <nav className={`flex ${sidebarCollapsed ? "flex-col items-center gap-2" : "flex-col gap-1"}`}>
                {primaryNavItems.map((item) => (
                  <ShellNavLink key={item.to} item={item} collapsed={sidebarCollapsed} />
                ))}
              </nav>
            </div>

            <div>
              {!sidebarCollapsed ? (
                <p className="mb-2 px-1 text-[11px] uppercase tracking-[0.22em] text-muted-soft">
                  全局资源
                </p>
              ) : null}
              <nav className={`flex ${sidebarCollapsed ? "flex-col items-center gap-2" : "flex-col gap-1"}`}>
                {operationsNavItems.map((item) => (
                  <ShellNavLink key={item.to} item={item} collapsed={sidebarCollapsed} />
                ))}
              </nav>
            </div>

            {!sidebarCollapsed ? (
              <div>
              <div className="mb-2 flex items-center justify-between gap-2 px-1">
                <p className="text-[11px] uppercase tracking-[0.22em] text-muted-soft">
                  最近 Workspace
                </p>
                <span className="text-[11px] text-muted-soft">
                  {workspacesQuery.data ? recentWorkspaces.length : "..."}
                </span>
              </div>
              <div className="space-y-2">
                {recentWorkspaces.length === 0 ? (
                  <div className="rounded-2xl border border-dashed border-border bg-panel-strong/35 px-4 py-4 text-xs text-muted">
                    暂无活跃 Workspace。
                  </div>
                ) : (
                  recentWorkspaces.map((workspace) => (
                    <NavLink
                      key={workspace.id}
                      to={`/workspaces/${workspace.id}`}
                      className="block rounded-2xl border border-border bg-panel-strong/55 px-4 py-3 transition hover:border-accent/30"
                    >
                      <p className="truncate text-sm font-medium text-copy">{workspace.title}</p>
                      <p className="mt-1 truncate font-mono text-[11px] text-muted">
                        {workspace.slug}
                      </p>
                    </NavLink>
                  ))
                )}
              </div>
              </div>
            ) : null}
          </div>

          <div className="mt-auto w-full">
            {sidebarCollapsed ? (
              <div className="flex flex-col items-center gap-3">
                <div
                  className="flex size-10 items-center justify-center rounded-2xl border border-border bg-panel text-sm font-medium text-copy"
                  title={user?.username ?? "-"}
                >
                  {(user?.username ?? "?").slice(0, 1).toUpperCase()}
                </div>
                <button
                  aria-label="退出"
                  className="inline-flex size-9 items-center justify-center rounded-lg border border-border-strong text-muted transition hover:border-accent/40 hover:text-accent"
                  onClick={() => void logout()}
                  title="退出"
                  type="button"
                >
                  <LogOut className="size-4" strokeWidth={1.8} />
                </button>
              </div>
            ) : (
              <div className="flex items-center justify-between gap-3 rounded-2xl border border-border bg-panel px-4 py-3 text-xs text-muted">
                <div className="min-w-0 truncate">
                  <p className="text-[10px] uppercase tracking-[0.22em] text-muted-soft">已登录</p>
                  <p className="truncate text-sm text-copy">{user?.username ?? "-"}</p>
                </div>
                <button
                  type="button"
                  onClick={() => void logout()}
                  className="inline-flex items-center gap-1 rounded-lg border border-border-strong px-3 py-1.5 text-xs text-muted transition hover:border-accent/40 hover:text-accent"
                >
                  <LogOut className="size-3.5" strokeWidth={1.8} />
                  退出
                </button>
              </div>
            )}
          </div>
        </aside>

        <div className="flex min-h-[calc(100vh-1.5rem)] min-w-0 flex-1 flex-col gap-4">
          <header className="overflow-hidden rounded-2xl border border-border bg-panel px-4 py-4 shadow-whisper md:px-5">
            <div className="flex flex-col gap-3">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-[11px] font-medium uppercase tracking-[0.22em] text-accent">
                    {meta.eyebrow}
                  </p>
                  <h1 className="mt-1 font-serif text-[1.55rem] font-medium leading-tight tracking-tight text-copy md:text-[1.9rem]">
                    {meta.title}
                  </h1>
                  <p className="mt-1 max-w-5xl text-sm leading-relaxed text-muted">
                    {meta.description}
                  </p>
                </div>

                <div className="hidden shrink-0 items-center gap-2 rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-2 text-[11px] uppercase tracking-[0.18em] text-muted md:flex">
                  <span className="size-1.5 rounded-full bg-success" />
                  实时
                </div>
              </div>

              <nav className="flex gap-2 overflow-x-auto pb-1 md:hidden">
                {[...primaryNavItems, ...operationsNavItems].map((item) => (
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
      { index: true, element: <WorkspaceDashboardPage /> },
      { path: "workspaces", element: <WorkspacesPage /> },
      { path: "workspaces/:wsId", element: <WorkspaceDetailPage /> },
      { path: "workspaces/:wsId/design-works/:dwId", element: <DesignWorkPage /> },
      { path: "workspaces/:wsId/dev-works/:dvId", element: <DevWorkPage /> },
      { path: "agent-hosts", element: <AgentHostsPage /> },
      { path: "dev-works", element: <CrossWorkspaceDevWorkPage /> },
      { path: "repos", element: <ReposPage /> },
      { path: "repos/:repoId", element: <RepoDetailPage /> },
    ],
  },
];

export function createAppRouter(initialEntries?: string[]) {
  return initialEntries
    ? createMemoryRouter(routes, { initialEntries })
    : createBrowserRouter(routes);
}

export const appRouter = createAppRouter();
