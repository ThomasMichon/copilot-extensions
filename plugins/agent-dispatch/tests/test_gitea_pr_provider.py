"""Tests for the Gitea reviewer-side provider adapter (``gitea_pr_provider``).

Two layers, mirroring ``test_github_provider_adapter.py``'s own split:

- :func:`observe_pr_state` -- pure classification, plain dict fixtures, no
  transport call at all.
- :class:`GiteaPRAdapter` -- the thin ``curl``-based REST wrapper, tested
  with an injected fake runner (no network).
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from agent_dispatch.gitea_pr_provider import (
    GiteaPRAdapter,
    GiteaPRObservationError,
    _split,
    observe_pr_state,
)
from agent_dispatch.github_provider_adapter import PRObservation
from agent_dispatch.provider_state_machine import ApprovalStatus, HoldReason, Mergeability


def _pr(**overrides: object) -> dict:
    base = {
        "number": 42,
        "title": "Add feature",
        "draft": False,
        "mergeable": True,
        "head": {"sha": "head-sha"},
        "base": {"sha": "base-sha"},
        "labels": [],
    }
    base.update(overrides)
    return base


def _review(
    *, state: str, review_id: int, login: str = "reviewer", dismissed: bool = False,
    stale: bool = False, official: bool | None = None, team: str | None = None,
) -> dict:
    row: dict = {
        "id": review_id, "state": state, "user": {"login": login},
        "dismissed": dismissed, "stale": stale,
    }
    if official is not None:
        row["official"] = official
    if team is not None:
        row["user"] = None
        row["team"] = {"name": team}
    return row


# --- _split --------------------------------------------------------------


def test_split_parses_host_owner_repo():
    assert _split("gitea.example.com/example/project") == (
        "gitea.example.com", "example", "project",
    )


@pytest.mark.parametrize("bad", ["example/project", "a/b/c/d", "", "a//c"])
def test_split_rejects_malformed_repo(bad):
    with pytest.raises(ValueError):
        _split(bad)


# --- GiteaPRAdapter: credential-authority allowlisting ----------------------


def test_adapter_requires_non_empty_api_bases():
    with pytest.raises(ValueError, match="api_bases"):
        GiteaPRAdapter("review-bot")


def test_adapter_rejects_empty_api_bases_explicitly():
    with pytest.raises(ValueError, match="api_bases"):
        GiteaPRAdapter("review-bot", api_bases={})


def test_adapter_rejects_an_api_base_that_normalizes_to_empty():
    with pytest.raises(ValueError, match="api_bases"):
        GiteaPRAdapter("review-bot", api_bases={"gitea.example.com": "/"})


def test_adapter_rejects_an_api_base_that_is_not_a_usable_url():
    """Same shape validation as the registrar's forge.api_base -- a value
    that is merely non-empty text (not an absolute http(s) authority, or
    one carrying embedded credentials/query/fragment) must never
    "validate" successfully and then either fail on every real request or
    target an unintended authority."""
    with pytest.raises(ValueError, match="api_bases"):
        GiteaPRAdapter(
            "review-bot", api_bases={"gitea.example.com": "not a URL"}
        )


def test_adapter_accepts_a_path_hosted_api_base():
    adapter = GiteaPRAdapter(
        "review-bot", api_bases={"h": "https://h.example.com/gitea"}
    )
    assert adapter.api_bases == {"h": "https://h.example.com/gitea"}


def test_fetch_pr_refuses_a_key_outside_the_configured_mapping(monkeypatch):
    """A payload_ref's key is caller-supplied data, not a trusted
    credential authority -- a ref naming an unconfigured key must never
    reach this adapter's token."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def boom(*_a, **_k):
        raise AssertionError("must not make any request for an unconfigured key")

    adapter = GiteaPRAdapter(
        "review-bot", runner=boom,
        api_bases={"trusted.example.com": "https://trusted.example.com"},
    )

    with pytest.raises(RuntimeError, match="not in this adapter's configured api_bases"):
        adapter.fetch_pr("evil.example.com/owner/repo", 1)


