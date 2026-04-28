"""DevWork Step5 review parser (Phase 4).

The reviewer only does **shape validation**:

  * Extract JSON from one of three sources (file > fenced block > bare JSON).
  * Verify ``score`` is an int; ``issues`` is a list; ``problem_category`` is
    a valid :class:`ProblemCategory` member or None.
  * Return a :class:`ReviewOutcome` the SM can act on.

The score/threshold comparison and first_pass_success rule are business
decisions belonging to the SM (PRD L191), so we keep them out of here.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.exceptions import BadRequestError
from src.models import ProblemCategory

logger = logging.getLogger(__name__)

# Capture last ```json``` fenced block in the stdout.  DOTALL so ``.`` spans
# newlines; non-greedy ``.*?`` so we don't eat across fences.
_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

# Allowed values for ``next_round_hints[].kind``. Mirrors the enum spelled
# out in ``_NEXT_ROUND_HINTS_GUIDE`` in dev_prompt_composer — extending
# either side requires updating both in lockstep.
_NEXT_ROUND_HINT_KINDS = ("missing_feature", "optimization")


@dataclass(frozen=True)
class ReviewOutcome:
    score: int
    issues: list[dict]
    problem_category: ProblemCategory | None
    next_round_hints: list[dict] = field(default_factory=list)


def _coerce(payload: dict) -> ReviewOutcome:
    if "score" not in payload:
        raise BadRequestError("review output missing 'score'")
    try:
        score = int(payload["score"])
    except (TypeError, ValueError) as exc:
        raise BadRequestError("review output 'score' not an int") from exc

    raw_issues = payload.get("issues")
    if raw_issues is None:
        issues: list[dict] = []
    elif isinstance(raw_issues, list):
        # Normalise non-dict entries to {"message": str(item)} so SM/logging
        # code can assume ``list[dict]`` without blowing up.
        issues = [i if isinstance(i, dict) else {"message": str(i)} for i in raw_issues]
    else:
        raise BadRequestError("review output 'issues' must be a list")

    raw_cat = payload.get("problem_category")
    category: ProblemCategory | None
    if raw_cat is None or raw_cat == "":
        category = None
    else:
        try:
            category = ProblemCategory(raw_cat)
        except ValueError as exc:
            raise BadRequestError(
                f"review output 'problem_category' must be one of "
                f"{[c.value for c in ProblemCategory]} or null; got {raw_cat!r}"
            ) from exc

    raw_hints = payload.get("next_round_hints")
    if raw_hints is None:
        hints: list[dict] = []
    elif isinstance(raw_hints, list):
        # Mirror ``issues`` normalisation: non-dict entries become
        # ``{"message": str(item)}`` so consumers can assume list[dict].
        hints = [
            h if isinstance(h, dict) else {"message": str(h)}
            for h in raw_hints
        ]
        # Enum guard on ``kind``: present values must match the documented
        # set; missing/empty ``kind`` is allowed (rendered without prefix).
        for h in hints:
            kind = h.get("kind")
            if kind in (None, ""):
                continue
            if kind not in _NEXT_ROUND_HINT_KINDS:
                raise BadRequestError(
                    f"review output 'next_round_hints[].kind' must be one of "
                    f"{list(_NEXT_ROUND_HINT_KINDS)} or omitted; got {kind!r}"
                )
    else:
        raise BadRequestError(
            "review output 'next_round_hints' must be a list"
        )

    return ReviewOutcome(
        score=score,
        issues=issues,
        problem_category=category,
        next_round_hints=hints,
    )


def _parse_from_text(text: str) -> dict:
    """Try fenced ``` ```json ... ``` `` block first, then bare json.loads."""
    matches = _FENCE_RE.findall(text or "")
    if matches:
        # Use the LAST fenced block — LLMs sometimes echo an example earlier.
        try:
            return json.loads(matches[-1])
        except json.JSONDecodeError as exc:
            raise BadRequestError(
                f"review output JSON fence not valid JSON: {exc.msg}"
            ) from exc
    stripped = (text or "").strip()
    if not stripped:
        raise BadRequestError("review output empty")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise BadRequestError(
            f"review output neither fenced nor bare JSON: {exc.msg}"
        ) from exc


def parse_review_output(
    stdout: str, output_json_path: str | None = None
) -> ReviewOutcome:
    """Parse Step5 LLM output.

    Resolution order (first success wins):

      1. Read ``output_json_path`` if it exists and is non-empty.
      2. Look for a ```json``` fenced block in ``stdout``.
      3. Treat the stripped ``stdout`` as a bare JSON object.

    Raises :class:`BadRequestError` on unparseable / shape-invalid input.
    """
    if output_json_path:
        p = Path(output_json_path)
        if p.exists() and p.stat().st_size > 0:
            try:
                raw = p.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(
                    "review output file %s unreadable: %s — falling back to stdout",
                    output_json_path,
                    exc,
                )
            else:
                payload = _parse_from_text(raw)
                return _coerce(payload)

    payload = _parse_from_text(stdout or "")
    return _coerce(payload)
