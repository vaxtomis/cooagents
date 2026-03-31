import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Bot,
  CirclePlay,
  GitMerge,
  LayoutDashboard,
  MoveRight,
  ScrollText,
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
import { EventLogPage } from "./pages/EventLogPage";
import { MergeQueuePage } from "./pages/MergeQueuePage";

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
  { to: "/events", label: "事件日志", icon: ScrollText },
];

function resolvePageMeta(pathname: string): PageMeta {
  if (pathname === "/") {
    return {
      title: "概览",
      eyebrow: "Dashboard Overview",
      description: "Phase 1 shell aligned to the approved Pencil layout for live stats, approvals, and host health.",
    };
  }

  if (pathname === "/runs") {
    return {
      title: "Runs",
      eyebrow: "Server-backed Queue",
      description: "The list surface is wired now so search, filtering, and pagination can plug into the real API next.",
    };
  }

  if (pathname.startsWith("/runs/")) {
    return {
      title: "Run Detail",
      eyebrow: "Live Run Timeline",
      description: "This route will grow into the SSE-backed operational view with artifacts, jobs, trace data, and actions.",
    };
  }

  if (pathname === "/agent-hosts") {
    return {
      title: "Agent 主机",
      eyebrow: "Phase 2 Placeholder",
      description: "Navigation, URL structure, and visual shell are stable now so the detailed host management UI can land later without churn.",
    };
  }

  if (pathname === "/merge-queue") {
    return {
      title: "Merge 队列",
      eyebrow: "Phase 2 Placeholder",
      description: "This route stays routable in phase 1 while the deeper merge queue controls and conflict handling UI are deferred.",
    };
  }

  return {
    title: "事件日志",
    eyebrow: "Phase 2 Placeholder",
    description: "The global events endpoint exists now; this route is reserved for the richer browsing and filtering UI in the next slice.",
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

function StatShellCard({ title, value, tone, icon }: { title: string; value: string; tone: "accent" | "warning" | "danger" | "success" | "muted"; icon: ReactNode }) {
  const toneClass = {
    accent: "text-accent",
    warning: "text-warning",
    danger: "text-danger",
    success: "text-success",
    muted: "text-muted",
  }[tone];

  return (
    <section className="rounded-[24px] border border-white/6 bg-panel p-5 shadow-panel">
      <p className="text-xs uppercase tracking-[0.24em] text-muted/80">{title}</p>
      <div className="mt-4 flex items-end justify-between gap-3">
        <div className="font-mono text-4xl font-bold text-white">{value}</div>
        <div className={`flex size-10 items-center justify-center rounded-2xl border border-white/6 bg-white/3 ${toneClass}`}>
          {icon}
        </div>
      </div>
    </section>
  );
}

function SurfaceCard({ title, kicker, children }: { title: string; kicker: string; children: ReactNode }) {
  return (
    <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[11px] uppercase tracking-[0.3em] text-muted/75">{kicker}</p>
          <h2 className="mt-2 text-lg font-semibold text-white">{title}</h2>
        </div>
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function StageStrip() {
  return (
    <div className="grid grid-cols-14 gap-1.5">
      {Array.from({ length: 14 }, (_, index) => {
        const active = index < 6;
        const current = index === 5;
        const className = current
          ? "bg-accent shadow-[0_0_24px_rgba(168,85,247,0.45)]"
          : active
            ? "bg-success"
            : "bg-white/8";

        return <span key={index} className={`h-1.5 rounded-full ${className}`} />;
      })}
    </div>
  );
}

function OverviewShellPage() {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-5">
        <StatShellCard title="运行中" value="05" tone="accent" icon={<LayoutDashboard className="size-4.5" strokeWidth={1.8} />} />
        <StatShellCard title="待审批" value="03" tone="warning" icon={<CirclePlay className="size-4.5" strokeWidth={1.8} />} />
        <StatShellCard title="失败中" value="02" tone="danger" icon={<MoveRight className="size-4.5" strokeWidth={1.8} />} />
        <StatShellCard title="主机" value="10" tone="success" icon={<Server className="size-4.5" strokeWidth={1.8} />} />
        <StatShellCard title="最近 24h" value="28" tone="muted" icon={<ScrollText className="size-4.5" strokeWidth={1.8} />} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <SurfaceCard title="活跃任务" kicker="Queue Snapshot">
          <div className="space-y-3">
            {[
              ["PROJ-442", "需求澄清与工时分解", "运行中"],
              ["PROJ-449", "设计评审与验收文案准备", "设计中"],
              ["PROJ-451", "回归修复与分支整理", "开发中"],
            ].map(([ticket, summary, status]) => (
              <article key={ticket} className="rounded-2xl border border-white/6 bg-panel-strong/70 px-4 py-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-mono text-sm text-white">{ticket}</p>
                    <p className="mt-1 text-sm text-muted">{summary}</p>
                  </div>
                  <span className="rounded-full border border-accent/20 bg-accent/10 px-3 py-1 text-xs text-accent">{status}</span>
                </div>
                <div className="mt-4">
                  <StageStrip />
                </div>
              </article>
            ))}
          </div>
        </SurfaceCard>

        <div className="space-y-4">
          <SurfaceCard title="待审批" kicker="Action Queue">
            <div className="space-y-3">
              <div className="rounded-2xl border border-warning/18 bg-warning/7 p-4">
                <p className="font-mono text-sm text-white">PROJ-399</p>
                <p className="mt-1 text-sm text-muted">需求说明书等待确认</p>
                <div className="mt-4 flex gap-2">
                  <button className="rounded-full bg-danger px-3 py-1.5 text-xs font-medium text-white">Reject</button>
                  <button className="rounded-full bg-success px-3 py-1.5 text-xs font-medium text-white">Approve</button>
                </div>
              </div>
            </div>
          </SurfaceCard>

          <SurfaceCard title="Agent 主机" kicker="Pool Health">
            <div className="space-y-3 text-sm text-muted">
              <div className="flex items-center justify-between rounded-2xl border border-white/6 bg-panel-strong/70 px-4 py-3">
                <div>
                  <p className="text-white">codex-worker-01</p>
                  <p className="mt-1 text-xs">Linux · West 1</p>
                </div>
                <span className="rounded-full border border-success/20 bg-success/10 px-3 py-1 text-xs text-success">active</span>
              </div>
              <div className="flex items-center justify-between rounded-2xl border border-white/6 bg-panel-strong/70 px-4 py-3">
                <div>
                  <p className="text-white">claude-host-02</p>
                  <p className="mt-1 text-xs">macOS · East 2</p>
                </div>
                <span className="rounded-full border border-accent/20 bg-accent/10 px-3 py-1 text-xs text-accent">busy 2/4</span>
              </div>
            </div>
          </SurfaceCard>
        </div>
      </div>
    </div>
  );
}

function RunsShellPage() {
  return (
    <div className="space-y-4">
      <SurfaceCard title="Run filters" kicker="Query Controls">
        <div className="flex flex-wrap gap-3">
          {[
            "status: running",
            "stage: DEV_EXEC",
            "sort: updated_at desc",
            "ticket: PROJ",
          ].map((token) => (
            <span key={token} className="rounded-full border border-white/8 bg-panel-strong px-4 py-2 text-sm text-muted">
              {token}
            </span>
          ))}
        </div>
      </SurfaceCard>

      <SurfaceCard title="Runs table shell" kicker="List Layout">
        <div className="overflow-hidden rounded-[22px] border border-white/6 bg-panel-strong/70">
          <div className="grid grid-cols-[1.1fr_1.3fr_1fr_1fr_0.8fr] gap-3 border-b border-white/6 px-4 py-3 text-[11px] uppercase tracking-[0.24em] text-muted/75">
            <span>Ticket</span>
            <span>Summary</span>
            <span>Stage</span>
            <span>Status</span>
            <span>Updated</span>
          </div>
          {[
            ["PROJ-442", "Refactor dashboard shell", "DEV_EXEC", "running", "2m ago"],
            ["PROJ-439", "Design review playback", "DESIGN_REVIEW", "waiting", "8m ago"],
            ["PROJ-430", "Incident clean-up", "DONE", "completed", "17m ago"],
          ].map((row) => (
            <div key={row[0]} className="grid grid-cols-[1.1fr_1.3fr_1fr_1fr_0.8fr] gap-3 border-t border-white/5 px-4 py-4 text-sm">
              <span className="font-mono text-white">{row[0]}</span>
              <span className="text-muted">{row[1]}</span>
              <span className="text-white">{row[2]}</span>
              <span className="text-muted">{row[3]}</span>
              <span className="text-muted">{row[4]}</span>
            </div>
          ))}
        </div>
      </SurfaceCard>
    </div>
  );
}

function RunDetailShellPage() {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-4">
        <SurfaceCard title="Run summary shell" kicker="Primary Context">
          <div className="grid gap-3 md:grid-cols-3">
            {[
              ["Ticket", "PROJ-442"],
              ["Current stage", "DEV_EXEC"],
              ["Status", "running"],
            ].map(([label, value]) => (
              <div key={label} className="rounded-2xl border border-white/6 bg-panel-strong/80 p-4">
                <p className="text-xs uppercase tracking-[0.24em] text-muted/75">{label}</p>
                <p className="mt-3 font-mono text-base text-white">{value}</p>
              </div>
            ))}
          </div>
          <div className="mt-4">
            <StageStrip />
          </div>
        </SurfaceCard>

        <SurfaceCard title="Artifacts, jobs, and trace" kicker="Detail Modules">
          <div className="grid gap-3 md:grid-cols-2">
            {[
              "Artifacts list with content and diff drawers",
              "Jobs list with live output panes",
              "Trace timeline with level and source filters",
              "Approval and cancel actions bound to the run",
            ].map((item) => (
              <div key={item} className="rounded-2xl border border-white/6 bg-panel-strong/80 p-4 text-sm text-muted">
                {item}
              </div>
            ))}
          </div>
        </SurfaceCard>
      </div>

      <div className="space-y-4">
        <SurfaceCard title="Live connection" kicker="SSE Status">
          <div className="rounded-2xl border border-success/18 bg-success/7 p-4 text-sm text-muted">
            <div className="flex items-center justify-between gap-3">
              <span className="text-white">event stream</span>
              <span className="rounded-full border border-success/20 bg-success/10 px-3 py-1 text-xs text-success">connected</span>
            </div>
            <p className="mt-3">The final page will revalidate run, trace, jobs, and artifact sections on incoming run-specific events.</p>
          </div>
        </SurfaceCard>
      </div>
    </div>
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
              <p className="text-xs text-muted">phase 1 dashboard</p>
            </div>
          </div>

          <nav className="mt-8 flex flex-col gap-1.5">
            {navItems.map((item) => (
              <ShellNavLink key={item.to} item={item} />
            ))}
          </nav>

          <div className="mt-auto rounded-[24px] border border-white/6 bg-panel p-4 text-sm text-muted">
            <p className="text-white">Frontend shell ready</p>
            <p className="mt-2">Real Dashboard, Runs, and Run Detail pages will replace these placeholders in the next tasks.</p>
          </div>
        </aside>

        <div className="flex min-h-[calc(100vh-1.5rem)] flex-1 flex-col gap-4">
          <header className="overflow-hidden rounded-[30px] border border-white/6 bg-[radial-gradient(circle_at_top_left,rgba(168,85,247,0.18),transparent_38%),linear-gradient(180deg,rgba(24,24,27,0.96),rgba(15,15,18,0.92))] p-5 shadow-shell md:p-7">
            <div className="flex flex-col gap-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-[11px] uppercase tracking-[0.32em] text-accent/85">{meta.eyebrow}</p>
                  <h1 className="mt-3 text-3xl font-semibold tracking-tight text-white md:text-[2.1rem]">{meta.title}</h1>
                  <p className="mt-3 max-w-3xl text-sm leading-6 text-muted md:text-[15px]">{meta.description}</p>
                </div>

                <div className="hidden rounded-full border border-white/8 bg-white/4 px-4 py-2 text-xs uppercase tracking-[0.24em] text-muted md:flex md:items-center md:gap-2">
                  <span className="size-2 rounded-full bg-success" />
                  shell online
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
      { path: "runs", element: <RunsShellPage /> },
      { path: "runs/:runId", element: <RunDetailShellPage /> },
      { path: "agent-hosts", element: <AgentHostsPage /> },
      { path: "merge-queue", element: <MergeQueuePage /> },
      { path: "events", element: <EventLogPage /> },
    ],
  },
];

export function createAppRouter(initialEntries?: string[]) {
  const options = {};
  return initialEntries
    ? createMemoryRouter(routes, { initialEntries, ...options })
    : createBrowserRouter(routes, options);
}

export const appRouter = createAppRouter();

