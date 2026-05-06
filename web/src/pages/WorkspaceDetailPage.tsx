import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import useSWR from "swr";
import { createDesignWork, listDesignWorkPage } from "../api/designWorks";
import { listDesignDocs } from "../api/designDocs";
import { createDevWork, listDevWorkPage } from "../api/devWorks";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { archiveWorkspace, getWorkspace } from "../api/workspaces";
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
  designs: "Design work",
  devworks: "Development work",
  events: "Events",
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
      <p className="text-xs text-muted">Mode {dw.mode} / Loop {dw.loop}</p>
      {dw.output_design_doc_id ? (
        <p className="truncate font-mono text-[11px] text-muted">Doc {dw.output_design_doc_id}</p>
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
          Frontend mockup required
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
  const [showRepos, setShowRepos] = useState(false);
  const [repoRefs, setRepoRefs] = useState<RepoRefsEditorRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedTitle = title.trim();
    const trimmedSlug = slug.trim();
    const trimmedInput = userInput.trim();
    if (!trimmedTitle) return setError("Title is required");
    if (!SLUG_RE.test(trimmedSlug)) return setError("Slug must be kebab-case");
    if (trimmedInput.length === 0) return setError("Brief is required");

    let designRefs: RepoRef[] | undefined;
    if (showRepos && repoRefs.length > 0) {
      for (const row of repoRefs) {
        if (!row.repo_id || !row.base_branch) {
          return setError("Every linked repository needs a repo and base branch");
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
      setOpen(false);
      onCreated(created);
    } catch (err) {
      setError(extractError(err, "Failed to create design work"));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-copy transition hover:border-accent/40 hover:text-accent"
      >
        New design work
      </button>
    );
  }

  return (
    <form className="space-y-3 rounded-2xl border border-border bg-panel-strong/60 p-4" onSubmit={submit}>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1 text-xs text-muted">
          <span>Title</span>
          <input
            className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className="space-y-1 text-xs text-muted">
          <span>Slug</span>
          <input
            className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 font-mono text-sm text-copy outline-none"
            value={slug}
            placeholder="feature-x"
            onChange={(event) => setSlug(event.target.value.toLowerCase())}
          />
        </label>
      </div>

      <label className="block space-y-1 text-xs text-muted">
        <span>Brief</span>
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
          <span>Needs frontend mockup</span>
        </label>
        <label className="space-y-1 text-xs text-muted">
          <span>Agent</span>
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
            ? "Hide repository bindings"
            : repoRefs.length > 0
              ? `Attach repositories (${repoRefs.length} configured)`
              : "Attach repositories (optional)"}
        </button>
        {showRepos ? (
          <RepoRefsEditor minRows={0} mode="design" onChange={setRepoRefs} value={repoRefs} />
        ) : null}
      </div>

      <p className="text-[11px] text-muted-soft">This flow currently creates new design work items only.</p>

      {error ? <p className="text-xs text-danger">{error}</p> : null}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] disabled:opacity-60"
        >
          {submitting ? "Creating..." : "Submit"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs text-muted transition hover:text-copy"
        >
          Cancel
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
        Round {dv.iteration_rounds} / Score {dv.last_score ?? "-"}
      </p>
      <p className="truncate font-mono text-[11px] text-muted">Doc {dv.design_doc_id}</p>
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
  const [repoRefs, setRepoRefs] = useState<RepoRefsEditorRow[]>([]);
  const [prompt, setPrompt] = useState("");
  const [agent, setAgent] = useState<AgentKind>("claude");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const disabled = publishedDocs.length === 0;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!designDocId) return setError("Choose a published design doc");
    if (repoRefs.length === 0) return setError("Add at least one repository");

    const mountSeen = new Set<string>();
    for (const [index, row] of repoRefs.entries()) {
      if (!row.repo_id) return setError(`Row ${index + 1}: choose a repository`);
      if (!row.base_branch) return setError(`Row ${index + 1}: choose a base branch`);
      const mount = row.mount_name.trim();
      if (!mount) return setError(`Row ${index + 1}: mount_name is required`);
      if (!MOUNT_NAME_RE.test(mount)) return setError(`Row ${index + 1}: invalid mount_name`);
      if (mountSeen.has(mount)) return setError(`mount_name "${mount}" is duplicated`);
      mountSeen.add(mount);
    }
    if (!prompt.trim()) return setError("Prompt is required");

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
      setOpen(false);
      onCreated(created);
    } catch (err) {
      setError(extractError(err, "Failed to create development work"));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        disabled={disabled}
        title={disabled ? "A published design doc is required first" : undefined}
        onClick={() => setOpen(true)}
        className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-copy transition hover:border-accent/40 hover:text-accent disabled:opacity-40"
      >
        New development work
      </button>
    );
  }

  return (
    <form className="space-y-3 rounded-2xl border border-border bg-panel-strong/60 p-4" onSubmit={submit}>
      <label className="block space-y-1 text-xs text-muted">
        <span>Published design doc</span>
        <select
          className="w-full rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-sm text-copy outline-none [&_option]:bg-panel-strong"
          value={designDocId}
          onChange={(event) => setDesignDocId(event.target.value)}
        >
          <option value="">Select one</option>
          {publishedDocs.map((doc) => (
            <option key={doc.id} value={doc.id}>
              {doc.slug}@{doc.version}
            </option>
          ))}
        </select>
      </label>

      <div className="block space-y-1 text-xs text-muted">
        <span>Repository bindings</span>
        <RepoRefsEditor minRows={1} mode="dev" onChange={setRepoRefs} value={repoRefs} />
      </div>

      <label className="block space-y-1 text-xs text-muted">
        <span>Prompt</span>
        <textarea
          aria-label="DevWork prompt"
          className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none"
          rows={4}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </label>

      <label className="block space-y-1 text-xs text-muted">
        <span>Agent</span>
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
          {submitting ? "Creating..." : "Submit"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs text-muted transition hover:text-copy"
        >
          Cancel
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
        <p className="mt-2 text-xs text-muted">Correlation {event.correlation_id}</p>
      ) : null}
      {event.payload ? (
        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-2xl bg-panel-deep p-3 text-[11px] text-copy">
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
        <h2 className="font-serif text-xl font-medium text-copy">Missing workspace id</h2>
      </section>
    );
  }
  return <WorkspaceDetailContent workspaceId={wsId} />;
}