def test_fetch_pr_allows_a_key_in_the_mapping_case_insensitively(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"GITEA.EXAMPLE.COM": "https://gitea.example.com"},
    )
    raw = adapter.fetch_pr("gitea.example.com/example/project", 7)
    assert raw["pull_request"]["number"] == 7


def test_fetch_pr_uses_the_configured_path_hosted_api_base(monkeypatch):
    """A Gitea instance mounted under a path (e.g. ``https://h/gitea``)
    must be queried at that real base, not a bare ``https://<key>``
    reconstructed from the ref."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    seen_urls = []

    def runner(args, **kwargs):
        url = args[4]
        seen_urls.append(url)
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"h": "https://h/gitea"},
    )
    adapter.fetch_pr("h/example/project", 7)
    assert all(u.startswith("https://h/gitea/api/v1") for u in seen_urls)


# --- observe_pr_state: approval dimension ---------------------------------


def test_no_reviews_maps_to_none_approval():
    observation = observe_pr_state(_pr(), reviews=[])
    assert observation.approval_status == ApprovalStatus.NONE


def test_single_approval_maps_to_approved():
    observation = observe_pr_state(_pr(), reviews=[_review(state="APPROVED", review_id=1)])
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_request_changes_wins_over_approval_from_another_reviewer():
    observation = observe_pr_state(
        _pr(),
        reviews=[
            _review(state="APPROVED", review_id=1, login="alice"),
            _review(state="REQUEST_CHANGES", review_id=2, login="bob"),
        ],
    )
    assert observation.approval_status == ApprovalStatus.CHANGES_REQUESTED


def test_later_review_from_same_reviewer_supersedes_earlier_one():
    """An earlier REQUEST_CHANGES superseded by that same reviewer's later
    APPROVED must not still block -- only the latest review per reviewer
    counts."""
    observation = observe_pr_state(
        _pr(),
        reviews=[
            _review(state="REQUEST_CHANGES", review_id=1, login="alice"),
            _review(state="APPROVED", review_id=2, login="alice"),
        ],
    )
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_later_comment_only_review_does_not_erase_an_earlier_verdict():
    """Gitea keeps an approval/rejection as the official verdict until
    another *verdict-bearing* review changes it -- a later COMMENT-only
    review from the same reviewer must not erase it."""
    observation = observe_pr_state(
        _pr(),
        reviews=[
            _review(state="REQUEST_CHANGES", review_id=1, login="alice"),
            _review(state="COMMENT", review_id=2, login="alice"),
        ],
    )
    assert observation.approval_status == ApprovalStatus.CHANGES_REQUESTED


def test_later_comment_only_review_does_not_erase_an_earlier_approval():
    observation = observe_pr_state(
        _pr(),
        reviews=[
            _review(state="APPROVED", review_id=1, login="alice"),
            _review(state="COMMENT", review_id=2, login="alice"),
        ],
    )
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_comment_only_review_maps_to_pending():
    observation = observe_pr_state(_pr(), reviews=[_review(state="COMMENT", review_id=1)])
    assert observation.approval_status == ApprovalStatus.PENDING


def test_request_review_after_request_changes_resets_to_pending():
    """A fresh REQUEST_REVIEW row (Gitea's record of re-requesting that
    reviewer) must be eligible to supersede that reviewer's own older
    verdict -- a re-request after changes must return to PENDING, not
    stay reported as the stale CHANGES_REQUESTED, since Gitea itself
    includes request-review rows when selecting the latest approval
    state per reviewer."""
    observation = observe_pr_state(
        _pr(),
        reviews=[
            _review(state="REQUEST_CHANGES", review_id=1, login="alice"),
            _review(state="REQUEST_REVIEW", review_id=2, login="alice"),
        ],
    )
    assert observation.approval_status == ApprovalStatus.PENDING


def test_unofficial_approval_does_not_approve():
    """Gitea's `official` field marks whether a review counts toward
    merge policy -- a non-required reviewer's APPROVED must never flip
    the aggregate status to APPROVED, since Gitea itself wouldn't count
    it toward merge eligibility."""
    observation = observe_pr_state(
        _pr(),
        reviews=[_review(state="APPROVED", review_id=1, login="alice", official=False)],
    )
    assert observation.approval_status != ApprovalStatus.APPROVED


def test_unofficial_request_changes_does_not_block():
    observation = observe_pr_state(
        _pr(),
        reviews=[
            _review(state="REQUEST_CHANGES", review_id=1, login="alice", official=False),
        ],
    )
    assert observation.approval_status != ApprovalStatus.CHANGES_REQUESTED


def test_unofficial_review_does_not_erase_an_earlier_official_approval():
    observation = observe_pr_state(
        _pr(),
        reviews=[
            _review(state="APPROVED", review_id=1, login="alice", official=True),
            _review(state="REQUEST_CHANGES", review_id=2, login="alice", official=False),
        ],
    )
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_pending_team_review_request_maps_to_pending_not_none():
    """A pending team review request carries `team` instead of `user`
    (no individual reviewer) -- it is still a genuine pending request and
    must not silently drop the "review still pending" signal down to
    NONE just because there is no user login to key it by."""
    observation = observe_pr_state(
        _pr(), reviews=[_review(state="REQUEST_REVIEW", review_id=1, team="reviewers")],
    )
    assert observation.approval_status == ApprovalStatus.PENDING


def test_pending_request_from_one_reviewer_wins_over_another_reviewers_approval():
    """An official REQUEST_REVIEW re-request on one reviewer must not
    lose to a stale APPROVED from another -- a requested review still
    outstanding must not let the aggregate advance to APPROVED."""
    observation = observe_pr_state(
        _pr(),
        reviews=[
            _review(state="APPROVED", review_id=1, login="alice"),
            _review(state="REQUEST_REVIEW", review_id=2, login="bob"),
        ],
    )
    assert observation.approval_status == ApprovalStatus.PENDING


def test_pending_review_is_ignored_entirely():
    observation = observe_pr_state(_pr(), reviews=[_review(state="PENDING", review_id=1)])
    assert observation.approval_status == ApprovalStatus.NONE


def test_dismissed_request_changes_does_not_still_block():
    observation = observe_pr_state(
        _pr(),
        reviews=[_review(state="REQUEST_CHANGES", review_id=1, dismissed=True)],
    )
    assert observation.approval_status == ApprovalStatus.NONE


def test_dismissed_approval_does_not_still_approve():
    observation = observe_pr_state(
        _pr(), reviews=[_review(state="APPROVED", review_id=1, dismissed=True)]
    )
    assert observation.approval_status == ApprovalStatus.NONE


def test_stale_request_changes_does_not_block():
    """Gitea 1.26+ exposes ``stale`` separately from ``dismissed`` (a
    review attached to an older head); an undismissed-but-stale verdict
    must not determine the current status either."""
    observation = observe_pr_state(
        _pr(), reviews=[_review(state="REQUEST_CHANGES", review_id=1, stale=True)]
    )
    assert observation.approval_status == ApprovalStatus.NONE


def test_stale_approval_does_not_still_approve():
    observation = observe_pr_state(
        _pr(), reviews=[_review(state="APPROVED", review_id=1, stale=True)]
    )
    assert observation.approval_status == ApprovalStatus.NONE


def test_fresh_review_still_counts_alongside_a_stale_one():
    observation = observe_pr_state(
        _pr(),
        reviews=[
            _review(state="REQUEST_CHANGES", review_id=1, login="alice", stale=True),
            _review(state="APPROVED", review_id=2, login="bob"),
        ],
    )
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_dismissed_review_comments_are_still_fetched_and_can_block(monkeypatch):
    """Dismissal invalidates only the review's own verdict -- Gitea tracks
    comment resolution independently per comment, so a dismissed review's
    still-unresolved inline feedback must keep blocking."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?page=1" in url:
            return _status(
                json.dumps([_review(state="REQUEST_CHANGES", review_id=1, dismissed=True)]), 200
            )
        if "/pulls/7/reviews?page=" in url:
            return _status(json.dumps([]), 200)
        if "/reviews/1/comments?page=1" in url:
            return _status(json.dumps([{"id": 1, "body": "still open"}]), 200)
        if "/reviews/1/comments?page=" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    observation = adapter.observe("gitea.example.com/example/project", 7)
    assert observation.approval_status == ApprovalStatus.NONE  # dismissed: no verdict
    assert HoldReason.BLOCKING_THREADS in observation.holds  # but comments still checked


