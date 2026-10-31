"""Good-citizen HTTP primitives shared by source connectors."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
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
    request: httpx.Request | None = None


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
        request_headers: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(
            base_url=self.base_url,
            headers=headers or {},
            transport=transport,
            timeout=timeout,
            trust_env=request_headers is None,
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
        self._request_headers = request_headers

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        etag: bool = True,
        retry_status: Callable[[httpx.Response], bool] | None = None,
        purpose: str = "unclassified",
    ) -> ApiResult:
        """GET JSON while applying throttle, retry, rate-limit, and ETag handling."""
        return self.request_json(
            "GET", path, params=params, headers=headers, etag=etag, retry_status=retry_status,
            purpose=purpose,
        )

    def post_json(
        self,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        purpose: str = "unclassified",
    ) -> ApiResult:
        """POST JSON while applying the same good-citizen retry discipline."""
        return self.request_json(
            "POST",
            path,
            params=params,
            json=json,
            headers=headers,
            etag=False,
            purpose=purpose,
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
        retry_status: Callable[[httpx.Response], bool] | None = None,
        purpose: str = "unclassified",
    ) -> ApiResult:
        """Request JSON with bounded retries and upstream-directed backoff."""
        from agent_index.indexing.work_counts import observe_http, observe_http_purpose

        observations = [
            count for count in (observe_http_purpose(purpose), observe_http())
            if count is not None
        ]
        params = {key: value for key, value in (params or {}).items() if value is not None}
        retries = 0
        while True:
            self._wait_min_interval()
            url_path = path if path.startswith("/") else f"/{path}"
            request_headers = dict(headers or {})
            cache_key = self._cache_key(method, url_path, params) if etag else ""
            if etag and (cached_etag := self._etag_cache.get(cache_key)):
                request_headers["If-None-Match"] = cached_etag
            if self._request_headers is not None:
                request_headers.update(self._request_headers())
            try:
                redirect_policy: dict[str, Any] = (
                    {"follow_redirects": False} if self._request_headers is not None else {}
                )
                for counts in observations:
                    counts.attempts += 1
                    counts.retries += int(retries > 0)
                response = self.client.request(
                    method,
                    url_path,
                    params=params,
                    json=json,
                    headers=request_headers,
                    **redirect_policy,
                )
            except httpx.TimeoutException:
                if retries >= self._max_retries:
                    raise
                self._sleep_backoff(retries)
                retries += 1
                continue
            self._last_request_at = time.monotonic()
            for counts in observations:
                counts.responses += 1
                counts.response_bytes += len(response.content)

            if response.status_code == httpx.codes.NOT_MODIFIED:
                for counts in observations:
                    counts.not_modified += 1
                return ApiResult(
                    data=None, headers=response.headers, not_modified=True, request=response.request,
                )

            if response.status_code in self._retry_statuses and (
                retry_status is None or retry_status(response)
            ):
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
            data = response.json()
            for counts in observations:
                counts.json_results += 1
            return ApiResult(data=data, headers=response.headers, request=response.request)

    def paginate_continuation(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        continuation_param: str = "continuationToken",
        continuation_header: str = "x-ms-continuationtoken",
        purpose: str = "unclassified",
    ) -> list[ApiResult]:
        """Collect pages using a continuation-token response header."""
        pages: list[ApiResult] = []
        next_token: str | None = None
        while True:
            page_params = dict(params or {})
            if next_token:
                page_params[continuation_param] = next_token
            result = self.get_json(path, params=page_params, purpose=purpose)
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
    # The reset timestamp is more than 60s in the past -- the rate-limit
    # window has already reset, so there's nothing to wait for. Returning
    # `reset_value` itself here (a prior bug) treated a stale/garbled epoch
    # timestamp as a SECONDS delay: `time.sleep(reset_value)` on a real
    # GitHub epoch value (~1.7e9) sleeps for ~54 years, which in practice
    # manifested as an indefinitely stuck reindex worker (observed in
    # production: a worker idle in this exact call for 8+ hours with zero
    # CPU activity, py-spy confirming `_respect_rate_limit` -> `time.sleep`).
    return 0.0



def _first_header(response: httpx.Response, names: tuple[str, ...]) -> str | None:
    for name in names:
        if value := response.headers.get(name):
            return value
    return None
