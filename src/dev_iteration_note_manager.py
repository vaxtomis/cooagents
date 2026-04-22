"""DevWork iteration note manager — FS path resolution + DB row lifecycle.

Split on purpose (F2=B): the SM writes the header first, lets the LLM append
the three required H2 sections to the markdown, then asks the manager to
``record_round`` the on-disk file.  Merging the two steps would force the SM
to hand the manager a prompt/LLM indirection it doesn't own.

Responsibilities:
  * ``path_for`` — deterministic absolute path under workspaces_root
  * ``record_round`` — INSERT dev_iteration_notes (UNIQUE(dev_work_id, round))
  * ``latest_for`` — most recent note per dev_work
  * ``append_score`` — append integer to score_history_json array
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.exceptions import BadRequestError

logger = logging.getLogger(__name__)

# Hard cap on the score_history_json array length. max_rounds=5 means at
# most ~5 entries per note in normal operation; 100 is defense against
# backfill / migration abuse that could grow the row without bound.
_SCORE_HISTORY_MAX = 100


class DevIterationNoteManager:
    def __init__(self, db: Any, workspaces_root: Path | str) -> None:
        self.db = db
        self.workspaces_root = Path(workspaces_root).expanduser().resolve()

    @staticmethod
    def _new_id() -> str:
        return f"note-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def path_for(
        self, workspace_row: dict[str, Any], dev_work_id: str, round_n: int
    ) -> Path:
        """Return the absolute iteration-note path; enforce workspaces_root."""
        slug_dir = self.workspaces_root / workspace_row["slug"]
        target = (
            slug_dir / "devworks" / dev_work_id
            / f"iteration-round-{round_n}.md"
        )
        resolved = target.resolve()
        try:
            resolved.relative_to(self.workspaces_root)
        except ValueError as exc:
            raise BadRequestError(
                f"iteration note path escapes workspaces_root: {target}"
            ) from exc
        return target

    async def record_round(
        self,
        *,
        workspace_row: dict[str, Any],
        dev_work_id: str,
        round_n: int,
        markdown_path: str,
    ) -> dict[str, Any]:
        """INSERT dev_iteration_notes row for an already-on-disk markdown.

        ``UNIQUE(dev_work_id, round)`` enforces single entry per round.
        Duplicates raise ``sqlite IntegrityError`` — the SM treats that as a
        fatal invariant violation and escalates.
        """
        # workspace_row is accepted for symmetry with DesignDocManager; only
        # used to double-check the derived path is still under workspaces_root.
        self.path_for(workspace_row, dev_work_id, round_n)
        note_id = self._new_id()
        now = self._now()
        await self.db.execute(
            "INSERT INTO dev_iteration_notes"
            "(id, dev_work_id, round, markdown_path, score_history_json, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (note_id, dev_work_id, round_n, markdown_path, None, now),
        )
        return {
            "id": note_id,
            "dev_work_id": dev_work_id,
            "round": round_n,
            "markdown_path": markdown_path,
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
        # Keep the tail — newest entries are the ones Phase 8 metrics need.
        if len(history) > _SCORE_HISTORY_MAX:
            history = history[-_SCORE_HISTORY_MAX:]
        await self.db.execute(
            "UPDATE dev_iteration_notes SET score_history_json=? WHERE id=?",
            (json.dumps(history), note_id),
        )
