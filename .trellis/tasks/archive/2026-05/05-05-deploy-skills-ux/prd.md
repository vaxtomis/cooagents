# brainstorm: optimize cooagents deployment and skills UX

## Goal

Optimize cooagents deployment scripts and related Skills so both human operators and AI agents can set up, upgrade, and troubleshoot the project with less friction, fewer manual steps, and clearer guidance.

## What I already know

* The user wants to optimize deployment scripts and Skills for easier deployment by both humans and AI.
* There is currently no active Trellis task for this work, so a new brainstorm task was created at `.trellis/tasks/05-05-deploy-skills-ux/`.
* The repo has a deployment/bootstrap entry point at `scripts/bootstrap.sh`.
* The repo has user-facing Skills at `skills/cooagents-setup/SKILL.md` and `skills/cooagents-upgrade/SKILL.md`.
* The repo is a single-package project with the `backend` spec layer.
* `scripts/bootstrap.sh` currently handles environment validation, `acpx` installation, Python deps install, local frontend build, runtime dir creation, and database initialization.
* `src/app.py` deploys `skills/` at service startup via `src.skill_deployer.deploy_skills(settings)`.
* `src/skill_deployer.py` currently copies local Skills to configured OpenClaw and Hermes directories, but SSH target deployment is explicitly unimplemented.
* The setup Skill currently includes many responsibilities beyond bootstrap: auth env generation, service start, health checks, local agent host registration, and optional OpenClaw/Hermes integration.
* The README currently documents two first-install entry points: direct `./scripts/bootstrap.sh` and invoking `/cooagents-setup`.
* The setup Skill itself documents a bootstrap paradox for first install: before cooagents is running, the Skill must be copied manually into the host Skills directory or read from the cloned repo path.
* Existing tests assert that bootstrap performs local web build, and that setup/upgrade Skills validate both `/health` and the dashboard root HTML.
* Code/docs reference a `cooagents-workflow` skill as part of the deployed bundle, but the current `skills/` directory only contains `cooagents-setup` and `cooagents-upgrade`.
* The user chose full-chain scope for this task.
* For the first implementation round, the user chose a unified non-interactive CLI core as the primary architecture.
* The user chose `Repo-first` as the canonical first-install path: clone repo first, then run the unified deployment core; Skills become post-install convenience, not the primary bootstrap path.
* The user chose a mostly conservative automation boundary: the unified CLI should own repo-internal work and additionally own service start/restart, but not fully automate broader machine-external concerns like reverse proxy or systemd unit authoring by default.
* The user chose `Hybrid` as the canonical remote/SSH model: remote machines should still use the same repo-first local CLI core, while the controller side may provide an SSH wrapper that remotely triggers that same path.
* The user clarified that OpenClaw/Hermes and `cooagents-worker` have fundamentally different roles:
  * OpenClaw/Hermes are notification and interaction consumers that read progress from cooagents.
  * `cooagents-worker` is the actual execution layer for cooagents tasks.
* Because of that distinction, execution-host modeling and notification/runtime integration modeling should not be collapsed into one undifferentiated host abstraction.
* The user chose to keep those layers separate in the target design:
  * `agent_hosts` remains execution-plane modeling.
  * OpenClaw/Hermes remain separate notifier/runtime integration modeling.

## Assumptions (temporary)

* "Deployment" here includes local environment setup, dependency install, configuration bootstrap, and upgrade/repair flows, not only production release.
* The pain points likely span both executable scripts and the instructional flow inside Skills.
* We should improve the experience without fundamentally changing the product architecture unless current deployment constraints force it.
* The biggest current friction is likely the split between executable automation in scripts and large amounts of imperative logic duplicated in SKILL instructions.
* A good result probably centralizes more logic into executable, testable commands and leaves Skills thinner and more orchestrational.
* Earlier thinking that `agent_hosts` might become the single source of truth for all remote integration targets is likely too coarse given the user's clarification about execution vs notification roles.

## Open Questions

* None blocking. Ready for final requirement confirmation.

## Requirements (evolving)

