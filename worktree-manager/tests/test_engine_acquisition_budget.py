"""Pure admission controls for a request's native acquisition deadline."""

import pytest

from worktree_manager.engine_acquisition_budget import TIMEOUT_ENV, acquisition_timeout


def test_default_acquisition_budget_is_unchanged():
    assert acquisition_timeout({}) == 180


@pytest.mark.parametrize("value,expected", [
    ("0.5", 0.5), ("180", 180), ("600", 600), ("1.2e3", 1200), ("3600", 3600),
])
def test_explicit_budget_is_finite_positive_and_bounded(value, expected):
    environment = {TIMEOUT_ENV: value}
    assert acquisition_timeout(environment) == expected
    assert environment == {TIMEOUT_ENV: value}


@pytest.mark.parametrize("value", [
    "", "0", "-1", "nan", "inf", "-inf", "3600.01", "1e300", "invalid",
])
def test_invalid_budget_refuses_without_echoing_the_value(value):
    with pytest.raises(
        ValueError,
        match=f"^{TIMEOUT_ENV} must be a positive finite number no greater than 3600$",
    ):
        acquisition_timeout({TIMEOUT_ENV: value})
