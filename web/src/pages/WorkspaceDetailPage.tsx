import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import useSWR from "swr";
import { createDesignWork, listDesignWorkPage } from "../api/designWorks";
import { listDesignDocs } from "../api/designDocs";
import { createDevWork, listDevWorkPage } from "../api/devWorks";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { archiveWorkspace, getWorkspace } from "../api/workspaces";
import { AppDialog } from "../components/AppDialog";
import { PaginationControls } from "../components/PaginationControls";
import { EmptyState, SectionPanel } from "../components/SectionPanel";
import {
  MOUNT_NAME_RE,
  RepoRefsEditor,
  type RepoRefsEditorRow,
} from "../components/RepoRefsEditor";
import { SegmentedControl } from "../components/SegmentedControl";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type {
  AgentKind,
  DesignDoc,
  DesignWork,
  DevRepoRef,
  DevWork,
  RepoRef,
  WorkspaceEvent,
} from "../types";

const SLUG_RE = /^[a-z0-9](?:[a-z0-9]|-(?!-)){0,61}[a-z0-9]$|^[a-z0-9]$/;
const WORK_ITEM_PAGE_SIZE = 6;
const EVENTS_PAGE_SIZE = 20;

const TAB_IDS = ["designs", "devworks", "events"] as const;
type TabId = (typeof TAB_IDS)[number];

const TAB_LABELS: Record<TabId, string> = {
  designs: "设计工作",
  devworks: "开发工作",
  events: "事件流",
};