def test_unrecognized_review_state_raises():
    with pytest.raises(GiteaPRObservationError, match="review state"):
        observe_pr_state(_pr(), reviews=[_review(state="SOMETHING_NEW", review_id=1)])


# --- observe_pr_state: mergeability dimension -----------------------------


def test_not_mergeable_is_unknown_not_conflicted():
    """Gitea's `mergeable` is a plain boolean, unlike GitHub's
    discriminating MERGEABLE/CONFLICTING/UNKNOWN tri-state: Gitea also
    reports `false` while conflict-checking is still running, when that
    check errored, and for draft/WIP PRs -- not only for a real conflict.
    With no discriminating signal available, UNKNOWN is the only safe
    classification; DRAFT/WIP holds (handled separately by _holds())
    still cover those cases on their own."""
    observation = observe_pr_state(_pr(mergeable=False))
    assert observation.mergeability == Mergeability.UNKNOWN


def test_not_mergeable_draft_is_unknown_not_conflicted():
    observation = observe_pr_state(_pr(mergeable=False, draft=True))
    assert observation.mergeability == Mergeability.UNKNOWN
    assert HoldReason.DRAFT in observation.holds


def test_mergeable_unknown_is_unknown():
    observation = observe_pr_state(_pr(mergeable=None))
    assert observation.mergeability == Mergeability.UNKNOWN


