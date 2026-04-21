import type { ReactNode } from "react";

export function StatCard({
  title,
  value,
  subtitle,
  icon,
}: {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: ReactNode;
}) {
  return (
    <section className="rounded-[24px] border border-border bg-panel p-5 shadow-panel">
      <p className="text-xs uppercase tracking-[0.24em] text-muted/80">{title}</p>
      <div className="mt-4 flex items-end justify-between gap-3">
        <div>
          <div className="font-mono text-4xl font-bold text-copy">{value}</div>
          {subtitle ? <p className="mt-2 text-sm text-muted">{subtitle}</p> : null}
        </div>
        {icon ? <div className="flex size-10 items-center justify-center rounded-2xl border border-border bg-panel-strong/40 text-accent">{icon}</div> : null}
      </div>
    </section>
  );
}
