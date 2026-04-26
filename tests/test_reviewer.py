"""Phase 4: reviewer JSON parsing unit tests."""
from __future__ import annotations

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
