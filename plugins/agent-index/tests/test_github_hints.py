"""Targeted hints acquire fresh complete work items, never repository history."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from agent_index.indexing.change_hints import ChangeHints, MetadataRevision, RefRange
from agent_index.sources.github import GitHubConnector

SOURCE = "github:acme/widgets"
REPO_URL = "https://api.github.com/repos/acme/widgets"
STAMP = "2026-01-02T00:00:00Z"


def hints(**kwargs):
    return ChangeHints(**{
        "source": SOURCE, "canonical_family": SOURCE, "account_id": "123",
        "item_ids": ("1",), **kwargs,
    })


def issue(number=1, **kwargs):
    return {
        "number": number, "title": f"Item {number}", "body": "Useful body",
        "updated_at": STAMP, "repository_url": REPO_URL,
        "url": f"{REPO_URL}/issues/{number}", "comments": 0, **kwargs,
    }


def connector(handler):
    return GitHubConnector(
        SOURCE, token="fixture-token", transport=httpx.MockTransport(handler), min_interval_s=0,
    )


def test_unique_items_revisions_events_and_fresh_paginated_comments():
    requests = []

    def handler(request):
        requests.append(request)
        assert "if-none-match" not in request.headers
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        number = int(request.url.path.split("/")[5])
        if request.url.path.endswith("/comments"):
            page = int(request.url.params["page"])
            data = [{"id": i, "body": f"Comment {i}"} for i in (
                range(1, 101) if page == 1 else range(101, 102)
            )] if number == 1 else []
            headers = {"ETag": '"comment-tag"'}
            if number == 1 and page == 1:
                headers["Link"] = '<https://api.github.com/ignored>; rel="next"'
            return httpx.Response(200, json=data, headers=headers)
        return httpx.Response(200, json=issue(
            number, comments=101 if number == 1 else 0,
            **({"pull_request": {}} if number == 2 else {}),
        ), headers={"ETag": '"item-tag"'})

    provider = connector(handler)
    envelope = hints(
        item_ids=("1", "1", "2"), event_ids=("delivery-b", "delivery-a"),
        metadata_revisions=(
            MetadataRevision("1", "2026-01-01T00:00:00Z"),
            MetadataRevision("1", STAMP), MetadataRevision("2", STAMP),
        ),
    )
    before = envelope.to_json()
    for cursor in ("prior-poll", None):
        result = provider.discover_hints(envelope, cursor)
        assert result.commit == cursor and not result.deletions and result.complete
        assert [entry.path for entry in result.upserts] == ["issues/1.md", "pulls/2.md"]
        assert "Comment 101" in result.upserts[0].content
        assert result.upserts[1].metadata["type"] == "pull_request"
    assert envelope.to_json() == before
    assert [request.url.path for request in requests].count("/user") == 1
    assert len(requests) == 11  # identity once, two rounds of 2 items + 3 pages.
    assert all(request.url.path != "/repos/acme/widgets/issues" for request in requests)


@pytest.mark.parametrize("repository", ["Acme/Widgets", "ACME/widgets"])
def test_canonical_response_casing_preserves_exact_configured_scope(repository):
    canonical_url = "https://api.github.com/repos/" + repository

    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=issue(
            repository_url=canonical_url, url=canonical_url + "/issues/1",
        ))

    result = connector(handler).discover_hints(hints(), "prior")
    assert result.upserts[0].source == SOURCE + ":issues"
    assert result.upserts[0].metadata["repo"] == repository
    assert result.commit == "prior"


@pytest.mark.parametrize("overrides", [
    {"item_ids": ("01",)}, {"item_ids": ("-1",)}, {"item_ids": ("٠١",)},
    {"ref_ranges": (RefRange("before", "after"),)},
    {"item_ids": (), "event_ids": ("delivery-only",)},
    {"metadata_revisions": (MetadataRevision("1", "opaque"),)},
    {"metadata_revisions": (MetadataRevision("1", "2026-01-01"),)},
    {"source": SOURCE + ":issues"},
])
def test_invalid_acquisition_fields_refuse_before_http(overrides):
    provider = connector(lambda _: pytest.fail("invalid fields must not reach HTTP"))
    with pytest.raises(ValueError):
        provider.discover_hints(hints(**overrides), "prior")


@pytest.mark.parametrize("overrides", [
    {"account_id": "other"}, {"canonical_family": "github:other/repo"},
])
def test_foreign_scope_never_fetches_items(overrides):
    def handler(request):
        assert request.url.path == "/user"
        return httpx.Response(200, json={"id": 123})

    with pytest.raises(ValueError, match="scope"):
        connector(handler).discover_hints(hints(**overrides), "prior")


@pytest.mark.parametrize("payload", [
    issue(number=2), issue(number=True), issue(repository_url="https://example.test/other"),
    issue(url=REPO_URL + "/issues/9"), issue(comments=None),
    issue(updated_at="not-a-time"), issue(updated_at="2025-01-01T00:00:00Z"),
    [],
])
def test_wrong_or_stale_item_is_not_a_completed_delta(payload):
    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        assert not request.url.path.endswith("/comments")
        return httpx.Response(200, json=payload)

    with pytest.raises(ValueError):
        connector(handler).discover_hints(
            hints(metadata_revisions=(MetadataRevision("1", STAMP),)), "prior",
        )


@pytest.mark.parametrize("status", [304, 404])
def test_absent_or_not_modified_item_is_not_tombstone_authority(status):
    def handler(request):
        return httpx.Response(200, json={"id": 123}) if request.url.path == "/user" else (
            httpx.Response(status)
        )

    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        connector(handler).discover_hints(hints(), "prior")


@pytest.mark.parametrize("payload,count,more,status", [
    ([], 1, False, 200), ([{"id": 1}], 0, False, 200),
    ([{"id": 1}, {"id": 1}], 2, False, 200),
    ([{"id": True}], 1, False, 200), ({}, 0, False, 200),
    ([], 0, True, 200), ([], 0, False, 304),
])
def test_incomplete_or_malformed_comments_refuse(payload, count, more, status):
    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123})
        if request.url.path.endswith("/comments"):
            headers = {"Link": '<https://example.test/next>; rel="next"'} if more else {}
            return httpx.Response(status, json=payload, headers=headers)
        return httpx.Response(200, json=issue(comments=count))

    with pytest.raises(ValueError):
        connector(handler).discover_hints(hints(), "prior")


def test_empty_hints_and_anonymous_scope_are_explicit(monkeypatch):
    for name in ("AGENT_INDEX_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    provider = GitHubConnector(
        SOURCE, transport=httpx.MockTransport(lambda _: pytest.fail("empty requires no HTTP")),
    )
    envelope = replace(hints(), item_ids=(), account_id="anonymous")
    assert provider.hint_scope == (SOURCE, "anonymous")
    assert provider.discover_hints(envelope, "prior").upserts == []


def test_cancellation_precedes_acquisition():
    provider = connector(lambda _: pytest.fail("cancelled acquisition"))

    def cancelled():
        raise RuntimeError("cancel")

    with pytest.raises(RuntimeError, match="cancel"):
        provider.discover_hints(hints(), "prior", cancel_check=cancelled)
