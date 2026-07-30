"""MCP protocol-version model: legacy handshake vs. modern per-request metadata.

MCP revision ``2026-07-28`` ("modern") replaced the stateful ``initialize``
handshake + ``Mcp-Session-Id`` session with a **stateless, per-request** model:
every request self-describes by carrying its protocol version, client identity,
and client capabilities in ``params._meta`` (the
``io.modelcontextprotocol/*`` fields), and a client may optionally probe a
server up front with the new ``server/discover`` RPC. Revisions ``2025-11-25``
and earlier ("legacy") establish a session with ``initialize`` /
``notifications/initialized`` instead.

This module is the single source of truth both directions of the bridge share:

* the **client** side (:mod:`agent_mcp.client`) uses it to negotiate an upstream
  server's era, stamp modern requests with ``_meta``, and build the HTTP
  metadata headers the Streamable HTTP transport mirrors;
* the **server** side (:mod:`agent_mcp.transports.cli`) uses it to answer
  ``server/discover``, negotiate an ``initialize``, and reject an unsupported
  version.

Keeping the wire constants here (rather than scattered string literals) makes
the two eras auditable in one place and lets tests assert against them.
"""

from __future__ import annotations

import base64
from typing import Any

# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

#: The modern, stateless per-request-metadata revision.
MODERN = "2026-07-28"
#: The legacy handshake revision we advertise/speak for backward compatibility.
LEGACY = "2025-06-18"

#: Every revision this bridge can speak, **modern preferred first**. Advertised
#: verbatim in ``server/discover.supportedVersions`` and used to negotiate.
SUPPORTED_VERSIONS: tuple[str, ...] = (MODERN, LEGACY)

#: The first legacy revision that defined the ``MCP-Protocol-Version`` HTTP
#: header; earlier ones sent none. Used only for documentation/negotiation.
FIRST_HEADER_VERSION = "2025-06-18"


def is_modern(version: str | None) -> bool:
    """Whether ``version`` is a modern (per-request-metadata) revision.

    Any revision ``>= 2026-07-28`` is modern. Revisions are ``YYYY-MM-DD`` date
    strings, so a lexical compare is also chronological. ``None`` (a request
    with no declared version) is treated as legacy.
    """
    return bool(version) and version >= MODERN


# ---------------------------------------------------------------------------
# ``_meta`` field keys (the ``io.modelcontextprotocol/*`` namespace)
# ---------------------------------------------------------------------------

META_PREFIX = "io.modelcontextprotocol/"
META_PROTOCOL_VERSION = META_PREFIX + "protocolVersion"
META_CLIENT_INFO = META_PREFIX + "clientInfo"
META_CLIENT_CAPABILITIES = META_PREFIX + "clientCapabilities"
META_SERVER_INFO = META_PREFIX + "serverInfo"

# ---------------------------------------------------------------------------
# JSON-RPC error codes
# ---------------------------------------------------------------------------

#: Returned by a server when it does not implement the requested protocol
#: version (carries ``data.supported`` / ``data.requested``). See SEP versioning.
UNSUPPORTED_PROTOCOL_VERSION = -32022
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

# ---------------------------------------------------------------------------
# Client identity + request metadata (modern)
# ---------------------------------------------------------------------------


