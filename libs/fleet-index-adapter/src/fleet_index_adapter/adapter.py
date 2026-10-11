"""Routing-foundation service adapter for agent-index.

Maps a validated ``index.search``/``health`` :class:`~fleet_contracts.RouteRequest`
to agent-index's existing, unmodified HTTP query API (``GET /search``,
``GET /health``) and back into the typed, bounded
:class:`~fleet_contracts.RouteResponse` wire contract -- consuming the
existing service exactly as documented, without a second index installer or
coupling to any unreleased standalone package (architecture.md "Sequencing
and proof": *"A useful route is not gated on new index packaging ... consume
its existing supported API while #5768 owns the independent distribution."*).

This module makes no claim to the controller/connector runtime itself
(Phase 2 of ``machine-fleet-routing-foundation``); it proves out the Phase 3
"index adapter" contract in isolation, ahead of that runtime existing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from fleet_contracts import ContractError, HealthResult, RouteRequest, RouteResponse, SearchHit
from fleet_contracts.records import ServiceOffer

DEFAULT_TIMEOUT = 5.0


class BackendUnavailable(RuntimeError):
    """The fixed backend did not answer, or answered with an unusable shape.

    Deliberately distinct from :class:`fleet_contracts.ContractError` (a
    malformed wire *request*, the caller's fault): this always means the
    backend itself -- not the caller -- failed or misbehaved.
    """


def _get(base_url: str, path: str, params: dict[str, str | None], timeout: float) -> dict:
    """A plain, dependency-free ``GET`` against a locally-resolved, trusted
    fixed backend. ``base_url`` is never client-supplied (see module/README
    docstring), so this is not an SSRF sink despite the generic signature."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BackendUnavailable(f"backend did not answer at {path}") from exc
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise BackendUnavailable(f"backend returned an unparsable response at {path}") from exc
    if not isinstance(data, dict):
        raise BackendUnavailable(f"backend response at {path} was not a JSON object")
    return data


def _hit_from_backend(raw: object) -> SearchHit:
    """Build a bounded :class:`SearchHit` from one of agent-index's existing
    ``query_surface.hit_to_dict`` shapes. Any out-of-bounds or wrongly-typed
    field becomes :class:`BackendUnavailable` -- the backend's fault, never
    silently coerced or truncated into something that merely looks valid."""
    if not isinstance(raw, dict):
        raise BackendUnavailable("backend returned a non-object search hit")
    try:
        return SearchHit(
            chunk_id=str(raw.get("chunk_id") or raw.get("id") or ""),
            score=float(raw.get("score", 0.0)),
            file_path=str(raw.get("file_path", "")),
            source=str(raw.get("source", "")),
            chunk_type=str(raw.get("chunk_type", "")),
            language=str(raw.get("language", "")),
            content=str(raw.get("content", "")),
            line_start=raw.get("line_start"),
            line_end=raw.get("line_end"),
        )
    except (ContractError, TypeError, ValueError) as exc:
        raise BackendUnavailable("backend returned an out-of-bounds search hit") from exc


def _route_health(request: RouteRequest, base_url: str, timeout: float) -> RouteResponse:
    data = _get(base_url, "/health", {}, timeout)
    status = data.get("status")
    available = status == "ok"
    detail = str(status) if status is not None else None
    return RouteResponse.for_request(request, health=HealthResult(available, detail))


def _route_search(request: RouteRequest, base_url: str, timeout: float) -> RouteResponse:
    parameters = request.parameters
    if parameters is None:
        raise ContractError("index.search requires validated search parameters")
    data = _get(base_url, "/search", {
        "q": parameters.q,
        "limit": str(parameters.limit),
        "source": parameters.source,
        "language": parameters.language,
        "repo": parameters.repo,
    }, timeout)
    raw_hits = data.get("hits")
    if not isinstance(raw_hits, list):
        raise BackendUnavailable("backend search response did not include a hit list")
    hits = tuple(_hit_from_backend(item) for item in raw_hits)
    return RouteResponse.for_request(request, hits=hits)


def route(
    request: RouteRequest,
    offer: ServiceOffer,
    *,
    base_url: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> RouteResponse:
    """Execute an already-authorized ``request`` against the fixed
    ``base_url`` backend ``offer`` was approved for, and return the typed,
    bounded response.

    The caller (the eventual connector) is responsible for everything this
    function deliberately does not do: authenticating the request, matching
    it to ``offer`` via ``request.check_offer(registration, now=...)``, and
    resolving ``base_url`` from its own locally approved configuration --
    never from a client-supplied value.

    Raises :class:`fleet_contracts.ContractError` for a request this adapter
    cannot serve (wrong adapter kind, unoffered operation, missing required
    parameters) and :class:`BackendUnavailable` for any backend failure or
    out-of-bounds backend response. A well-formed, in-bounds response from
    the backend always produces a well-formed, in-bounds
    :class:`~fleet_contracts.RouteResponse` -- this function never raises on
    a question of taste, only on a genuine contract violation.
    """
    if offer.adapter != "index-v1":
        raise ContractError("this adapter only serves index-v1 service offers")
    if request.operation not in offer.operations:
        raise ContractError("operation is not offered by this service")

    if request.operation == "health":
        return _route_health(request, base_url, timeout)
    if request.operation == "index.search":
        return _route_search(request, base_url, timeout)
    raise ContractError("unsupported routing operation")
