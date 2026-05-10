"""DevWork iteration note manager — workspace-relative path + DB row lifecycle.

Phase 3 refactor: the manager no longer composes absolute filesystem paths.
``relative_for`` returns a workspace-relative POSIX key; the caller is
responsible for writing bytes via ``WorkspaceFileRegistry``. ``record_round``
INSERTs the dev_iteration_notes row with that relative path.

Responsibilities:
  * ``relative_for`` — deterministic workspace-relative POSIX key
  * ``record_round`` — INSERT dev_iteration_notes (UNIQUE(dev_work_id, round))
  * ``latest_for`` — most recent note per dev_work
  * ``append_score`` — append integer to score_history_json array
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.storage.base import normalize_key

logger = logging.getLogger(__name__)

# Hard cap on the score_history_json array length. max_rounds=10 means at
# most ~10 entries per note in normal operation; 100 is defense against
# backfill / migration abuse that could grow the row without bound.
_SCORE_HISTORY_MAX = 100


class DevIterationNoteManager:
    def __init__(self, db: Any) -> None:
        self.db = db

    @staticmethod
    def _new_id() -> str:
        return f"note-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def relative_for(dev_work_id: str, round_n: int) -> str:
        """Workspace-relative POSIX key for the iteration note markdown."""
        return f"devworks/{dev_work_id}/iteration-round-{round_n}.md"

    async def record_round(
        self,
        *,
        workspace_row: dict[str, Any],  # noqa: ARG002 — reserved for Phase 5 register()
        dev_work_id: str,
        round_n: int,
        markdown_path: str,
    ) -> dict[str, Any]:
        """INSERT dev_iteration_notes with a workspace-relative markdown_path.

        ``UNIQUE(dev_work_id, round)`` enforces single entry per round.
        ``markdown_path`` is validated via ``normalize_key`` — leading '/',
        backslash, drive letter, or '..' segments raise ``BadRequestError``.
        """
        rel = normalize_key(markdown_path).as_posix()
        note_id = self._new_id()
        now = self._now()
        await self.db.execute(
            "INSERT INTO dev_iteration_notes"
            "(id, dev_work_id, round, markdown_path, score_history_json, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (note_id, dev_work_id, round_n, rel, None, now),
        )
        return {
            "id": note_id,
            "dev_work_id": dev_work_id,
            "round": round_n,
            "markdown_path": rel,
            "score_history_json": None,
            "created_at": now,
        }

    async def latest_for(self, dev_work_id: str) -> dict[str, Any] | None:
        return await self.db.fetchone(
            "SELECT * FROM dev_iteration_notes WHERE dev_work_id=? "
            "ORDER BY round DESC LIMIT 1",
            (dev_work_id,),
        )

    async def append_score(self, note_id: str, score: int) -> None:
        """Append an integer score to the note's score_history JSON array."""
        row = await self.db.fetchone(
            "SELECT score_history_json FROM dev_iteration_notes WHERE id=?",
            (note_id,),
        )
        if row is None:
            logger.warning("append_score: note %s not found", note_id)
            return
        raw = row["score_history_json"]
        if raw:
            try:
                history = json.loads(raw)
                if not isinstance(history, list):
                    history = []
            except (ValueError, TypeError):
                history = []
        else:
            history = []
        history.append(int(score))
        if len(history) > _SCORE_HISTORY_MAX:
            history = history[-_SCORE_HISTORY_MAX:]
        await self.db.execute(
            "UPDATE dev_iteration_notes SET score_history_json=? WHERE id=?",
            (json.dumps(history), note_id),
        )
