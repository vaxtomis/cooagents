import type { ReactNode } from "react";

export type StatusTone = "success" | "warning" | "danger" | "accent" | "muted";

const STATUS_META: Record<string, { label: string; tone: StatusTone; className: string; dotClassName: string }> = {
  active: {
    label: "active",
    tone: "success",
    className: "border-success/20 bg-success/12 text-success",
    dotClassName: "bg-success",
  },
  approved: {
    label: "approved",
    tone: "success",
    className: "border-success/20 bg-success/12 text-success",
    dotClassName: "bg-success",
  },
  cancelled: {
    label: "cancelled",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  completed: {
    label: "completed",
    tone: "accent",
    className: "border-accent/20 bg-accent/12 text-accent",
    dotClassName: "bg-accent",
  },
  dispatched: {
    label: "dispatched",
    tone: "accent",
    className: "border-accent/20 bg-accent/12 text-accent",
    dotClassName: "bg-accent",
  },
  failed: {
    label: "failed",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  interrupted: {
    label: "interrupted",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  offline: {
    label: "offline",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  queued: {
    label: "queued",
    tone: "warning",
    className: "border-warning/20 bg-warning/12 text-warning",
    dotClassName: "bg-warning",
  },
  rejected: {
    label: "rejected",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  review: {
    label: "review",
    tone: "warning",
    className: "border-warning/20 bg-warning/12 text-warning",
    dotClassName: "bg-warning",
  },
  running: {
    label: "running",
    tone: "success",
    className: "border-success/20 bg-success/12 text-success",
    dotClassName: "bg-success",
  },
  starting: {
    label: "starting",
    tone: "muted",
    className: "border-white/8 bg-white/4 text-muted",
    dotClassName: "bg-muted",
  },
  timeout: {
    label: "timeout",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  waiting: {
    label: "waiting",
    tone: "warning",
    className: "border-warning/20 bg-warning/12 text-warning",
    dotClassName: "bg-warning",
  },
};

export function resolveStatusBadge(status: string | null | undefined) {
  const normalized = String(status ?? "unknown").toLowerCase();
  return STATUS_META[normalized] ?? {
    label: normalized,
    tone: "muted" as StatusTone,
    className: "border-white/8 bg-white/4 text-muted",
    dotClassName: "bg-muted",
  };
}

export function StatusBadge({
  status,
  label,
  icon,
  className = "",
}: {
  status: string | null | undefined;
  label?: string;
  icon?: ReactNode;
  className?: string;
}) {
  const meta = resolveStatusBadge(status);
  const resolvedLabel = label ?? meta.label;

  return (
    <span
      aria-label={resolvedLabel}
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium ${meta.className} ${className}`.trim()}
      data-tone={meta.tone}
      role="status"
    >
      {icon ?? <span className={`size-1.5 rounded-full ${meta.dotClassName}`} />}
      {resolvedLabel}
    </span>
  );
}
