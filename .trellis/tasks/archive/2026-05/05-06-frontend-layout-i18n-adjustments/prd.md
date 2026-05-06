# brainstorm: 调整前端布局与汉化

## Goal

把当前前端从“居中卡片式控制台”调整为更高密度的工作台：减少左右留白，让 Workspace 进入后的主要信息占据页面主体；把关键流程从小范围 tab/内嵌展开改为更明确的页面切换或弹窗；并完成用户可见文案的中文化。

## What I already know

* 用户明确反馈三点问题：
  * 页面左右留空过多，没有充分利用宽屏空间。
  * 应尽可能使用页面切换或弹窗，而不是在小区域里做局部转换。
  * 进入 Workspace 后，真正有效的信息页面只占整体页面很小部分。
  * 需要汉化。
* 前端位于 `web/`，使用 React 18、Vite、TypeScript、Tailwind v4、React Router、SWR、Headless UI、lucide-react。
* App shell 在 `web/src/router.tsx`：
  * 顶层容器使用 `max-w-[1360px]`、左右 padding、286px 侧栏和大页头。
  * 侧栏包含导航、最近 Workspace、说明卡片、登录信息，占用了持续空间。
  * 每个页面还有独立的大 header，重复展示说明性文案。
* Workspace 详情页在 `web/src/pages/WorkspaceDetailPage.tsx`：
  * `designs` / `devworks` / `events` 使用一个 `SectionPanel` 内的 `SegmentedControl` 切换。
  * DesignWork 和 DesignDoc 被放在同一个局部网格里。
  * 创建 DesignWork / DevWork 使用内嵌展开表单。
  * DevWorks 仍以卡片网格展示，宽屏信息密度有限。
* 已有组件包括 `SectionPanel`、`SegmentedControl`、`PaginationControls`，但它们仍偏向卡片化、局部切换和英文文案。
* `.trellis/spec/frontend/dashboard-collection-patterns.md` 已经要求 dense operational layout、workspace-first navigation、分页集合视图和避免低密度卡片/大页头。
* 当前前端已经有部分中文文案，例如 dashboard metrics、状态 badge、gate 操作等，但仍有大量英文导航、按钮、表单、错误提示、分页控件和测试断言。
* 前一次归档任务 `05-06-frontend-layout-menu-pagination-interaction` 已选择过 Workspace-first IA、分页 API、共享控件等方向；这次反馈表明视觉布局和交互边界还要继续推进，而不是只做轻量 polish。

## Assumptions (temporary)

* “汉化”优先覆盖用户可见的前端 UI 文案；开发者日志、API 字段名、测试描述、协议枚举和代码内部标识不作为 MVP 必改项，除非它们直接出现在界面上。
* “更多页面切换或弹窗”优先应用在 Workspace 内的一级内容与创建流程：
  * Workspace 概览 / 设计工作 / 开发工作 / 事件流拆成明确的子页面或路由状态。
  * 创建 Workspace、DesignWork、DevWork、Repo 等较重表单改为 modal/dialog，避免挤占列表区域。
* 本次可以先集中改前端布局和文案；若已有分页 API 足够则不改后端。
* 需要保持现有温暖 Claude-inspired 视觉系统，但降低大圆角卡片、大说明块和居中容器的存在感。

## Open Questions

* None blocking. User selected full authenticated frontend pass.

## Requirement Update (2026-05-06)

* Remove the global `CrossWorkspace DevWork` menu entry from the authenticated shell.
* Add a global `Agent Host 管理` menu entry for registering and managing agent host servers.
* Implement an authenticated `Agent Host` management page in `web/` using the existing `/api/v1/agent-hosts` backend resource.
* Make the desktop left sidebar collapsible. A lightweight persisted preference is acceptable.
* Keep the rest of the workspace-first layout direction unchanged.

## Requirements (evolving)

* 减少全局左右留白：
  * 放宽或移除 `max-w-[1360px]` 对主工作区的限制。
  * 缩小侧栏和页头占比，宽屏下让主体内容优先获得空间。
  * 避免把页面主体包在多层大圆角卡片中。
* 重构 Workspace 详情的信息架构：
  * 避免把 DesignWork、DevWork、Events 都压在单个 `Collections` 面板内。
  * 将 Workspace 内关键集合提升为更明确的页面切换或子路由。
  * 保留必要的快速切换，但不让 tab 成为主要内容的空间瓶颈。
