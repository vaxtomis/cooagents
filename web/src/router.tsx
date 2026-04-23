import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Bot,
  FolderKanban,
  GitBranch,
  LayoutDashboard,
  LogOut,
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
import { CrossWorkspaceDevWorkPage } from "./pages/CrossWorkspaceDevWorkPage";
import { DesignWorkPage } from "./pages/DesignWorkPage";
import { DevWorkPage } from "./pages/DevWorkPage";
import { LoginPage } from "./pages/LoginPage";
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

const navItems: NavItem[] = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/workspaces", label: "工作区域", icon: FolderKanban },
  { to: "/dev-works", label: "跨区域 DevWorks", icon: GitBranch },
];

function resolvePageMeta(pathname: string): PageMeta {
  if (pathname === "/") {
    return {
      title: "概览",
      eyebrow: "Workspace 仪表盘",
      description:
        "活跃 Workspace、人工介入、一次性准出率与平均循环轮次的聚合视图。",
    };
  }

  if (pathname === "/workspaces") {
    return {
      title: "工作区域",
      eyebrow: "Workspace 清单",
      description: "创建、筛选、归档 Workspace；点击进入详情。",
    };
  }

  if (/^\/workspaces\/[^/]+\/design-works\/[^/]+$/.test(pathname)) {
    return {
      title: "DesignWork 详情",
      eyebrow: "设计工作",
      description:
        "查看 D0-D7 流程、missing_sections、设计文档预览、tick/cancel 操作以及审核历史。",
    };
  }

  if (/^\/workspaces\/[^/]+\/dev-works\/[^/]+$/.test(pathname)) {
    return {
      title: "DevWork 详情",
      eyebrow: "开发工作",
      description:
        "Step1-5 进度、迭代设计文件、审核历史与闸门操作面板。",
    };
  }

  if (/^\/workspaces\/[^/]+$/.test(pathname)) {
    return {
      title: "Workspace 详情",
      eyebrow: "设计 / 开发 / 事件",
      description:
        "管理 DesignWork、DesignDoc、DevWork 与工作区事件。",
    };
  }

  if (pathname === "/dev-works") {
    return {
      title: "跨区域 DevWorks",
      eyebrow: "跨 Workspace 视图",
      description: "按 Workspace 分组展示所有 DevWork，仅供浏览。",
    };
  }

  return { title: "", eyebrow: "", description: "" };
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
      <div className="mx-auto flex min-h-screen w-full max-w-[1280px] gap-6 px-4 py-5 md:px-8 md:py-8">
        <aside className="hidden w-[256px] shrink-0 flex-col rounded-[32px] border border-border bg-panel p-6 shadow-whisper md:flex">
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
                Workspace 驱动的运维节奏
              </p>
              <p className="mt-2 leading-relaxed">
                概览、工作区域与跨区域 DevWorks 共享同一刷新节律。
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
                className="inline-flex items-center gap-1 rounded-lg border border-border-strong px-3 py-1.5 text-xs text-muted transition hover:border-accent/40 hover:text-accent"
              >
                <LogOut className="size-3.5" strokeWidth={1.8} />
                退出
              </button>
            </div>
          </div>
        </aside>

        <div className="flex min-h-[calc(100vh-2rem)] flex-1 flex-col gap-6">
          <header className="overflow-hidden rounded-[32px] border border-border bg-panel px-6 py-10 shadow-whisper md:px-12 md:py-12">
            <div className="flex flex-col gap-6">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-accent">
                    {meta.eyebrow}
                  </p>
                  <h1 className="mt-3 font-serif text-[2.5rem] font-medium leading-[1.15] tracking-tight text-copy md:text-[3.25rem]">
                    {meta.title}
                  </h1>
                  <p className="mt-5 max-w-2xl text-[15px] leading-relaxed text-muted md:text-base">
                    {meta.description}
                  </p>
                </div>

                <div className="hidden shrink-0 items-center gap-2 rounded-lg border border-border-strong bg-panel-strong/50 px-4 py-2 text-[11px] uppercase tracking-[0.22em] text-muted md:flex">
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
      { index: true, element: <WorkspaceDashboardPage /> },
      { path: "workspaces", element: <WorkspacesPage /> },
      { path: "workspaces/:wsId", element: <WorkspaceDetailPage /> },
      { path: "workspaces/:wsId/design-works/:dwId", element: <DesignWorkPage /> },
      { path: "workspaces/:wsId/dev-works/:dvId", element: <DevWorkPage /> },
      { path: "dev-works", element: <CrossWorkspaceDevWorkPage /> },
    ],
  },
];

export function createAppRouter(initialEntries?: string[]) {
  return initialEntries
    ? createMemoryRouter(routes, { initialEntries })
    : createBrowserRouter(routes);
}

export const appRouter = createAppRouter();