def client_meta(version: str, client_info: dict[str, Any],
                capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the modern per-request ``_meta`` block a client stamps on requests.

    Returns the three ``io.modelcontextprotocol/*`` fields every modern request
    carries: the protocol version, the client identity, and the client
    capabilities (an empty object when the client declares none).
    """
    return {
        META_PROTOCOL_VERSION: version,
        META_CLIENT_INFO: dict(client_info),
        META_CLIENT_CAPABILITIES: dict(capabilities or {}),
    }


def inject_client_meta(msg: dict, version: str, client_info: dict[str, Any],
                       capabilities: dict[str, Any] | None = None) -> dict:
    """Stamp a modern client request's ``params._meta`` in place and return it.

    Merges (does not clobber) an existing ``_meta`` so a decorator or caller that
    already set unrelated ``_meta`` keys keeps them. A message with no ``params``
    gains one. Notifications and responses are returned untouched.
    """
    if "method" not in msg or msg.get("id") is None:
        return msg
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}
        msg["params"] = params
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
    meta.update(client_meta(version, client_info, capabilities))
    params["_meta"] = meta
    return msg


def request_protocol_version(msg: dict) -> str | None:
    """The protocol version a request declares in ``params._meta``, or ``None``.

    This is the wire source of truth for a request's era: a value present means
    the sender is speaking modern; absent means legacy.
    """
    params = msg.get("params")
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    version = meta.get(META_PROTOCOL_VERSION)
    return version if isinstance(version, str) else None


# ---------------------------------------------------------------------------
# HTTP metadata headers (Streamable HTTP mirrors body fields into headers)
# ---------------------------------------------------------------------------

_HEADER_SAFE_MAX = 0x7E
_HEADER_SAFE_MIN = 0x21


def encode_header_value(value: str) -> str:
    """Encode a header value, using the ``=?base64?..?=`` sentinel when needed.

    ``Mcp-Name`` / ``Mcp-Param-*`` values must be plain ASCII in the visible
    range. A value that isn't (non-ASCII, control chars, leading/trailing
    whitespace) -- or that already looks like the sentinel -- is Base64-encoded
    per the spec so it round-trips unambiguously.
    """
    needs = (
        value != value.strip()
        or (value.startswith("=?base64?") and value.endswith("?="))
        or any(not (_HEADER_SAFE_MIN <= ord(c) <= _HEADER_SAFE_MAX or c == " ")
                for c in value)
    )
    if not needs:
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?base64?{encoded}?="


def _mcp_name(msg: dict) -> str | None:
    """The ``Mcp-Name`` source value for a request (``params.name`` or ``.uri``).

    Per the spec this header is required for ``tools/call`` (name),
    ``resources/read`` (uri) and ``prompts/get`` (name); other methods carry no
    ``Mcp-Name``.
    """
    method = msg.get("method")
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return None
    if method in ("tools/call", "prompts/get"):
        name = params.get("name")
        return name if isinstance(name, str) else None
    if method == "resources/read":
        uri = params.get("uri")
        return uri if isinstance(uri, str) else None
    return None


def http_metadata_headers(msg: dict, version: str) -> dict[str, str]:
    """Build the modern Streamable-HTTP metadata headers for one request.

    Returns ``MCP-Protocol-Version`` and ``Mcp-Method`` (always) plus ``Mcp-Name``
    (when the method carries one), with ``Mcp-Name`` sentinel-encoded if it is
    not header-safe. Notifications/responses (no method+id) get no headers.
    """
    headers: dict[str, str] = {}
    method = msg.get("method")
    if not isinstance(method, str):
        return headers
    headers["MCP-Protocol-Version"] = version
    headers["Mcp-Method"] = method
    name = _mcp_name(msg)
    if name is not None:
        headers["Mcp-Name"] = encode_header_value(name)
    return headers


# ---------------------------------------------------------------------------
# Negotiation
# ---------------------------------------------------------------------------


def negotiate(requested: str | None,
              supported: tuple[str, ...] = SUPPORTED_VERSIONS) -> str | None:
    """Pick the version to speak given a peer's ``requested`` version.

    Returns ``requested`` if we support it; otherwise ``None`` (caller emits an
    ``UnsupportedProtocolVersionError`` or falls back). A ``None`` request (a
    legacy client that declared nothing) is answered with our newest supported
    version so an old client still gets a concrete answer.
    """
    if requested is None:
        return supported[0] if supported else None
    return requested if requested in supported else None


# ---------------------------------------------------------------------------
# Result / error builders (server side)
# ---------------------------------------------------------------------------

#: Default cache hints for cacheable list/discover results. A short TTL keeps a
#: catalog fresh while still letting a client skip an immediate re-fetch; the
#: bridge's catalog is host-local, so ``public`` scope is safe.
DEFAULT_TTL_MS = 60_000
DEFAULT_CACHE_SCOPE = "public"


def discover_result(server_info: dict[str, Any], capabilities: dict[str, Any],
                    *, supported: tuple[str, ...] = SUPPORTED_VERSIONS,
                    instructions: str | None = None,
                    ttl_ms: int = DEFAULT_TTL_MS,
                    cache_scope: str = DEFAULT_CACHE_SCOPE) -> dict[str, Any]:
    """Build a ``server/discover`` result advertising versions + capabilities."""
    result: dict[str, Any] = {
        "resultType": "complete",
        "supportedVersions": list(supported),
        "capabilities": capabilities,
        "_meta": {META_SERVER_INFO: dict(server_info)},
        "ttlMs": ttl_ms,
        "cacheScope": cache_scope,
    }
    if instructions:
        result["instructions"] = instructions
    return result


def unsupported_version_error(request: dict, requested: str | None,
                              supported: tuple[str, ...] = SUPPORTED_VERSIONS) -> dict:
    """A JSON-RPC ``UnsupportedProtocolVersionError`` echoing ``request``'s id."""
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "error": {
            "code": UNSUPPORTED_PROTOCOL_VERSION,
            "message": "Unsupported protocol version",
            "data": {"supported": list(supported), "requested": requested},
        },
    }


def is_unsupported_version_error(resp: dict) -> bool:
    """Whether a response is a recognized ``UnsupportedProtocolVersionError``.

    A modern server answers a probe it can't satisfy with this specific code;
    the client treats it as "modern, but pick another version" -- distinct from
    the *any other error* case that means "legacy, fall back to initialize".
    """
    err = resp.get("error") if isinstance(resp, dict) else None
    return isinstance(err, dict) and err.get("code") == UNSUPPORTED_PROTOCOL_VERSION