def test_mergeable_with_no_status_rollup_is_clean():
    observation = observe_pr_state(_pr(mergeable=True), status_rollup=None)
    assert observation.mergeability == Mergeability.CLEAN


@pytest.mark.parametrize(
    ("status_rollup", "expected"),
    [
        ("pending", Mergeability.CHECKS_PENDING),
        ("failure", Mergeability.CHECKS_FAILED),
    ],
)
def test_blocking_status_rollup_takes_priority_over_an_ambiguous_mergeable_value(
    status_rollup, expected,
):
    """mergeable: false (or null) is ambiguous on its own -- but a
    definitive BLOCKING check-status rollup is not, and must not be
    suppressed by that ambiguity. A PR with real pending/failing checks
    must still reach CHECKS_PENDING/CHECKS_FAILED even though `mergeable`
    itself gives no useful signal."""
    for mergeable in (False, None):
        observation = observe_pr_state(_pr(mergeable=mergeable), status_rollup=status_rollup)
        assert observation.mergeability == expected


@pytest.mark.parametrize("status_rollup", ["success", "skipped"])
def test_clean_status_rollup_does_not_override_an_ambiguous_mergeable_value(status_rollup):
    """A CLEAN rollup only proves the checks passed -- it says nothing
    about a real merge conflict, so it must not override an explicit
    `mergeable: false`/`None`. With `mergeable` not definitively True,
    the result must stay UNKNOWN even though the checks are clean."""
    for mergeable in (False, None):
        observation = observe_pr_state(_pr(mergeable=mergeable), status_rollup=status_rollup)
        assert observation.mergeability == Mergeability.UNKNOWN


