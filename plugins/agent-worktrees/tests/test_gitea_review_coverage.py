"""Malformed review acquisition cannot become successful empty or partial history."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_worktrees.pr_snapshot_payload import snapshot_payload
from agent_worktrees.providers.base import ProviderError
from agent_worktrees.providers.gitea import GiteaProvider


def review(number: int, **changes: object) -> dict:
    return {
        "id": number, "state": "APPROVED", "user": {"login": "reviewer"},
        "submitted_at": "2026-01-01T00:00:00Z", "commit_id": "a" * 40,
        **changes,
    }


def read(monkeypatch: pytest.MonkeyPatch, pages: list[object]) -> tuple:
    provider = GiteaProvider()
    calls: list[int] = []

    def request(method: str, url: str, token: str, *, payload: dict | None = None) -> tuple[int, str]:
        assert method == "GET"
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        calls.append(page)
        assert page <= len(pages), "unexpected extra request"
        return 200, json.dumps(pages[page - 1])

    monkeypatch.setattr(provider, "_curl_with_retry", request)
    return provider._all_review_objs("example/repo", 7, "https://forge.example.test", "synthetic"), calls


@pytest.mark.parametrize("final", [[], [review(51, state="PENDING", submitted_at=None)]])
def test_complete_pages_retain_later_and_pending_reviews(
    monkeypatch: pytest.MonkeyPatch, final: list,
) -> None:
    pages = [[review(n) for n in range(1, 51)], final]
    if final:
        pages.append([])
    result, calls = read(monkeypatch, pages)
    assert calls == list(range(1, len(pages) + 1)) and len(result) == 50 + len(final)
    if final:
        assert result[-1].id == 51 and result[-1].submitted_at == ""


def test_server_clamped_short_pages_do_not_truncate_review_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls = read(monkeypatch, [[review(1)], [review(2)], []])
    assert [item.id for item in result] == [1, 2] and calls == [1, 2, 3]


@pytest.mark.parametrize("invalid", [
    {}, None, [None], [review(True)], [review(0)], [review("1")],
    [review(51, user=None)], [review(51, user={"login": ""})],
    [review(51, state=None)], [review(51, dismissed="false")],
    [review(51, submitted_at=42)], [review(51, commit_id=False)],
    [review(1)], [review(51)] * 51,
])
def test_malformed_later_page_never_returns_accumulated_reviews(
    monkeypatch: pytest.MonkeyPatch, invalid: object,
) -> None:
    with pytest.raises(ProviderError, match="malformed reviews on page 2"):
        read(monkeypatch, [[review(n) for n in range(1, 51)], invalid])


def test_empty_review_history_is_complete_but_provider_failure_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GiteaProvider()

    def request(method: str, url: str, token: str, *, payload: dict | None = None) -> tuple[int, str]:
        if "/reviews?" in url:
            return 200, "[]"
        if "/commits/" in url:
            return 200, '{"state": "success", "statuses": []}'
        return 200, json.dumps({
            "state": "open", "head": {"sha": "a" * 40},
            "base": {"sha": "b" * 40, "ref": "main"}, "updated_at": "2026-01-01T00:00:00Z",
        })

    monkeypatch.setattr(provider, "_curl", request)
    snap = provider.get_snapshot(
        "example/repo", 7, api_base="https://forge.example.test", token="synthetic",
    )
    wire = snapshot_payload(snap, "example/repo", 7, "gitea", "https://forge.example.test")
    assert wire["reviews"] == [] and wire["reviews_complete"] is True
    assert wire["updated_at"] == "2026-01-01T00:00:00Z"
    monkeypatch.setattr(provider, "_curl_with_retry", lambda *args: (403, "{}"))
    with pytest.raises(ProviderError, match="HTTP 403"):
        provider.get_snapshot(
            "example/repo", 7, api_base="https://forge.example.test", token="synthetic",
        )