* Audit current deployment/setup scripts and related Skills.
* Identify friction points for both human and AI-driven setup flows.
* Propose a clearer, lower-friction deployment path.
* Preserve the ability to verify successful install/upgrade through explicit health and dashboard checks.
* Preserve or improve secret-handling safety around `.env`, `AGENT_API_TOKEN`, webhook secrets, and runtime-specific tokens.
* Treat this task as full-chain scope: first install, upgrade/repair, dual-runtime host integration, and remote/SSH skill deployment are all in scope for the design.
* The core deployment logic should move into a unified non-interactive CLI or equivalent executable entrypoint that both humans and Skills/agents can call.
* Skills should become thin orchestration layers over the same executable deployment core instead of carrying most logic in prose.
* The canonical first-install path should be repo-first: obtain the repo, then run the unified deployment core from within the repo.
* The unified deployment core should own repo-local automation, including service start/restart, while keeping broader machine-level infrastructure configuration explicit by default.
* Remote/SSH deployment should follow a hybrid model: the remote host runs the same repo-local deployment core, while a controller-side SSH wrapper may invoke it remotely for convenience.
* Execution-host modeling should stay centered on `agent_hosts` / `cooagents-worker`.
* OpenClaw/Hermes integration should be treated as notifier/interaction-runtime integration, not as task execution hosts.
* OpenClaw/Hermes integration should remain a separate notifier/runtime integration model rather than being folded into `agent_hosts`.

## Acceptance Criteria (evolving)

* [ ] Current deployment/setup flow is documented from code and repo inspection.
* [ ] Human and AI pain points are identified with concrete examples.
* [ ] A scoped MVP improvement plan is agreed before implementation.
* [ ] The chosen MVP explicitly states which scenarios are in scope: first install, upgrade, runtime integration, remote deployment.
* [ ] The final design reduces duplicated logic between shell scripts and Skill instructions.
* [ ] The agreed design explicitly covers first install, upgrade/repair, OpenClaw/Hermes integration, and remote/SSH deployment.
* [ ] A single executable deployment core is identified as the canonical path for both human and AI-driven operations.
* [ ] The final design makes repo-first installation the primary documented and tested entry path.
* [ ] The final design clearly defines the primary model for remote/SSH deployment and how it relates to the local CLI core.
* [ ] The final design defines one canonical host inventory/source-of-truth model for remote deployment targets.
* [ ] The final design cleanly separates execution-host concerns from OpenClaw/Hermes notifier/runtime integration concerns.
* [ ] The final design keeps `agent_hosts` execution-only while still simplifying OpenClaw/Hermes setup and skill deployment through the unified deployment core.

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* Full product architecture redesign unrelated to deployment/setup ergonomics
* Non-deployment features outside scripts, setup flow, or related Skills

## Technical Approach (evolving)

Use a unified non-interactive deployment core as the canonical entrypoint. Humans can call it directly; Skills should gather the minimal required inputs, invoke the same core, then present results and recovery guidance.

Execution-host deployment and notifier/runtime integration remain separate layers:

* Execution plane: `agent_hosts`, SSH reachability, `cooagents-worker`, remote CLI invocation.
* Notification/interaction plane: OpenClaw and Hermes integration, skill sync, hooks/webhooks, runtime-facing env injection.

The unified deployment core should orchestrate both layers through one command surface, but keep their configuration and responsibilities distinct.

## Decision (ADR-lite)

**Context**: Current deployment behavior is split across `bootstrap.sh`, long `SKILL.md` instructions, startup-time skill sync, and runtime-specific branching. This creates duplication, drift risk, and weak testability.

**Decision**: The first implementation round will center on a unified non-interactive deployment core instead of continuing to expand Skill-only logic.

**Consequences**: This should improve testability, reduce documentation drift, and let both humans and AI agents follow the same contract.

**Context**: First install currently has a bootstrap paradox because the setup Skill is useful before cooagents is running, but also depends on manual Skill placement or repo-path prompting.

**Decision**: The canonical first-install path will be repo-first rather than Skill-first. Users or agents first get the repo, then invoke the same deployment core from inside it.

