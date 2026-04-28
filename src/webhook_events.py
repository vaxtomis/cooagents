"""Frozen event-name registry for outbound webhooks.

Why: PRD Phase 5 forbids ad-hoc event strings. Notifier asserts every
outbound event_name is in KNOWN_EVENTS so a typo can never enter the
contract silently. Adding a new event = adding a member here AND
updating tests/openclaw_event_contract.json.

Scope note: DEV_WORK_GATE_ENTRY_WAITING is NOT registered — "entry gate"
is the user's own POST /dev-works action (picking a design version +
writing a prompt), not an SM waiting state. Only the exit gate becomes
a SM waiting state, and only when
config.devwork.require_human_exit_confirm=true (v1 default false).
"""
from __future__ import annotations

from enum import StrEnum


class WebhookEvent(StrEnum):
    # Workspace lifecycle
    WORKSPACE_CREATED = "workspace.created"
    WORKSPACE_ARCHIVED = "workspace.archived"
    WORKSPACE_HUMAN_INTERVENTION = "workspace.human_intervention"

    # DesignWork state machine
    DESIGN_WORK_STARTED = "design_work.started"
    DESIGN_WORK_ROUND_COMPLETED = "design_work.round_completed"
    DESIGN_WORK_ESCALATED = "design_work.escalated"
    DESIGN_WORK_CANCELLED = "design_work.cancelled"
    DESIGN_WORK_LLM_COMPLETED = "design_work.llm_completed"
    DESIGN_WORK_MOCKUP_RECORDED = "design_work.mockup_recorded"

    # DesignDoc publication
    DESIGN_DOC_PUBLISHED = "design_doc.published"

    # DevWork state machine
    DEV_WORK_STARTED = "dev_work.started"
    DEV_WORK_STEP_STARTED = "dev_work.step_started"
    DEV_WORK_STEP_COMPLETED = "dev_work.step_completed"
    # Phase 3 (devwork-acpx-overhaul): naming-convention parity only.
    # Heartbeat events are written via emit_workspace_event (table-only) and
    # are NOT delivered through WebhookNotifier.deliver in Phase 3. Kept on
    # the enum so consumers can opt in once the delivery flag flips.
    DEV_WORK_PROGRESS = "dev_work.progress"
    DEV_WORK_ROUND_COMPLETED = "dev_work.round_completed"
    DEV_WORK_SCORE_PASSED = "dev_work.score_passed"
    DEV_WORK_ESCALATED = "dev_work.escalated"
    DEV_WORK_CANCELLED = "dev_work.cancelled"
    DEV_WORK_COMPLETED = "dev_work.completed"

    # Gates (only exit gate exists in v1)
    DEV_WORK_GATE_EXIT_WAITING = "dev_work.gate.exit_waiting"

    # Merge — registered for forward compatibility; no emit site in
    # Phase 5 (merge_manager link is dumb under the new schema and will
    # be rewritten in a later phase).
    DEV_WORK_MERGE_CONFLICT = "dev_work.merge_conflict"

    # Internal — failure self-log
    WEBHOOK_DELIVERY_FAILED = "webhook.delivery_failed"


KNOWN_EVENTS: frozenset[str] = frozenset(e.value for e in WebhookEvent)
