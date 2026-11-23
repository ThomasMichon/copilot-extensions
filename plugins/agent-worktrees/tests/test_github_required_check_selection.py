"""Integration proof follows current required results, not arbitrary history."""

from __future__ import annotations

from collections.abc import Callable
import importlib
from typing import Any, Protocol, cast

import pytest

from agent_worktrees.providers.github_required_checks import RequiredChecksError


CURRENT = "https://checks.example.invalid/current"
PREVIOUS = "https://checks.example.invalid/previous"
OTHER = "https://checks.example.invalid/other"


class _Fixture(Protocol):
    head: str
    checks: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    run_pages: list[dict[str, Any]] | None

    def verify(self) -> str: ...


def _fixture() -> _Fixture:
    factory = cast(
        Callable[[], _Fixture],
        importlib.import_module("test_github_required_checks").GitHubFixture,
    )
    fixture = factory()
    fixture.checks[0]["link"] = CURRENT
    fixture.runs[0].update(html_url=CURRENT, details_url=CURRENT)
    return fixture


@pytest.mark.parametrize("previous_first", [False, True])
def test_selected_current_pass_supersedes_failed_same_head_history(previous_first: bool) -> None:
    fixture = _fixture()
    previous = {
        **fixture.runs[0], "conclusion": "failure",
        "html_url": PREVIOUS, "details_url": PREVIOUS,
    }
    fixture.runs.insert(0 if previous_first else 1, previous)
    assert fixture.verify() == fixture.head


@pytest.mark.parametrize("identity", ["missing", "unmatched", "ambiguous"])
def test_failed_history_needs_unambiguous_selected_result(identity: str) -> None:
    fixture = _fixture()
    previous = {**fixture.runs[0], "conclusion": "failure"}
    if identity == "missing":
        fixture.checks[0].pop("link")
    elif identity == "unmatched":
        fixture.checks[0]["link"] = OTHER
    else:
        fixture.runs.append(dict(fixture.runs[0]))
    fixture.runs.append({**previous, "html_url": PREVIOUS, "details_url": PREVIOUS})
    with pytest.raises(RequiredChecksError):
        fixture.verify()


@pytest.mark.parametrize("mutation", [
    {"app": {"id": 77}}, {"head_sha": "b" * 40},
    {"status": "queued"}, {"conclusion": "failure"},
])
def test_selected_result_cannot_borrow_older_integration_success(
    mutation: dict[str, Any],
) -> None:
    fixture = _fixture()
    fixture.runs.append({
        **fixture.runs[0], "html_url": PREVIOUS, "details_url": PREVIOUS,
    })
    fixture.runs[0].update(mutation)
    with pytest.raises(RequiredChecksError):
        fixture.verify()


@pytest.mark.parametrize("other_present", [False, True])
def test_independent_selected_producer_needs_its_own_bound_proof(other_present: bool) -> None:
    fixture = _fixture()
    fixture.checks.append({"name": "PR gate", "bucket": "pass", "link": OTHER})
    if other_present:
        fixture.runs.append({
            **fixture.runs[0], "app": {"id": 77},
            "html_url": OTHER, "details_url": OTHER,
        })
    with pytest.raises(RequiredChecksError):
        fixture.verify()


def test_selected_current_integration_proof_can_arrive_on_a_later_page() -> None:
    fixture = _fixture()
    previous = {
        **fixture.runs[0], "conclusion": "failure",
        "html_url": PREVIOUS, "details_url": PREVIOUS,
    }
    fixture.run_pages = [
        {"total_count": 2, "check_runs": [previous]},
        {"total_count": 2, "check_runs": fixture.runs},
    ]
    assert fixture.verify() == fixture.head
