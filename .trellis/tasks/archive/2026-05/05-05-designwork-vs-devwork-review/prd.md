# brainstorm: review designwork vs devwork optimization

## Goal

Review the current `DesignWork` implementation and UX, compare it with `DevWork`, and define actionable improvements for both frontend design and document-contract quality.

## What I already know

* The repo has separate `DesignWorkPage` and `DevWorkPage` flows, plus separate API clients and progress components.
* `DesignWork` backend is a D0-D7 state machine centered on design-doc generation, validation, optional mockup, and publish.
* `DevWork` backend is a 5-step state machine centered on iteration notes, context retrieval, implementation, review, and gate flow.
* Frontend tests for `DesignWorkPage`, `DevWorkPage`, and `WorkspaceDetailPage` are currently passing.
* `DevWork` UI is more mature than `DesignWork` in information architecture, context visibility, and operational affordances.
* `DesignWork` already has a mostly clear state flow and final artifact path, but chapter-level output formatting is still weak and relies mostly on prompt guidance.
* The chosen direction is `Prompt + Validator`, not prompt-only.
* Not all design tasks are user-facing features; some are backend or internal-logic optimizations.
* The user chose to replace `用户案例` with a more general `场景案例`.
* The user chose medium-strength validation for `场景案例`.
* The user chose medium-strength validation for `验收标准`.
* The user chose medium-strength validation for `打分 rubric`.

## Assumptions (temporary)

* The current focus is requirements and contract refinement, not immediate implementation.
* We should preserve a single DesignDoc contract whenever possible instead of splitting into multiple document templates.

## Open Questions

* None at the moment.

## Requirements (evolving)

* Compare `DesignWork` and `DevWork` in page structure, state presentation, operational flow, and feedback quality.
* Compare frontend code reuse, naming consistency, responsibility boundaries, and maintainability.
* Distinguish between UX/design gaps and code-structure debt.
* Document the current `DesignWork` input/output contract.
* Identify which DesignDoc chapters need stricter formatting constraints.
* Cover at least `场景案例`, `验收标准`, and `打分 rubric`.
* The new constraints must work for both user-facing feature design and backend/internal optimization design.
* Keep one unified document contract unless a split is clearly necessary.
* `验收标准` should be validated at medium strength.
* `打分 rubric` should be validated at medium strength.

## Acceptance Criteria (evolving)

* [ ] Clear list of `DesignWork` UX gaps relative to `DevWork`
* [ ] Clear list of frontend code-structure improvement points
* [ ] Clear `DesignWork` I/O contract by state
* [ ] Concrete formatting constraints proposed for `场景案例`, `验收标准`, and `打分 rubric`
* [ ] Recommendations distinguish low-cost alignment from larger redesign/refactor work

## Definition of Done

* Conclusions are grounded in the actual repo code and current implementation.
* Recommendations cover both UX/design and code-structure concerns.
* DesignDoc contract proposals are precise enough to implement in prompt and validator logic.

## Out of Scope

* No backend state-machine logic changes yet
* No direct page refactor unless requested later

## Technical Notes

* Task directory: `.trellis/tasks/05-05-designwork-vs-devwork-review`
* Relevant frontend files:
  * `web/src/pages/DesignWorkPage.tsx`
  * `web/src/pages/DevWorkPage.tsx`
  * `web/src/pages/WorkspaceDetailPage.tsx`
  * `web/src/components/DesignWorkStateProgress.tsx`
  * `web/src/components/DevWorkStepProgress.tsx`
  * `web/src/pages/CrossWorkspaceDevWorkPage.tsx`
  * `web/src/router.tsx`
* Relevant backend/document-contract files:
  * `src/design_work_sm.py`
  * `routes/design_works.py`
  * `src/design_prompt_composer.py`
  * `src/design_validator.py`
  * `src/design_doc_manager.py`
  * `templates/design_prompt.md.tpl`
  * `templates/design_doc.md.tpl`
* Current confirmed gaps:
  * `DesignWork` supports repo binding on create, but its detail page does not expose repo context.
  * `DesignWork` uses an unlabeled progress strip; `DevWork` uses labeled step pills.
  * `DevWork` uses tabs and master-detail structure; `DesignWork` is still a single long stacked page.
  * The two detail pages already have meaningful duplicate code that could be extracted.
* Current `DesignWork` I/O reality:
  * Create input is explicitly modeled by `CreateDesignWorkRequest`.
  * Prompt input is explicitly modeled by `PromptInputs` and persisted to `.drafts/*prompt-loopN.md`.
  * LLM output is delivered as a file side effect, not a structured return object.
  * Final DesignDoc path, core front-matter fields, and required H2 sections are code-constrained.
  * `design_doc.md.tpl` behaves more like a target sample; runtime enforcement comes from `design_prompt.md.tpl` plus `validate_design_markdown()`.
* Current decisions:
  * Use `Prompt + Validator`
  * Rename `用户案例` to `场景案例`
  * Keep a single document contract
  * `场景案例` validation strength: medium
* Each scenario case must start with `### SC-xx <title>`
* Each scenario case must include `Actor`, `Main Flow`, and `Expected Result`
* `Trigger` and `Preconditions` stay as prompt-guided recommended fields, not hard validator requirements
* `验收标准` validation strength: medium
* Each acceptance item should use checklist form with `AC-xx` numbering
* Acceptance items must remain testable and observable, but validator does not need to enforce one fixed sentence pattern
* `打分 rubric` validation strength: medium
* Rubric should be a markdown table
* Rubric must contain at least `维度 | 权重 | 判定标准`
* `权重` should be an integer-like value per row
* Total weight can be recommended by prompt, but is not a hard validator requirement