@pytest.mark.parametrize(
    ("status_rollup", "expected"),
    [
        ("success", Mergeability.CLEAN),
        ("skipped", Mergeability.CLEAN),
        ("pending", Mergeability.CHECKS_PENDING),
        ("warning", Mergeability.CHECKS_PENDING),
        ("failure", Mergeability.CHECKS_FAILED),
        ("error", Mergeability.CHECKS_FAILED),
    ],
)
def test_status_rollup_maps_to_mergeability(status_rollup, expected):
    observation = observe_pr_state(_pr(mergeable=True), status_rollup=status_rollup)
    assert observation.mergeability == expected


def test_unrecognized_status_rollup_raises():
    with pytest.raises(GiteaPRObservationError, match="combined commit status"):
        observe_pr_state(_pr(mergeable=True), status_rollup="something-new")


def test_unrecognized_mergeable_value_raises():
    with pytest.raises(GiteaPRObservationError, match="mergeable"):
        observe_pr_state(_pr(mergeable="yes"))


# --- observe_pr_state: holds ------------------------------------------------


def test_draft_is_a_hold():
    observation = observe_pr_state(_pr(draft=True))
    assert HoldReason.DRAFT in observation.holds


def test_wip_title_is_a_hold():
    observation = observe_pr_state(_pr(title="[WIP] still working"))
    assert HoldReason.WIP in observation.holds


def test_unresolved_review_comment_is_blocking():
    observation = observe_pr_state(
        _pr(), review_comment_groups=[[{"id": 1, "body": "fix this"}]]
    )
    assert HoldReason.BLOCKING_THREADS in observation.holds


def test_resolved_review_comment_is_not_blocking():
    observation = observe_pr_state(
        _pr(),
        review_comment_groups=[[{"id": 1, "body": "fixed", "resolver": {"login": "bob"}}]],
    )
    assert HoldReason.BLOCKING_THREADS not in observation.holds


def test_resolved_thread_with_unresolved_reply_is_not_blocking():
    """Gitea records the resolver on a resolved thread's root comment while
    replies stay unset -- a group is resolved if ANY comment in it carries
    one, not only its first/root comment."""
    observation = observe_pr_state(
        _pr(),
        review_comment_groups=[[
            {"id": 1, "body": "root", "resolver": {"login": "bob"}},
            {"id": 2, "body": "reply", "resolver": None},
        ]],
    )
    assert HoldReason.BLOCKING_THREADS not in observation.holds


def test_one_unresolved_group_blocks_even_with_another_resolved():
    observation = observe_pr_state(
        _pr(),
        review_comment_groups=[
            [{"id": 1, "body": "resolved", "resolver": {"login": "bob"}}],
            [{"id": 2, "body": "still open"}],
        ],
    )
    assert HoldReason.BLOCKING_THREADS in observation.holds


def test_no_holds_is_an_empty_frozenset():
    observation = observe_pr_state(_pr())
    assert observation.holds == frozenset()


# --- observe_pr_state: revision + number ------------------------------------


@pytest.mark.parametrize("missing", ["head", "base"])
def test_missing_revision_field_raises(missing):
    payload = _pr()
    payload[missing] = {}
    with pytest.raises(GiteaPRObservationError):
        observe_pr_state(payload)


def test_missing_number_raises():
    payload = _pr()
    payload["number"] = None
    with pytest.raises(GiteaPRObservationError, match="number"):
        observe_pr_state(payload)


def test_observation_carries_the_pr_number_and_revision():
    observation = observe_pr_state(_pr(number=99))
    assert observation.number == 99
    assert isinstance(observation, PRObservation)
    assert observation.revision.diff_hash == "head-sha"
    assert observation.revision.base_sha == "base-sha"


# --- GiteaPRAdapter: curl wrapper (fake runner, no network) -----------------


def _status(body: str, code: int) -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=f"{body}\n{code}", stderr="")


def _fake_responses(*responses):
    values = iter(responses)
    return lambda *_args, **_kwargs: next(values)


