"""Pytest policy checks injected by ``run-plugin-tests.py``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

try:
    from plugin_test_containment import (
        ALLOW_HOST_STATE_ENV,
        CONTAINED_ENV,
        ROOT_ENV_NAMES,
        SANDBOX_ENV,
    )
except ModuleNotFoundError:
    from tools.plugin_test_containment import (
        ALLOW_HOST_STATE_ENV,
        CONTAINED_ENV,
        ROOT_ENV_NAMES,
        SANDBOX_ENV,
    )

_TIERS = {"T0", "T1", "T2", "T3", "T4"}
_EFFECTS = {
    "filesystem",
    "process",
    "network",
    "service",
    "host-state",
    "external-system",
}
_ALLOWED_EFFECTS = {
    "T0": set(),
    "T1": {"filesystem"},
    "T2": {"filesystem", "process", "network"},
    "T3": _EFFECTS,
    "T4": _EFFECTS,
}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def validate_contained_environment() -> None:
    """Fail when a default-tier run can resolve mutable state outside its sandbox."""
    if os.environ.get(CONTAINED_ENV) != "1":
        raise pytest.UsageError(
            "plugin suites must run through tools/run-plugin-tests.py containment"
        )
    raw_sandbox = os.environ.get(SANDBOX_ENV)
    if not raw_sandbox:
        raise pytest.UsageError(f"{SANDBOX_ENV} is required for contained tests")
    if os.environ.get(ALLOW_HOST_STATE_ENV) == "1":
        if os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1":
            raise pytest.UsageError(
                f"{ALLOW_HOST_STATE_ENV} requires explicit test tiers"
            )
        return
    sandbox = Path(raw_sandbox)
    escaped = []
    for name in ROOT_ENV_NAMES:
        value = os.environ.get(name)
        if not value or not _is_within(Path(value), sandbox):
            escaped.append(f"{name}={value!r}")
    if escaped:
        raise pytest.UsageError(
            "test state roots escape the runner sandbox: " + ", ".join(escaped)
        )


def validate_declaration(tier: str | None, effects: set[str]) -> str | None:
    """Return a declaration error, or ``None`` when the tier/effects are valid."""
    if tier is None:
        if effects:
            return "effect markers require a portfolio_tier marker"
        return None
    if tier not in _TIERS:
        return f"unknown portfolio tier {tier!r}"
    unknown = effects - _EFFECTS
    if unknown:
        return f"unknown test effects: {', '.join(sorted(unknown))}"
    forbidden = effects - _ALLOWED_EFFECTS[tier]
    if forbidden:
        return (
            f"{tier} does not allow effects: {', '.join(sorted(forbidden))}"
        )
    return None


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "portfolio_tier(name): execution tier T0 through T4 for a test family",
    )
    config.addinivalue_line(
        "markers",
        "effect(name): declared filesystem/process/network/service/host-state/"
        "external-system effect",
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    validate_contained_environment()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    allow_explicit = (
        os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") == "1"
    )
    errors = []
    for item in items:
        tier_marks = list(item.iter_markers("portfolio_tier"))
        if len(tier_marks) > 1:
            errors.append(f"{item.nodeid}: multiple portfolio_tier markers")
            continue
        tier = None
        if tier_marks:
            if len(tier_marks[0].args) != 1:
                errors.append(
                    f"{item.nodeid}: portfolio_tier requires exactly one argument"
                )
                continue
            tier = str(tier_marks[0].args[0]).upper()
        effects = set()
        malformed_effect = False
        for mark in item.iter_markers("effect"):
            if len(mark.args) != 1:
                errors.append(f"{item.nodeid}: effect requires exactly one argument")
                malformed_effect = True
                break
            effects.add(str(mark.args[0]).lower())
        if malformed_effect:
            continue
        error = validate_declaration(tier, effects)
        if error:
            errors.append(f"{item.nodeid}: {error}")
            continue
        if tier in {"T3", "T4"} and not allow_explicit:
            item.add_marker(
                pytest.mark.skip(
                    reason=f"{tier} requires --allow-explicit-tiers"
                )
            )
    if errors:
        raise pytest.UsageError("invalid test portfolio declarations:\n- " + "\n- ".join(errors))
