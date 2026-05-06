# brainstorm: frontend layout menu pagination interaction optimization

## Goal

Design an optimized frontend experience for the current page by reviewing the existing frontend code, improving layout, menu/navigation, pagination, and interaction patterns, with visual direction informed by `DESIGN.md`.

## What I already know

* The request is focused on the current frontend page.
* The design scope includes layout, menu/navigation, pagination, and interaction patterns.
* `DESIGN.md` should be used as a style reference.
* The frontend lives under `web/` and uses React 18, Vite, TypeScript, Tailwind v4, React Router, SWR, Headless UI, and lucide-react.
* The app shell is implemented in `web/src/router.tsx`; `ShellLayout` owns the desktop left sidebar, mobile horizontal nav, page header, page meta, and authenticated outlet.
* Global design variables in `web/src/index.css` already mirror `DESIGN.md`: parchment background, ivory panels, warm sand surfaces, terracotta accent, warm neutral text, serif headings, and ring/whisper shadows.
* The current UI is an operational control console, not a marketing/product landing page. The design should stay dense, scannable, and workflow-oriented.
* Common page primitives exist in `web/src/components/SectionPanel.tsx`, but tabs, segmented filters, cards, action buttons, error blocks, and pagination/loading controls are reimplemented inside multiple pages.
* Current primary list pages use card grids:
  * `WorkspacesPage` lists workspaces with status filtering.
  * `ReposPage` lists repositories with create/sync/fetch/delete actions.
  * `CrossWorkspaceDevWorkPage` groups DevWorks by workspace and filters by step.
* Detail pages use tabs:
  * `WorkspaceDetailPage`: designs / devworks / events.
  * `RepoDetailPage`: branches / tree / log.
  * `DevWorkPage`: notes / reviews / gate.
* Pagination is inconsistent:
  * Workspace events use incremental limit growth, `50 -> 100 -> 150 -> 200`, through `setEventLimit(...)`.
  * Repo log always fetches a fixed `LOG_LIMIT = 50` and only shows a note when capped.
  * Workspace/repo/devwork list pages currently have no visible pagination or count controls.
* Source files are valid UTF-8; the mojibake observed in PowerShell output is a terminal encoding artifact.
* `.trellis/spec/` currently has backend and general guides, but no frontend-specific spec layer.

## Assumptions (temporary)

* "Current page" likely means the current dashboard page family, with the most reusable improvements applied at the app shell and shared component level.
* The desired output for this brainstorm is a concrete design and implementation plan before code changes.
* Backend API changes should be avoided for the MVP unless a visible pagination behavior cannot be improved with existing client-side contracts.
* We should preserve the existing Claude-inspired warm editorial style, but adapt it for a compact admin/workbench interface.

## Open Questions

* None blocking. Ready for final requirement confirmation.

## Requirements (evolving)

* Review current frontend page code and related components.
* Review `DESIGN.md` for visual and interaction guidance.
* Propose layout, menu, pagination, and interaction improvements that fit the existing codebase.
* Preserve the existing warm design token system and serif/sans hierarchy.
* Improve navigation clarity without making the dashboard feel like a landing page.
* Unify repeated controls: segmented filters, tabs, action buttons, empty/error/loading states, and pagination/load-more affordances.
* Make list-heavy pages easier to scan and operate repeatedly.
* Keep mobile behavior usable: touch targets, non-overlapping text, and predictable collapsed navigation.
* Reorganize the dashboard around a Workspace-first information architecture.
* Make Workspace the primary context for design work, development work, events, and related repos.
* Backend/API changes are in scope for search, sorting, filtering, and server-side pagination.
* Use a consistent paginated list contract for redesigned collection views.
* Prefer additive or opt-in API response changes where practical so existing unpaginated callers are not surprised.

## Acceptance Criteria (evolving)

* [x] Current page structure and relevant frontend files are identified.
* [x] Existing style and interaction constraints are documented.
* [x] A concrete MVP design direction is proposed.
* [x] User chooses MVP scope before implementation.
* [ ] Out-of-scope items are explicitly listed.
* [x] Out-of-scope items are explicitly listed.
* [x] Implementation plan names the concrete files/components to change.
* [ ] User confirms final requirements before implementation.

## Definition of Done (team quality bar)

* Tests added/updated if implementation follows.
* Lint / typecheck / CI green if implementation follows.
* Docs/notes updated if behavior changes.
* Rollout/rollback considered if risky.

## Out of Scope (explicit)

* Backend API changes unless page behavior requires them.
* Full product redesign beyond the current page unless requested.
* Replacing the current React/Tailwind stack.
* Introducing a heavy external component framework.
* Marketing-style hero sections, decorative gradients, or large illustrative areas.
* Deep analytics/alerting workflows beyond list navigation, filtering, and operational visibility.
* Auth/session model changes.

## Technical Notes

