from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from agent_index.indexing.work_counts import measure_reindex
from agent_index.sources.good_citizen_http import GoodCitizenSession, _rate_reset_delay


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


def test_rate_reset_delay_computes_positive_delay_for_future_reset() -> None:
    response = _json_response(200, {}, headers={"X-RateLimit-Reset": str(int(time.time()) + 30)})
    delay = _rate_reset_delay(response)
    assert delay is not None
    assert 0 < delay <= 31


def test_rate_reset_delay_is_zero_for_a_stale_reset_header() -> None:
    """A reset timestamp more than 60s in the past means the rate-limit
    window has already reset -- there's nothing to wait for. A prior bug
    returned the raw (garbled/stale) epoch value itself as the delay, so
    `time.sleep(reset_value)` on a real GitHub epoch timestamp (~1.7e9)
    slept for ~54 years -- observed in production as an indefinitely stuck
    reindex worker (8+ hours idle, zero CPU, py-spy confirming this exact
    call site)."""
    stale_reset = int(time.time()) - 3600
    response = _json_response(200, {}, headers={"X-RateLimit-Reset": str(stale_reset)})
    assert _rate_reset_delay(response) == 0.0


def test_session_does_not_sleep_for_a_wildly_stale_reset_header(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    # A real GitHub-style epoch value, but stale (long past) -- the buggy
    # fallback would have slept for this many SECONDS (~54 years).
    stale_epoch = 1_700_000_000

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            200,
            {"ok": True},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(stale_epoch)},
        )

    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )

    assert session.get_json("/items").data == {"ok": True}
    assert sleeps == [] or all(delay < 60 for delay in sleeps)


def test_byte_requests_share_json_retry_and_purpose_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _delay: None)
    payload = b"\xff\x00raw\r\ncontent"
    raw_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal raw_attempts
        if request.url.path == "/items":
            return _json_response(200, {"number": 1})
        assert request.headers["Accept"] == "application/octet-stream"
        assert request.url.params["ref"] == "pinned"
        raw_attempts += 1
        if raw_attempts == 1:
            return httpx.Response(503, content=b"retry")
        return httpx.Response(
            200, content=payload, headers={"Content-Type": "application/octet-stream"},
        )

    session = GoodCitizenSession(
        base_url="https://example.test", transport=httpx.MockTransport(handler),
        min_interval_s=0, max_retries=1,
    )

    @measure_reindex
    def run() -> dict[str, Any]:
        assert session.get_json("/items", purpose="identity_page").data == {"number": 1}
        result = session.get_bytes(
            "/raw", params={"ref": "pinned"},
            headers={"Accept": "application/octet-stream"}, purpose="item_body",
        )
        assert result.data == payload
        assert result.headers["Content-Type"] == "application/octet-stream"
        return {}

    counts = run()["work_counts"]
    assert counts["http"]["attempts"] == counts["http"]["responses"] == 3
    assert counts["http"]["retries"] == counts["http"]["json_results"] == 1
    raw = counts["http_by_purpose"]["item_body"]
    assert raw["attempts"] == raw["responses"] == 2
    assert raw["retries"] == 1 and raw["json_results"] == 0
    assert raw["response_bytes"] == len(b"retry") + len(payload)
    assert counts["http_by_purpose"]["identity_page"]["json_results"] == 1


def test_byte_requests_preserve_conditional_response_semantics() -> None:
    validators: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        validators.append(request.headers.get("If-None-Match"))
        if len(validators) == 1:
            return httpx.Response(200, content=b"raw", headers={"ETag": '"raw"'})
        return httpx.Response(304)

    session = GoodCitizenSession(
        base_url="https://example.test", transport=httpx.MockTransport(handler),
        min_interval_s=0,
    )
    assert session.get_bytes("/raw", etag=True).data == b"raw"
    unchanged = session.get_bytes("/raw", etag=True)
    assert unchanged.not_modified and unchanged.data is None
    assert validators == [None, '"raw"']


def test_byte_request_failure_does_not_become_empty_content() -> None:
    session = GoodCitizenSession(
        base_url="https://example.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
        min_interval_s=0, max_retries=0,
    )
    with pytest.raises(httpx.HTTPStatusError):
        session.get_bytes("/missing")
