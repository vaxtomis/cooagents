import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import useSWR from "swr";
import { createDesignWork, listDesignWorks } from "../api/designWorks";
import { listDesignDocs } from "../api/designDocs";
import { createDevWork, listDevWorks } from "../api/devWorks";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { archiveWorkspace, getWorkspace } from "../api/workspaces";
import { EmptyState, SectionPanel } from "../components/SectionPanel";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type {
  AgentKind,
  DesignDoc,
  DesignWork,
  DevWork,
  WorkspaceEvent,
} from "../types";

const SLUG_RE = /^[a-z0-9](?:[a-z0-9]|-(?!-)){0,61}[a-z0-9]$|^[a-z0-9]$/;
const EVENTS_PAGE_SIZE = 50;
const EVENTS_MAX_LIMIT = 200;

const TAB_IDS = ["designs", "devworks", "events"] as const;
type TabId = (typeof TAB_IDS)[number];

const TAB_LABELS: Record<TabId, string> = {
  designs: "设计工作",
  devworks: "开发工作",
  events: "事件",
};

function DesignWorkRow({ workspaceId, dw }: { workspaceId: string; dw: DesignWork }) {
  return (
    <Link
      className="flex flex-col gap-2 rounded-2xl border border-border bg-panel-strong/80 p-4 transition hover:border-accent/30"
      to={`/workspaces/${workspaceId}/design-works/${dw.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="truncate font-medium text-copy">{dw.title ?? dw.sub_slug ?? dw.id}</p>
        <StatusBadge status={dw.current_state} />
      </div>
      <p className="text-xs text-muted">mode={dw.mode} · loop={dw.loop}</p>
      {dw.output_design_doc_id ? (
        <p className="truncate font-mono text-[11px] text-muted">doc: {dw.output_design_doc_id}</p>
      ) : null}
    </Link>
  );
}

function DesignDocRow({ doc }: { doc: DesignDoc }) {
  return (
    <article className="rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-mono text-sm text-copy">
            {doc.slug}@{doc.version}
          </p>
          <p className="mt-1 truncate text-xs text-muted">{doc.path}</p>
        </div>
        <StatusBadge status={doc.status} />
      </div>
      {doc.needs_frontend_mockup ? (
        <p className="mt-3 rounded-lg border border-warning/25 bg-warning/10 px-2 py-1 text-[11px] text-warning">
          需要前端 mockup
        </p>
      ) : null}
    </article>
  );
}

function DesignWorkCreateForm({
  workspaceId,
  onCreated,
}: {
  workspaceId: string;
  onCreated: (dw: DesignWork) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [userInput, setUserInput] = useState("");
  const [needsFrontendMockup, setNeedsFrontendMockup] = useState(false);
  const [agent, setAgent] = useState<AgentKind>("claude");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedTitle = title.trim();
    const trimmedSlug = slug.trim();
    const trimmedInput = userInput.trim();
    if (!trimmedTitle) return setError("请填写标题");
    if (!SLUG_RE.test(trimmedSlug)) return setError("slug 必须为 kebab-case");
    if (trimmedInput.length === 0) return setError("请填写用户输入");
    setError(null);
    setSubmitting(true);
    try {
      const created = await createDesignWork({
        workspace_id: workspaceId,
        title: trimmedTitle,
        slug: trimmedSlug,
        user_input: trimmedInput,
        mode: "new",
        needs_frontend_mockup: needsFrontendMockup,
        agent,
      });
      setTitle("");
      setSlug("");
      setUserInput("");
      setNeedsFrontendMockup(false);
      setOpen(false);
      onCreated(created);
    } catch (err) {
      setError(extractError(err, "创建失败"));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button
        className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-copy transition hover:border-accent/40 hover:text-accent"
        onClick={() => setOpen(true)}
        type="button"
      >
        新建 DesignWork
      </button>
    );
  }

  return (
    <form className="space-y-3 rounded-2xl border border-border bg-panel-strong/60 p-4" onSubmit={submit}>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1 text-xs text-muted">
          <span>标题</span>
          <input
            className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none"
            onChange={(event) => setTitle(event.target.value)}
            value={title}
          />
        </label>
        <label className="space-y-1 text-xs text-muted">
          <span>Slug</span>
          <input
            className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 font-mono text-sm text-copy outline-none"
            onChange={(event) => setSlug(event.target.value.toLowerCase())}
            placeholder="feature-foo"
            value={slug}
          />
        </label>
      </div>
      <label className="block space-y-1 text-xs text-muted">
        <span>用户输入</span>
        <textarea
          className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none"
          onChange={(event) => setUserInput(event.target.value)}
          rows={4}
          value={userInput}
        />
      </label>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="flex items-center gap-2 text-xs text-muted">
          <input
            checked={needsFrontendMockup}
            onChange={(event) => setNeedsFrontendMockup(event.target.checked)}
            type="checkbox"
          />
          <span>需要前端 mockup</span>
        </label>
        <label className="space-y-1 text-xs text-muted">
          <span>Agent</span>
          <select
            className="w-full rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-sm text-copy outline-none [&_option]:bg-panel-strong"
            onChange={(event) => setAgent(event.target.value as AgentKind)}
            value={agent}
          >
            <option value="claude">Claude</option>
            <option value="codex">Codex</option>
          </select>
        </label>
      </div>
      <div className="space-y-1">
        <p className="text-[11px] text-muted-soft">v1 仅支持 mode=new（新增设计）。</p>
      </div>
      {error ? <p className="text-xs text-danger">{error}</p> : null}
      <div className="flex gap-2">
        <button
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] disabled:opacity-60"
          disabled={submitting}
          type="submit"
        >
          {submitting ? "创建中..." : "提交"}
        </button>
        <button
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs text-muted transition hover:text-copy"
          onClick={() => setOpen(false)}
          type="button"
        >
          取消
        </button>
      </div>
    </form>
  );
}

function DevWorkRow({ workspaceId, dv }: { workspaceId: string; dv: DevWork }) {
  return (
    <Link
      className="flex flex-col gap-2 rounded-2xl border border-border bg-panel-strong/80 p-4 transition hover:border-accent/30"
      to={`/workspaces/${workspaceId}/dev-works/${dv.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="truncate font-mono text-xs text-copy">{dv.id}</p>
        <StatusBadge status={dv.current_step} />
      </div>
      <p className="text-xs text-muted">
        迭代轮次 {dv.iteration_rounds} · 分数 {dv.last_score ?? "-"}
      </p>
      <p className="truncate font-mono text-[11px] text-muted">doc: {dv.design_doc_id}</p>
    </Link>
  );
}

function DevWorkCreateForm({
  workspaceId,
  publishedDocs,
  onCreated,
}: {
  workspaceId: string;
  publishedDocs: DesignDoc[];
  onCreated: (dv: DevWork) => void;
}) {
  const [open, setOpen] = useState(false);
  const [designDocId, setDesignDocId] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [prompt, setPrompt] = useState("");
  const [agent, setAgent] = useState<AgentKind>("claude");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const disabled = publishedDocs.length === 0;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!designDocId) return setError("请选择已发布的 DesignDoc");
    if (!repoPath.trim()) return setError("请填写 repo_path");
    if (!prompt.trim()) return setError("请填写 prompt");
    setError(null);
    setSubmitting(true);
    try {
      const created = await createDevWork({
        workspace_id: workspaceId,
        design_doc_id: designDocId,
        repo_path: repoPath.trim(),
        prompt: prompt.trim(),
        agent,
      });
      setDesignDocId("");
      setRepoPath("");
      setPrompt("");
      setOpen(false);
      onCreated(created);
    } catch (err) {
      setError(extractError(err, "创建失败"));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <div className="flex items-center gap-2">
        <button
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-copy transition hover:border-accent/40 hover:text-accent disabled:opacity-40"
          disabled={disabled}
          onClick={() => setOpen(true)}
          title={disabled ? "需要至少一个已发布 DesignDoc" : undefined}
          type="button"
        >
          新建 DevWork
        </button>
      </div>
    );
  }

  return (
    <form className="space-y-3 rounded-2xl border border-border bg-panel-strong/60 p-4" onSubmit={submit}>
      <label className="block space-y-1 text-xs text-muted">
        <span>DesignDoc（已发布）</span>
        <select
          className="w-full rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-sm text-copy outline-none [&_option]:bg-panel-strong"
          onChange={(event) => setDesignDocId(event.target.value)}
          value={designDocId}
        >
          <option value="">请选择</option>
          {publishedDocs.map((doc) => (
            <option key={doc.id} value={doc.id}>
              {doc.slug}@{doc.version}
            </option>
          ))}
        </select>
      </label>
      <label className="block space-y-1 text-xs text-muted">
        <span>repo_path</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 font-mono text-sm text-copy outline-none"
          onChange={(event) => setRepoPath(event.target.value)}
          placeholder="/absolute/path/to/repo"
          value={repoPath}
        />
      </label>
      <label className="block space-y-1 text-xs text-muted">
        <span>prompt</span>
        <textarea
          className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none"
          onChange={(event) => setPrompt(event.target.value)}
          rows={4}
          value={prompt}
        />
      </label>
      <label className="block space-y-1 text-xs text-muted">
        <span>Agent</span>
        <select
          className="w-full rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-sm text-copy outline-none [&_option]:bg-panel-strong"
          onChange={(event) => setAgent(event.target.value as AgentKind)}
          value={agent}
        >
          <option value="claude">Claude</option>
          <option value="codex">Codex</option>
        </select>
      </label>
      {error ? <p className="text-xs text-danger">{error}</p> : null}
      <div className="flex gap-2">
        <button
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] disabled:opacity-60"
          disabled={submitting}
          type="submit"
        >
          {submitting ? "创建中..." : "提交"}
        </button>
        <button
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs text-muted transition hover:text-copy"
          onClick={() => setOpen(false)}
          type="button"
        >
          取消
        </button>
      </div>
    </form>
  );
}

