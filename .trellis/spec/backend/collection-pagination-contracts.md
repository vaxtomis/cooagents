# Collection Pagination Contracts

> Executable backend and cross-layer contract for collection-style routes that now expose an opt-in paginated envelope.

---

## Scenario: Opt-In Collection Pagination

### 1. Scope / Trigger
- Trigger: changing any list-style route in `routes/` that serves workspace, repo, DesignWork, DevWork, event, or repo-log collections.
- Trigger: changing any query shape or response envelope consumed by `web/src/api/*`.
- Why this requires code-spec depth: these endpoints are cross-layer contracts and drift here breaks both frontend paging behavior and backend tests.

### 2. Signatures
- `GET /api/v1/workspaces?paginate=true&status=&query=&sort=&limit=&offset=`
- `GET /api/v1/repos?paginate=true&role=&fetch_status=&query=&sort=&limit=&offset=`
- `GET /api/v1/design-works?workspace_id=<id>&paginate=true&state=&query=&sort=&limit=&offset=`
- `GET /api/v1/dev-works?workspace_id=<id>&paginate=true&step=&query=&sort=&limit=&offset=`
- `GET /api/v1/workspaces/{workspace_id}/events?limit=&offset=&event_name=`
- `GET /api/v1/repos/{repo_id}/log?ref=&path=&limit=&offset=&paginate=true`

### 3. Contracts
- Default behavior for legacy callers remains the old non-paginated shape where the route already returned an array or `RepoLog`.
- Paginated callers must pass `paginate=true`.
- Paginated response shape:

```json
{
  "items": [...],
  "pagination": {
    "limit": 12,
    "offset": 0,
    "total": 57,
    "has_more": true
  }
}
```

- Workspace events keep the historical `events` array key, but the `pagination` object must now always include `total`.
- Repo log paging contract:
  - `limit` defaults to the inspector default when omitted.
  - `offset` maps to `git log --skip=<offset>`.
  - paginated repo log returns `items` instead of `entries`; non-paginated callers still receive `RepoLog.entries`.
- Sorting must remain explicit allowlists owned by the route or backend owner; never pass raw SQL fragments from the client.

### 4. Validation & Error Matrix
- `status` outside `active|archived` -> `400 bad_request`
- `role` outside `RepoRole` enum -> `400 bad_request`
- `fetch_status` outside `unknown|healthy|error` -> `400 bad_request`
- `state` outside `DesignWorkState` enum -> `400 bad_request`
- `step` outside `DevWorkStep` enum -> `400 bad_request`
- `sort` outside the route-owned allowlist -> `400 bad_request`
- `limit < 1` or route-specific max exceeded -> `422` via FastAPI `Query(...)`
- `offset < 0` -> `422` via FastAPI `Query(...)`
- unknown `workspace_id` on events -> `404 not_found`
- unhealthy or missing repo clone on repo log -> same route/inspector behavior as existing repo inspector endpoints

### 5. Good / Base / Bad Cases
- Good: add a new collection filter by extending the route allowlist, count query, and frontend SWR key in the same change.
- Base: keep a legacy array helper and add a new `*Page` helper for redesigned screens instead of silently changing all callers at once.
- Bad: change an existing array route to an envelope unconditionally and break every untouched caller.
- Bad: compute pagination metadata in the frontend from array length; the backend owns `total` and `has_more`.

### 6. Tests Required
- Route tests for each new paginated path must assert:
  - envelope keys exist
  - `limit`, `offset`, `total`, `has_more` values are correct
  - sort/filter params still work
- Repo inspector tests must assert:
  - `offset` skips commits deterministically
  - `log_count()` matches commit volume
- Workspace events route tests must assert `pagination.total` is present in default and paged responses.
- Frontend page tests should assert the page helper is used, not the legacy array helper.

### 7. Wrong vs Correct

#### Wrong

```python
@router.get("/repos")
async def list_repos(request: Request):
    return await request.app.state.repo_registry_repo.list_all()
```

#### Correct

```python
@router.get("/repos", response_model=list[Repo] | RepoPage)
async def list_repos(..., paginate: bool = False):
    if paginate:
        page = await registry.list_page(...)
        return RepoPage(**page)
    return await registry.list_all(...)
```

---

## Design Decision: Additive Pagination Helpers

**Context**: the redesign needed paged collection views, but the existing frontend still had callers that expected raw arrays and `RepoLog.entries`.

**Decision**: add paginated route behavior and frontend `*Page` helpers additively instead of replacing the old helpers immediately.

**Consequences**:
- collection routes stay backward-compatible during incremental UI migration
- new UI must prefer the paginated helper
- future cleanup can remove legacy helpers only after all callers are migrated
