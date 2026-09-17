"""Tests for the Phase 10 item 3 GitHub provider adapter.

Two layers, tested separately per the module's own scope split:

- :func:`observe_pr_state` -- pure classification, plain dict fixtures, no
  ``gh`` invocation at all.
- :class:`GitHubPRAdapter` -- the thin ``gh``-CLI wrapper, tested with an
  injected fake runner (no network), the same pattern
  ``test_repository_issue_loops.py`` already established for
  ``GitHubProvider``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_dispatch.github_provider_adapter import (
    GitHubPRAdapter,
    GitHubPRObservationError,
    PRObservation,
    observe_pr_state,
)
from agent_dispatch.provider_state_machine import (
    ApprovalStatus,
    HoldReason,
    Mergeability,
    Revision,
)


def _pr(**overrides: object) -> dict:
    base = {
        "number": 42,
        "title": "Add feature",
        "isDraft": False,
        "headRefOid": "head-sha",
        "baseRefOid": "base-sha",
        "reviewDecision": None,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "labels": {"nodes": []},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": None}}]},
        "reviewThreads": {"nodes": []},
    }
    base.update(overrides)
    return base


# --- observe_pr_state: approval dimension -----------------------------------


def test_no_review_decision_maps_to_none_approval():
    observation = observe_pr_state(_pr(reviewDecision=None))
    assert observation.approval_status == ApprovalStatus.NONE


@pytest.mark.parametrize(
    ("review_decision", "expected"),
    [
        ("APPROVED", ApprovalStatus.APPROVED),
        ("CHANGES_REQUESTED", ApprovalStatus.CHANGES_REQUESTED),
        ("REVIEW_REQUIRED", ApprovalStatus.PENDING),
    ],
)
def test_review_decision_maps_to_declared_approval_status(review_decision, expected):
    observation = observe_pr_state(_pr(reviewDecision=review_decision))
    assert observation.approval_status == expected


def test_unrecognized_review_decision_raises_rather_than_guesses():
    with pytest.raises(GitHubPRObservationError, match="reviewDecision"):
        observe_pr_state(_pr(reviewDecision="SOMETHING_NEW"))


# --- observe_pr_state: mergeability dimension -------------------------------


def test_conflicting_maps_to_conflicted():
    observation = observe_pr_state(_pr(mergeable="CONFLICTING"))
    assert observation.mergeability == Mergeability.CONFLICTED


def test_unknown_mergeable_maps_to_unknown_mergeability():
    observation = observe_pr_state(_pr(mergeable="UNKNOWN"))
    assert observation.mergeability == Mergeability.UNKNOWN


def test_mergeable_with_no_checks_configured_is_clean():
    observation = observe_pr_state(
        _pr(mergeable="MERGEABLE", commits={"nodes": [{"commit": {"statusCheckRollup": None}}]})
    )
    assert observation.mergeability == Mergeability.CLEAN


def test_mergeable_with_no_commits_at_all_is_clean():
    observation = observe_pr_state(_pr(mergeable="MERGEABLE", commits={"nodes": []}))
    assert observation.mergeability == Mergeability.CLEAN


@pytest.mark.parametrize(
    ("rollup_state", "expected"),
    [
        ("SUCCESS", Mergeability.CLEAN),
        ("PENDING", Mergeability.CHECKS_PENDING),
        ("EXPECTED", Mergeability.CHECKS_PENDING),
        ("FAILURE", Mergeability.CHECKS_FAILED),
        ("ERROR", Mergeability.CHECKS_FAILED),
    ],
)
def test_status_check_rollup_maps_to_declared_mergeability(rollup_state, expected):
    observation = observe_pr_state(
        _pr(
            mergeable="MERGEABLE",
            commits={"nodes": [{"commit": {"statusCheckRollup": {"state": rollup_state}}}]},
        )
    )
    assert observation.mergeability == expected


def test_unrecognized_rollup_state_raises_rather_than_guesses():
    with pytest.raises(GitHubPRObservationError, match="statusCheckRollup"):
        observe_pr_state(
            _pr(
                mergeable="MERGEABLE",
                commits={"nodes": [{"commit": {"statusCheckRollup": {"state": "WEIRD"}}}]},
            )
        )


def test_unrecognized_mergeable_value_raises_rather_than_guesses():
    with pytest.raises(GitHubPRObservationError, match="mergeable"):
        observe_pr_state(_pr(mergeable="SOMETHING_ELSE"))


def test_only_the_last_commits_rollup_is_consulted():
    observation = observe_pr_state(
        _pr(
            mergeable="MERGEABLE",
            commits={
                "nodes": [
                    {"commit": {"statusCheckRollup": {"state": "FAILURE"}}},
                    {"commit": {"statusCheckRollup": {"state": "SUCCESS"}}},
                ]
            },
        )
    )
    assert observation.mergeability == Mergeability.CLEAN


# --- observe_pr_state: hold dimension ---------------------------------------


def test_draft_pr_carries_draft_hold():
    observation = observe_pr_state(_pr(isDraft=True))
    assert HoldReason.DRAFT in observation.holds


def test_non_draft_pr_carries_no_draft_hold():
    observation = observe_pr_state(_pr(isDraft=False))
    assert HoldReason.DRAFT not in observation.holds


@pytest.mark.parametrize("title", ["WIP: add feature", "[WIP] add feature", "wip add feature"])
def test_wip_title_marker_carries_wip_hold(title):
    observation = observe_pr_state(_pr(title=title))
    assert HoldReason.WIP in observation.holds


def test_wip_label_carries_wip_hold_even_without_title_marker():
    observation = observe_pr_state(
        _pr(title="Add feature", labels={"nodes": [{"name": "do-not-merge"}]})
    )
    assert HoldReason.WIP in observation.holds


def test_ordinary_title_and_labels_carry_no_wip_hold():
    observation = observe_pr_state(
        _pr(title="Add feature", labels={"nodes": [{"name": "enhancement"}]})
    )
    assert HoldReason.WIP not in observation.holds


def test_unresolved_review_thread_carries_blocking_threads_hold():
    observation = observe_pr_state(_pr(reviewThreads={"nodes": [{"isResolved": False}]}))
    assert HoldReason.BLOCKING_THREADS in observation.holds


def test_all_resolved_threads_carry_no_blocking_threads_hold():
    observation = observe_pr_state(
        _pr(reviewThreads={"nodes": [{"isResolved": True}, {"isResolved": True}]})
    )
    assert HoldReason.BLOCKING_THREADS not in observation.holds


def test_multiple_holds_can_co_occur_independently():
    observation = observe_pr_state(
        _pr(
            isDraft=True,
            title="WIP: add feature",
            reviewThreads={"nodes": [{"isResolved": False}]},
        )
    )
    assert observation.holds == frozenset(
        {HoldReason.DRAFT, HoldReason.WIP, HoldReason.BLOCKING_THREADS}
    )


# --- observe_pr_state: revision --------------------------------------------


def test_revision_carries_head_and_base_sha():
    observation = observe_pr_state(_pr(headRefOid="abc123", baseRefOid="def456"))
    assert observation.revision == Revision(diff_hash="abc123", base_sha="def456")


@pytest.mark.parametrize("missing_field", ["headRefOid", "baseRefOid"])
def test_missing_revision_field_raises(missing_field):
    payload = _pr()
    payload[missing_field] = None
    with pytest.raises(GitHubPRObservationError):
        observe_pr_state(payload)


def test_missing_number_raises():
    payload = _pr()
    payload["number"] = None
    with pytest.raises(GitHubPRObservationError, match="number"):
        observe_pr_state(payload)


def test_observation_carries_the_pr_number():
    observation = observe_pr_state(_pr(number=99))
    assert observation.number == 99
    assert isinstance(observation, PRObservation)


# --- GitHubPRAdapter: gh CLI wrapper (fake runner, no network) -------------


def _fake_responses(*responses):
    values = iter(responses)
    return lambda *_args, **_kwargs: next(values)


def test_fetch_pr_verifies_identity_and_repo_then_returns_the_pr_node():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        if args[1:] == ["api", "user", "--jq", ".login"]:
            return SimpleNamespace(returncode=0, stdout="review-bot\n", stderr="")
        if args[1:] == ["api", "repos/example/project", "--jq", ".full_name"]:
            return SimpleNamespace(returncode=0, stdout="example/project\n", stderr="")
        if args[1] == "api" and args[2] == "graphql":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"data": {"repository": {"pullRequest": _pr(number=7)}}}),
                stderr="",
            )
        raise AssertionError(f"unexpected gh invocation: {args}")

    adapter = GitHubPRAdapter("review-bot", runner=runner)

    raw = adapter.fetch_pr("example/project", 7)

    assert raw["number"] == 7
    # Identity + repo verification happened before the data fetch.
    assert calls[0][1:] == ["api", "user", "--jq", ".login"]
    assert calls[1][1:] == ["api", "repos/example/project", "--jq", ".full_name"]


def test_fetch_pr_caches_identity_verification_per_repo():
    responses = _fake_responses(
        SimpleNamespace(returncode=0, stdout="review-bot\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="example/project\n", stderr=""),
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"repository": {"pullRequest": _pr(number=1)}}}),
            stderr="",
        ),
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"repository": {"pullRequest": _pr(number=2)}}}),
            stderr="",
        ),
    )
    adapter = GitHubPRAdapter("review-bot", runner=responses)

    adapter.fetch_pr("example/project", 1)
    adapter.fetch_pr("example/project", 2)  # only the graphql call should fire


def test_identity_mismatch_raises():
    responses = _fake_responses(
        SimpleNamespace(returncode=0, stdout="someone-else\n", stderr=""),
    )
    adapter = GitHubPRAdapter("review-bot", runner=responses)

    with pytest.raises(RuntimeError, match="identity mismatch"):
        adapter.fetch_pr("example/project", 1)


def test_repository_mismatch_raises():
    responses = _fake_responses(
        SimpleNamespace(returncode=0, stdout="review-bot\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="someone/else\n", stderr=""),
    )
    adapter = GitHubPRAdapter("review-bot", runner=responses)

    with pytest.raises(RuntimeError, match="repository identity mismatch"):
        adapter.fetch_pr("example/project", 1)


def test_graphql_errors_raise():
    responses = _fake_responses(
        SimpleNamespace(returncode=0, stdout="review-bot\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="example/project\n", stderr=""),
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"errors": [{"message": "not found"}]}),
            stderr="",
        ),
    )
    adapter = GitHubPRAdapter("review-bot", runner=responses)

    with pytest.raises(RuntimeError, match="GitHub PR fetch failed"):
        adapter.fetch_pr("example/project", 404)


def test_gh_command_failure_raises():
    responses = _fake_responses(
        SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    adapter = GitHubPRAdapter("review-bot", runner=responses)

    with pytest.raises(RuntimeError, match="GitHub operation failed"):
        adapter.fetch_pr("example/project", 1)


def test_observe_fetches_and_classifies_in_one_call():
    responses = _fake_responses(
        SimpleNamespace(returncode=0, stdout="review-bot\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="example/project\n", stderr=""),
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"data": {"repository": {"pullRequest": _pr(number=7, reviewDecision="APPROVED")}}}
            ),
            stderr="",
        ),
    )
    adapter = GitHubPRAdapter("review-bot", runner=responses)

    observation = adapter.observe("example/project", 7)

    assert observation.number == 7
    assert observation.approval_status == ApprovalStatus.APPROVED