def test_empty_status_rollup_with_zero_total_count_is_not_pending(monkeypatch):
    """Gitea's combined-status endpoint reports "pending" even with
    total_count == 0 (no CI configured at all) -- confirmed live against a
    real instance. fetch_pr must not pass that default "pending" through as
    a real rollup (observe_pr_state would then wrongly report
    CHECKS_PENDING instead of CLEAN)."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "pending", "total_count": 0}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter("review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"})
    raw = adapter.fetch_pr("gitea.example.com/example/project", 7)
    assert raw["status_rollup"] is None


def test_fetch_pr_unwraps_nested_commit_detail_for_last_commit_at(monkeypatch):
    """Gitea's ``git/commits/{sha}`` response nests author/committer under
    an inner ``commit`` key -- confirmed live; a flat read silently returns
    None forever instead of a real timestamp."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(
                json.dumps({
                    "sha": "head-sha",
                    "commit": {
                        "author": {"date": "2026-01-01T00:00:00Z"},
                        "committer": {"date": "2026-01-02T00:00:00Z"},
                    },
                }),
                200,
            )
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter("review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"})
    raw = adapter.fetch_pr("gitea.example.com/example/project", 7)
    observation = observe_pr_state(
        raw["pull_request"], raw["reviews"], raw["review_comment_groups"], raw["status_rollup"],
    )
    assert observation.last_commit_at is not None


def test_fetch_pr_skips_comments_request_when_count_is_zero(monkeypatch):
    """Gitea reports comments_count on each review -- a review known to
    carry none (approval/comment-only) must not cost a wasted request on
    every poll of a long-lived PR."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?page=1" in url:
            return _status(
                json.dumps([
                    {**_review(state="APPROVED", review_id=1), "comments_count": 0},
                ]),
                200,
            )
        if "/pulls/7/reviews?page=" in url:
            return _status(json.dumps([]), 200)
        if "/reviews/1/comments" in url:
            raise AssertionError("must not fetch comments when comments_count is 0")
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    observation = adapter.observe("gitea.example.com/example/project", 7)
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_fetch_pr_still_fetches_comments_when_count_is_absent(monkeypatch):
    """A missing comments_count (older Gitea, or an omitted field) must
    still fetch, for compatibility -- only an explicit zero skips."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    fetched = []

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?page=1" in url:
            return _status(json.dumps([_review(state="COMMENT", review_id=1)]), 200)
        if "/pulls/7/reviews?page=" in url:
            return _status(json.dumps([]), 200)
        if "/reviews/1/comments" in url:
            fetched.append(url)
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    adapter.observe("gitea.example.com/example/project", 7)
    assert fetched


def test_fetch_pr_verifies_identity_and_repo_then_returns_full_payload(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter("review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"})

    raw = adapter.fetch_pr("gitea.example.com/example/project", 7)

    assert raw["pull_request"]["number"] == 7
    assert raw["status_rollup"] == "success"
    assert any(c[4].endswith("/api/v1/user") for c in calls)


