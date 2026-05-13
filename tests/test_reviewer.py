"""Phase 4: reviewer JSON parsing unit tests."""
from __future__ import annotations

import json

import pytest

from src.exceptions import BadRequestError
from src.models import ProblemCategory
from src.reviewer import parse_review_output


def test_fenced_json_passes():
    stdout = """Some preamble text.

```json
{"score": 90, "issues": [], "problem_category": null}
```
"""
    outcome = parse_review_output(stdout)
    assert outcome.score == 90
    assert outcome.issues == []
    assert outcome.problem_category is None


def test_bare_json_passes():
    stdout = '{"score": 50, "issues": [{"m": "x"}], "problem_category": "req_gap"}'
    outcome = parse_review_output(stdout)
    assert outcome.score == 50
    assert outcome.issues == [{"m": "x"}]
    assert outcome.problem_category == ProblemCategory.req_gap


def test_reads_output_file_first(tmp_path):
    p = tmp_path / "out.json"
    p.write_text(
        '{"score": 72, "issues": [], "problem_category": "impl_gap"}',
        encoding="utf-8",
    )
    outcome = parse_review_output(
        "stdout is noise", output_json_path=str(p)
    )
    assert outcome.score == 72
    assert outcome.problem_category == ProblemCategory.impl_gap


def test_last_fenced_block_wins():
    stdout = """```json
{"score": 1, "issues": [], "problem_category": "req_gap"}
```

actual:

```json
{"score": 99, "issues": [], "problem_category": null}
```
"""
    outcome = parse_review_output(stdout)
    assert outcome.score == 99


def test_missing_score_raises():
    with pytest.raises(BadRequestError):
        parse_review_output('{"issues": []}')


def test_score_not_int_raises():
    with pytest.raises(BadRequestError):
        parse_review_output('{"score": "high", "issues": []}')


def test_invalid_problem_category_raises():
    with pytest.raises(BadRequestError):
        parse_review_output(
            '{"score": 10, "issues": [], "problem_category": "foo"}'
        )


def test_non_dict_issue_items_normalised():
    outcome = parse_review_output(
        '{"score": 10, "issues": ["bare string"], "problem_category": "req_gap"}'
    )
    assert outcome.issues == [{"message": "bare string"}]


def test_issues_not_list_raises():
    with pytest.raises(BadRequestError):
        parse_review_output(
            '{"score": 10, "issues": "oops", "problem_category": null}'
        )


def test_empty_stdout_raises():
    with pytest.raises(BadRequestError):
        parse_review_output("")


def test_not_json_raises():
    with pytest.raises(BadRequestError):
        parse_review_output("this is not JSON at all")


