from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx

from agent_index.sources.good_citizen_http import GoodCitizenSession


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


def test_session_respects_rate_limit_headers(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    reset_at = str(int(time.time()) + 5)

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            200,
            {"ok": True},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset_at},
        )

    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )

    assert session.get_json("/items").data == {"ok": True}
    assert any(delay > 0 for delay in sleeps)


def test_session_uses_etags_and_skips_304() -> None:
    seen_if_none_match: list[str | None] = []

    def responses() -> Iterator[httpx.Response]:
        yield _json_response(200, [], headers={"ETag": '"cached"'})
        yield httpx.Response(304)

    response_iter = responses()

    def handler(request: httpx.Request) -> httpx.Response:
        seen_if_none_match.append(request.headers.get("If-None-Match"))
        return next(response_iter)

    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )

    assert session.get_json("/items").data == []
    assert session.get_json("/items").not_modified is True
    assert seen_if_none_match == [None, '"cached"']


def test_session_follows_continuation_token_pages() -> None:
    requested_tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_tokens.append(request.url.params.get("continuationToken"))
        if requested_tokens[-1] is None:
            return _json_response(200, {"value": [1]}, headers={"x-ms-continuationtoken": "next"})
        return _json_response(200, {"value": [2]})

    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )

    pages = session.paginate_continuation("/items", params={"api-version": "7.1"})

    assert [page.data for page in pages] == [{"value": [1]}, {"value": [2]}]
    assert requested_tokens == [None, "next"]
