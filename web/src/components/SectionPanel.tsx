import type { ReactNode } from "react";

interface SectionPanelProps {
  title: string;
  kicker: string;
  children: ReactNode;
  actions?: ReactNode;
}

export function SectionPanel({ title, kicker, children, actions }: SectionPanelProps) {
  return (
    <section className="rounded-2xl border border-border bg-panel p-4 shadow-panel md:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-[0.22em] text-muted-soft">{kicker}</p>
          <h2 className="mt-1 font-serif text-[1.35rem] font-medium leading-snug tracking-tight text-copy">
            {title}
          </h2>
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

interface MetricCardProps {
  label: string;
  value: string;
}

export function MetricCard({ label, value }: MetricCardProps) {
  return (
    <div className="rounded-xl border border-border bg-panel-strong/70 p-3">
      <p className="text-[11px] uppercase tracking-[0.18em] text-muted-soft">{label}</p>
      <p className="mt-2 break-all font-mono text-sm text-copy">{value}</p>
    </div>
  );
}

interface EmptyStateProps {
  copy: string;
}

export function EmptyState({ copy }: EmptyStateProps) {
  return (
    <p className="rounded-xl border border-dashed border-border bg-panel-strong/40 px-4 py-5 text-sm text-muted">
      {copy}
    </p>
  );
}
