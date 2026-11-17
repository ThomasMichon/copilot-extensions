"""Bounded review-count metadata without changing the existing response pair."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

COUNT_WRITE_OUT = "\n%header{x-total-count}\n%{http_code}"


class CountedResponse(tuple[int, str]):
    """Keep status/body unpacking compatible with existing transport callers."""

    total_count: int | None

    def __new__(
        cls, status: int, body: str, total_count: int | None,
    ) -> CountedResponse:
        selected = super().__new__(cls, (status, body))
        selected.total_count = total_count
        return selected


def review_page(method: str, url: str) -> bool:
    return method == "GET" and re.search(
        r"/api/v1/repos/[^/]+/[^/]+/pulls/[1-9][0-9]*/reviews$", urlsplit(url).path,
    ) is not None


def counted_response(status: int, body: str) -> CountedResponse:
    """A missing count stays unknown; legacy response doubles retain their body."""
    content, separator, raw_count = body.rpartition("\n")
    if not separator:
        return CountedResponse(status, body, None)
    if not raw_count:
        return CountedResponse(status, content, None)
    if raw_count.isascii() and raw_count.isdecimal() and len(raw_count) <= 18:
        return CountedResponse(status, content, int(raw_count))
    return CountedResponse(status, body, None)
