export function EventLogPage() {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
        <h2 className="text-xl font-semibold text-white">Event log backend is ready for the richer UI</h2>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-muted">
          The global `/api/v1/events` endpoint is already in place. This page is intentionally lightweight in phase 1 while the
          real trace explorer, filters, and pagination controls are scheduled for the next slice.
        </p>
      </section>

      <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
        <p className="text-[11px] uppercase tracking-[0.3em] text-muted/75">Planned modules</p>
        <ul className="mt-4 space-y-3 text-sm text-muted">
          <li>Level, span type, run, and source filters</li>
          <li>Infinite or paged event browsing over the new endpoint</li>
          <li>Payload inspector with run links back into detail pages</li>
        </ul>
      </section>
    </div>
  );
}