def test_empty_output_file_falls_back_to_stdout(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    outcome = parse_review_output(
        '{"score": 80, "issues": [], "problem_category": null}',
        output_json_path=str(p),
    )
    assert outcome.score == 80


def test_problem_category_unchanged_in_phase8():
    """Phase 8 invariant (PRD L227): the enum is the SM/route contract."""
    assert tuple(c.value for c in ProblemCategory) == (
        "req_gap", "impl_gap", "design_hollow",
    )


def test_parse_review_extracts_next_round_hints():
    out = parse_review_output(json.dumps({
        "score": 90,
        "issues": [],
        "next_round_hints": [
            {"kind": "missing_feature", "message": "no /logout endpoint"},
            {"kind": "optimization", "mount": "backend",
             "message": "auth.py:42-58 can use lru_cache"},
        ],
        "problem_category": None,
    }))
    assert len(out.next_round_hints) == 2
    assert out.next_round_hints[0]["kind"] == "missing_feature"
    assert out.next_round_hints[1]["mount"] == "backend"


def test_parse_review_missing_next_round_hints_defaults_empty():
    out = parse_review_output(json.dumps({
        "score": 90, "issues": [], "problem_category": None,
    }))
    assert out.next_round_hints == []


def test_parse_review_rejects_non_list_next_round_hints():
    with pytest.raises(BadRequestError, match="next_round_hints"):
        parse_review_output(json.dumps({
            "score": 90, "issues": [],
            "next_round_hints": "not-a-list",
            "problem_category": None,
        }))


def test_parse_review_normalises_non_dict_hint_items():
    """Mirror of test_non_dict_issue_items_normalised for hints."""
    out = parse_review_output(json.dumps({
        "score": 90, "issues": [],
        "next_round_hints": ["bare hint string"],
        "problem_category": None,
    }))
    assert out.next_round_hints == [{"message": "bare hint string"}]


def test_parse_review_rejects_unknown_hint_kind():
    """Phase 5: kind enum guard catches typos / hallucinated values."""
    with pytest.raises(BadRequestError, match="next_round_hints"):
        parse_review_output(json.dumps({
            "score": 90, "issues": [],
            "next_round_hints": [
                {"kind": "refactor", "message": "rename foo"},
            ],
            "problem_category": None,
        }))


def test_parse_review_allows_omitted_hint_kind():
    """``kind`` is optional; missing/empty values pass through unchanged."""
    out = parse_review_output(json.dumps({
        "score": 90, "issues": [],
        "next_round_hints": [
            {"message": "no kind here"},
            {"kind": "", "message": "empty kind here"},
        ],
        "problem_category": None,
    }))
    assert len(out.next_round_hints) == 2
    assert "kind" not in out.next_round_hints[0]
    assert out.next_round_hints[1]["kind"] == ""


def test_parse_review_extracts_plan_verification():
    out = parse_review_output(json.dumps({
        "score": 90,
        "issues": [],
        "plan_verification": [
            {"id": "DW-01", "status": "done", "verified": True},
            {"id": "DW-02", "status": "deferred", "verified": True},
        ],
        "problem_category": None,
    }))
    assert out.plan_verification == [
        {"id": "DW-01", "status": "done", "verified": True},
        {"id": "DW-02", "status": "deferred", "verified": True},
    ]


def test_parse_review_extracts_score_breakdown():
    breakdown = {
        "plan_score_a": 85,
        "actual_score_b": 70,
        "final_score": 60,
        "plan_coverage": 0.85,
        "execution_coverage": 0.70,
        "previous_actual_score_b": 60,
    }
    out = parse_review_output(json.dumps({
        "score": 60,
        "issues": [],
        "score_breakdown": breakdown,
        "problem_category": "impl_gap",
    }))
    assert out.score_breakdown == breakdown


def test_parse_review_rejects_score_breakdown_score_mismatch():
    with pytest.raises(BadRequestError, match="score_breakdown"):
        parse_review_output(json.dumps({
            "score": 70,
            "issues": [],
            "score_breakdown": {"plan_score_a": 85, "actual_score_b": 70},
            "problem_category": "impl_gap",
        }))


def test_parse_review_rejects_score_breakdown_final_score_mismatch():
    with pytest.raises(BadRequestError, match="final_score"):
        parse_review_output(json.dumps({
            "score": 60,
            "issues": [],
            "score_breakdown": {
                "plan_score_a": 85,
                "actual_score_b": 70,
                "final_score": 61,
            },
            "problem_category": "impl_gap",
        }))


def test_parse_review_missing_plan_verification_defaults_empty():
    out = parse_review_output(json.dumps({
        "score": 90, "issues": [], "problem_category": None,
    }))
    assert out.plan_verification == []


def test_parse_review_rejects_non_list_plan_verification():
    with pytest.raises(BadRequestError, match="plan_verification"):
        parse_review_output(json.dumps({
            "score": 90,
            "issues": [],
            "plan_verification": "DW-01",
            "problem_category": None,
        }))


def test_parse_review_rejects_invalid_plan_verification_status():
    with pytest.raises(BadRequestError, match="plan_verification"):
        parse_review_output(json.dumps({
            "score": 90,
            "issues": [],
            "plan_verification": [
                {"id": "DW-01", "status": "skipped", "verified": True},
            ],
            "problem_category": None,
        }))


def test_parse_review_rejects_non_bool_plan_verification_verified():
    with pytest.raises(BadRequestError, match="plan_verification"):
        parse_review_output(json.dumps({
            "score": 90,
            "issues": [],
            "plan_verification": [
                {"id": "DW-01", "status": "done", "verified": "true"},
            ],
            "problem_category": None,
        }))
