"""Routing-foundation service adapter for a Gateway-resolved credential vault.

Maps a validated ``credential.get``/``health`` :class:`~fleet_contracts.RouteRequest`
to a fixed, locally-approved vault backend's HTTP API and back into the
typed, bounded :class:`~fleet_contracts.RouteResponse` wire contract.

This is the **portable counterpart** of the dotfiles-private
``gateway_server.py``/``credential_push.py`` pair already running on a real
Gateway host: a Windows-side producer (a registered controller/manager
machine that is already authenticated -- ``az login``, ``gh auth``, etc.)
mints or already holds a credential and *pushes* it into the vault backend's
custody; a consumer (e.g. a containerized service on a CDE machine) *pulls*
one *scoped* credential back out, on demand, through this adapter. Neither
push nor pull ever happens over a credential's own bearer connection to the
resource it authorizes -- both sides only ever talk to the vault backend.

Per the architecture proposal's authority table (same one
``agent-fleet-index-adapter`` follows), this module owns exactly one thing:
**a typed route to a fixed trusted backend and its API contract**. It does
not mint, cache, or custody any credential itself; does not resolve
``base_url`` (the eventual connector does, from its own locally approved
configuration -- never from a client-supplied value); and does not
authenticate or authorize the request (the eventual controller/connector
does, before this module is ever reached, by binding ``request.check_offer``
to a live, scoped registration).

**Only the Gateway is addressable.** A containerized consumer of this
adapter never reaches a vault backend directly on its own published port --
the deployment model this adapter assumes is the same one
``agent-fleet-index-adapter`` assumes: ``base_url`` always resolves to an
internal, Gateway-fronted address (a Caddy/Chisel-published route, a
container-network-internal hostname, or a loopback reverse-proxy), and the
backing service itself publishes no other port anywhere.

This module makes no claim to the controller/connector runtime itself
(Phase 2 of ``machine-fleet-routing-foundation``); it proves out the
``vault-v1`` adapter contract in isolation, ahead of that runtime existing,
the same way ``agent-fleet-index-adapter`` proved out ``index-v1``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from fleet_contracts import (
    ContractError,
    CredentialResult,
    HealthResult,
    RouteRequest,
    RouteResponse,
)
from fleet_contracts.records import MAX_MESSAGE_BYTES, ServiceOffer

DEFAULT_TIMEOUT = 5.0
# One byte past the contract's own wire-size limit: enough to detect overflow
# without ever buffering an actually-unbounded backend body.
_MAX_READ_BYTES = MAX_MESSAGE_BYTES + 1


class BackendUnavailable(RuntimeError):
    """The fixed vault backend did not answer, or answered with an unusable
    shape.

    Deliberately distinct from :class:`fleet_contracts.ContractError` (a
    malformed wire *request*, the caller's fault): this always means the
    backend itself -- not the caller -- failed or misbehaved. A *resolved*
    "this credential isn't available right now" answer (expired, not yet
    pushed, denied) is not an error at all -- see ``_route_credential_get``;
    it comes back as an ordinary, honest ``CredentialResult(available=False,
    reason=...)``.
    """


def _get(base_url: str, path: str, timeout: float) -> dict:
    """A plain, dependency-free ``GET`` against a locally-resolved, trusted
    fixed backend. ``base_url`` is never client-supplied (see module/README
    docstring), so this is not an SSRF sink despite the generic signature.

    Reads at most ``_MAX_READ_BYTES`` of the response body -- a faulty or
    compromised backend cannot force this adapter to buffer an unbounded
    body merely by sending one.
    """
    url = f"{base_url.rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read(_MAX_READ_BYTES)
            if len(payload) >= _MAX_READ_BYTES:
                raise BackendUnavailable(f"backend response at {path} exceeds the wire-size limit")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BackendUnavailable(f"backend did not answer at {path}") from exc
    return _parse_json_object(payload, path)


def _post(base_url: str, path: str, body: dict, timeout: float) -> dict:
    """A plain, dependency-free ``POST`` of a small JSON body against the
    same locally-resolved, trusted fixed backend as :func:`_get`, with the
    same bounded-read guarantee."""
    url = f"{base_url.rstrip('/')}{path}"
    encoded = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=encoded, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(_MAX_READ_BYTES)
            if len(payload) >= _MAX_READ_BYTES:
                raise BackendUnavailable(f"backend response at {path} exceeds the wire-size limit")
    except urllib.error.HTTPError as exc:
        # A vault backend reports "credential not available right now" as a
        # non-2xx status with a small JSON body (see README's wire contract);
        # read and parse it the same bounded way rather than treating every
        # non-2xx as a transport failure.
        payload = exc.read(_MAX_READ_BYTES)
        if len(payload) >= _MAX_READ_BYTES:
            raise BackendUnavailable(
                f"backend response at {path} exceeds the wire-size limit"
            ) from exc
        return _parse_json_object(payload, path)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BackendUnavailable(f"backend did not answer at {path}") from exc
    return _parse_json_object(payload, path)


def _parse_json_object(payload: bytes, path: str) -> dict:
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise BackendUnavailable(f"backend returned an unparsable response at {path}") from exc
    if not isinstance(data, dict):
        raise BackendUnavailable(f"backend response at {path} was not a JSON object")
    return data


def _route_health(request: RouteRequest, base_url: str, timeout: float) -> RouteResponse:
    data = _get(base_url, "/health", timeout)
    status = data.get("status")
    available = status == "ok"
    detail = str(status) if status is not None else None
    return RouteResponse.for_request(request, health=HealthResult(available, detail))


def _credential_result_from_backend(data: dict) -> CredentialResult:
    """Build a bounded :class:`CredentialResult` from the vault backend's
    response. Any out-of-bounds or wrongly-typed field becomes
    :class:`BackendUnavailable` -- the backend's fault, never silently
    coerced or truncated into something that merely looks valid."""
    available = data.get("available")
    if available is True:
        raw_fields = data.get("fields")
        if not isinstance(raw_fields, dict):
            raise BackendUnavailable("backend reported an available credential with no fields")
        try:
            fields = tuple((str(name), str(value)) for name, value in raw_fields.items())
            return CredentialResult(True, fields)
        except (ContractError, TypeError, ValueError) as exc:
            raise BackendUnavailable("backend returned an out-of-bounds credential") from exc
    if available is False:
        reason = data.get("reason")
        try:
            return CredentialResult(False, reason=str(reason) if reason is not None else None)
        except (ContractError, TypeError, ValueError) as exc:
            raise BackendUnavailable("backend returned an out-of-bounds credential") from exc
    raise BackendUnavailable("backend response did not include a boolean 'available' field")


def _route_credential_get(request: RouteRequest, base_url: str, timeout: float) -> RouteResponse:
    parameters = request.parameters
    if parameters is None:
        raise ContractError("credential.get requires validated credential parameters")
    fields = {
        name: value
        for name, value in (
            ("host", parameters.host), ("resource", parameters.resource),
            ("tenant", parameters.tenant), ("scope", parameters.scope),
        )
        if value is not None
    }
    data = _post(base_url, "/relay", {"action": parameters.action, "fields": fields}, timeout)
    credential = _credential_result_from_backend(data)
    return RouteResponse.for_request(request, credential=credential)


def route(
    request: RouteRequest,
    offer: ServiceOffer,
    *,
    base_url: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> RouteResponse:
    """Execute an already-authorized ``request`` against the fixed
    ``base_url`` vault backend ``offer`` was approved for, and return the
    typed, bounded response.

    The caller (the eventual connector) is responsible for everything this
    function deliberately does not do: authenticating the request, matching
    it to ``offer`` via ``request.check_offer(registration, now=...)``, and
    resolving ``base_url`` from its own locally approved configuration --
    always an internal, Gateway-fronted address, never a vault backend's own
    directly published port (see module docstring).

    Raises :class:`fleet_contracts.ContractError` for a request this adapter
    cannot serve (wrong adapter kind, unoffered operation, missing required
    parameters) and :class:`BackendUnavailable` for any backend transport
    failure or out-of-bounds backend response. An honestly-unavailable
    credential (expired, not pushed, denied) is not an error -- it comes
    back as a normal ``RouteResponse`` whose ``credential.available`` is
    ``False``, with a bounded ``reason``.
    """
    if offer.adapter != "vault-v1":
        raise ContractError("this adapter only serves vault-v1 service offers")
    if request.operation not in offer.operations:
        raise ContractError("operation is not offered by this service")

    if request.operation == "health":
        return _route_health(request, base_url, timeout)
    if request.operation == "credential.get":
        return _route_credential_get(request, base_url, timeout)
    raise ContractError("unsupported routing operation")
