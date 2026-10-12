"""Tests for the Gitea backlog-provider adapter (``gitea_provider``).

Mirrors ``test_repository_issue_loops.py``'s own ``GitHubProvider``/
``AzureDevOpsProvider`` test style (fake-runner injection, no network).
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from agent_dispatch.gitea_provider import GiteaProvider
from agent_dispatch.repository_issue_loops import Issue


def _status(body, code: int) -> SimpleNamespace:
    text = body if isinstance(body, str) else json.dumps(body)
    return SimpleNamespace(returncode=0, stdout=f"{text}\n{code}", stderr="")


def _issue_row(number: int, **overrides) -> dict:
    base = {
        "number": number,
        "title": f"Issue {number}",
        "html_url": f"https://gitea.example.com/example/project/issues/{number}",
        "labels": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "pull_request": None,
    }
    base.update(overrides)
    return base


def _provider(runner) -> GiteaProvider:
    return GiteaProvider(
        "issue-bot", runner=runner, api_base="https://gitea.example.com"
    )


def test_requires_api_base():
    with pytest.raises(ValueError, match="api_base"):
        GiteaProvider("issue-bot")


def test_requires_expected_login():
    with pytest.raises(ValueError, match="expected_login"):
        GiteaProvider("", api_base="https://gitea.example.com")


def test_missing_token_env_raises(monkeypatch):
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    provider = _provider(
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable"))
    )
    with pytest.raises(RuntimeError, match="GITEA_TOKEN"):
        provider.list_open_issues("example/project")


def test_identity_mismatch_raises(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    provider = _provider(lambda *a, **k: _status({"login": "someone-else"}, 200))
    with pytest.raises(RuntimeError, match="identity mismatch"):
        provider.list_open_issues("example/project")


def test_repository_mismatch_raises(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    responses = iter([
        _status({"login": "issue-bot"}, 200),
        _status({"full_name": "someone/else"}, 200),
    ])
    provider = _provider(lambda *a, **k: next(responses))
    with pytest.raises(RuntimeError, match="repository identity mismatch"):
        provider.list_open_issues("example/project")


def test_list_open_issues_filters_pull_requests_and_reads_comments(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if "/issues?state=open" in url and "page=1" in url:
            return _status(
                [_issue_row(1), _issue_row(2, pull_request={"url": "..."})], 200
            )
        if "/issues?state=open" in url and "page=2" in url:
            return _status([], 200)
        if "/issues/1/comments?" in url:
            return _status([], 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    issues = _provider(runner).list_open_issues("example/project")

    assert [issue.number for issue in issues] == [1]  # PR (issue 2) excluded
    assert not any("/issues/2/comments" in c[4] for c in calls)


def test_list_open_issues_paginates_until_a_short_page(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if "page=1" in url:
            return _status([_issue_row(n) for n in range(1, 51)], 200)
        if "page=2" in url:
            return _status([_issue_row(51)], 200)
        if "page=3" in url:
            return _status([], 200)
        if "/comments" in url:
            return _status([], 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    issues = _provider(runner).list_open_issues("example/project")
    assert len(issues) == 51


def test_all_comments_paginates_past_a_full_first_page(monkeypatch):
    """A busy issue with more comments than one page must not have its
    reservation marker (pushed onto a later page) silently missed --
    discovery would otherwise treat an already-reserved issue as
    unreserved and dispatch duplicate work."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    from agent_dispatch.issue_loop_markers import _marker

    marker = _marker({
        "loop": "backlog", "occurrence": 1, "state": "reserved",
        "at": 0, "label": "backlog-active", "issue": 1,
    })

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if "/issues?state=open" in url and "page=1" in url:
            return _status([_issue_row(1)], 200)
        if "/issues?state=open" in url and "page=2" in url:
            return _status([], 200)
        if "/issues/1/comments?page=1" in url:
            return _status(
                [{"id": n, "body": f"filler {n}", "user": {"login": "someone-else"}}
                 for n in range(1, 51)],
                200,
            )
        if "/issues/1/comments?page=2" in url:
            return _status(
                [{"id": 51, "body": marker, "user": {"login": "issue-bot"}}], 200
            )
        if "/issues/1/comments?page=" in url:
            return _status([], 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    (issue,) = _provider(runner).list_open_issues("example/project")
    assert len(issue.reservations) == 1
    assert issue.reservations[0]["loop"] == "backlog"


def test_all_comments_raises_past_the_bounded_scan(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        # Every page returns a full page of genuinely new ids, so the scan
        # legitimately never terminates within the bounded page count.
        page = int(url.rsplit("page=", 1)[1].split("&", 1)[0])
        start = (page - 1) * 50
        return _status([{"id": n} for n in range(start, start + 50)], 200)

    with pytest.raises(RuntimeError, match="bounded"):
        _provider(runner)._all_comments("example/project", 1)


def test_all_comments_stops_when_gitea_ignores_pagination(monkeypatch):
    """A live Gitea instance has been observed to let its issue-comments
    endpoint silently ignore ``page``/``limit`` and return the same full,
    unpaginated comment list on every call. Stopping only on an *empty*
    page would never trigger here, spuriously raising the bounded-scan
    error for any issue with at least one comment."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        # Same two comments returned verbatim regardless of page/limit.
        return _status(
            [{"id": 1, "body": "first"}, {"id": 2, "body": "second"}], 200
        )

    comments = _provider(runner)._all_comments("example/project", 1)
    assert [c["id"] for c in comments] == [1, 2]


def test_reservation_marker_roundtrips_through_comments(monkeypatch):
    """A reserve -> claim -> release cycle: each transition edits the same
    marker comment in place (mirrors GitHub's own edit-not-repost
    convention), and label add/remove brackets the active window."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    comment_body = {"value": None}
    labels_on_issue: set[int] = set()

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if url.endswith("/issues/1") and method == "GET":
            return _status({"labels": [{"id": i} for i in labels_on_issue]}, 200)
        if "/issues/1/comments?page=1" in url and method == "GET":
            if comment_body["value"] is None:
                return _status([], 200)
            return _status(
                [{"id": 99, "body": comment_body["value"], "user": {"login": "issue-bot"}}],
                200,
            )
        if "/issues/1/comments?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            payload = json.loads(args[args.index("-d") + 1])
            comment_body["value"] = payload["body"]
            return _status({"id": 99}, 201)
        if "/issues/comments/99" in url and method == "PATCH":
            payload = json.loads(args[args.index("-d") + 1])
            comment_body["value"] = payload["body"]
            return _status({"id": 99}, 200)
        if "/repos/example/project/labels?page=1" in url and method == "GET":
            return _status([{"id": 5, "name": "backlog-active"}], 200)
        if "/repos/example/project/labels?" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/labels") and method == "POST":
            labels_on_issue.add(5)
            return _status({}, 200)
        if "/issues/1/labels/5" in url and method == "DELETE":
            labels_on_issue.discard(5)
            return _status("", 204)
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    reservation = {"loop": "backlog", "occurrence": 1, "label": "backlog-active"}

    provider.reserve("example/project", issue, reservation)
    assert labels_on_issue == {5}
    assert "reserved" in comment_body["value"]

    provider.claim("example/project", issue, reservation, "task-1")
    assert "claimed" in comment_body["value"]
    assert "task-1" in comment_body["value"]

    provider.release("example/project", issue, reservation, "done")
    assert "released" in comment_body["value"]
    assert labels_on_issue == set()  # label removed: no other active reservation


def test_release_keeps_label_when_another_loop_is_still_active(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    from agent_dispatch.issue_loop_markers import _marker

    other_marker = _marker({
        "loop": "other-loop", "occurrence": 1, "state": "reserved",
        "at": 0, "label": "backlog-active", "issue": 1,
    })

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if "/issues/1/comments?page=1" in url and method == "GET":
            return _status(
                [{"id": 1, "body": f"x\n\n{other_marker}", "user": {"login": "issue-bot"}}], 200
            )
        if "/issues/1/comments?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            return _status({"id": 2}, 201)
        if "/issues/comments/" in url and method == "PATCH":
            return _status({"id": 1}, 200)
        if method == "DELETE":
            raise AssertionError("label must not be removed while another loop is active")
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    reservation = {"loop": "backlog", "occurrence": 1, "label": "backlog-active"}
    provider.release("example/project", issue, reservation, "done")


def test_release_re_adds_the_label_when_a_concurrent_reserve_races_the_delete(monkeypatch):
    """release()'s check-then-delete is not atomic with a concurrent
    reserve(): a racing loop can add the label (a no-op, already present)
    and not yet have written its own marker at the moment of release()'s
    pre-delete scan -- reserve-before-election overlap is expected, so
    this is a real race window. If a re-scan right after the delete shows
    a new active reservation on this same label, the label must be
    re-added so that reservation's marker is never left without it."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    from agent_dispatch.issue_loop_markers import _marker

    racing_marker = _marker({
        "loop": "racing-loop", "occurrence": 1, "state": "reserved",
        "at": 0, "label": "backlog-active", "issue": 1,
    })
    comment_scan_calls = {"count": 0}
    relabeled = []

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if "/issues/1/comments?page=1" in url and method == "GET":
            comment_scan_calls["count"] += 1
            if comment_scan_calls["count"] < 3:
                # _find_own_loop_comment (write) and the pre-delete scan:
                # nothing active from anyone else yet.
                return _status([], 200)
            # The post-delete re-scan: the racing loop's marker has now
            # landed.
            return _status(
                [{"id": 9, "body": racing_marker, "user": {"login": "issue-bot"}}], 200
            )
        if "/issues/1/comments?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            return _status({"id": 2}, 201)
        if "/labels?page=1" in url and method == "GET":
            return _status([{"id": 7, "name": "backlog-active"}], 200)
        if "/labels?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/labels") and method == "POST":
            relabeled.append(7)
            return _status([{"id": 7, "name": "backlog-active"}], 201)
        if url.endswith("/issues/1/labels/7") and method == "DELETE":
            return _status("", 204)
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    reservation = {"loop": "backlog", "occurrence": 1, "label": "backlog-active"}
    provider.release("example/project", issue, reservation, "done")
    assert relabeled == [7]


def test_release_treats_an_already_absent_label_as_success(monkeypatch):
    """A concurrent release, or a retry after a first DELETE that
    actually succeeded but whose response was lost, can see a 404 here
    after the marker is already `released`. The desired final state (no
    label) is already reached, so this must succeed rather than raising
    and making every such retry fail."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if "/issues/1/comments?page=1" in url and method == "GET":
            return _status([], 200)
        if "/issues/1/comments?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            return _status({"id": 1}, 201)
        if "/labels?page=1" in url and method == "GET":
            return _status([{"id": 7, "name": "backlog-active"}], 200)
        if "/labels?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/labels/7") and method == "DELETE":
            return _status({"message": "label does not exist"}, 404)
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    reservation = {"loop": "backlog", "occurrence": 1, "label": "backlog-active"}
    provider.release("example/project", issue, reservation, "done")


def test_label_id_resolution_raises_when_label_absent(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            raise AssertionError(
                "reserve() must not persist the reserved marker comment "
                "before the label prerequisite succeeds"
            )
        if "/labels?page=" in url and method == "GET":
            return _status([], 200)
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    with pytest.raises(RuntimeError, match="no label named"):
        provider.reserve(
            "example/project", issue,
            {"loop": "backlog", "occurrence": 1, "label": "missing-label"},
        )


def test_label_id_resolution_re_fetches_after_a_miss_instead_of_caching_forever(monkeypatch):
    """A missing-label error tells an operator to create the label -- but
    caching the (incomplete) lookup would make every later retry in this
    same resident process reuse the stale mapping and keep failing even
    after the label is created, until restart. The cache must be evicted
    on a miss so the next retry re-fetches."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    label_list_calls = {"count": 0}

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if "/labels?page=1" in url and method == "GET":
            label_list_calls["count"] += 1
            if label_list_calls["count"] == 1:
                return _status([], 200)  # first lookup: label does not exist yet
            return _status([{"id": 9, "name": "backlog-active"}], 200)  # now it does
        if "/labels?page=" in url and method == "GET":
            return _status([], 200)
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    with pytest.raises(RuntimeError, match="no label named"):
        provider._label_id("example/project", "backlog-active")
    # Without eviction, this second call would reuse the first (empty)
    # cached mapping and raise again despite the label now existing.
    assert provider._label_id("example/project", "backlog-active") == 9
    assert label_list_calls["count"] == 2


def test_reserve_re_adds_the_label_when_a_concurrent_release_races_the_marker_write(
    monkeypatch,
):
    """The label add and the marker write are not atomic with a
    concurrent release() (or another failed reserve()'s own rollback):
    either can observe this call's label add, scan for an active
    reservation, find none yet (this marker hasn't landed yet), and
    delete the label -- all before this marker write completes.
    Pre-election overlap is intentionally supported, so this window is
    real. The label must be re-added once the marker successfully lands,
    so the reservation it now records is never left unlabeled."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    get_issue_calls = {"count": 0}
    relabeled = []

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if url.endswith("/issues/1") and method == "GET":
            get_issue_calls["count"] += 1
            if get_issue_calls["count"] == 1:
                return _status({"labels": []}, 200)  # pre-add check: not present yet
            # post-marker re-check: a concurrent release() won the race and
            # removed it in between.
            return _status({"labels": []}, 200)
        if "/labels?page=1" in url and method == "GET":
            return _status([{"id": 7, "name": "backlog-active"}], 200)
        if "/labels?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/labels") and method == "POST":
            relabeled.append(7)
            return _status([{"id": 7, "name": "backlog-active"}], 201)
        if "/issues/1/comments?" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            return _status({"id": 2}, 201)
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    provider.reserve(
        "example/project", issue,
        {"loop": "backlog", "occurrence": 1, "label": "backlog-active"},
    )
    # One add from the normal happy path, one more from the post-marker
    # re-check discovering the concurrent release() won the race.
    assert relabeled == [7, 7]


def test_reserve_rolls_back_the_label_when_the_marker_comment_fails(monkeypatch):
    """The label add can succeed and then the marker-comment write can
    fail (transiently or permanently). Left alone, that leaves a labeled
    issue with no marker recording who reserved it or why -- a leaked
    reservation discovery can never distinguish from a real one. The
    label add must be rolled back so the failure leaves no state."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    deleted_label_ids = []

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if url.endswith("/issues/1") and method == "GET":
            return _status({"labels": []}, 200)
        if "/labels?page=1" in url and method == "GET":
            return _status([{"id": 7, "name": "backlog-active"}], 200)
        if "/labels?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/labels") and method == "POST":
            return _status([{"id": 7, "name": "backlog-active"}], 201)
        if "/issues/1/comments?" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            return _status("server error", 500)
        if url.endswith("/issues/1/labels/7") and method == "DELETE":
            deleted_label_ids.append(7)
            return _status("", 204)
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        provider.reserve(
            "example/project", issue,
            {"loop": "backlog", "occurrence": 1, "label": "backlog-active"},
        )
    assert deleted_label_ids == [7]


def test_reserve_does_not_roll_back_a_label_that_was_already_present(monkeypatch):
    """If the label was already applied to the issue before this reserve()
    call (e.g. a prior attempt's own label add that then failed on the
    comment step, or another loop having independently applied the same
    label), this call never introduced it -- a rollback must not strip a
    label it did not add itself."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    deleted_label_ids = []

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if url.endswith("/issues/1") and method == "GET":
            return _status({"labels": [{"id": 7, "name": "backlog-active"}]}, 200)
        if "/labels?page=1" in url and method == "GET":
            return _status([{"id": 7, "name": "backlog-active"}], 200)
        if "/labels?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/labels") and method == "POST":
            return _status([{"id": 7, "name": "backlog-active"}], 201)
        if "/issues/1/comments?" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            return _status("server error", 500)
        if method == "DELETE":
            raise AssertionError("must not delete a label this call did not add")
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        provider.reserve(
            "example/project", issue,
            {"loop": "backlog", "occurrence": 1, "label": "backlog-active"},
        )
    assert deleted_label_ids == []


def test_reserve_does_not_roll_back_a_label_another_loops_active_reservation_depends_on(
    monkeypatch,
):
    """Reservations deliberately race before coordinator election settles a
    winner. If another loop's own reservation (visible via its marker
    comment) is currently 'reserved'/'claimed' on this same label, a
    rollback must never silently clear it -- even though this call's own
    label add just failed to be followed by a successful marker write."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    from agent_dispatch.issue_loop_markers import _marker

    other_loop_marker = _marker({
        "loop": "other-loop", "occurrence": 1, "state": "reserved",
        "at": 0, "label": "backlog-active", "issue": 1,
    })

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if url.endswith("/issues/1") and method == "GET":
            return _status({"labels": []}, 200)
        if "/labels?page=1" in url and method == "GET":
            return _status([{"id": 7, "name": "backlog-active"}], 200)
        if "/labels?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/labels") and method == "POST":
            return _status([{"id": 7, "name": "backlog-active"}], 201)
        if "/issues/1/comments?page=1" in url and method == "GET":
            return _status(
                [{"id": 1, "body": other_loop_marker, "user": {"login": "issue-bot"}}], 200
            )
        if "/issues/1/comments?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            return _status("server error", 500)
        if method == "DELETE":
            raise AssertionError(
                "must not clear another loop's active reservation label"
            )
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        provider.reserve(
            "example/project", issue,
            {"loop": "backlog", "occurrence": 1, "label": "backlog-active"},
        )


def test_reserve_does_not_roll_back_when_its_own_marker_write_actually_landed(monkeypatch):
    """A comment-write transport error is indeterminate: Gitea may commit
    the POST before the client observes a timeout/error. If a fresh scan
    shows THIS loop's own marker now active (the write actually
    succeeded despite the exception), the label it depends on must
    survive -- rollback must not strip a trusted active marker of its
    required label just because the exception that reported the write's
    outcome was unreliable."""
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    from agent_dispatch.issue_loop_markers import _marker

    own_marker = _marker({
        "loop": "backlog", "occurrence": 1, "state": "reserved",
        "at": 0, "label": "backlog-active", "issue": 1,
    })

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if url.endswith("/issues/1") and method == "GET":
            return _status({"labels": []}, 200)
        if "/labels?page=1" in url and method == "GET":
            return _status([{"id": 7, "name": "backlog-active"}], 200)
        if "/labels?page=" in url and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/labels") and method == "POST":
            return _status([{"id": 7, "name": "backlog-active"}], 201)
        if "/issues/1/comments?page=1" in url and method == "GET":
            # The scan AFTER the "failed" write shows this loop's own
            # marker already active -- the POST actually landed.
            return _status(
                [{"id": 1, "body": own_marker, "user": {"login": "issue-bot"}}], 200
            )
        if "/issues/1/comments?page=" in url and method == "GET":
            return _status([], 200)
        if "/issues/comments/1" in url and method == "PATCH":
            raise RuntimeError("curl: operation timed out")
        if method == "DELETE":
            raise AssertionError(
                "must not clear this loop's own now-active reservation label"
            )
        raise AssertionError(f"unexpected curl invocation: {method} {url}")

    provider = _provider(runner)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)
    with pytest.raises(RuntimeError, match="timed out"):
        provider.reserve(
            "example/project", issue,
            {"loop": "backlog", "occurrence": 1, "label": "backlog-active"},
        )


def test_http_error_status_raises(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    provider = _provider(lambda *a, **k: _status("server error", 500))
    with pytest.raises(RuntimeError, match="HTTP 500"):
        provider.list_open_issues("example/project")


def test_curl_level_failure_raises(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")
    provider = _provider(
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="curl: timeout")
    )
    with pytest.raises(RuntimeError, match="Gitea operation failed"):
        provider.list_open_issues("example/project")


def test_curl_timeout_raises_sanitized_error_without_the_argv(monkeypatch):
    """A stalled Gitea connection must not block the resident backlog
    loop indefinitely -- the call is bounded -- and the raised error must
    never echo back raw command metadata."""
    monkeypatch.setenv("GITEA_TOKEN", "super-secret-token")

    def runner(args, **kwargs):
        assert kwargs.get("timeout") == 120
        raise subprocess.TimeoutExpired(cmd=args, timeout=120)

    provider = _provider(runner)
    with pytest.raises(RuntimeError, match="timed out") as exc_info:
        provider.list_open_issues("example/project")
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
        return _status({"login": "issue-bot"}, 200)

    provider = GiteaProvider("issue-bot", runner=runner, api_base="https://gitea.example.com")
    provider._call("GET", "/user")
    assert any(
        inp is not None and "Authorization: token super-secret-token" in inp
        for inp in seen_inputs
    )
