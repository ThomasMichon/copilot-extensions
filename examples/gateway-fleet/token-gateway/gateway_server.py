"""Experimental reference implementation; see the parent README."""


from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
from jwt import PyJWKClient

from index_search import IndexSearchError, search_index
from credential_record import load_credential, store_credential, valid_credential


_VENDOR_DIR = Path(__file__).parent / "vendor"
if str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))


os.environ.setdefault("AGENT_VAULT_CACHE", "1")
os.environ.setdefault(
    "AGENT_VAULT_CACHE_DIR", str(Path.home() / "harness-gateway-fleet" / "relay-cache")
)

from agent_vault.cache import PersistentCache

TENANT_ID = os.environ.get("HGF_TENANT_ID", "00000002-0000-4000-8000-000000000000")
APP_ID = os.environ.get("HGF_APP_ID", "00000001-0000-4000-8000-000000000000")
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
REQUIRED_ROLE = "GatewayClient"

LISTEN_HOST = "0.0.0.0"


LISTEN_PORT = 8787
LOG_PATH = Path.home() / "harness-gateway-fleet" / "gateway.log"

log = logging.getLogger("hgf.gateway")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stderr))
    log.setLevel(logging.INFO)


_jwks_client = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600)


_relay_cache = PersistentCache()


RELAY_CAPABILITIES = ("get-azure-token",)
_RELAY_ACTIONS = frozenset({"capabilities", "get-azure-token"})


_EXPIRY_SAFETY_MARGIN = 300


def _normalize_resource(target: str) -> str:
    pass


    t = (target or "").strip()
    if t.endswith("/.default"):
        t = t[: -len("/.default")]
    return t.rstrip("/")


def _cache_entry(resource: str, tenant: str) -> str:
    return f"relay/az-token/{_normalize_resource(resource)}/{tenant or 'default'}"


def _push_credential(resource: str, tenant: str, token: str, expires_epoch: float) -> bool:
    entry = _cache_entry(resource, tenant)
    return store_credential(_relay_cache, entry, token, expires_epoch)


def _relay_get_azure_token(fields: dict) -> tuple[int, dict]:
    pass


    target = fields.get("scope") or fields.get("resource") or ""
    if not target or not _normalize_resource(target):
        return 400, {"error": "missing 'resource' or 'scope' field"}
    tenant = fields.get("tenant", "")
    entry = _cache_entry(target, tenant)
    record = load_credential(_relay_cache, entry)
    if record is None:
        return 404, {"error": "no_cached_token", "resource": _normalize_resource(target)}
    expires_epoch = record["expires_epoch"]
    if expires_epoch - _EXPIRY_SAFETY_MARGIN <= time.time():
        return 404, {"error": "cached_token_expired", "resource": _normalize_resource(target)}
    return 200, {
        "token": record["token"],
        "resource": _normalize_resource(target),
        "expires_on": expires_epoch,
    }


class AuthError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _valid_field(value, required=False):
    return (
        isinstance(value, str) and len(value) <= 4096
        and (not required or bool(value))
        and all(32 < ord(char) < 127 for char in value)
    )


