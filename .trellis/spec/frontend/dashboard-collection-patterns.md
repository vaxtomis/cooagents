# Dashboard Collection Patterns

> Concrete frontend rules for workspace-first navigation and paged collection views.

---

## Scenario: Workspace-First Collection UI

### 1. Scope / Trigger
- Trigger: changing `web/src/router.tsx`, collection-oriented pages, or shared collection controls.
- Trigger: changing `web/src/api/*` list helpers that consume paginated route envelopes.
- Why this requires code-spec depth: these rules sit on the backend/frontend boundary and define reusable UI behavior, not one-off screen polish.

### 2. Signatures
- `listWorkspacePage(params) -> Promise<WorkspacePage>`
- `listRepoPage(params) -> Promise<RepoPage>`
- `listDesignWorkPage(params) -> Promise<DesignWorkPage>`
- `listDevWorkPage(params) -> Promise<DevWorkPage>`
- `repoLogPage(id, params) -> Promise<RepoLogPage>`
- shared controls:
  - `SegmentedControl<T extends string>`
  - `PaginationControls`

### 3. Contracts
- Keep legacy array helpers when unchanged screens still depend on them; paged screens use the `*Page` helper explicitly.
- `PaginationControls` expects `pagination.limit`, `offset`, `total`, and `has_more`; do not synthesize these from local array length.
- `SegmentedControl` owns compact filter/tabs where the option set is small and mutually exclusive.
- App shell contract:
  - primary nav: `Overview`, `Workspaces`
  - secondary nav: cross-workspace operations such as `Cross-workspace DevWorks` and `Repository Registry`
  - recent workspace recall belongs in the shell, not duplicated per page
- Workspace detail contract:
  - Design work, development work, and events all page explicitly
  - creation forms can stay inline, but collection browsing must use shared paging behavior

### 4. Validation & Error Matrix
- page helpers must include `paginate=true`; forgetting it is a caller bug because the backend falls back to legacy shapes
- SWR keys must include page inputs (`limit`, `offset`, filter, sort, query) or stale page caches will leak between views
- if a collection query fails, render a contained error state with a retry action, not a blank page
- if a paged collection returns zero items, render an explicit empty state rather than a missing panel

### 5. Good / Base / Bad Cases
- Good: add a shared control once, then convert multiple pages to it.
- Base: keep form flows local to a page if only that page uses them, while still reusing collection chrome.
- Bad: introduce another ad hoc tab pill or pager because one screen "only needs a tiny variant".
- Bad: make a dashboard page look like marketing content with oversized headers, decorative hero treatments, or low-density cards.

### 6. Tests Required
- page tests should assert the paginated helper is called with the expected `limit` and `offset`
- page tests should cover filter/search/sort interactions when they affect the SWR key
- repo detail tests should assert log paging uses `repoLogPage`
- build and typecheck must pass after route/helper signature updates

### 7. Wrong vs Correct

#### Wrong

```ts
const query = useSWR(["repos"], listRepos);
const rows = query.data ?? [];
```

#### Correct

```ts
const query = useSWR(
  ["repos-page", status, role, search, sort, limit, offset],
  () => listRepoPage({ status, role, query: search, sort, limit, offset }),
);
const rows = query.data?.items ?? [];
```

---

## Convention: Dense Operational Layout

**What**: prefer compact mastheads, row-oriented collection cards, and metadata-forward layouts.

**Why**: this dashboard is an operations surface; scan speed matters more than visual spectacle.

**Example**:

```tsx
<SectionPanel kicker="Directory" title="Repository registry">
  <div className="space-y-3">
    {repos.map((repo) => (
      <RepoRow key={repo.id} repo={repo} ... />
    ))}
  </div>
  <PaginationControls pagination={page.pagination} ... />
</SectionPanel>
```

**Related**: `.trellis/spec/backend/collection-pagination-contracts.md`
