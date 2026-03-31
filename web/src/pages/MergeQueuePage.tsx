export function MergeQueuePage() {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
        <h2 className="text-xl font-semibold text-white">Merge queue UI is deferred, not the route</h2>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-muted">
          The shell keeps this destination live now so users can navigate to a stable URL while the real merge queue table,
          conflict grouping, and resolution affordances are built in phase 2.
        </p>
      </section>

      <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
        <p className="text-[11px] uppercase tracking-[0.3em] text-muted/75">Planned modules</p>
        <ul className="mt-4 space-y-3 text-sm text-muted">
          <li>Queue ordering with priority and branch metadata</li>
          <li>Conflict buckets wired to repo merge diagnostics</li>
          <li>Per-run merge outcome cards and retry affordances</li>
        </ul>
      </section>
    </div>
  );
}
