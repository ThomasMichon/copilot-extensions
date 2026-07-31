"""Good-citizen HTTP primitives shared by source connectors."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx

_DEFAULT_TIMEOUT = 20.0
_DEFAULT_MIN_INTERVAL_S = 0.2
_RATE_REMAINING_THRESHOLD = 2
_MAX_RETRIES = 4
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 60.0

_RATE_REMAINING_HEADERS = (
    "X-RateLimit-Remaining",
    "x-ms-ratelimit-remaining",
    "x-ms-ratelimit-remaining-resource",
    "x-ms-ratelimit-remaining-subscription-reads",
    "x-ms-ratelimit-remaining-tenant-reads",
)
_RATE_RESET_HEADERS = (
    "X-RateLimit-Reset",
    "x-ms-ratelimit-reset",
    "x-ms-ratelimit-reset-resource",
    "x-ms-ratelimit-reset-subscription-reads",
    "x-ms-ratelimit-reset-tenant-reads",
)


@dataclass(frozen=True)
class ApiResult:
    """JSON response plus headers from a polite upstream request."""

    data: Any
    headers: httpx.Headers
    not_modified: bool = False


class GoodCitizenSession:
    """Small sequential HTTP client that honors upstream throttling signals."""

    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
        max_retries: int = _MAX_RETRIES,
        rate_remaining_threshold: int = _RATE_REMAINING_THRESHOLD,
        retry_statuses: set[int] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(
            base_url=self.base_url,
            headers=headers or {},
            transport=transport,
            timeout=timeout,
        )
        self.owns_client = client is None
        self._min_interval_s = min_interval_s
        self._max_retries = max_retries
        self._rate_remaining_threshold = rate_remaining_threshold
        self._retry_statuses = retry_statuses or {
            httpx.codes.TOO_MANY_REQUESTS,
            httpx.codes.FORBIDDEN,
        }
        self._last_request_at = 0.0
        self._etag_cache: dict[str, str] = {}

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        etag: bool = True,
    ) -> ApiResult:
        """GET JSON while applying throttle, retry, rate-limit, and ETag handling."""
        return self.request_json("GET", path, params=params, headers=headers, etag=etag)

    def post_json(
        self,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ApiResult:
        """POST JSON while applying the same good-citizen retry discipline."""
        return self.request_json(
            "POST",
            path,
            params=params,
            json=json,
            headers=headers,
            etag=False,
        )

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        etag: bool = False,
    ) -> ApiResult:
        """Request JSON with bounded retries and upstream-directed backoff."""
        params = {key: value for key, value in (params or {}).items() if value is not None}
        retries = 0
        while True:
            self._wait_min_interval()
            url_path = path if path.startswith("/") else f"/{path}"
            request_headers = dict(headers or {})
            cache_key = self._cache_key(method, url_path, params) if etag else ""
            if etag and (cached_etag := self._etag_cache.get(cache_key)):
                request_headers["If-None-Match"] = cached_etag
            try:
                response = self.client.request(
                    method,
                    url_path,
                    params=params,
                    json=json,
                    headers=request_headers,
                )
            except httpx.TimeoutException:
                if retries >= self._max_retries:
                    raise
                self._sleep_backoff(retries)
                retries += 1
                continue
            self._last_request_at = time.monotonic()

            if response.status_code == httpx.codes.NOT_MODIFIED:
                return ApiResult(data=None, headers=response.headers, not_modified=True)

            if response.status_code in self._retry_statuses:
                if retries >= self._max_retries:
                    response.raise_for_status()
                self._sleep_for_retry(response, retries)
                retries += 1
                continue

            if 500 <= response.status_code < 600:
                if retries >= self._max_retries:
                    response.raise_for_status()
                self._sleep_backoff(retries)
                retries += 1
                continue

            response.raise_for_status()
            if etag and (response_etag := response.headers.get("ETag")):
                self._etag_cache[cache_key] = response_etag
            self._respect_rate_limit(response)
            return ApiResult(data=response.json(), headers=response.headers)

    def paginate_continuation(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        continuation_param: str = "continuationToken",
        continuation_header: str = "x-ms-continuationtoken",
    ) -> list[ApiResult]:
        """Collect pages using a continuation-token response header."""
        pages: list[ApiResult] = []
        next_token: str | None = None
        while True:
            page_params = dict(params or {})
            if next_token:
                page_params[continuation_param] = next_token
            result = self.get_json(path, params=page_params)
            if result.not_modified:
                return pages
            pages.append(result)
            next_token = result.headers.get(continuation_header)
            if not next_token:
                return pages

    def _wait_min_interval(self) -> None:
        if self._min_interval_s <= 0 or self._last_request_at <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)

    def _sleep_for_retry(self, response: httpx.Response, retry: int) -> None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            time.sleep(max(_parse_retry_after(retry_after), 0.0))
            return
        reset_delay = _rate_reset_delay(response)
        if reset_delay is not None:
            time.sleep(reset_delay)
            return
        self._sleep_backoff(retry)

    def _respect_rate_limit(self, response: httpx.Response) -> None:
        remaining_raw = _first_header(response, _RATE_REMAINING_HEADERS)
        if remaining_raw is None:
            return
        try:
            remaining = int(remaining_raw)
        except ValueError:
            return
        if remaining > self._rate_remaining_threshold:
            return
        delay = _rate_reset_delay(response)
        if delay is not None:
            time.sleep(delay)

    @staticmethod
    def _sleep_backoff(retry: int) -> None:
        base = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2**retry))
        time.sleep(base + random.uniform(0.0, min(base, 1.0)))  # noqa: S311

    def _cache_key(self, method: str, path: str, params: dict[str, Any]) -> str:
        query = urlencode(sorted((k, str(v)) for k, v in params.items() if v is not None))
        return f"{method.upper()} {urljoin(self.base_url + '/', path.lstrip('/'))}" + (
            f"?{query}" if query else ""
        )


def has_next_link(link_header: str) -> bool:
    """Return whether an RFC 5988 Link header advertises a next page."""
    if not link_header:
        return False
    for part in link_header.split(","):
        pieces = [piece.strip() for piece in part.split(";")]
        if any(piece == 'rel="next"' for piece in pieces[1:]):
            return True
    return False


def _parse_retry_after(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return _BACKOFF_BASE_S


def _rate_reset_delay(response: httpx.Response) -> float | None:
    raw = _first_header(response, _RATE_RESET_HEADERS)
    if not raw:
        return None
    try:
        reset_value = float(raw)
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(raw).timestamp() - time.time() + 1.0)
        except (TypeError, ValueError):
            return None
    now = time.time()
    if reset_value > now - 60:
        return max(0.0, reset_value - now + 1.0)
    return max(0.0, reset_value + 1.0)


def _first_header(response: httpx.Response, names: tuple[str, ...]) -> str | None:
    for name in names:
        if value := response.headers.get(name):
            return value
    return None
