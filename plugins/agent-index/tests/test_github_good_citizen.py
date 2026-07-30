from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx

from agent_index.sources.github import GitHubConnector


def _json_response(
    status_code: int,
    payload,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers=headers or {},
    )


def test_github_connector_paginates_and_parses_entries(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = dict(request.url.params)
        if path.endswith("/issues") and query.get("page") == "1":
            return _json_response(
                200,
                [
                    {
                        "number": 1,
                        "title": "Issue title",
                        "body": "Issue body",
                        "state": "open",
                        "labels": [{"name": "bug"}],
                        "user": {"login": "octocat"},
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:01:00Z",
                        "url": "https://api.github.com/repos/acme/widgets/issues/1",
                        "html_url": "https://github.com/acme/widgets/issues/1",
                        "repository_url": "https://api.github.com/repos/acme/widgets",
                    }
                ],
                headers={
                    "Link": (
                        '<https://api.github.com/repos/acme/widgets/issues?page=2>; '
                        'rel="next"'
                    ),
                    "ETag": '"issues-1"',
                },
            )
        if path.endswith("/issues") and query.get("page") == "2":
            return _json_response(
                200,
                [
                    {
                        "number": 2,
                        "title": "Pull title",
                        "body": "Pull body",
                        "state": "closed",
                        "labels": [{"name": "enhancement"}],
                        "user": {"login": "contrib"},
                        "created_at": "2026-01-02T00:00:00Z",
                        "updated_at": "2026-01-02T00:01:00Z",
                        "url": "https://api.github.com/repos/acme/widgets/issues/2",
                        "html_url": "https://github.com/acme/widgets/pull/2",
                        "repository_url": "https://api.github.com/repos/acme/widgets",
                        "pull_request": {"url": "https://api.github.com/repos/acme/widgets/pulls/2"},
                    }
                ],
            )
        if path.endswith("/comments"):
            return _json_response(200, [{"body": "Comment body", "user": {"login": "reviewer"}}])
        raise AssertionError(f"unexpected request: {request.url}")

    connector = GitHubConnector(
        "github:acme/widgets",
        token="fake-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )
    entries = connector.discover()

    assert {entry.path for entry in entries} == {"issues/1.md", "pulls/2.md"}
    assert {entry.language for entry in entries} == {"issue", "pull_request"}
    assert all("Comment body" in entry.content for entry in entries)


def test_github_connector_uses_etags_and_skips_304() -> None:
    seen_if_none_match: list[str | None] = []

    def responses() -> Iterator[httpx.Response]:
        yield _json_response(200, [], headers={"ETag": '"cached"'})
        yield httpx.Response(304)

    response_iter = responses()

    def handler(request: httpx.Request) -> httpx.Response:
        seen_if_none_match.append(request.headers.get("If-None-Match"))
        return next(response_iter)

    connector = GitHubConnector(
        "github:acme/widgets",
        token="fake-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )

    assert connector.discover() == []
    assert connector.discover() == []
    assert seen_if_none_match == [None, '"cached"']


def test_github_connector_sleeps_for_rate_limit_and_retry_after(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    reset_at = str(int(time.time()) + 10)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        path = request.url.path
        if path.endswith("/issues") and calls == 1:
            return _json_response(
                200,
                [],
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset_at},
            )
        if path.endswith("/issues/1/comments") and calls == 2:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return _json_response(200, [])

    connector = GitHubConnector(
        "github:acme/widgets",
        token="fake-token",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )

    assert connector.discover() == []
    assert connector._comments_for(1) == []
    assert calls == 3
    assert any(delay > 0 for delay in sleeps)
    assert 3 in sleeps