* Key files inspected:
  * `DESIGN.md`
  * `web/package.json`
  * `web/vite.config.ts`
  * `web/src/router.tsx`
  * `web/src/index.css`
  * `web/src/components/SectionPanel.tsx`
  * `web/src/pages/WorkspacesPage.tsx`
  * `web/src/pages/ReposPage.tsx`
  * `web/src/pages/CrossWorkspaceDevWorkPage.tsx`
  * `web/src/pages/WorkspaceDetailPage.tsx`
  * `web/src/pages/RepoDetailPage.tsx`
  * `web/src/pages/repo/LogList.tsx`
  * `web/src/pages/DevWorkPage.tsx`
  * `web/src/pages/DesignWorkPage.tsx`
  * `web/src/api/workspaceEvents.ts`
  * `web/src/api/repos.ts`
* Code reference points:
  * App shell / menu: `web/src/router.tsx:43`, `web/src/router.tsx:162`
  * Design tokens: `web/src/index.css:3`, `web/src/index.css:9`
  * Shared panel primitive: `web/src/components/SectionPanel.tsx:10`
  * Workspace event pagination: `web/src/pages/WorkspaceDetailPage.tsx:29`, `web/src/pages/WorkspaceDetailPage.tsx:653`
  * Repo log cap: `web/src/pages/repo/LogList.tsx:12`, `web/src/pages/repo/LogList.tsx:17`
  * Repeated card patterns: `web/src/pages/WorkspacesPage.tsx:25`, `web/src/pages/ReposPage.tsx:70`
  * Repeated tab patterns: `web/src/pages/DevWorkPage.tsx:296`
  * Current workspace list route: `routes/workspaces.py:46`
  * Current workspace event pagination route: `routes/workspace_events.py:23`, `routes/workspace_events.py:88`
  * Current repo list/log routes: `routes/repos.py:57`, `routes/repos.py:245`
  * Current DesignWork/DevWork list routes: `routes/design_works.py:140`, `routes/dev_works.py:222`
* Existing constraints:
  * Vite dev/preview bind to `127.0.0.1:4173`.
  * Production dashboard is mounted by FastAPI from `web/dist`.
  * Existing frontend tests use Vitest and Testing Library.
  * SWR polling is already used for workspace-driven pages.
  * Backend list endpoints mostly return arrays today; only Workspace Events already returns a pagination envelope.

## Research Notes

### What the current code suggests

* The fastest useful improvement is to create shared UI primitives and apply them consistently, rather than redesign each page independently.
* The shell should continue to anchor navigation, but the page header is currently large for repeated operational views; it can become more compact and context-rich.
* Card grids work for small data sets, but operational list pages need stronger scan affordances: counts, compact metadata rows, primary/secondary action hierarchy, and consistent filter placement.
* The pagination model should be honest about backend contracts:
  * Events already support `limit`, `offset`, and `has_more`.
  * Repo logs currently support `limit` but not offset in the frontend API wrapper.
  * Many list APIs return arrays and may need client-side pagination if we want immediate MVP behavior without backend changes.
* Since backend/API changes are now in scope, the full redesign should introduce a reusable pagination envelope instead of bolting unrelated one-off controls onto each page.

### Backend contract direction

Use a common envelope shape for collection views:

```ts
type PaginatedEnvelope<T> = {
  items: T[];
  pagination: {
    limit: number;
    offset: number;
    total: number;
    has_more: boolean;
  };
};
```

Recommended route behavior:

* `GET /workspaces`: support status, query search, sort, limit, offset.
* `GET /design-works`: keep `workspace_id` required; add state/mode/search/sort/limit/offset.
* `GET /dev-works`: keep `workspace_id` required; add step/search/sort/limit/offset.
* `GET /repos`: add role/fetch_status/search/sort/limit/offset.
* `GET /workspaces/{id}/events`: keep existing event_name/limit/offset and add `total` to the existing pagination object.
* `GET /repos/{id}/log`: add offset/cursor support if feasible in `RepoInspector`; otherwise expose a clear capped server contract and defer deep log paging.

### Feasible approaches here

**Approach A: Shared polish pass** (lowest risk)

* How it works: Keep current page structure. Add reusable primitives for segmented controls, tabs, action buttons, error/empty/loading panels, card/list rows, and load-more controls. Apply them to the most visible pages.
* Pros: Small blast radius, easier tests, preserves current mental model.
* Cons: Does not fundamentally improve information density or long-list ergonomics.

**Approach B: Workbench layout + unified list/pagination system** (recommended)

* How it works: Keep the app shell and warm visual language, but make the main header more compact, add clearer sidebar grouping/status affordances, introduce shared toolbar/list/pagination primitives, and apply them to Workspaces, Repos, Cross-workspace DevWorks, Workspace detail events, and Repo log.
* Pros: Meaningfully improves layout, menu, pagination, and interaction consistency while staying within current architecture.
* Cons: Touches more files and needs broader visual/test verification.

**Approach C: Full dashboard IA redesign** (selected)

* How it works: Reorganize navigation around operational domains, convert major pages to table/list hybrid views, add persistent workspace/repo context selectors, and potentially adjust API contracts for real server-side pagination everywhere.
* Pros: Best long-term control-plane UX.
* Cons: Larger product decision, likely backend/API work, higher risk for one iteration.

## Expansion Sweep

### Future evolution

