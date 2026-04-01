import type { ReactNode } from "react";

export type StatusTone = "success" | "warning" | "danger" | "accent" | "muted";

const STATUS_META: Record<string, { label: string; tone: StatusTone; className: string; dotClassName: string }> = {
  active: {
    label: "在线",
    tone: "success",
    className: "border-success/20 bg-success/12 text-success",
    dotClassName: "bg-success",
  },
  approved: {
    label: "已通过",
    tone: "success",
    className: "border-success/20 bg-success/12 text-success",
    dotClassName: "bg-success",
  },
  cancelled: {
    label: "已取消",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  completed: {
    label: "已完成",
    tone: "accent",
    className: "border-accent/20 bg-accent/12 text-accent",
    dotClassName: "bg-accent",
  },
  dispatched: {
    label: "已分派",
    tone: "accent",
    className: "border-accent/20 bg-accent/12 text-accent",
    dotClassName: "bg-accent",
  },
  failed: {
    label: "失败",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  interrupted: {
    label: "已中断",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  offline: {
    label: "离线",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  queued: {
    label: "排队中",
    tone: "warning",
    className: "border-warning/20 bg-warning/12 text-warning",
    dotClassName: "bg-warning",
  },
  rejected: {
    label: "已驳回",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  review: {
    label: "审核中",
    tone: "warning",
    className: "border-warning/20 bg-warning/12 text-warning",
    dotClassName: "bg-warning",
  },
  running: {
    label: "运行中",
    tone: "success",
    className: "border-success/20 bg-success/12 text-success",
    dotClassName: "bg-success",
  },
  starting: {
    label: "启动中",
    tone: "muted",
    className: "border-white/8 bg-white/4 text-muted",
    dotClassName: "bg-muted",
  },
  timeout: {
    label: "超时",
    tone: "danger",
    className: "border-danger/20 bg-danger/12 text-danger",
    dotClassName: "bg-danger",
  },
  waiting: {
    label: "等待中",
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
      className={`inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium ${meta.className} ${className}`.trim()}
      data-tone={meta.tone}
      role="status"
    >
      {icon ?? <span className={`size-1.5 rounded-full ${meta.dotClassName}`} />}
      {resolvedLabel}
    </span>
  );
}
