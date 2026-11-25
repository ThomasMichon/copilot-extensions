"""Actor-free decisions for bounded unit-job checkout and full-history fallback."""
from __future__ import annotations

import pytest

from promotion_unit_contract import UnitEvidenceError, ci_requires_full_history

BASE = "a" * 40
HEAD = "b" * 40


@pytest.mark.parametrize("proven", [False, True])
def test_full_history_is_required_without_positive_base_ancestry(proven: bool) -> None:
    calls: list[list[str]] = []

    def probe(command: list[str]) -> bool:
        calls.append(command)
        return proven

    assert ci_requires_full_history("pull_request", BASE, HEAD, probe) is not proven
    assert calls == [["git", "merge-base", "--is-ancestor", BASE, HEAD]]


def test_non_pr_unit_events_need_no_history_probe() -> None:
    def probe(command: list[str]) -> bool:
        raise AssertionError(f"Unexpected history request: {command}")

    assert ci_requires_full_history("workflow_dispatch", "", "", probe) is False


@pytest.mark.parametrize(
    "base,head", [("invalid", HEAD), (BASE, "invalid")], ids=["invalid-base", "invalid-head"],
)
def test_invalid_pr_history_identity_refuses_before_git(base: str, head: str) -> None:
    def probe(command: list[str]) -> bool:
        raise AssertionError(f"Invalid source reached Git: {command}")

    with pytest.raises(UnitEvidenceError, match="immutable source SHA"):
        ci_requires_full_history("pull_request", base, head, probe)
