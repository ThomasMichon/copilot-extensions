"""Tests for the Gitea backlog-provider adapter (``gitea_provider``).

Mirrors ``test_repository_issue_loops.py``'s own ``GitHubProvider``/
``AzureDevOpsProvider`` test style (fake-runner injection, no network).
"""

from __future__ import annotations

import json
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
        if url.endswith("/issues/1/comments"):
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
        if "/comments" in url:
            return _status([], 200)
        raise AssertionError(f"unexpected curl invocation: {url}")

    issues = _provider(runner).list_open_issues("example/project")
    assert len(issues) == 51


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
        if url.endswith("/issues/1/comments") and method == "GET":
            if comment_body["value"] is None:
                return _status([], 200)
            return _status(
                [{"id": 99, "body": comment_body["value"], "user": {"login": "issue-bot"}}],
                200,
            )
        if url.endswith("/issues/1/comments") and method == "POST":
            payload = json.loads(args[args.index("-d") + 1])
            comment_body["value"] = payload["body"]
            return _status({"id": 99}, 201)
        if "/issues/comments/99" in url and method == "PATCH":
            payload = json.loads(args[args.index("-d") + 1])
            comment_body["value"] = payload["body"]
            return _status({"id": 99}, 200)
        if "/repos/example/project/labels?" in url and method == "GET":
            return _status([{"id": 5, "name": "backlog-active"}], 200)
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
        if url.endswith("/issues/1/comments") and method == "GET":
            return _status(
                [{"id": 1, "body": f"x\n\n{other_marker}", "user": {"login": "issue-bot"}}], 200
            )
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


def test_label_id_resolution_raises_when_label_absent(monkeypatch):
    monkeypatch.setenv("GITEA_TOKEN", "tok")

    def runner(args, **kwargs):
        method = args[args.index("-X") + 1]
        url = args[4]
        if url.endswith("/api/v1/user"):
            return _status({"login": "issue-bot"}, 200)
        if url.endswith("/api/v1/repos/example/project"):
            return _status({"full_name": "example/project"}, 200)
        if url.endswith("/issues/1/comments") and method == "GET":
            return _status([], 200)
        if url.endswith("/issues/1/comments") and method == "POST":
            return _status({"id": 1}, 201)
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
