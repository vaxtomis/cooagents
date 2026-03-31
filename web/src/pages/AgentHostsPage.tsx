import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import useSWR from "swr";
import {
  checkAgentHost,
  createAgentHost,
  deleteAgentHost,
  listAgentHosts,
  updateAgentHost,
} from "../api/agents";
import { StatusBadge } from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import type { AgentHost } from "../types";

type HostFormState = {
  id: string;
  host: string;
  agent_type: string;
  max_concurrent: string;
  ssh_key: string;
  labels: string;
};

const DEFAULT_FORM: HostFormState = {
  agent_type: "both",
  host: "",
  id: "",
  labels: "",
  max_concurrent: "2",
  ssh_key: "",
};

function SectionPanel({
  title,
  kicker,
  children,
}: {
  title: string;
  kicker: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
      <p className="text-[11px] uppercase tracking-[0.3em] text-muted/75">{kicker}</p>
      <h2 className="mt-2 text-lg font-semibold text-white">{title}</h2>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function EmptyState({ copy }: { copy: string }) {
  return <p className="rounded-2xl border border-dashed border-white/8 bg-white/3 px-4 py-6 text-sm text-muted">{copy}</p>;
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 3 }, (_, index) => (
        <div key={index} className="h-48 animate-pulse rounded-[24px] border border-white/6 bg-panel-strong/70" />
      ))}
    </div>
  );
}

function ConfigBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[20px] border border-white/6 bg-black/18 px-4 py-3">
      <p className="text-[11px] uppercase tracking-[0.24em] text-muted/70">{label}</p>
      <p className="mt-2 text-sm font-medium text-white">{value}</p>
    </div>
  );
}

function HostTag({ label }: { label: string }) {
  return (
    <span className="rounded-full border border-white/8 bg-white/4 px-3 py-1 text-xs text-white/85">
      {label}
    </span>
  );
}

