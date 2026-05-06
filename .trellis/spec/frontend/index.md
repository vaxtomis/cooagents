# Frontend Development Guidelines

> Project-specific frontend rules for the current `cooagents` dashboard in `web/`.

---

## Overview

The frontend is a React 18 + Vite + TypeScript dashboard with:

- app shell and routing in `web/src/router.tsx`
- API client adapters in `web/src/api/`
- page-level screens in `web/src/pages/`
- shared controls in `web/src/components/`
- warm Claude-inspired visual tokens in `web/src/index.css`

Follow these docs before changing dashboard navigation, collection pages, or shared controls.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Dashboard Collection Patterns](./dashboard-collection-patterns.md) | Workspace-first shell rules, paged collection helpers, segmented filters, and pagination control conventions | Complete |

---

## Pre-Development Checklist

Read these before writing frontend code:

1. Always read [Dashboard Collection Patterns](./dashboard-collection-patterns.md) when touching `web/src/router.tsx`, `web/src/pages/*`, or `web/src/api/*` collection helpers
2. Also read the shared thinking guides from `.trellis/spec/guides/`

---

## Quality Check

Before finishing frontend work, confirm:

- collection pages use shared controls before introducing new one-off variants
- API helpers match backend route shapes exactly
- SWR keys include every filter, sort, and page input that changes the response
- desktop and mobile layouts preserve dense operational scanning rather than landing-page chrome
- tests cover changed pages and helpers

---

## Scope Note

These docs cover `web/` only. Backend route behavior remains defined under `.trellis/spec/backend/`.
