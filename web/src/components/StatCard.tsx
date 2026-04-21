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
    <section className="rounded-2xl border border-border bg-panel p-6 shadow-whisper transition hover:shadow-panel">
      <p className="text-[11px] font-medium uppercase tracking-[0.24em] text-accent">
        {title}
      </p>
      <div className="mt-5 flex items-end justify-between gap-3">
        <div>
          <div className="font-serif text-[3rem] font-medium leading-none tracking-tight text-copy [font-variant-numeric:tabular-nums]">
            {value}
          </div>
          {subtitle ? <p className="mt-3 text-sm leading-relaxed text-muted">{subtitle}</p> : null}
        </div>
        {icon ? (
          <div className="flex size-10 items-center justify-center rounded-xl border border-border-strong bg-panel-strong/50 text-accent">
            {icon}
          </div>
        ) : null}
      </div>
    </section>
  );
}