function parseLabels(value: string) {
  return value
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function toFormState(host: AgentHost | null): HostFormState {
  if (!host) {
    return DEFAULT_FORM;
  }

  return {
    agent_type: host.agent_type,
    host: host.host,
    id: host.id,
    labels: host.labels.join(", "),
    max_concurrent: String(host.max_concurrent),
    ssh_key: host.ssh_key ?? "",
  };
}

export function AgentHostsPage() {
  const polling = usePolling(15_000);
  const hostsQuery = useSWR(["agent-hosts"], listAgentHosts, polling);
  const [selectedHostId, setSelectedHostId] = useState<string | null>(null);
  const [form, setForm] = useState<HostFormState>(DEFAULT_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [formPending, setFormPending] = useState(false);
  const [rowPending, setRowPending] = useState<Record<string, "check" | "delete" | null>>({});
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const hosts = hostsQuery.data ?? [];
  const selectedHost = useMemo(
    () => hosts.find((host) => host.id === selectedHostId) ?? null,
    [hosts, selectedHostId],
  );
  const isEditing = selectedHost !== null;

  useEffect(() => {
    if (!hostsQuery.data) {
      return;
    }

    if (selectedHostId && !selectedHost) {
      setSelectedHostId(null);
      setForm(DEFAULT_FORM);
      return;
    }

    setForm(toFormState(selectedHost));
  }, [hostsQuery.data, selectedHost, selectedHostId]);

  function updateForm(field: keyof HostFormState) {
    return (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      setForm((current) => ({ ...current, [field]: event.target.value }));
    };
  }

  function resetForm() {
    setSelectedHostId(null);
    setForm(DEFAULT_FORM);
    setFormError(null);
    setActionMessage(null);
  }

  async function refreshHosts() {
    await hostsQuery.mutate();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormPending(true);
    setFormError(null);
    setActionMessage(null);

    const labels = parseLabels(form.labels);
    const payload = {
      agent_type: form.agent_type.trim(),
      host: form.host.trim(),
      labels,
      max_concurrent: Number(form.max_concurrent),
      ssh_key: form.ssh_key,
    };

    try {
      if (isEditing && selectedHost) {
        await updateAgentHost(selectedHost.id, payload);
        setActionMessage(`Saved ${selectedHost.id}`);
      } else {
        const hostId = form.id.trim();
        await createAgentHost({
          ...payload,
          id: hostId,
        });
        setSelectedHostId(hostId);
        setActionMessage(`Created ${hostId}`);
      }
      await refreshHosts();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Host save failed");
    } finally {
      setFormPending(false);
    }
  }

  async function handleCheck(hostId: string) {
    setRowPending((current) => ({ ...current, [hostId]: "check" }));
    setActionMessage(null);
    try {
      const result = await checkAgentHost(hostId);
      setActionMessage(`Last check: ${result.online ? "online" : "offline"}`);
      await refreshHosts();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Host check failed");
    } finally {
      setRowPending((current) => ({ ...current, [hostId]: null }));
    }
  }

  async function handleDelete(hostId: string) {
    setRowPending((current) => ({ ...current, [hostId]: "delete" }));
    setActionMessage(null);
    try {
      await deleteAgentHost(hostId);
      if (selectedHostId === hostId) {
        resetForm();
      }
      await refreshHosts();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "Host delete failed");
    } finally {
      setRowPending((current) => ({ ...current, [hostId]: null }));
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <SectionPanel kicker="Host Registry" title="Agent host configuration">
        {hostsQuery.error ? (
          <div className="rounded-[24px] border border-danger/15 bg-danger/8 p-5">
            <h3 className="text-base font-semibold text-white">Host inventory failed to load</h3>
            <p className="mt-2 text-sm text-muted">Retry the host query to restore the registry.</p>
            <button className="mt-4 rounded-full bg-white px-4 py-2 text-sm font-medium text-black" onClick={() => void refreshHosts()} type="button">
              Retry
            </button>
          </div>
        ) : !hostsQuery.data ? (
          <LoadingSkeleton />
        ) : hosts.length === 0 ? (
          <EmptyState copy="No agent hosts are registered yet." />
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {hosts.map((host) => {
              const isSelected = host.id === selectedHostId;
              const pendingState = rowPending[host.id];

              return (
                <article
                  className={`rounded-[24px] border bg-panel-strong/80 p-5 transition ${
                    isSelected ? "border-accent/30 shadow-[0_0_0_1px_rgba(168,85,247,0.22)]" : "border-white/6"
                  }`}
                  key={host.id}
                >
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <p className="text-[11px] uppercase tracking-[0.28em] text-muted/70">Host configuration</p>
                      <p className="mt-2 font-mono text-sm text-white">{host.id}</p>
                      <p className="mt-1 text-sm text-muted">{host.host}</p>
                    </div>
                    <StatusBadge status={host.status} />
                  </div>

                  <div className="mt-5 grid gap-3 sm:grid-cols-2">
                    <ConfigBlock label="Agent type" value={host.agent_type} />
                    <ConfigBlock label="Max concurrent" value={String(host.max_concurrent)} />
                    <ConfigBlock label="SSH key" value={host.ssh_key ? "Configured" : "Missing"} />
                    <ConfigBlock
                      label="Current load"
                      value={`${host.current_load}/${host.max_concurrent} in use`}
                    />
                  </div>

                  <div className="mt-4 rounded-[20px] border border-white/6 bg-black/18 px-4 py-3">
                    <p className="text-[11px] uppercase tracking-[0.24em] text-muted/70">Labels</p>
                    {host.labels.length > 0 ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {host.labels.map((label) => (
                          <HostTag key={`${host.id}-${label}`} label={label} />
                        ))}
                      </div>
                    ) : (
                      <p className="mt-2 text-sm text-muted">No labels configured</p>
                    )}
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
                      onClick={() => {
                        setSelectedHostId(host.id);
                        setActionMessage(null);
                        setFormError(null);
                      }}
                      type="button"
                    >
                      {`Edit ${host.id}`}
                    </button>
                    <button
                      className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8 disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={pendingState !== undefined && pendingState !== null}
                      onClick={() => void handleCheck(host.id)}
                      type="button"
                    >
                      {pendingState === "check" ? "Checking..." : `Check ${host.id}`}
                    </button>
                    <button
                      className="rounded-full bg-danger px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={pendingState !== undefined && pendingState !== null}
                      onClick={() => void handleDelete(host.id)}
                      type="button"
                    >
                      {pendingState === "delete" ? "Deleting..." : `Delete ${host.id}`}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </SectionPanel>

      <SectionPanel kicker="Host Form" title={isEditing ? "Edit host" : "Create host"}>
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted">
            {isEditing ? `Selected ${selectedHostId}` : "Register a new host or choose an existing host to edit."}
          </p>
          <button
            className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
            onClick={resetForm}
            type="button"
          >
            Create new
          </button>
        </div>

        <form className="mt-5 space-y-4" onSubmit={handleSubmit}>
          <Field label="Host id">
            <input
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isEditing}
              onChange={updateForm("id")}
              required
              type="text"
              value={form.id}
            />
          </Field>

          <Field label="Host address">
            <input
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
              onChange={updateForm("host")}
              required
              type="text"
              value={form.host}
            />
          </Field>

          <Field label="Agent type">
            <select
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
              onChange={updateForm("agent_type")}
              value={form.agent_type}
            >
              <option value="both">both</option>
              <option value="codex">codex</option>
              <option value="claude">claude</option>
            </select>
          </Field>

          <Field label="Max concurrent">
            <input
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
              min={1}
              onChange={updateForm("max_concurrent")}
              required
              type="number"
              value={form.max_concurrent}
            />
          </Field>

          <Field label="SSH key">
            <input
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
              onChange={updateForm("ssh_key")}
              type="text"
              value={form.ssh_key}
            />
          </Field>

          <Field label="Labels">
            <input
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
              onChange={updateForm("labels")}
              placeholder="comma, separated, labels"
              type="text"
              value={form.labels}
            />
          </Field>

          {actionMessage ? <p className="text-sm text-muted">{actionMessage}</p> : null}
          {formError ? <p className="text-sm text-danger">{formError}</p> : null}

          <button
            className="w-full rounded-full bg-white px-4 py-3 text-sm font-medium text-black transition hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={formPending}
            type="submit"
          >
            {formPending ? "Saving..." : "Save host"}
          </button>
        </form>
      </SectionPanel>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-2 text-sm text-muted">
      <span>{label}</span>
      {children}
    </label>
  );
}