function DesignWorkRow({ workspaceId, dw }: { workspaceId: string; dw: DesignWork }) {
  return (
    <Link
      className="flex flex-col gap-2 rounded-xl border border-border bg-panel-strong/80 p-3 transition hover:border-accent/30 md:flex-row md:items-center md:justify-between"
      to={`/workspaces/${workspaceId}/design-works/${dw.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="truncate font-medium text-copy">{dw.title ?? dw.sub_slug ?? dw.id}</p>
        <StatusBadge status={dw.current_state} />
      </div>
      <p className="text-xs text-muted">模式 {dw.mode} / 循环 {dw.loop}</p>
      {dw.output_design_doc_id ? (
        <p className="truncate font-mono text-[11px] text-muted">文档 {dw.output_design_doc_id}</p>
      ) : null}
    </Link>
  );
}

function DesignDocRow({ doc }: { doc: DesignDoc }) {
  return (
    <article className="rounded-xl border border-border bg-panel-strong/80 p-3">
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
  onCancel,
}: {
  workspaceId: string;
  onCreated: (dw: DesignWork) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [userInput, setUserInput] = useState("");
  const [needsFrontendMockup, setNeedsFrontendMockup] = useState(false);
  const [agent, setAgent] = useState<AgentKind>("claude");
  const [showRepos, setShowRepos] = useState(false);
  const [repoRefs, setRepoRefs] = useState<RepoRefsEditorRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedTitle = title.trim();
    const trimmedSlug = slug.trim();
    const trimmedInput = userInput.trim();
    if (!trimmedTitle) return setError("标题不能为空");
    if (!SLUG_RE.test(trimmedSlug)) return setError("Slug 标识必须是 kebab-case");
    if (trimmedInput.length === 0) return setError("需求说明不能为空");

    let designRefs: RepoRef[] | undefined;
    if (showRepos && repoRefs.length > 0) {
      for (const row of repoRefs) {
        if (!row.repo_id || !row.base_branch) {
          return setError("每个关联仓库都需要选择仓库和基准分支");
        }
      }
      designRefs = repoRefs.map((row) => ({
        repo_id: row.repo_id,
        base_branch: row.base_branch,
      }));
    }

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
        repo_refs: designRefs,
      });
      setTitle("");
      setSlug("");
      setUserInput("");
      setNeedsFrontendMockup(false);
      setShowRepos(false);
      setRepoRefs([]);
      onCreated(created);
    } catch (err) {
      setError(extractError(err, "创建设计工作失败"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="space-y-3" onSubmit={submit}>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1 text-xs text-muted">
          <span>标题</span>
          <input
            className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className="space-y-1 text-xs text-muted">
          <span>Slug 标识</span>
          <input
            className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 font-mono text-sm text-copy outline-none"
            value={slug}
            placeholder="feature-x"
            onChange={(event) => setSlug(event.target.value.toLowerCase())}
          />
        </label>
      </div>

      <label className="block space-y-1 text-xs text-muted">
        <span>需求说明</span>
        <textarea
          className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none"
          value={userInput}
          rows={4}
          onChange={(event) => setUserInput(event.target.value)}
        />
      </label>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="flex items-center gap-2 text-xs text-muted">
          <input
            type="checkbox"
            checked={needsFrontendMockup}
            onChange={(event) => setNeedsFrontendMockup(event.target.checked)}
          />
          <span>需要前端 mockup</span>
        </label>
        <label className="space-y-1 text-xs text-muted">
          <span>执行 Agent</span>
          <select
            className="w-full rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-sm text-copy outline-none [&_option]:bg-panel-strong"
            value={agent}
            onChange={(event) => setAgent(event.target.value as AgentKind)}
          >
            <option value="claude">Claude</option>
            <option value="codex">Codex</option>
          </select>
        </label>
      </div>

      <div className="space-y-2">
        <button
          type="button"
          aria-expanded={showRepos}
          onClick={() => setShowRepos((value) => !value)}
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs text-muted transition hover:border-accent/40 hover:text-accent"
        >
          {showRepos
            ? "隐藏仓库绑定"
            : repoRefs.length > 0
              ? `关联仓库（已配置 ${repoRefs.length} 个）`
              : "关联仓库（可选）"}
        </button>
        {showRepos ? (
          <RepoRefsEditor minRows={0} mode="design" onChange={setRepoRefs} value={repoRefs} />
        ) : null}
      </div>

      <p className="text-[11px] text-muted-soft">当前流程只创建新的 DesignWork。</p>

      {error ? <p className="text-xs text-danger">{error}</p> : null}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] disabled:opacity-60"
        >
          {submitting ? "创建中..." : "提交"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs text-muted transition hover:text-copy"
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
      className="flex flex-col gap-2 rounded-xl border border-border bg-panel-strong/80 p-3 transition hover:border-accent/30 md:flex-row md:items-center md:justify-between"
      to={`/workspaces/${workspaceId}/dev-works/${dv.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="truncate font-mono text-xs text-copy">{dv.id}</p>
        <StatusBadge status={dv.current_step} />
      </div>
      <p className="text-xs text-muted">
        轮次 {dv.iteration_rounds} / 分数 {dv.last_score ?? "-"}
      </p>
      <p className="truncate font-mono text-[11px] text-muted">文档 {dv.design_doc_id}</p>
    </Link>
  );
}

function DevWorkCreateForm({
  workspaceId,
  publishedDocs,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  publishedDocs: DesignDoc[];
  onCreated: (dv: DevWork) => void;
  onCancel: () => void;
}) {
  const [designDocId, setDesignDocId] = useState("");
  const [repoRefs, setRepoRefs] = useState<RepoRefsEditorRow[]>([]);
  const [prompt, setPrompt] = useState("");
  const [agent, setAgent] = useState<AgentKind>("claude");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!designDocId) return setError("请选择已发布的设计文档");
    if (repoRefs.length === 0) return setError("至少添加一个仓库");

    const mountSeen = new Set<string>();
    for (const [index, row] of repoRefs.entries()) {
      if (!row.repo_id) return setError(`第 ${index + 1} 行：请选择仓库`);
      if (!row.base_branch) return setError(`第 ${index + 1} 行：请选择基准分支`);
      const mount = row.mount_name.trim();
      if (!mount) return setError(`第 ${index + 1} 行：mount_name 不能为空`);
      if (!MOUNT_NAME_RE.test(mount)) return setError(`第 ${index + 1} 行：mount_name 不合法`);
      if (mountSeen.has(mount)) return setError(`mount_name "${mount}" 重复`);
      mountSeen.add(mount);
    }
    if (!prompt.trim()) return setError("执行提示不能为空");

    const payloadRefs: DevRepoRef[] = repoRefs.map((row) => ({
      repo_id: row.repo_id,
      base_branch: row.base_branch,
      mount_name: row.mount_name.trim(),
      base_rev_lock: !!row.base_rev_lock,
      is_primary: !!row.is_primary,
    }));

    setError(null);
    setSubmitting(true);
    try {
      const created = await createDevWork({
        workspace_id: workspaceId,
        design_doc_id: designDocId,
        repo_refs: payloadRefs,
        prompt: prompt.trim(),
        agent,
      });
      setDesignDocId("");
      setRepoRefs([]);
      setPrompt("");
      onCreated(created);
    } catch (err) {
      setError(extractError(err, "创建开发工作失败"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="space-y-3" onSubmit={submit}>
      <label className="block space-y-1 text-xs text-muted">
        <span>已发布设计文档</span>
        <select
          className="w-full rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-sm text-copy outline-none [&_option]:bg-panel-strong"
          value={designDocId}
          onChange={(event) => setDesignDocId(event.target.value)}
        >
          <option value="">请选择</option>
          {publishedDocs.map((doc) => (
            <option key={doc.id} value={doc.id}>
              {doc.slug}@{doc.version}
            </option>
          ))}
        </select>
      </label>

      <div className="block space-y-1 text-xs text-muted">
        <span>仓库绑定</span>
        <RepoRefsEditor minRows={1} mode="dev" onChange={setRepoRefs} value={repoRefs} />
      </div>

      <label className="block space-y-1 text-xs text-muted">
        <span>执行提示</span>
        <textarea
          aria-label="DevWork 执行提示"
          className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none"
          rows={4}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </label>

      <label className="block space-y-1 text-xs text-muted">
        <span>执行 Agent</span>
        <select
          className="w-full rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-sm text-copy outline-none [&_option]:bg-panel-strong"
          value={agent}
          onChange={(event) => setAgent(event.target.value as AgentKind)}
        >
          <option value="claude">Claude</option>
          <option value="codex">Codex</option>
        </select>
      </label>

      {error ? <p className="text-xs text-danger">{error}</p> : null}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] disabled:opacity-60"
        >
          {submitting ? "创建中..." : "提交"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs text-muted transition hover:text-copy"
        >
          取消
        </button>
      </div>
    </form>
  );
}

function EventRow({ event }: { event: WorkspaceEvent }) {
  return (
    <article className="rounded-xl border border-border bg-panel-strong/80 p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-sm text-copy">{event.event_name}</p>
        <span className="text-xs text-muted">{event.ts}</span>
      </div>
      {event.correlation_id ? (
        <p className="mt-2 text-xs text-muted">关联 ID {event.correlation_id}</p>
      ) : null}
      {event.payload ? (
        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-2xl bg-panel-deep p-3 text-[11px] text-copy">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      ) : null}
    </article>
  );
}

function LoadingBlock({ rows = 2 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="h-20 animate-pulse rounded-xl border border-border bg-panel-strong/70" />
      ))}
    </div>
  );
}

function QueryErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-2xl border border-danger/15 bg-danger/8 p-4">
      <p className="text-sm text-copy">{message}</p>
      <button
        className="mt-3 rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
        onClick={onRetry}
        type="button"
      >
        重试
      </button>
    </div>
  );
}