def validate_bearer(auth_header: str | None) -> dict:
    pass
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthError(401, "missing or malformed Authorization header")
    token = auth_header[len("Bearer ") :].strip()
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=APP_ID,
            issuer=ISSUER,
            options={"require": ["exp", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(401, "token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(401, f"invalid token: {exc}") from exc
    roles = claims.get("roles", [])
    if not isinstance(roles, list) or REQUIRED_ROLE not in roles:
        raise AuthError(403, f"token lacks required role {REQUIRED_ROLE!r}")
    if claims.get("tid") != TENANT_ID:
        raise AuthError(403, "token is not authorized for this tenant")
    return claims


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reject_auth(self, exc: AuthError) -> None:
        log.warning("auth failed: %s", exc.message)
        self.close_connection = True
        try:
            self._json(exc.status, {"error": exc.message})
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)

            deadline = time.monotonic() + 0.1
            remaining = 64 * 1024
            while remaining:
                seconds = deadline - time.monotonic()
                if seconds <= 0:
                    break
                self.connection.settimeout(min(0.1, seconds))
                data = self.connection.recv(min(4096, remaining))
                if not data:
                    break
                remaining -= len(data)
        except OSError as error:
            log.debug("auth rejection connection ended: %s", type(error).__name__)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok", "time": time.time()})
            return
        route, _, query = self.path.partition("?")
        if route == "/api/v1/index/search":
            try:
                validate_bearer(self.headers.get("Authorization"))
            except AuthError as exc:
                self._reject_auth(exc)
                return
            try:
                payload = search_index(query)
            except IndexSearchError as exc:
                self._json(exc.status, {"error": exc.code})
                return
            self._json(200, payload)
            return
        if self.path == "/api/v1/status":
            try:
                claims = validate_bearer(self.headers.get("Authorization"))
            except AuthError as exc:
                self._reject_auth(exc)
                return
            self._json(
                200,
                {
                    "authenticated": True,
                    "oid": claims.get("oid"),
                    "roles": claims.get("roles"),
                    "exp": claims.get("exp"),
                    "server_time": time.time(),
                },
            )
            return
        self._json(404, {"error": "not found"})

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= 65536:
            return None
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None

    def do_POST(self) -> None:
        try:
            claims = validate_bearer(self.headers.get("Authorization"))
        except AuthError as exc:
            self._reject_auth(exc)
            return

        if self.path == "/api/v1/chat":
            from chat_proxy import ChatProxyError, forward, read_request
            try:
                result = forward(read_request(self))
            except ChatProxyError as exc:
                self._json(exc.status, {"error": exc.code})
                return
            self._json(200, result)
            return

        body = self._read_json_body()
        if body is None:
            self._json(400, {"error": "missing or invalid JSON body"})
            return

        if self.path == "/api/v1/credentials/push":
            resource = body.get("resource", "")
            token = body.get("token", "")
            expires_epoch = body.get("expires_epoch")
            tenant = body.get("tenant", "")
            if (
                not _valid_field(resource, required=True)
                or not _normalize_resource(resource)
                or not _valid_field(tenant)
                or not valid_credential(token, expires_epoch)
            ):
                self._json(
                    400, {"error": "invalid credential fields"}
                )
                return
            ok = _push_credential(resource, tenant, token, float(expires_epoch))
            if not ok:
                self._json(503, {"error": "cache_unavailable"})
                return
            log.info(
                "credentials/push: oid=%s resource=%s expires_in=%.0fs",
                claims.get("oid"),
                _normalize_resource(resource),
                float(expires_epoch) - time.time(),
            )
            self._json(200, {"ok": True, "resource": _normalize_resource(resource)})
            return

        if self.path == "/api/v1/relay":
            action = body.get("action", "")
            fields = body.get("fields", {})
            if (
                not isinstance(action, str) or not isinstance(fields, dict)
                or any(not _valid_field(fields.get(name, ""))
                       for name in ("resource", "scope", "tenant"))
            ):
                self._json(400, {"error": "invalid relay fields"})
                return
            if action == "capabilities":
                self._json(200, {"capabilities": list(RELAY_CAPABILITIES)})
                return
            if action not in _RELAY_ACTIONS:
                self._json(400, {"error": "unsupported action"})
                return
            if action == "get-azure-token":
                status, payload = _relay_get_azure_token(fields)
                log.info(
                    "relay: oid=%s action=%s resource=%s status=%d",
                    claims.get("oid"), action, fields.get("resource") or fields.get("scope"),
                    status,
                )
                self._json(status, payload)
                return

        self._json(404, {"error": "not found"})


def main() -> None:
    _setup_logging()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    log.info("Gateway API listening on %s:%d (pid handled by caller)", LISTEN_HOST, LISTEN_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Gateway API stopping")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