* 表单流程改造：
  * 创建类流程优先使用 modal/dialog。
  * modal 内保留完整字段、校验、提交中、错误和取消状态。
* 中文化：
  * 翻译导航、页头、按钮、表单标签、placeholder、空状态、错误提示、分页控件、筛选/排序控件、确认弹窗等用户可见文案。
  * 状态类英文枚举如果作为技术标识有必要保留，可在旁边显示中文含义。
* 保持操作台气质：
  * 页面应更密、更可扫描，适合重复操作。
  * 不新增营销式 hero、装饰性视觉块或大面积说明文案。
* 验证：
  * 更新受影响的前端测试。
  * 运行前端测试和 build。
  * 如进入实现阶段，使用浏览器检查桌面/移动端布局，确认无明显留白浪费、重叠、溢出。

## Acceptance Criteria (evolving)

* [ ] App shell 宽屏下主内容区域明显扩大，左右留白减少。
* [ ] Workspace 详情不再把主要内容限制在一个小的 `Collections` tab 面板中。
* [ ] Workspace 内 DesignWork、DevWork、Events 至少有清晰的页面级切换或子路由式体验。
* [ ] 创建 DesignWork / DevWork 的流程使用弹窗或等价的页面级流程，而不是内嵌展开表单。
* [ ] 用户可见英文文案完成中文化，技术枚举除外。
* [ ] 分页、筛选、排序、空状态、错误状态在中文 UI 下仍可读且不溢出。
* [ ] 受影响测试更新并通过。
* [ ] `npm --prefix web run build` 通过。

## Definition of Done (team quality bar)

* Tests added/updated where UI behavior or assertions change.
* Lint / typecheck / CI green.
* Docs/notes updated if behavior changes.
* Rollout/rollback considered if risky.

## Out of Scope (explicit)

* 替换 React/Vite/Tailwind 技术栈。
* 引入重型组件框架。
* 改造认证、后端权限或会话模型。
* 完整多语言 i18n 框架，除非实现中发现硬编码中文会明显阻碍后续维护。
* 后端 API 字段、数据库字段、内部枚举全面中文化。
* 营销页、hero、插画和装饰性视觉重做。

## Technical Notes

* Key files inspected:
  * `web/package.json`
  * `web/src/router.tsx`
  * `web/src/index.css`
  * `web/src/pages/WorkspaceDetailPage.tsx`
  * `web/src/pages/WorkspaceDashboardPage.tsx`
  * `web/src/pages/WorkspacesPage.tsx`
  * `web/src/components/SectionPanel.tsx`
  * `web/src/components/PaginationControls.tsx`
  * `web/src/components/SegmentedControl.tsx`
  * `.trellis/spec/frontend/index.md`
  * `.trellis/spec/frontend/dashboard-collection-patterns.md`
  * `.trellis/tasks/archive/2026-05/05-06-frontend-layout-menu-pagination-interaction/prd.md`
* Code reference points:
  * Shell max width/sidebar/header: `web/src/router.tsx:181`, `web/src/router.tsx:182`, `web/src/router.tsx:279`
  * English nav/meta copy: `web/src/router.tsx:46`, `web/src/router.tsx:51`, `web/src/router.tsx:59`
  * Workspace tabs: `web/src/pages/WorkspaceDetailPage.tsx:37`, `web/src/pages/WorkspaceDetailPage.tsx:553`
  * Inline create forms: `web/src/pages/WorkspaceDetailPage.tsx:82`, `web/src/pages/WorkspaceDetailPage.tsx:276`
  * Workspace detail constrained grids: `web/src/pages/WorkspaceDetailPage.tsx:566`, `web/src/pages/WorkspaceDetailPage.tsx:631`
  * Shared panel density: `web/src/components/SectionPanel.tsx:10`
  * English pagination text: `web/src/components/PaginationControls.tsx:39`
* Constraints:
  * `rg` is unavailable in this desktop shell due access denied; used Git/PowerShell/Node inspection instead.
  * Existing frontend specs already prefer dense operational scanning.
  * Some files contain valid Chinese UTF-8; PowerShell output may display mojibake, so Node reads are more reliable for Chinese verification.

## Expansion Sweep

### Future evolution

* This could later become a true workspace workbench with persistent Workspace context, saved filters, command palette, and activity drawer.
* If future multilingual support matters, a thin local copy dictionary could evolve into a real i18n layer later.

### Related scenarios

