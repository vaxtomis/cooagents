import type { ReactNode } from "react";

interface SectionPanelProps {
  title: string;
  kicker: string;
  children: ReactNode;
  actions?: ReactNode;
  titleAccessory?: ReactNode;
  density?: "default" | "compact";
}

export function SectionPanel({
  title,
  kicker,
  children,
  actions,
  titleAccessory,
  density = "default",
}: SectionPanelProps) {
  const compact = density === "compact";

  return (
    <section
      className={[
        "relative overflow-hidden rounded-[28px] border border-border-strong bg-panel/95 shadow-shell",
        compact ? "p-3 md:p-4" : "p-4 md:p-5",
      ].join(" ")}
      data-panel-tone="console"
    >
      <div className="pointer-events-none absolute inset-[1px] rounded-[27px] border border-white/4" />
      <div className="pointer-events-none absolute inset-x-6 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(169,112,45,0.85),transparent)]" />

      <div
        className={[
          "relative flex flex-wrap items-center justify-between",
          compact ? "gap-2" : "gap-3",
        ].join(" ")}
      >
        <div className={["relative min-w-0", titleAccessory ? "pr-12" : ""].join(" ")}>
          <p
            className={[
              "font-medium uppercase text-accent-soft",
              compact ? "text-[10px] tracking-[0.22em]" : "text-[11px] tracking-[0.26em]",
            ].join(" ")}
          >
            {kicker}
          </p>
          <h2
            className={[
              "font-semibold leading-snug text-copy",
              compact ? "mt-0.5 text-[1.15rem] md:text-[1.28rem]" : "mt-1 text-[1.3rem] md:text-[1.45rem]",
            ].join(" ")}
          >
            {title}
          </h2>
          {titleAccessory ? (
            <div className="absolute right-0 top-1/2 -translate-y-1/2">
              {titleAccessory}
            </div>
          ) : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>

      <div className={compact ? "relative mt-3" : "relative mt-4"}>{children}</div>
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
