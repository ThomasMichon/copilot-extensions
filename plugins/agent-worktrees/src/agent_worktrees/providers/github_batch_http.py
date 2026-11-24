"""Fixed GraphQL query transport; no caller-supplied document or destination."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import json
import math
import time
from typing import Any

from .github_batch_query import ENDPOINT, PageRequest, query
from .observation_credentials import ObservationCredentialError, ObservationCredentials
from .observation_http import MAX_BODY, _ARGUMENTS


@dataclass(frozen=True)
class Reply:
    status: int
    value: object
    retry_at: float | None = None
    primary_reset: float | None = None
    primary_exhausted: bool = False


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate batch response key")
        result[key] = value
    return result


def _response(raw: str) -> Reply:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BODY:
        raise ValueError("batch response capture exceeded")
    # curl may include informational responses before the final HTTP/1.1 block.
    while raw.startswith("HTTP/"):
        headers, separator, body = raw.partition("\r\n\r\n")
        if not separator:
            headers, separator, body = raw.partition("\n\n")
        if not separator or len(headers) > 32768:
            raise ValueError("invalid batch response headers")
        lines = headers.splitlines()
        parts = lines[0].split()
        if len(parts) < 2 or not parts[1].isdigit():
            raise ValueError("invalid batch response status")
        status = int(parts[1])
        if 100 <= status < 200:
            raw = body
            continue
        if not 200 <= status <= 599:
            raise ValueError("invalid batch response status")
        fields: dict[str, str] = {}
        for line in lines[1:]:
            key, colon, item = line.partition(":")
            key = key.strip().lower()
            if not colon or not key or key in fields:
                # Repeated unrelated headers (e.g. Vary) carry no quota meaning.
                if key in ("retry-after", "x-ratelimit-reset", "x-ratelimit-remaining"):
                    raise ValueError("ambiguous batch quota header")
                continue
            fields[key] = item.strip()
        now = time.time()
        retry_at = None
        if "retry-after" in fields:
            retry = fields["retry-after"]
            if retry.isdigit() and len(retry) <= 10:
                retry_at = now + int(retry)
            else:
                try:
                    retry_at = parsedate_to_datetime(retry).timestamp()
                except (ValueError, TypeError, OverflowError):
                    raise ValueError("invalid batch retry time") from None
            if not math.isfinite(retry_at) or retry_at < now:
                raise ValueError("invalid batch retry time")
        reset = fields.get("x-ratelimit-reset", "")
        primary_reset = float(reset) if reset.isdigit() and len(reset) <= 12 else None
        try:
            value = json.loads(body, object_pairs_hook=_unique, parse_constant=lambda _: None)
        except (ValueError, RecursionError):
            value = None
        return Reply(status, value, retry_at, primary_reset, fields.get("x-ratelimit-remaining") == "0")
    raise ValueError("missing batch response headers")


def read_query(
    repository: str, requests: tuple[PageRequest, ...], *,
    credentials: ObservationCredentials, deadline: float, invoke: Callable[..., Any],
) -> Reply:
    document = query(repository, requests)
    if deadline <= time.monotonic():
        raise ObservationCredentialError("batch operation deadline exhausted", transient=True)
    material = credentials.acquire()
    request_deadline = min(deadline, time.monotonic() + material.expires_at - time.time())
    remaining = request_deadline - time.monotonic()
    if remaining <= 0:
        raise ObservationCredentialError("batch credential expired before send", transient=True)
    scheme = "Bear" + "er "
    authorization = scheme + material.token
    # curl's config quoting accepts these JSON escapes; all variables are ASCII.
    payload = json.dumps(document, separators=(",", ":"), ensure_ascii=True)
    config = (
        "header = " + json.dumps("Authorization: " + authorization) + "\n"
        "data-binary = " + json.dumps(payload) + "\n"
    )
    result = invoke(
        ["curl", *_ARGUMENTS, "-sS", "--include", "--max-time", str(remaining),
         "-X", "POST", ENDPOINT, "-H", "Content-Type: application/json",
         "-H", "Accept: application/vnd.github+json",
         "-H", "X-GitHub-Api-Version: 2022-11-28", "--config", "-"],
        input_text=config, timeout=remaining, max_stdout_bytes=MAX_BODY,
        max_stderr_bytes=8192,
    )
    if time.monotonic() >= request_deadline or time.time() >= material.expires_at:
        raise ObservationCredentialError("batch response exceeded credential or operation deadline", transient=True)
    if result.returncode:
        raise ObservationCredentialError("batch query transport unavailable", transient=True)
    return _response(result.stdout)
