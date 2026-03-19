import json


MAX_EVENT_REPEATS = 3


def _payload_matches(payload_json, match_fields):
    if not match_fields:
        return True
    if not payload_json:
        return False
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return all(payload.get(key) == value for key, value in match_fields.items())


async def count_matching_events(db, run_id, event_type, match_fields=None):
    rows = await db.fetchall(
        "SELECT payload_json FROM events WHERE run_id=? AND event_type=?",
        (run_id, event_type),
    )
    return sum(1 for row in rows if _payload_matches(row.get("payload_json"), match_fields or {}))


async def can_emit_event(db, run_id, event_type, match_fields=None, max_count=MAX_EVENT_REPEATS):
    count = await count_matching_events(db, run_id, event_type, match_fields or {})
    return count < max_count
