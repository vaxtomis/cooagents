# Bootstrap Web Build Design

## Goal

Ensure a fresh `cooagents` install or upgrade produces a usable dashboard by making local web asset build part of the required bootstrap flow.

## Current State

- FastAPI only mounts the dashboard SPA when `web/dist/index.html` exists.
- `web/dist` and `web/node_modules` are ignored by git, so a fresh clone does not contain built assets.
- `scripts/bootstrap.sh` currently installs backend dependencies and initializes the database, but does not install web dependencies or build the dashboard.
- `cooagents-setup` and `cooagents-upgrade` treat backend health as sufficient success criteria, which allows a partial install where the API works but the dashboard is missing.

## Recommended Approach

Keep `scripts/bootstrap.sh` as the single installation entry point and extend it to:

1. Require `npm` alongside `node`
2. Install web dependencies in `web/`
3. Build the Vite app locally
4. Fail fast if `web/dist/index.html` is missing after the build

The setup and upgrade skills should continue to call only `bash scripts/bootstrap.sh`, but their instructions and success checks should reflect that bootstrap now includes dashboard build and that the root route must return HTML after startup.

## Alternatives Considered

### 1. Build web in skills instead of bootstrap

Rejected because it duplicates installation logic across setup/upgrade paths and leaves the documented manual bootstrap path incomplete.

### 2. Commit `web/dist` into the repository

Rejected because generated assets are noisy in diffs, easy to drift from source, and do not remove the existing Node requirement used by bootstrap.

## Implementation Outline

### Bootstrap

- Add an explicit `npm --version` check
- Add a new "Build web dashboard" phase after Python dependency installation
- Run `npm ci` and `npm run build` inside `web/`
- Exit with an error if the build fails or `web/dist/index.html` is absent
- Update bootstrap completion output to mention the dashboard

### Skills

- Update `cooagents-setup` to describe bootstrap as responsible for web build
- Add a post-start verification step for `http://127.0.0.1:8321/` returning HTML
- Update `cooagents-upgrade` with the same expectation
- Extend troubleshooting docs with frontend dependency/build failure cases

### Documentation

- Update README install and skill sections to mention local dashboard build as part of bootstrap
- Clarify that a successful install produces both API availability and dashboard assets

## Error Handling

- `npm` missing: bootstrap exits with `ERROR: npm not found`
- `npm ci` failure: bootstrap exits non-zero and skills treat the install/upgrade as failed
- `npm run build` failure: bootstrap exits non-zero and troubleshooting points to frontend build diagnostics
- Missing `web/dist/index.html` after a successful-looking build: bootstrap exits non-zero to prevent silent partial installs

## Testing Strategy

- Add a focused regression test proving the dashboard root is not mounted when `web/dist/index.html` is absent
- Preserve existing SPA tests for `web/dist` assets
- Run targeted Python tests for SPA mounting behavior
- Run `npm run build` in `web/` to verify the local build path works end to end