**Consequences**: This removes the Skill bootstrap paradox from the main happy path and narrows the job of Skills to post-install convenience and orchestration.

**Context**: Deployment convenience needs automation, but over-automating machine-wide concerns like reverse proxy, service managers, and host runtime mutation would widen the blast radius and weaken predictability.

**Decision**: Use a mostly conservative automation boundary. The unified deployment core owns repo-internal setup plus service start/restart, but broader machine-level infrastructure remains explicit unless later requirements force more automation.

**Consequences**: This keeps the main path testable and lower-risk while still removing the most common manual steps.

**Context**: Remote/SSH support is required, but pushing all logic from the controller would drift away from the repo-first local-core model and create a second implementation path.

**Decision**: Use a hybrid remote model. Remote machines still use the same repo-local deployment core, and the controller may offer an SSH wrapper that invokes that same command remotely.

**Consequences**: This preserves one executable core across local and remote flows.

**Context**: The current codebase has overlapping remote-target concepts, but the user clarified that `cooagents-worker` and OpenClaw/Hermes are not the same class of target: one executes tasks, the others consume progress and provide interaction entrypoints.

**Decision**: Do not collapse execution hosts and notifier runtimes into one undifferentiated remote-target abstraction.

**Consequences**: Execution-host deployment should align with `agent_hosts` and `cooagents-worker`, while OpenClaw/Hermes remain a separate notifier/runtime integration concern.

**Context**: There was still a possible follow-up choice between introducing a generic notifier-runtime abstraction versus preserving OpenClaw/Hermes as a separate but explicit integration layer.

**Decision**: Keep OpenClaw/Hermes as a separate notifier/runtime integration model rather than forcing a generic merged abstraction in the first round.

**Consequences**: This preserves the real architectural distinction, lowers refactor risk, and still allows the deployment experience to be unified at the CLI layer.

## Technical Notes

* Created via Trellis brainstorm workflow.
* Initial files to inspect:
  * `README.md`
  * `scripts/bootstrap.sh`
  * `skills/cooagents-setup/SKILL.md`
  * `skills/cooagents-upgrade/SKILL.md`
* Additional files inspected:
  * `src/app.py`
  * `src/skill_deployer.py`
  * `src/agent_worker/cli.py`
  * `src/auth.py`
  * `config/settings.yaml`
  * `scripts/generate_password_hash.py`
  * `tests/test_bootstrap_flow.py`
  * `tests/test_skill_deployer.py`
  * `.trellis/spec/backend/index.md`
* Current friction candidates from repo inspection:
  * First-install Skill acquisition is manual even though later deployment is automatic.
  * Setup Skill contains large platform-branching instructions that are only partially executable and hard to test.
  * Upgrade flow depends on bootstrap side effects and service restart details encoded in prose.
  * SSH skill deployment is declared in config support but not implemented in code.
  * There may be drift between documented/deployed Skills and the actual `skills/` bundle contents (`cooagents-workflow` reference).
  * Repo conventions already support `argparse`-based Python CLIs, which makes a script/CLI-first deployment design plausible.
  * Auth/env generation already has a reusable executable contract in `scripts/generate_password_hash.py`; some setup logic is already scriptable rather than Skill-only.
  * The repo already has a real SSH/remote execution model for agent hosts (`SshDispatcher`, `HealthProbeLoop`, `cooagents-worker`), so deployment design should align with that instead of inventing a second remote model.
  * `config/settings.yaml` and `src.config` already encode OpenClaw SSH targets and Hermes/OpenClaw integration knobs, but current skill deployment only implements local filesystem copies.
  * `SshDispatcher.run_remote()` is already implemented for remote work execution, while `skill_deployer` still treats SSH skill deployment as unimplemented.
  * Current code has overlapping remote-target concepts: generic `agent_hosts` registry on one side, and runtime-specific deployment targets under `openclaw.targets` on the other.
  * The user clarified an architectural boundary that supports keeping these as distinct layers rather than forcing consolidation:
    * `agent_hosts` / `cooagents-worker` = execution plane
    * OpenClaw/Hermes = notification + interaction plane
