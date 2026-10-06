"""Tests for the Gitea reviewer-side provider adapter (``gitea_pr_provider``).

Two layers, mirroring ``test_github_provider_adapter.py``'s own split:

- :func:`observe_pr_state` -- pure classification, plain dict fixtures, no
  transport call at all.
- :class:`GiteaPRAdapter` -- the thin ``curl``-based REST wrapper, tested
  with an injected fake runner (no network).
"""

from __future__ import annotations

import json
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
    stale: bool = False,
) -> dict:
    return {
        "id": review_id, "state": state, "user": {"login": login},
        "dismissed": dismissed, "stale": stale,
    }


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


def test_comment_only_review_maps_to_pending():
    observation = observe_pr_state(_pr(), reviews=[_review(state="COMMENT", review_id=1)])
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


def test_dismissed_review_comments_are_not_blocking(monkeypatch):
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
            return _status(
                json.dumps([_review(state="REQUEST_CHANGES", review_id=1, dismissed=True)]), 200
            )
        if "/reviews/1/comments" in url:
            raise AssertionError("a dismissed review's comments must not be fetched")
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    observation = adapter.observe("gitea.example.com/example/project", 7)
    assert HoldReason.BLOCKING_THREADS not in observation.holds


def test_unrecognized_review_state_raises():
    with pytest.raises(GiteaPRObservationError, match="review state"):
        observe_pr_state(_pr(), reviews=[_review(state="SOMETHING_NEW", review_id=1)])


# --- observe_pr_state: mergeability dimension -----------------------------


def test_not_mergeable_is_conflicted():
    observation = observe_pr_state(_pr(mergeable=False))
    assert observation.mergeability == Mergeability.CONFLICTED


def test_mergeable_unknown_is_unknown():
    observation = observe_pr_state(_pr(mergeable=None))
    assert observation.mergeability == Mergeability.UNKNOWN


def test_mergeable_with_no_status_rollup_is_clean():
    observation = observe_pr_state(_pr(mergeable=True), status_rollup=None)
    assert observation.mergeability == Mergeability.CLEAN


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
    observation = observe_pr_state(_pr(), review_comments=[{"id": 1, "body": "fix this"}])
    assert HoldReason.BLOCKING_THREADS in observation.holds


def test_resolved_review_comment_is_not_blocking():
    observation = observe_pr_state(
        _pr(), review_comments=[{"id": 1, "body": "fixed", "resolver": {"login": "bob"}}]
    )
    assert HoldReason.BLOCKING_THREADS not in observation.holds


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
        raw["pull_request"], raw["reviews"], raw["review_comments"], raw["status_rollup"],
    )
    assert observation.last_commit_at is not None


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
        if "/pulls/7/reviews?" in url:
            return _status(json.dumps([_review(state="APPROVED", review_id=1)]), 200)
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
    """A review with more inline comments than one page must not have an
    unresolved comment on a later page silently clear BLOCKING_THREADS."""
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
            return _status(json.dumps([_review(state="COMMENT", review_id=1)]), 200)
        if "/reviews/1/comments?page=1" in url:
            return _status(
                json.dumps(
                    [{"id": n, "resolver": {"login": "x"}} for n in range(1, 51)]
                ),
                200,
            )
        if "/reviews/1/comments?page=2" in url:
            return _status(json.dumps([{"id": 51, "resolver": None}]), 200)
        if "/commits/head-sha/status" in url:
            return _status(json.dumps({"state": "success", "total_count": 1}), 200)
        if url.endswith("/git/commits/head-sha"):
            return _status(json.dumps({}), 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    adapter = GiteaPRAdapter(
        "review-bot", runner=runner, api_bases={"gitea.example.com": "https://gitea.example.com"},
    )
    observation = adapter.observe("gitea.example.com/example/project", 7)
    assert HoldReason.BLOCKING_THREADS in observation.holds


def test_all_review_comments_raises_past_the_bounded_scan(monkeypatch):
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
        adapter._all_review_comments("https://gitea.example.com", "example", "project", 7, 1)