function WorkspaceDetailContent({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const polling = useWorkspacePolling();
  const [tab, setTab] = useState<TabId>("designs");
  const [designOffset, setDesignOffset] = useState(0);
  const [devOffset, setDevOffset] = useState(0);
  const [eventOffset, setEventOffset] = useState(0);
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
    if (typeof window !== "undefined" && !window.confirm("Archive this workspace?")) {
      return;
    }
    setArchivePending(true);
    setArchiveError(null);
    try {
      await archiveWorkspace(workspaceId);
      navigate("/workspaces");
    } catch (err) {
      setArchiveError(extractError(err, "Failed to archive workspace"));
    } finally {
      setArchivePending(false);
    }
  }

  const workspace = workspaceQuery.data;

  return (
    <div className="space-y-6">
      <SectionPanel
        kicker="Workspace"
        title={workspace?.title ?? "Loading workspace..."}
        actions={
          workspace && workspace.status === "active" ? (
            <button
              type="button"
              disabled={archivePending}
              onClick={() => void handleArchive()}
              className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-muted transition hover:border-danger/30 hover:text-danger disabled:opacity-50"
            >
              {archivePending ? "Archiving..." : "Archive"}
            </button>
          ) : undefined
        }
      >
        {workspace ? (
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
            <StatusBadge status={workspace.status} />
            <span className="font-mono">{workspace.slug}</span>
            <span>Updated {new Date(workspace.updated_at).toLocaleString()}</span>
            <span className="truncate">{workspace.root_path}</span>
          </div>
        ) : null}
        {archiveError ? <p className="mt-3 text-xs text-danger">{archiveError}</p> : null}
      </SectionPanel>

      <SectionPanel
        kicker="Collections"
        title={TAB_LABELS[tab]}
        actions={
          <SegmentedControl
            ariaLabel="Workspace detail tabs"
            options={TAB_IDS.map((id) => ({ value: id, label: TAB_LABELS[id] }))}
            value={tab}
            onChange={setTab}
          />
        }
      >
        {tab === "designs" ? (
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs uppercase tracking-[0.24em] text-muted-soft">Design work</p>
                <DesignWorkCreateForm
                  workspaceId={workspaceId}
                  onCreated={() => {
                    setDesignOffset(0);
                    void designWorksQuery.mutate();
                  }}
                />
              </div>

              {designWorks.length === 0 ? (
                <EmptyState copy="No design work items yet." />
              ) : (
                <div className="space-y-3">
                  {designWorks.map((dw) => (
                    <DesignWorkRow key={dw.id} workspaceId={workspaceId} dw={dw} />
                  ))}
                </div>
              )}

              {designWorksQuery.data ? (
                <PaginationControls
                  pagination={designWorksQuery.data.pagination}
                  itemLabel="Design work"
                  onPageChange={setDesignOffset}
                  disabled={designWorksQuery.isLoading}
                />
              ) : null}
            </div>

            <div className="space-y-3">
              <p className="text-xs uppercase tracking-[0.24em] text-muted-soft">Design docs</p>
              {docs.length === 0 ? (
                <EmptyState copy="No design docs yet." />
              ) : (
                <div className="space-y-3">
                  {docs.map((doc) => (
                    <DesignDocRow key={doc.id} doc={doc} />
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}

        {tab === "devworks" ? (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs uppercase tracking-[0.24em] text-muted-soft">Development work</p>
              <DevWorkCreateForm
                workspaceId={workspaceId}
                publishedDocs={publishedDocs}
                onCreated={() => {
                  setDevOffset(0);
                  void devWorksQuery.mutate();
                }}
              />
            </div>

            {devWorks.length === 0 ? (
              <EmptyState copy="No development work items yet." />
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {devWorks.map((dv) => (
                  <DevWorkRow key={dv.id} workspaceId={workspaceId} dv={dv} />
                ))}
              </div>
            )}

            {devWorksQuery.data ? (
              <PaginationControls
                pagination={devWorksQuery.data.pagination}
                itemLabel="Development work"
                onPageChange={setDevOffset}
                disabled={devWorksQuery.isLoading}
              />
            ) : null}
          </div>
        ) : null}

        {tab === "events" ? (
          <div className="space-y-3">
            {events.length === 0 ? (
              <EmptyState copy="No workspace events yet." />
            ) : (
              <div className="space-y-3">
                {events.map((event) => (
                  <EventRow key={event.event_id} event={event} />
                ))}
              </div>
            )}

            {eventsQuery.data ? (
              <PaginationControls
                pagination={eventsQuery.data.pagination}
                itemLabel="Events"
                onPageChange={setEventOffset}
                disabled={eventsQuery.isLoading}
              />
            ) : null}
          </div>
        ) : null}
      </SectionPanel>
    </div>
  );
}
