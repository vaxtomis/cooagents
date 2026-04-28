"""Phase 8: frozen contract — WebhookEvent enum + outbound envelope keys.

These tests fail loudly on any accidental schema drift. When you need to
add or rename an event, update `_EXPECTED_EVENT_NAMES` deliberately in the
same commit as the enum change.

Envelope shape + signature header are exercised functionally in
`test_webhook_notifier.py`; this module adds a small additional enum
snapshot so a new event cannot leak into production without going through
`WebhookEvent` + KNOWN_EVENTS.
"""
from __future__ import annotations

from src.webhook_events import KNOWN_EVENTS, WebhookEvent


# Frozen list — keep alphabetical for readability. Any diff here forces a
# deliberate contract update in the same commit as the enum change.
_EXPECTED_EVENT_NAMES: frozenset[str] = frozenset({
    "design_doc.published",
    "design_work.cancelled",
    "design_work.escalated",
    "design_work.llm_completed",
    "design_work.mockup_recorded",
    "design_work.round_completed",
    "design_work.started",
    "dev_work.cancelled",
    "dev_work.completed",
    "dev_work.escalated",
    "dev_work.gate.exit_waiting",
    "dev_work.merge_conflict",
    "dev_work.progress",
    "dev_work.round_completed",
    "dev_work.score_passed",
    "dev_work.started",
    "dev_work.step_completed",
    "dev_work.step_started",
    "webhook.delivery_failed",
    "workspace.archived",
    "workspace.created",
    "workspace.human_intervention",
})


def test_webhook_event_enum_snapshot():
    """Frozen set of event names. Adding/removing requires an explicit diff here."""
    actual = frozenset(e.value for e in WebhookEvent)
    missing = _EXPECTED_EVENT_NAMES - actual
    extra = actual - _EXPECTED_EVENT_NAMES
    assert not missing and not extra, (
        f"WebhookEvent enum drift — missing={sorted(missing)} extra={sorted(extra)}. "
        "Update _EXPECTED_EVENT_NAMES in this test if the change is intentional."
    )


def test_known_events_matches_enum():
    """KNOWN_EVENTS must stay derived from WebhookEvent (no hand-rolled strings)."""
    assert KNOWN_EVENTS == frozenset(e.value for e in WebhookEvent)


def test_every_event_value_is_dotted_namespace():
    """Every event name follows ``namespace.action[.qualifier]`` convention."""
    for event in WebhookEvent:
        parts = event.value.split(".")
        assert 2 <= len(parts) <= 3, (
            f"event {event.value!r} must have 2 or 3 dot-separated segments"
        )
        for part in parts:
            assert part and part.replace("_", "").isalnum(), (
                f"event segment {part!r} in {event.value!r} must be snake_case alnum"
            )
