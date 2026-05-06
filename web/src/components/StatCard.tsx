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
    <section className="relative overflow-hidden rounded-[24px] border border-border bg-panel p-6 shadow-panel transition hover:-translate-y-0.5 hover:shadow-shell">
      <div className="pointer-events-none absolute inset-[1px] rounded-[23px] border border-white/4" />
      <div className="pointer-events-none absolute inset-x-5 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(201,154,84,0.55),transparent)]" />

      <div className="relative">
        <p className="text-[11px] font-medium uppercase tracking-[0.24em] text-accent-soft">{title}</p>
        <div className="mt-5 flex items-end justify-between gap-3">
          <div>
            <div className="text-[3rem] font-semibold leading-none tracking-[-0.05em] text-copy [font-variant-numeric:tabular-nums]">
              {value}
            </div>
            {subtitle ? <p className="mt-3 text-sm leading-relaxed text-muted">{subtitle}</p> : null}
          </div>
          {icon ? (
            <div className="flex size-10 items-center justify-center rounded-[14px] border border-border bg-panel-deep text-accent-soft">
              {icon}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