* Repo detail, cross-workspace DevWorks, and Workspaces directory should eventually share the same density and Chinese UI rules.
* Detail pages for DesignWork and DevWork should also avoid low-density explanatory chrome after the shell is tightened.

### Failure and edge cases

* Long Chinese labels can wrap differently from English; buttons, segmented controls, side nav, table/list rows, and pagination must be checked on mobile and desktop.
* Modal forms need focus management and keyboard dismissal behavior if implemented with Headless UI Dialog.
* Route-level splitting should preserve back/forward behavior and direct links.

## Feasible Approaches

### Approach A: Workspace-first core pass (recommended)

* How it works:
  * Tighten app shell and header.
  * Redesign Workspace detail into page/subroute-level sections.
  * Move DesignWork/DevWork create forms into dialogs.
  * Translate shell, Workspace dashboard, Workspaces list, Workspace detail, shared controls.
* Pros:
  * Directly addresses the user's concrete complaint.
  * Lower blast radius than touching every screen at once.
  * Gives a reusable pattern for the rest of the app.
* Cons:
  * Some secondary pages may still contain English until a follow-up pass.

### Approach B: Full authenticated frontend pass

* How it works:
  * Apply layout, modal/page transitions, and Chinese copy to all authenticated routes in one task.
  * Includes Workspaces, Workspace detail, DesignWork detail, DevWork detail, cross-workspace DevWorks, Repos, Repo detail, shared controls, and tests.
* Pros:
  * No mixed Chinese/English experience across the app.
  * Best visible consistency.
* Cons:
  * Larger patch with higher regression risk.
  * More tests and browser verification needed in one iteration.

### Approach C: Add i18n layer first, then redesign

* How it works:
  * Introduce translation dictionaries/helpers first.
  * Then use those keys during layout redesign.
* Pros:
  * Better long-term if multiple languages are likely.
* Cons:
  * Adds infrastructure before the layout pain is fixed.
  * More moving parts for a product that currently only needs Chinese UI.

## Technical Approach (evolving)

Selected MVP: Approach B, full authenticated frontend pass.

Implementation shape:

* Shell:
  * Make the desktop shell full-width with smaller outer padding.
  * Convert the large header into a compact workbench masthead.
  * Reduce or remove sidebar explanatory cards that do not help repeated operation.
  * Translate shell labels and page meta.
  * Replace the global `CrossWorkspace DevWork` entry with `Agent Host 管理`.
  * Add a desktop sidebar collapse toggle and preserve the collapsed state locally.
* Workspace detail:
  * Promote `designs`, `devworks`, and `events` into route-like views or clear page-level navigation.
  * Give each collection full-width list space.
  * Move create forms to modals.
* Secondary authenticated pages:
  * Apply the same compact layout and Chinese UI rules to dashboard, Workspaces, DesignWork detail, DevWork detail, cross-workspace DevWorks, Repos, and Repo detail.
  * Keep route structure stable unless changing a route materially improves Workspace page-level navigation.
  * Add an `Agent Host` management page that follows the existing operational collection pattern instead of introducing a separate visual system.
* Shared controls:
  * Translate `PaginationControls`, `SegmentedControl` labels supplied by callers, `SectionPanel` usage, empty/error states.
  * Add compact variants only if needed by existing patterns.
* Verification:
  * Update page/component tests.
  * Run `npm --prefix web run test`.
  * Run `npm --prefix web run build`.
  * Start Vite and inspect main desktop/mobile screens.

## Decision (ADR-lite)

**Context**: The first improvement option would address the Workspace path quickly, but the user explicitly chose a full frontend pass so the app does not remain half Chinese and half English or half compact and half card-heavy.

**Decision**: Implement Approach B. Apply layout density, page/modal interaction boundaries, and Chinese user-visible copy across authenticated frontend routes in this task.

**Consequences**: The patch will touch more pages and tests, so verification must include frontend tests, TypeScript build, and browser layout inspection. Backend contracts should remain unchanged unless an existing route already supports the needed behavior.

## Additional Acceptance Criteria (2026-05-06)

* [ ] The desktop shell no longer shows a `CrossWorkspace DevWork` global menu item.
* [ ] The desktop shell shows an `Agent Host 管理` global menu item that routes to a working management page.
* [ ] Users can create an agent host server from the frontend against the existing `/api/v1/agent-hosts` API.
* [ ] The desktop left sidebar can be collapsed and expanded without breaking navigation readability.