function EventRow({ event }: { event: WorkspaceEvent }) {
  return (
    <article className="rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-sm text-copy">{event.event_name}</p>
        <span className="text-xs text-muted">{event.ts}</span>
      </div>
      {event.correlation_id ? (
        <p className="mt-2 text-xs text-muted">corr: {event.correlation_id}</p>
      ) : null}
      {event.payload ? (
        <pre className="mt-3 overflow-x-auto rounded-2xl bg-panel-deep p-3 text-[11px] text-copy whitespace-pre-wrap">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      ) : null}
    </article>
  );
}

export function WorkspaceDetailPage() {
  const { wsId } = useParams();
  if (!wsId) {
    return (
      <section className="rounded-[32px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="font-serif text-xl font-medium text-copy">缺少 Workspace ID</h2>
      </section>
    );
  }
  return <WorkspaceDetailContent workspaceId={wsId} />;
}

function WorkspaceDetailContent({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const polling = useWorkspacePolling();
  const [tab, setTab] = useState<TabId>("designs");
  const [eventLimit, setEventLimit] = useState(EVENTS_PAGE_SIZE);
  const [archivePending, setArchivePending] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  const workspaceQuery = useSWR(
    ["workspace", workspaceId],
    () => getWorkspace(workspaceId),
    polling,
  );
  const designWorksQuery = useSWR(
    ["design-works", workspaceId],
    () => listDesignWorks(workspaceId),
    polling,
  );
  const allDocsQuery = useSWR(
    ["design-docs", workspaceId],
    () => listDesignDocs(workspaceId),
    polling,
  );
  const publishedDocsQuery = useSWR(
    ["design-docs", workspaceId, "published"],
    () => listDesignDocs(workspaceId, "published"),
    polling,
  );
  const devWorksQuery = useSWR(
    ["dev-works", workspaceId],
    () => listDevWorks(workspaceId),
    polling,
  );
  const eventsQuery = useSWR(
    ["workspace-events", workspaceId, eventLimit],
    () => listWorkspaceEvents(workspaceId, { limit: eventLimit }),
    polling,
  );

  const designWorks = useMemo(() => designWorksQuery.data ?? [], [designWorksQuery.data]);
  const docs = useMemo(() => allDocsQuery.data ?? [], [allDocsQuery.data]);
  const publishedDocs = useMemo(
    () => publishedDocsQuery.data ?? [],
    [publishedDocsQuery.data],
  );
  const devWorks = useMemo(() => devWorksQuery.data ?? [], [devWorksQuery.data]);
  const events = eventsQuery.data?.events ?? [];
  const hasMore = eventsQuery.data?.pagination.has_more ?? false;

  async function handleArchive() {
    if (typeof window !== "undefined" && !window.confirm("确认归档此 Workspace？")) {
      return;
    }
    setArchivePending(true);
    setArchiveError(null);
    try {
      await archiveWorkspace(workspaceId);
      navigate("/workspaces");
    } catch (err) {
      setArchiveError(extractError(err, "归档失败"));
    } finally {
      setArchivePending(false);
    }
  }

  const workspace = workspaceQuery.data;

  return (
    <div className="space-y-6">
      <SectionPanel
        actions={
          workspace && workspace.status === "active" ? (
            <button
              className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-muted transition hover:border-danger/30 hover:text-danger disabled:opacity-50"
              disabled={archivePending}
              onClick={() => void handleArchive()}
              type="button"
            >
              {archivePending ? "归档中..." : "归档"}
            </button>
          ) : undefined
        }
        kicker="工作区详情"
        title={workspace?.title ?? "加载中..."}
      >
        {workspace ? (
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
            <StatusBadge status={workspace.status} />
            <span className="font-mono">{workspace.slug}</span>
            <span>更新于 {workspace.updated_at}</span>
            <span className="truncate">{workspace.root_path}</span>
          </div>
        ) : null}
        {archiveError ? <p className="mt-3 text-xs text-danger">{archiveError}</p> : null}
      </SectionPanel>

      <SectionPanel
        actions={
          <div className="flex gap-2" role="tablist" aria-label="详情 tabs">
            {TAB_IDS.map((id) => {
              const selected = tab === id;
              return (
                <button
                  aria-controls={`workspace-tabpanel-${id}`}
                  aria-selected={selected}
                  className={[
                    "rounded-full border px-3 py-1.5 text-xs font-medium transition",
                    selected
                      ? "border-accent/30 bg-accent/15 text-copy"
                      : "border-border-strong bg-panel-strong/50 text-muted hover:border-copy/20 hover:text-copy",
                  ].join(" ")}
                  id={`workspace-tab-${id}`}
                  key={id}
                  onClick={() => setTab(id)}
                  role="tab"
                  tabIndex={selected ? 0 : -1}
                  type="button"
                >
                  {TAB_LABELS[id]}
                </button>
              );
            })}
          </div>
        }
        kicker="详情面板"
        title={TAB_LABELS[tab]}
      >
        {tab === "designs" ? (
          <div
            aria-labelledby="workspace-tab-designs"
            className="grid gap-6 lg:grid-cols-2"
            id="workspace-tabpanel-designs"
            role="tabpanel"
          >
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs uppercase tracking-[0.24em] text-muted-soft">DesignWorks</p>
                <DesignWorkCreateForm onCreated={() => void designWorksQuery.mutate()} workspaceId={workspaceId} />
              </div>
              {designWorks.length === 0 ? (
                <EmptyState copy="暂无 DesignWork。" />
              ) : (
                <div className="space-y-3">
                  {designWorks.map((dw) => (
                    <DesignWorkRow dw={dw} key={dw.id} workspaceId={workspaceId} />
                  ))}
                </div>
              )}
            </div>
            <div className="space-y-3">
              <p className="text-xs uppercase tracking-[0.24em] text-muted-soft">DesignDocs</p>
              {docs.length === 0 ? (
                <EmptyState copy="暂无 DesignDoc。" />
              ) : (
                <div className="space-y-3">
                  {docs.map((doc) => (
                    <DesignDocRow doc={doc} key={doc.id} />
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}

        {tab === "devworks" ? (
          <div
            aria-labelledby="workspace-tab-devworks"
            className="space-y-4"
            id="workspace-tabpanel-devworks"
            role="tabpanel"
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs uppercase tracking-[0.24em] text-muted-soft">DevWorks</p>
              <DevWorkCreateForm
                onCreated={() => void devWorksQuery.mutate()}
                publishedDocs={publishedDocs}
                workspaceId={workspaceId}
              />
            </div>
            {devWorks.length === 0 ? (
              <EmptyState copy="暂无 DevWork。" />
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {devWorks.map((dv) => (
                  <DevWorkRow dv={dv} key={dv.id} workspaceId={workspaceId} />
                ))}
              </div>
            )}
          </div>
        ) : null}

        {tab === "events" ? (
          <div
            aria-labelledby="workspace-tab-events"
            className="space-y-3"
            id="workspace-tabpanel-events"
            role="tabpanel"
          >
            {events.length === 0 ? (
              <EmptyState copy="暂无事件。" />
            ) : (
              <div className="space-y-3">
                {events.map((event) => (
                  <EventRow event={event} key={event.event_id} />
                ))}
              </div>
            )}
            {hasMore ? (
              <button
                className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs text-muted transition hover:text-copy"
                onClick={() => setEventLimit((current) => Math.min(current + EVENTS_PAGE_SIZE, EVENTS_MAX_LIMIT))}
                type="button"
              >
                加载更多
              </button>
            ) : null}
          </div>
        ) : null}
      </SectionPanel>
    </div>
  );
}
