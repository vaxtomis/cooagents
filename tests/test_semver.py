"""Phase 3: SemVer helper tests (v1 scope)."""
import pytest

from src.semver import next_version, parse


def test_new_returns_1_0_0():
    assert next_version(None, "new") == "1.0.0"


def test_new_with_parent_raises_value_error():
    with pytest.raises(ValueError):
        next_version("1.0.0", "new")


@pytest.mark.parametrize("kind", ["patch", "minor", "major"])
def test_other_kinds_not_implemented(kind):
    with pytest.raises(NotImplementedError):
        next_version(None, kind)


def test_parse_basic():
    assert parse("1.2.3") == (1, 2, 3)
    assert parse("0.0.0") == (0, 0, 0)


@pytest.mark.parametrize("bad", ["1.2", "1.2.3.4", "x.y.z", "", "1.a.3"])
def test_parse_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse(bad)