def test_identity_mismatch_raises(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    responses = _fake_responses(_status(json.dumps({"login": "someone-else"}), 200))
    adapter = GiteaPRAdapter("review-bot", runner=responses, api_bases={"gitea.example.com": "https://gitea.example.com"})

    with pytest.raises(RuntimeError, match="identity mismatch"):
        adapter.fetch_pr("gitea.example.com/example/project", 1)


def test_repository_mismatch_raises(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    responses = _fake_responses(
        _status(json.dumps({"login": "review-bot"}), 200),
        _status(json.dumps({"full_name": "someone/else"}), 200),
    )
    adapter = GiteaPRAdapter("review-bot", runner=responses, api_bases={"gitea.example.com": "https://gitea.example.com"})

    with pytest.raises(RuntimeError, match="repository identity mismatch"):
        adapter.fetch_pr("gitea.example.com/example/project", 1)


def test_missing_token_env_raises(monkeypatch):
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    adapter = GiteaPRAdapter(
        "review-bot", runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable")), api_bases={"gitea.example.com": "https://gitea.example.com"}
    )

    with pytest.raises(RuntimeError, match="GITEA_TOKEN"):
        adapter.fetch_pr("gitea.example.com/example/project", 1)


def test_curl_failure_raises(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    responses = _fake_responses(SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    adapter = GiteaPRAdapter("review-bot", runner=responses, api_bases={"gitea.example.com": "https://gitea.example.com"})

    with pytest.raises(RuntimeError, match="Gitea operation failed"):
        adapter.fetch_pr("gitea.example.com/example/project", 1)


def test_curl_timeout_raises_sanitized_error_without_the_argv(monkeypatch):
    """A stalled Gitea request must not block the poll-path observation
    cycle indefinitely -- the call is bounded -- and the raised error
    must never echo back raw command metadata."""
    monkeypatch.setenv("GITEA_TOKEN", "super-secret-token")

    def runner(args, **kwargs):
        assert kwargs.get("timeout") == 120
        raise subprocess.TimeoutExpired(cmd=args, timeout=120)

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    with pytest.raises(RuntimeError, match="timed out") as exc_info:
        adapter.fetch_pr("gitea.example.com/example/project", 1)
    assert "super-secret-token" not in str(exc_info.value)


def test_token_is_never_passed_as_a_literal_argv_element(monkeypatch):
    """curl's argv is visible to any same-host process inspection (e.g.
    /proc/<pid>/cmdline) for the entire lifetime of the request -- the
    API token must travel via stdin (curl's `-H @-` header-from-file
    convention), never as a literal command-line argument."""
    monkeypatch.setenv("GITEA_TOKEN", "super-secret-token")
    seen_inputs = []

    def runner(args, **kwargs):
        assert not any("super-secret-token" in str(arg) for arg in args)
        seen_inputs.append(kwargs.get("input"))
        return _status(json.dumps({"login": "review-bot"}), 200)

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    adapter._call("https://gitea.example.com", "GET", "/user")
    assert any(
        inp is not None and "Authorization: token super-secret-token" in inp
        for inp in seen_inputs
    )


def test_http_error_status_raises(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    responses = _fake_responses(_status("not found", 404))
    adapter = GiteaPRAdapter("review-bot", runner=responses, api_bases={"gitea.example.com": "https://gitea.example.com"})

    with pytest.raises(RuntimeError, match="HTTP 404"):
        adapter.fetch_pr("gitea.example.com/example/project", 1)


def test_observe_fetches_and_classifies_in_one_call(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?page=1" in url:
            return _status(json.dumps([_review(state="APPROVED", review_id=1)]), 200)
        if "/pulls/7/reviews?page=" in url:
            return _status(json.dumps([]), 200)
        if "/reviews/1/comments" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter("review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"})

    observation = adapter.observe("gitea.example.com/example/project", 7)

    assert observation.number == 7
    assert observation.approval_status == ApprovalStatus.APPROVED


def test_fetch_pr_paginates_past_a_full_first_page_of_reviews(monkeypatch):
    """A PR with more reviews than one page must not have a later
    approval/change-request silently dropped."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?page=1" in url:
            return _status(
                json.dumps([
                    _review(state="COMMENT", review_id=n, login=f"user{n}")
                    for n in range(1, 51)
                ]),
                200,
            )
        if "/pulls/7/reviews?page=2" in url:
            return _status(
                json.dumps([_review(state="REQUEST_CHANGES", review_id=51, login="late-reviewer")]),
                200,
            )
        if "/pulls/7/reviews?page=" in url:
            return _status(json.dumps([]), 200)
        if "/reviews/" in url and "/comments" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    observation = adapter.observe("gitea.example.com/example/project", 7)
    assert observation.approval_status == ApprovalStatus.CHANGES_REQUESTED


def test_all_reviews_raises_past_the_bounded_scan(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        return _status(json.dumps([{"id": n} for n in range(50)]), 200)  # always full

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    with pytest.raises(RuntimeError, match="bounded"):
        adapter._all_reviews("https://gitea.example.com", "example", "project", 7)


def test_fetch_pr_paginates_past_a_full_first_page_of_review_comments(monkeypatch):
    """A review's own resolving comment can sit on a later page than the
    first (unresolved) replies -- without pagination, the thread would
    incorrectly stay reported as blocking forever once resolved."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?page=1" in url:
            return _status(json.dumps([_review(state="COMMENT", review_id=1)]), 200)
        if "/pulls/7/reviews?page=" in url:
            return _status(json.dumps([]), 200)
        if "/reviews/1/comments?page=1" in url:
            return _status(
                json.dumps([{"id": n, "resolver": None} for n in range(1, 51)]),
                200,
            )
        if "/reviews/1/comments?page=2" in url:
            return _status(json.dumps([{"id": 51, "resolver": {"login": "x"}}]), 200)
        if "/reviews/1/comments?page=" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    observation = adapter.observe("gitea.example.com/example/project", 7)
    # The resolving comment lives on page 2 -- only reachable thanks to
    # pagination -- so the thread must correctly report resolved, not
    # blocking.
    assert HoldReason.BLOCKING_THREADS not in observation.holds


def test_fetch_pr_treats_distinct_file_locations_in_one_review_as_separate_threads(
    monkeypatch,
):
    """A single Gitea review can carry multiple independent inline
    conversations (one per file/line it comments on). Grouping the whole
    review as one thread would let ONE resolved conversation's
    `any(resolver)` clear BLOCKING_THREADS even while another conversation
    in the SAME review stays open -- comments must be partitioned by their
    (path, position) diff location before that rule applies."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        if url.endswith("/pulls/7"):
            return _status(json.dumps(_pr(number=7)), 200)
        if "/pulls/7/reviews?page=1" in url:
            return _status(json.dumps([_review(state="COMMENT", review_id=1)]), 200)
        if "/pulls/7/reviews?page=" in url:
            return _status(json.dumps([]), 200)
        if "/reviews/1/comments?page=1" in url:
            return _status(
                json.dumps([
                    {"id": 1, "path": "a.py", "position": 10, "resolver": {"login": "x"}},
                    {"id": 2, "path": "b.py", "position": 20, "resolver": None},
                ]),
                200,
            )
        if "/reviews/1/comments?page=" in url:
            return _status(json.dumps([]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    observation = adapter.observe("gitea.example.com/example/project", 7)
    # a.py's own conversation is resolved, but b.py's is not -- the review
    # as a whole must still block.
    assert HoldReason.BLOCKING_THREADS in observation.holds


def test_all_review_comments_raises_past_the_bounded_scan(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        # Every page returns a full page of genuinely new ids, so the scan
        # legitimately never terminates within the bounded page count.
        page = int(url.rsplit("page=", 1)[1].split("&", 1)[0])
        start = (page - 1) * 50
        return _status(json.dumps([{"id": n} for n in range(start, start + 50)]), 200)

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    with pytest.raises(RuntimeError, match="bounded"):
        adapter._all_review_comments("https://gitea.example.com", "example", "project", 7, 1)


def test_all_review_comments_stops_when_gitea_ignores_pagination(monkeypatch):
    """A live Gitea instance has been observed to let its review-comments
    endpoint silently ignore ``page``/``limit`` and return the same full,
    unpaginated comment list on every call. Stopping only on an *empty*
    page would never trigger here, spuriously raising the bounded-scan
    error for every observation of a review with at least one comment."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status(json.dumps({"login": "review-bot"}), 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status(json.dumps({"full_name": "example/project"}), 200)
        return _status(json.dumps([{"id": 1}, {"id": 2}]), 200)

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    comments = adapter._all_review_comments("https://gitea.example.com", "example", "project", 7, 1)
    assert [c["id"] for c in comments] == [1, 2]
