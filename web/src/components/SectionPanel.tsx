import type { ReactNode } from "react";

interface SectionPanelProps {
  title: string;
  kicker: string;
  children: ReactNode;
  actions?: ReactNode;
}

export function SectionPanel({ title, kicker, children, actions }: SectionPanelProps) {
  return (
    <section
      className="relative overflow-hidden rounded-[28px] border border-border-strong bg-panel/95 p-4 shadow-shell md:p-5"
      data-panel-tone="console"
    >
      <div className="pointer-events-none absolute inset-[1px] rounded-[27px] border border-white/4" />
      <div className="pointer-events-none absolute inset-x-6 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(201,154,84,0.85),transparent)]" />

      <div className="relative flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.26em] text-accent-soft">
            {kicker}
          </p>
          <h2 className="mt-1 text-[1.3rem] font-semibold leading-snug tracking-[-0.03em] text-copy md:text-[1.45rem]">
            {title}
          </h2>
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>

      <div className="relative mt-4">{children}</div>
    </section>
  );
}

interface MetricCardProps {
  label: string;
  value: string;
}

export function MetricCard({ label, value }: MetricCardProps) {
  return (
    <div
      className="relative overflow-hidden rounded-[22px] border border-border bg-panel-strong/80 p-3 shadow-panel"
      data-card-tone="console"
    >
      <div className="pointer-events-none absolute inset-x-4 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(255,255,255,0.08),transparent)]" />
      <p className="text-[11px] uppercase tracking-[0.22em] text-muted-soft">{label}</p>
      <p className="mt-2 break-all font-mono text-sm text-copy">{value}</p>
    </div>
  );
}

interface EmptyStateProps {
  copy: string;
}

export function EmptyState({ copy }: EmptyStateProps) {
  return (
    <p
      className="rounded-[22px] border border-dashed border-border bg-panel-deep/72 px-4 py-5 text-sm text-muted"
      data-empty-tone="console"
    >
      {copy}
    </p>
  );
}