* The console may grow into a denser operations cockpit with workspace/repo/devwork global search, saved filters, and live activity drawers.
* Shared primitives now should leave room for table/list views and server-backed pagination later.

### Related scenarios

* Create/update flows should use the same button hierarchy, validation style, and expandable form behavior.
* Detail tabs should behave consistently across Workspace, Repo, and DevWork pages.
* Cross-workspace views should remain available, but as secondary operational views rather than the primary mental model.

### Failure and edge cases

* Pagination/load-more should show loading, disabled, and end-of-list states clearly.
* Destructive actions should keep explicit confirmation and visible per-row pending states.
* Mobile nav and filters must not overflow or hide the primary action.
* API pagination should preserve deterministic ordering and return enough metadata for total counts, next/previous state, and empty page recovery.

## Technical Approach (evolving)

Selected scope: Approach C, full dashboard information architecture redesign.

Selected IA: Workspace-first.

Selected pagination/search boundary: backend/API changes are allowed and should be included where they produce a materially better UX.

The redesign should preserve the existing warm Claude-inspired visual system, but reorganize the dashboard around a clearer operational model rather than only polishing the current routes.

Implementation planning should focus on:

* Shared UI primitives:
  * `PageToolbar` / list header area
  * `SegmentedControl`
  * `TabSwitch`
  * `ActionButton`
  * `LoadMoreControls`
  * richer `SectionPanel` variants for compact operational panels
  * table/list hybrid row primitives for dense operational collections
* Shell changes:
  * make Workspace the canonical primary navigation context.
  * add contextual secondary navigation for DesignWork, DevWork, Events, and Repos within a selected Workspace.
  * tighten page header spacing and convert it into a compact workbench masthead
  * improve mobile navigation affordance
* Page changes:
  * reorganize Workspaces, Repos, DevWorks, and events into a Workspace-first IA.
  * convert major operational collections to table/list hybrid views where scanning matters.
  * introduce consistent toolbar, counts, filters, sorting, and pagination states.
  * Workspace events: replace bare load-more button with count-aware server pagination controls.
  * Repo log: extend API support if needed so log pagination is real rather than a fixed 50-entry cap.
* Backend/API changes:
  * add shared pagination helpers for validation/clamping/count metadata.
  * update route SQL for workspaces, design works, dev works, repos, and events.
  * update repo inspector/log contract if true log pagination is feasible.
  * add or update backend tests for filtering, sorting, counts, and pagination bounds.
* Frontend API changes:
  * update `web/src/types/index.ts` pagination types.
  * update `web/src/api/*.ts` list clients to consume envelopes.
  * keep SWR keys stable and explicit for search/filter/sort/page state.
* Verification:
  * update affected component/page tests
  * run `npm --prefix web run test`
  * run `npm --prefix web run build`
  * if implementation follows, start Vite and inspect desktop/mobile layouts with screenshots.

## Implementation Plan

* PR1: Backend list contract
  * Introduce shared pagination helpers and envelope models.
  * Add paginated/filterable/sortable support for Workspaces, DesignWorks, DevWorks, Repos, Events.
  * Decide and implement Repo Log offset/cursor feasibility.
  * Add backend route tests.
* PR2: Frontend IA shell
  * Redesign app shell as Workspace-first workbench.
  * Add workspace selector/context area and secondary navigation.
  * Keep global views available as secondary operations.
* PR3: Shared frontend primitives
  * Add reusable toolbar, tabs, segmented controls, action buttons, pagination, list rows, empty/error/loading components.
  * Replace repeated inline tab/filter/button code.
* PR4: Page conversions
  * Convert workspace, repo, design work, dev work, event, and log collection views to the new list/pagination model.
  * Update page tests.
* PR5: Visual/accessibility verification
  * Run frontend tests/build and backend tests.
  * Inspect desktop/mobile layouts with the dev server and fix overflow/overlap issues.

## Decision (ADR-lite)

**Context**: The current dashboard already has warm visual styling, but the route/page structure is still a direct resource map. As data grows, card grids, duplicated tabs, and inconsistent pagination will make operations harder to scan.

**Decision**: Use Approach C, a full dashboard information architecture redesign.

**Consequences**: This opens the door to stronger navigation, table/list hybrid views, and real pagination patterns, but it requires one more product-level decision before implementation: the canonical organizing principle for the dashboard.

**Context**: The existing data model and README describe Workspace as the main orchestration boundary. Workspaces own DesignWorks, DesignDocs, DevWorks, workspace events, and file context, while repo browsing and cross-workspace DevWorks are supporting views.

**Decision**: Use a Workspace-first information architecture.

**Consequences**: The shell should prioritize selecting and operating within a Workspace. Cross-workspace DevWorks, repo registry, and global health can remain accessible, but they should not compete with Workspace as the primary navigation model.

**Context**: Full IA redesign benefits from real server pagination/search/sort contracts. Client-only pagination would make the visual redesign look complete while leaving long-list behavior weak.

**Decision**: Include backend/API changes for pagination, search, sorting, and filtering where needed.

**Consequences**: The implementation must touch backend routes, API client types, and frontend list controls. The work should define a consistent envelope contract and add backend tests in addition to frontend tests.
