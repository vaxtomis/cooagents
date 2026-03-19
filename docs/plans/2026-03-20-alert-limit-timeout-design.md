# Alert Limit And Timeout Alignment Design

## Goal

Limit repeated workflow alerts to at most 3 sends per logical event, and make execution timeout handling consistent across scheduler, state machine, and acpx invocation.

## Scope

- Cap repeated `host.unavailable`, `review.reminder`, and `job.timeout` notifications at 3.
- Reuse the existing `events` table for counting; do not add new schema.
- Make design/dev execution timeout values come from config instead of hardcoded literals in the state machine.
- Ensure scheduler-detected job timeouts mark the job as `timeout` and drive the run to `FAILED`.

## Design

- Add a small shared helper to count prior matching events by `run_id + event_type + selected payload fields`.
- Use that helper in `StateMachine` for `host.unavailable`.
- Use that helper in `Scheduler` for `review.reminder` and `job.timeout`, and persist those events before notifying.
- Read design/dev execution timeouts from `config.timeouts` with current defaults as fallback.
- Extend session cancellation so scheduler timeouts can terminate a session while preserving the terminal job status as `timeout`.

## Verification

- Repeated `host.unavailable` only records/notifies 3 times.
- Repeated `review.reminder` only records/notifies 3 times.
- Scheduler timeout marks the job `timeout`, emits `job.timeout` up to 3 times, and transitions the run to `FAILED`.
- Design dispatch/follow-up use configured timeout values.
