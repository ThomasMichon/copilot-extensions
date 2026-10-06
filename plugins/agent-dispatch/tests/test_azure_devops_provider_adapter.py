"""Tests for the Azure DevOps reviewer-side provider adapter."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from agent_dispatch.azure_devops_provider_adapter import (
    AzureDevOpsPRAdapter,
    AzureDevOpsPRObservationError,
    observe_pr_state,
)
from agent_dispatch.github_provider_adapter import PRObservation
from agent_dispatch.provider_state_machine import (
    ApprovalStatus,
    HoldReason,
    Mergeability,
    Revision,
)
from agent_dispatch.review_target_refs import ReviewTargetRef, parse_review_target_ref


def _pr(**overrides: object) -> dict:
    base = {
        "pullRequestId": 42,
        "title": "Add feature",
        "status": "active",
        "isDraft": False,
        "mergeStatus": "succeeded",
        "labels": [],
        "createdBy": {
            "id": "author-id",
            "uniqueName": "author@example.com",
            "displayName": "Author",
        },
        "lastMergeSourceCommit": {"commitId": "head-sha"},
        "lastMergeTargetCommit": {"commitId": "base-sha"},
        "lastMergeSourceCommitDetail": {"author": {"date": "2026-01-01T00:00:00Z"}},
        "reviewers": [],
        "threads": [],
        "policyEvaluations": [],
        "repository": {
            "id": "repo-guid",
            "name": "example-repo",
            "project": {"id": "project-guid", "name": "example-project"},
        },
        "artifactId": "vstfs:///Git/PullRequestId/project-guid%2frepo-guid%2f42",
    }
    base.update(overrides)
    return base


def _reviewer(
    *,
    vote: int,
    reviewer_id: str = "reviewer-id",
    unique_name: str = "reviewer@example.com",
    is_required: bool = False,
    is_container: bool = False,
    has_declined: bool = False,
) -> dict[str, object]:
    return {
        "id": reviewer_id,
        "uniqueName": unique_name,
        "displayName": "Reviewer",
        "vote": vote,
        "isRequired": is_required,
        "isContainer": is_container,
        "hasDeclined": has_declined,
    }


def _thread(status: str, *, is_deleted: bool = False) -> dict[str, object]:
    return {"status": status, "isDeleted": is_deleted}


# --- observe_pr_state: approval dimension -----------------------------------


def test_no_non_author_reviewers_maps_to_none_approval():
    observation = observe_pr_state(
        _pr(
            reviewers=[
                _reviewer(
                    vote=0,
                    reviewer_id="author-id",
                    unique_name="author@example.com",
                )
            ]
        )
    )
    assert observation.approval_status == ApprovalStatus.NONE


def test_zero_vote_maps_to_pending():
    observation = observe_pr_state(_pr(reviewers=[_reviewer(vote=0)]))
    assert observation.approval_status == ApprovalStatus.PENDING


@pytest.mark.parametrize("vote", [-10, -5])
def test_negative_votes_map_to_changes_requested(vote):
    observation = observe_pr_state(_pr(reviewers=[_reviewer(vote=vote)]))
    assert observation.approval_status == ApprovalStatus.CHANGES_REQUESTED


@pytest.mark.parametrize("vote", [5, 10])
def test_positive_votes_map_to_approved(vote):
    observation = observe_pr_state(_pr(reviewers=[_reviewer(vote=vote)]))
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_mixed_positive_and_zero_votes_stay_pending():
    observation = observe_pr_state(
        _pr(reviewers=[_reviewer(vote=10), _reviewer(vote=0, reviewer_id="other")])
    )
    assert observation.approval_status == ApprovalStatus.PENDING


def test_required_reviewer_pending_overrides_optional_approval():
    observation = observe_pr_state(
        _pr(
            reviewers=[
                _reviewer(vote=10),
                _reviewer(vote=0, reviewer_id="required", is_required=True),
            ]
        )
    )
    assert observation.approval_status == ApprovalStatus.PENDING


def test_declined_reviewers_are_ignored():
    observation = observe_pr_state(
        _pr(
            reviewers=[
                _reviewer(vote=0, has_declined=True),
                _reviewer(vote=10, reviewer_id="other"),
            ]
        )
    )
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_group_reviewer_rollup_prefers_leaf_votes_when_present():
    observation = observe_pr_state(
        _pr(
            reviewers=[
                _reviewer(vote=0, reviewer_id="group", unique_name="group", is_container=True),
                _reviewer(vote=10, reviewer_id="member", unique_name="member@example.com"),
            ]
        )
    )
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_required_group_reviewer_still_counts_when_individual_reviewer_is_present():
    observation = observe_pr_state(
        _pr(
            reviewers=[
                _reviewer(
                    vote=0,
                    reviewer_id="group",
                    unique_name="group",
                    is_container=True,
                    is_required=True,
                ),
                _reviewer(vote=10, reviewer_id="member", unique_name="member@example.com"),
            ]
        )
    )
    assert observation.approval_status == ApprovalStatus.PENDING


def test_unrecognized_vote_raises():
    with pytest.raises(AzureDevOpsPRObservationError, match="reviewer vote"):
        observe_pr_state(_pr(reviewers=[_reviewer(vote=1)]))


# --- observe_pr_state: mergeability dimension -------------------------------


@pytest.mark.parametrize(
    ("merge_status", "expected"),
    [
        ("notSet", Mergeability.UNKNOWN),
        ("queued", Mergeability.CHECKS_PENDING),
        ("conflicts", Mergeability.CONFLICTED),
        ("succeeded", Mergeability.CLEAN),
        ("rejectedByPolicy", Mergeability.CHECKS_FAILED),
        ("failure", Mergeability.CHECKS_FAILED),
    ],
)
def test_merge_status_maps_to_declared_mergeability(merge_status, expected):
    observation = observe_pr_state(_pr(mergeStatus=merge_status))
    assert observation.mergeability == expected


def test_unrecognized_merge_status_raises():
    with pytest.raises(AzureDevOpsPRObservationError, match="mergeStatus"):
        observe_pr_state(_pr(mergeStatus="weird"))


def test_blocking_policy_running_keeps_mergeability_pending_even_when_merge_status_succeeds():
    observation = observe_pr_state(
        _pr(
            mergeStatus="succeeded",
            policyEvaluations=[
                {"status": "running", "configuration": {"isBlocking": True}}
            ],
        )
    )
    assert observation.mergeability == Mergeability.CHECKS_PENDING


def test_blocking_policy_rejection_maps_to_checks_failed():
    observation = observe_pr_state(
        _pr(
            mergeStatus="succeeded",
            policyEvaluations=[
                {"status": "rejected", "configuration": {"isBlocking": True}}
            ],
        )
    )
    assert observation.mergeability == Mergeability.CHECKS_FAILED


def test_conflicted_merge_status_is_not_downgraded_by_running_policy():
    observation = observe_pr_state(
        _pr(
            mergeStatus="conflicts",
            policyEvaluations=[
                {"status": "running", "configuration": {"isBlocking": True}}
            ],
        )
    )
    assert observation.mergeability == Mergeability.CONFLICTED


def test_approved_blocking_policies_allow_clean_mergeability():
    observation = observe_pr_state(
        _pr(
            mergeStatus="succeeded",
            policyEvaluations=[
                {"status": "approved", "configuration": {"isBlocking": True}},
                {"status": "notApplicable", "configuration": {"isBlocking": True}},
            ],
        )
    )
    assert observation.mergeability == Mergeability.CLEAN


# --- observe_pr_state: hold dimension ---------------------------------------


def test_draft_pr_carries_draft_hold():
    observation = observe_pr_state(_pr(isDraft=True))
    assert HoldReason.DRAFT in observation.holds


@pytest.mark.parametrize("title", ["WIP: add feature", "[WIP] add feature"])
def test_wip_title_marker_carries_wip_hold(title):
    observation = observe_pr_state(_pr(title=title))
    assert HoldReason.WIP in observation.holds


def test_wip_label_carries_wip_hold():
    observation = observe_pr_state(_pr(labels=[{"name": "do-not-merge"}]))
    assert HoldReason.WIP in observation.holds


@pytest.mark.parametrize("status", ["active", "pending", "unknown"])
def test_unresolved_thread_status_carries_blocking_threads_hold(status):
    observation = observe_pr_state(_pr(threads=[_thread(status)]))
    assert HoldReason.BLOCKING_THREADS in observation.holds


def test_resolved_thread_statuses_do_not_block():
    observation = observe_pr_state(
        _pr(threads=[_thread("fixed"), _thread("closed"), _thread("wontFix")])
    )
    assert HoldReason.BLOCKING_THREADS not in observation.holds


def test_deleted_threads_do_not_block():
    observation = observe_pr_state(_pr(threads=[_thread("active", is_deleted=True)]))
    assert HoldReason.BLOCKING_THREADS not in observation.holds


def test_unrecognized_thread_status_raises():
    with pytest.raises(AzureDevOpsPRObservationError, match="thread status"):
        observe_pr_state(_pr(threads=[_thread("mystery")]))


# --- observe_pr_state: revision --------------------------------------------


def test_revision_carries_head_and_base_sha():
    observation = observe_pr_state(
        _pr(
            lastMergeSourceCommit={"commitId": "abc123"},
            lastMergeTargetCommit={"commitId": "def456"},
        )
    )
    assert observation.revision == Revision(diff_hash="abc123", base_sha="def456")


def test_observation_carries_last_commit_timestamp():
    observation = observe_pr_state(_pr())
    assert observation.last_commit_at == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    ).timestamp()


def test_missing_commit_detail_yields_no_last_commit_timestamp():
    observation = observe_pr_state(_pr(lastMergeSourceCommitDetail=None))
    assert observation.last_commit_at is None


def test_invalid_last_commit_timestamp_raises():
    with pytest.raises(AzureDevOpsPRObservationError, match="lastMergeSourceCommitDetail"):
        observe_pr_state(
            _pr(lastMergeSourceCommitDetail={"author": {"date": "not-a-date"}})
        )


@pytest.mark.parametrize("missing_field", ["lastMergeSourceCommit", "lastMergeTargetCommit"])
def test_missing_revision_field_raises(missing_field):
    payload = _pr()
    payload[missing_field] = None
    with pytest.raises(AzureDevOpsPRObservationError):
        observe_pr_state(payload)


def test_missing_number_raises():
    payload = _pr()
    payload["pullRequestId"] = None
    with pytest.raises(AzureDevOpsPRObservationError, match="pullRequestId"):
        observe_pr_state(payload)


def test_observation_carries_pr_number_and_common_shape():
    observation = observe_pr_state(_pr(pullRequestId=99))
    assert observation.number == 99
    assert isinstance(observation, PRObservation)


# --- AzureDevOpsPRAdapter: az CLI wrapper (fake runner, no network) ---------


def _fake_responses(*responses):
    values = iter(responses)
    return lambda *_args, **_kwargs: next(values)


def test_fetch_pr_verifies_identity_then_returns_pr_with_related_payloads():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        suffix = args[1:]
        if suffix[:4] == ["rest", "--method", "GET", "--url"] and "connectionData" in suffix[4]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"authenticatedUser": {"providerDisplayName": "review-bot"}}),
                stderr="",
            )
        if suffix[:4] == ["devops", "project", "show", "--project"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"name": "example-project"}),
                stderr="",
            )
        if suffix[:4] == ["rest", "--method", "GET", "--url"] and "/repositories/example-repo?" in suffix[4]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"name": "example-repo"}),
                stderr="",
            )
        if suffix[:4] == ["rest", "--method", "GET", "--url"] and "/pullRequests/7?" in suffix[4]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(_pr(pullRequestId=7)),
                stderr="",
            )
        if suffix[:4] == ["rest", "--method", "GET", "--url"] and suffix[4].endswith("/reviewers?api-version=7.1"):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"value": [_reviewer(vote=10)]}),
                stderr="",
            )
        if suffix[:4] == ["rest", "--method", "GET", "--url"] and suffix[4].endswith("/threads?api-version=7.1"):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"value": [_thread("closed")]}),
                stderr="",
            )
        if suffix[:4] == ["rest", "--method", "GET", "--url"] and "/_apis/policy/evaluations?" in suffix[4]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [{"status": "approved", "configuration": {"isBlocking": True}}]
                ),
                stderr="",
            )
        if suffix[:4] == ["rest", "--method", "GET", "--url"] and "/commits/head-sha?" in suffix[4]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"author": {"date": "2026-01-01T00:00:00Z"}}),
                stderr="",
            )
        raise AssertionError(f"unexpected az invocation: {args}")

    adapter = AzureDevOpsPRAdapter("review-bot", runner=runner)

    raw = adapter.fetch_pr("example-org/example-project/example-repo", 7)

    assert raw["pullRequestId"] == 7
    assert raw["reviewers"][0]["vote"] == 10
    assert raw["threads"][0]["status"] == "closed"
    assert calls[0][1:4] == ["rest", "--method", "GET"]


def test_fetch_pr_caches_identity_verification_per_repo():
    responses = _fake_responses(
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"authenticatedUser": {"providerDisplayName": "review-bot"}}),
            stderr="",
        ),
        SimpleNamespace(returncode=0, stdout=json.dumps({"name": "example-project"}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"name": "example-repo"}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps(_pr(pullRequestId=1)), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"value": []}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"value": []}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"author": {"date": "2026-01-01T00:00:00Z"}}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps([]), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps(_pr(pullRequestId=2)), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"value": []}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"value": []}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"author": {"date": "2026-01-01T00:00:00Z"}}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps([]), stderr=""),
    )
    adapter = AzureDevOpsPRAdapter("review-bot", runner=responses)

    adapter.fetch_pr("example-org/example-project/example-repo", 1)
    adapter.fetch_pr("example-org/example-project/example-repo", 2)


def test_identity_mismatch_raises():
    responses = _fake_responses(
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"authenticatedUser": {"providerDisplayName": "someone-else"}}),
            stderr="",
        ),
    )
    adapter = AzureDevOpsPRAdapter("review-bot", runner=responses)

    with pytest.raises(RuntimeError, match="identity mismatch"):
        adapter.fetch_pr("example-org/example-project/example-repo", 1)


def test_project_mismatch_raises():
    responses = _fake_responses(
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"authenticatedUser": {"providerDisplayName": "review-bot"}}),
            stderr="",
        ),
        SimpleNamespace(returncode=0, stdout=json.dumps({"name": "wrong-project"}), stderr=""),
    )
    adapter = AzureDevOpsPRAdapter("review-bot", runner=responses)

    with pytest.raises(RuntimeError, match="project identity mismatch"):
        adapter.fetch_pr("example-org/example-project/example-repo", 1)


def test_repository_mismatch_raises():
    responses = _fake_responses(
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"authenticatedUser": {"providerDisplayName": "review-bot"}}),
            stderr="",
        ),
        SimpleNamespace(returncode=0, stdout=json.dumps({"name": "example-project"}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"name": "someone-else"}), stderr=""),
    )
    adapter = AzureDevOpsPRAdapter("review-bot", runner=responses)

    with pytest.raises(RuntimeError, match="repository identity mismatch"):
        adapter.fetch_pr("example-org/example-project/example-repo", 1)


def test_pr_repository_mismatch_raises():
    responses = _fake_responses(
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"authenticatedUser": {"providerDisplayName": "review-bot"}}),
            stderr="",
        ),
        SimpleNamespace(returncode=0, stdout=json.dumps({"name": "example-project"}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"name": "example-repo"}), stderr=""),
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                _pr(
                    repository={
                        "id": "repo-guid",
                        "name": "wrong-repo",
                        "project": {"name": "example-project"},
                    }
                )
            ),
            stderr="",
        ),
    )
    adapter = AzureDevOpsPRAdapter("review-bot", runner=responses)

    with pytest.raises(RuntimeError, match="repository mismatch"):
        adapter.fetch_pr("example-org/example-project/example-repo", 7)


def test_az_command_failure_raises():
    responses = _fake_responses(
        SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    adapter = AzureDevOpsPRAdapter("review-bot", runner=responses)

    with pytest.raises(RuntimeError, match="Azure DevOps operation failed"):
        adapter.fetch_pr("example-org/example-project/example-repo", 1)


def test_observe_fetches_and_classifies_in_one_call():
    responses = _fake_responses(
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"authenticatedUser": {"providerDisplayName": "review-bot"}}),
            stderr="",
        ),
        SimpleNamespace(returncode=0, stdout=json.dumps({"name": "example-project"}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"name": "example-repo"}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps(_pr(pullRequestId=7)), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"value": [_reviewer(vote=10)]}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"value": []}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps({"author": {"date": "2026-01-01T00:00:00Z"}}), stderr=""),
        SimpleNamespace(returncode=0, stdout=json.dumps([]), stderr=""),
    )
    adapter = AzureDevOpsPRAdapter("review-bot", runner=responses)

    observation = adapter.observe("example-org/example-project/example-repo", 7)

    assert observation.number == 7
    assert observation.approval_status == ApprovalStatus.APPROVED


# --- reviewer target refs + routing ----------------------------------------


def test_parse_review_target_ref_accepts_github_and_azure_devops_shapes():
    assert parse_review_target_ref("github-pr:example/project#7") == ReviewTargetRef(
        provider="github",
        repo="example/project",
        number=7,
    )
    assert parse_review_target_ref(
        "azure-devops-pr:example-org/example-project/example-repo#9"
    ) == ReviewTargetRef(
        provider="azure-devops",
        repo="example-org/example-project/example-repo",
        number=9,
    )
    assert (
        parse_review_target_ref(
            "azure-devops-pr:example-org/example-project/example-repo#9"
        ).observation_repo
        == "azure-devops:example-org/example-project/example-repo"
    )


def test_parse_review_target_ref_ignores_emitter_metadata_suffixes():
    assert parse_review_target_ref(
        "github-pr:example/project#7@abc123:base=main"
    ) == ReviewTargetRef(
        provider="github",
        repo="example/project",
        number=7,
    )


