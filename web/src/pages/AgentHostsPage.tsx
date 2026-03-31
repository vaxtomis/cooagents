export function AgentHostsPage() {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
        <h2 className="text-xl font-semibold text-white">Host orchestration lands in phase 2</h2>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-muted">
          The route, shell, and navigation are already stable. Next step is wiring the real host inventory, capacity bars,
          drain controls, and recovery actions against the existing backend endpoint.
        </p>
      </section>

      <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
        <p className="text-[11px] uppercase tracking-[0.3em] text-muted/75">Planned modules</p>
        <ul className="mt-4 space-y-3 text-sm text-muted">
          <li>Host cards with labels, health, and concurrency slots</li>
          <li>Active job allocation and queue pressure indicators</li>
          <li>Drain / reactivate actions with audit visibility</li>
        </ul>
      </section>
    </div>
  );
}