export function WorkspaceDetailPage() {
  const { wsId } = useParams();
  if (!wsId) {
    return (
      <section className="rounded-[32px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="font-serif text-xl font-medium text-copy">缺少 Workspace 标识</h2>
      </section>
    );
  }
  return <WorkspaceDetailContent workspaceId={wsId} />;
}

function WorkspaceDetailContent({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const polling = useWorkspacePolling();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedView = searchParams.get("view");
  const tab: TabId = TAB_IDS.includes(selectedView as TabId) ? (selectedView as TabId) : "designs";
  const [designOffset, setDesignOffset] = useState(0);
  const [devOffset, setDevOffset] = useState(0);
  const [eventOffset, setEventOffset] = useState(0);
  const [designDialogOpen, setDesignDialogOpen] = useState(false);
  const [devDialogOpen, setDevDialogOpen] = useState(false);
  const [archivePending, setArchivePending] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  const workspaceQuery = useSWR(["workspace", workspaceId], () => getWorkspace(workspaceId), polling);
  const designWorksQuery = useSWR(
    ["design-works-page", workspaceId, designOffset],
    () =>
      listDesignWorkPage({
        workspace_id: workspaceId,
        sort: "updated_desc",
        limit: WORK_ITEM_PAGE_SIZE,
        offset: designOffset,
      }),
    polling,
  );
  const allDocsQuery = useSWR(["design-docs", workspaceId], () => listDesignDocs(workspaceId), polling);
  const publishedDocsQuery = useSWR(
    ["design-docs", workspaceId, "published"],
    () => listDesignDocs(workspaceId, "published"),
    polling,
  );
  const devWorksQuery = useSWR(
    ["dev-works-page", workspaceId, devOffset],
    () =>
      listDevWorkPage({
        workspace_id: workspaceId,
        sort: "updated_desc",
        limit: WORK_ITEM_PAGE_SIZE,
        offset: devOffset,
      }),
    polling,
  );
  const eventsQuery = useSWR(
    ["workspace-events", workspaceId, eventOffset],
    () => listWorkspaceEvents(workspaceId, { limit: EVENTS_PAGE_SIZE, offset: eventOffset }),
    polling,
  );

  const designWorks = useMemo(() => designWorksQuery.data?.items ?? [], [designWorksQuery.data]);
  const docs = useMemo(() => allDocsQuery.data ?? [], [allDocsQuery.data]);
  const publishedDocs = useMemo(() => publishedDocsQuery.data ?? [], [publishedDocsQuery.data]);
  const devWorks = useMemo(() => devWorksQuery.data?.items ?? [], [devWorksQuery.data]);
  const events = eventsQuery.data?.events ?? [];

  async function handleArchive() {
    if (typeof window !== "undefined" && !window.confirm("确认归档这个 Workspace？")) {
      return;
    }
    setArchivePending(true);
    setArchiveError(null);
    try {
      await archiveWorkspace(workspaceId);
      navigate("/workspaces");
    } catch (err) {
      setArchiveError(extractError(err, "归档 Workspace 失败"));
    } finally {
      setArchivePending(false);
    }
  }

  const workspace = workspaceQuery.data;
  const devCreateDisabled = publishedDocs.length === 0 || !!publishedDocsQuery.error;
  const devCreateDisabledTitle = publishedDocsQuery.error
    ? "已发布设计文档加载失败，请先重试"
    : devCreateDisabled
      ? "需要先发布设计文档"
      : undefined;

  function setTab(next: TabId) {
    const params = new URLSearchParams(searchParams);
    params.set("view", next);
    setSearchParams(params);
  }

  if (workspaceQuery.error) {
    return (
      <section className="rounded-[32px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="font-serif text-xl font-medium text-copy">Workspace 加载失败</h2>
        <p className="mt-2 text-sm text-muted">{extractError(workspaceQuery.error, "Workspace 加载失败")}</p>
        <button
          className="mt-4 rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
          onClick={() => void workspaceQuery.mutate()}
          type="button"
        >
          重试
        </button>
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <AppDialog
        description="从需求说明生成新的设计工作；可选绑定仓库作为上下文。"
        onClose={() => setDesignDialogOpen(false)}
        open={designDialogOpen}
        title="新建设计工作"
      >
        <DesignWorkCreateForm
          workspaceId={workspaceId}
          onCancel={() => setDesignDialogOpen(false)}
          onCreated={() => {
            setDesignDialogOpen(false);
            setDesignOffset(0);
            void designWorksQuery.mutate();
          }}
        />
      </AppDialog>

      <AppDialog
        description="选择已发布设计文档和目标仓库，创建开发执行项。"
        onClose={() => setDevDialogOpen(false)}
        open={devDialogOpen}
        title="新建开发工作"
      >
        <DevWorkCreateForm
          workspaceId={workspaceId}
          publishedDocs={publishedDocs}
          onCancel={() => setDevDialogOpen(false)}
          onCreated={() => {
            setDevDialogOpen(false);
            setDevOffset(0);
            void devWorksQuery.mutate();
          }}
        />
      </AppDialog>

      <SectionPanel
        kicker="Workspace"
        title={workspace?.title ?? "正在加载 Workspace..."}
        actions={
          workspace && workspace.status === "active" ? (
            <button
              type="button"
              disabled={archivePending}
              onClick={() => void handleArchive()}
              className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-muted transition hover:border-danger/30 hover:text-danger disabled:opacity-50"
            >
              {archivePending ? "归档中..." : "归档"}
            </button>
          ) : undefined
        }
      >
        {workspace ? (
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
            <StatusBadge status={workspace.status} />
            <span className="font-mono">{workspace.slug}</span>
            <span>更新于 {new Date(workspace.updated_at).toLocaleString()}</span>
            <span className="truncate">{workspace.root_path}</span>
          </div>
        ) : null}
        {archiveError ? <p className="mt-3 text-xs text-danger">{archiveError}</p> : null}
      </SectionPanel>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-panel px-4 py-3 shadow-whisper">
        <SegmentedControl
          ariaLabel="Workspace 详情视图"
          options={TAB_IDS.map((id) => ({ value: id, label: TAB_LABELS[id] }))}
          value={tab}
          onChange={setTab}
        />
        <p className="text-xs text-muted">当前视图：{TAB_LABELS[tab]}</p>
      </div>

      {tab === "designs" ? (
        <div className="space-y-4">
          <SectionPanel
            kicker="设计工作"
            title="DesignWork 列表"
            actions={
              <button
                className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] transition hover:bg-accent-soft"
                onClick={() => setDesignDialogOpen(true)}
                type="button"
              >
                新建设计工作
              </button>
            }
          >
            {designWorks.length === 0 ? (
              designWorksQuery.error ? (
                <QueryErrorState
                  message={extractError(designWorksQuery.error, "设计工作加载失败")}
                  onRetry={() => void designWorksQuery.mutate()}
                />
              ) : designWorksQuery.data === undefined ? (
                <LoadingBlock />
              ) : (
                <EmptyState copy="暂无设计工作。" />
              )
            ) : (
              <div className="space-y-2">
                {designWorks.map((dw) => (
                  <DesignWorkRow key={dw.id} workspaceId={workspaceId} dw={dw} />
                ))}
              </div>
            )}

            {designWorksQuery.data ? (
              <div className="mt-3">
                <PaginationControls
                  pagination={designWorksQuery.data.pagination}
                  itemLabel="设计工作"
                  onPageChange={setDesignOffset}
                  disabled={designWorksQuery.isLoading}
                />
              </div>
            ) : null}
          </SectionPanel>

          <SectionPanel kicker="设计文档" title="DesignDoc 列表">
            {docs.length === 0 ? (
              allDocsQuery.error ? (
                <QueryErrorState
                  message={extractError(allDocsQuery.error, "设计文档加载失败")}
                  onRetry={() => void allDocsQuery.mutate()}
                />
              ) : allDocsQuery.data === undefined ? (
                <LoadingBlock />
              ) : (
                <EmptyState copy="暂无设计文档。" />
              )
            ) : (
              <div className="grid gap-2 xl:grid-cols-2">
                {docs.map((doc) => (
                  <DesignDocRow key={doc.id} doc={doc} />
                ))}
              </div>
            )}
          </SectionPanel>
        </div>
      ) : null}

      {tab === "devworks" ? (
        <SectionPanel
          kicker="开发工作"
          title="DevWork 列表"
          actions={
            <button
              className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] transition hover:bg-accent-soft disabled:opacity-40"
              disabled={devCreateDisabled}
              onClick={() => setDevDialogOpen(true)}
              title={devCreateDisabledTitle}
              type="button"
            >
              新建开发工作
            </button>
          }
        >
          {publishedDocsQuery.error ? (
            <div className="mb-4">
              <QueryErrorState
                message={extractError(publishedDocsQuery.error, "已发布设计文档加载失败")}
                onRetry={() => void publishedDocsQuery.mutate()}
              />
            </div>
          ) : null}

          {devWorks.length === 0 ? (
            devWorksQuery.error ? (
              <QueryErrorState
                message={extractError(devWorksQuery.error, "开发工作加载失败")}
                onRetry={() => void devWorksQuery.mutate()}
              />
            ) : devWorksQuery.data === undefined ? (
              <LoadingBlock />
            ) : (
              <EmptyState copy="暂无开发工作。" />
            )
          ) : (
            <div className="space-y-2">
              {devWorks.map((dv) => (
                <DevWorkRow key={dv.id} workspaceId={workspaceId} dv={dv} />
              ))}
            </div>
          )}

          {devWorksQuery.data ? (
            <div className="mt-3">
              <PaginationControls
                pagination={devWorksQuery.data.pagination}
                itemLabel="开发工作"
                onPageChange={setDevOffset}
                disabled={devWorksQuery.isLoading}
              />
            </div>
          ) : null}
        </SectionPanel>
      ) : null}

      {tab === "events" ? (
        <SectionPanel kicker="事件流" title="Workspace 事件">
          {events.length === 0 ? (
            eventsQuery.error ? (
              <QueryErrorState
                message={extractError(eventsQuery.error, "Workspace 事件加载失败")}
                onRetry={() => void eventsQuery.mutate()}
              />
            ) : eventsQuery.data === undefined ? (
              <LoadingBlock rows={3} />
            ) : (
              <EmptyState copy="暂无 Workspace 事件。" />
            )
          ) : (
            <div className="space-y-2">
              {events.map((event) => (
                <EventRow key={event.event_id} event={event} />
              ))}
            </div>
          )}

          {eventsQuery.data ? (
            <div className="mt-3">
              <PaginationControls
                pagination={eventsQuery.data.pagination}
                itemLabel="事件"
                onPageChange={setEventOffset}
                disabled={eventsQuery.isLoading}
              />
            </div>
          ) : null}
        </SectionPanel>
      ) : null}
    </div>
  );
}
