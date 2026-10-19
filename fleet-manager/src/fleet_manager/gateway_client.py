"""Thin SDK: explicit discovered HTTPS endpoint, no hosting/model/installer effects."""

from __future__ import annotations

import ssl
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .gateway_contract import (
    MAX_WIRE,
    GatewayError,
    encode,
    endpoint,
    fields,
    parse,
    require,
    text,
    validate_jwks,
)
from .planning import digest
from .gateway_transport import DeadlineHTTPSConnection

ROUTES = frozenset({
    "/v1/challenge", "/v1/session", "/v1/index/query", "/v1/index/admit", "/v1/credentials/request", "/v1/credentials/result",
    "/v1/credentials/ack", "/v1/controllers/subscribe", "/v1/controllers/poll",
    "/v1/controllers/reply", "/v1/controllers/disconnect",
})


def post(origin: str, route: str, body: dict, ca: str, *, timeout: float = 5,
         deadline: float | None = None) -> dict:
    """Internal fixed POST transport; no ambient proxy, redirects or header forwarding."""
    require(route in ROUTES, "unsupported-route")
    origin = endpoint(origin, remote=True)
    expires = min(time.monotonic() + timeout, deadline) if deadline is not None else time.monotonic() + timeout
    require(expires > time.monotonic(), "transport-unavailable")
    raw = encode(body)
    try:
        context = ssl.create_default_context(cafile=ca)
    except (OSError, ssl.SSLError):
        raise GatewayError("transport-unavailable") from None
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    address = urlsplit(origin)
    connection = DeadlineHTTPSConnection(address.hostname, address.port, context=context,
                                         deadline=expires)
    transport = None

    def expire():
        if transport is not None:
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # The response may already have closed its connection.

    timer = threading.Timer(max(0, expires - time.monotonic()), expire)
    timer.start()
    try:
        connection.connect()
        transport = connection.sock
        require(time.monotonic() < expires, "transport-unavailable")
        connection.request("POST", route, body=raw, headers={"Content-Type": "application/json"})
        with connection.getresponse() as response:
            require(not 300 <= response.status < 400, "redirect-denied")
            require(response.status < 400, "remote-denied")
            require(response.status == 200, "transport-unavailable")
            received = response.read(MAX_WIRE + 1)
            require(time.monotonic() < expires, "transport-unavailable")
            require(response.length in (None, 0), "transport-unavailable")
            result = parse(received)
            require(type(result) is dict, "invalid-response")
            return result
    except (HTTPException, OSError, TimeoutError, ssl.SSLError):
        raise GatewayError("transport-unavailable") from None
    finally:
        timer.cancel()
        timer.join()
        connection.close()


@dataclass(frozen=True)
class Discovery:
    endpoint: str
    ca: str
    issuer: str
    audience: str
    gateway: str
    jwks: dict
    remote: bool = False

    def __post_init__(self):
        require(type(self.remote) is bool)
        object.__setattr__(self, "endpoint", endpoint(self.endpoint, remote=self.remote))
        validate_jwks(self.jwks)

    @classmethod
    def load(cls, path: Path) -> Discovery:
        try:
            with path.open("rb") as stream:
                value = parse(stream.read(MAX_WIRE + 1))
        except OSError:
            raise GatewayError("discovery-unavailable") from None
        fields(value, {"schema", "version", *cls.__dataclass_fields__, "remote"})
        require(value["schema"] == "fleet-manager.gateway-endpoint"
                and type(value["version"]) is int and value["version"] == 1)
        require(type(value["remote"]) is bool)
        for key in ("ca", "issuer", "audience", "gateway"):
            text(value[key])
        validate_jwks(value["jwks"])
        return cls(endpoint(value["endpoint"], remote=value["remote"]),
                   *(value[k] for k in ("ca", "issuer", "audience", "gateway")), value["jwks"], value["remote"])


class GatewayClient:
    """Access-token callback is external custody. No automatic fallback or token cache."""

    def __init__(self, discovery: Discovery, principal: str, proof_key: Any,
                 access_token: Callable[[], str]):
        self.discovery, self.principal = discovery, principal
        self._proof_key, self._access_token = proof_key, access_token
        self.epoch: str | None = None
        self._nonce: str | None = None

    def _send(self, route: str, payload: dict, *, deadline: float | None = None) -> dict:
        from .gateway_auth import make_proof, verify

        require(route in ROUTES, "unsupported-route")
        require(deadline is None or time.monotonic() < deadline, "transport-unavailable")
        require(route == "/v1/session" or self.epoch is not None, "session-required")
        if self._nonce is None:
            challenge = post(self.discovery.endpoint, "/v1/challenge", {}, self.discovery.ca,
                             deadline=deadline)
            fields(challenge, {"profile", "nonce"})
            require(challenge["profile"] == "jwt-pop-v1", "unsupported-profile")
            self._nonce = text(challenge["nonce"], 43)
        body = {"epoch": self.epoch, "payload": payload}
        try:
            access = self._access_token()
        except (OSError, ValueError, RuntimeError):
            raise GatewayError("credential-provider-unavailable") from None
        proof = make_proof(self._proof_key, access, target_uri=self.discovery.endpoint + route,
                           nonce=self._nonce)
        result = post(self.discovery.endpoint, route, {"access": access, "proof": proof, "body": body},
                      self.discovery.ca, deadline=deadline)
        fields(result, {"receipt"})
        receipt = verify(result["receipt"], self.discovery.jwks, self.discovery.issuer,
                         self.principal, lifetime=30)
        require(receipt["sub"] == self.discovery.gateway and receipt.get("route") == route
                and receipt.get("request_digest") == digest(body), "gateway-receipt-mismatch")
        if route != "/v1/session":
            require(receipt.get("epoch") == self.epoch, "gateway-restarted")
        response = receipt.get("response")
        require(type(response) is dict)
        if "error" in response:
            safe = {"gateway-restarted", "unsupported-operation", "backend-unsupported",
                    "controller-unavailable", "duplicate-request", "ack-uncertain",
                    "subscription-unavailable", "reply-out-of-order", "transport-unavailable"}
            error = response["error"]
            code = error if type(error) is str and error in safe else "gateway-request-denied"
            raise GatewayError(code)
        return response

    def connect(self, *, deadline: float | None = None) -> dict:
        self._nonce = None
        result = self._send("/v1/session", {}, deadline=deadline)
        self.epoch = text(result.get("epoch"))
        return result

    def query(self, value: dict) -> dict:
        return self._send("/v1/index/query", value)

    def request_credential(self, value: dict, *, deadline: float | None = None) -> dict:
        return self._send("/v1/credentials/request", value, deadline=deadline)

    def credential_result(self, request_id: str, request_digest: str, *,
                          deadline: float | None = None) -> dict:
        return self._send("/v1/credentials/result",
                          {"request_id": request_id, "request_digest": request_digest}, deadline=deadline)

    def acknowledge(self, request_id: str, request_digest: str, *,
                    deadline: float | None = None) -> dict:
        return self._send("/v1/credentials/ack",
                          {"request_id": request_id, "request_digest": request_digest}, deadline=deadline)

    def subscribe(self, lease: int, subscription: str | None = None) -> dict:
        payload = {"lease": lease}
        if subscription is not None:
            payload["subscription"] = subscription
        return self._send("/v1/controllers/subscribe", payload)

    def poll(self, subscription: str) -> dict:
        return self._send("/v1/controllers/poll", {"subscription": subscription})

    def reply(self, subscription: str, worker: str, request_id: str, receipt: str) -> dict:
        return self._send("/v1/controllers/reply", {
            "subscription": subscription, "worker": worker, "request_id": request_id, "receipt": receipt})

    def disconnect(self, subscription: str) -> dict:
        return self._send("/v1/controllers/disconnect", {"subscription": subscription})


@dataclass(frozen=True)
class CredentialMaterial:
    token: str = field(repr=False)
    expires_at: int
    source: str
    account: str
    audience: str
    scopes: tuple[str, ...]


def consume_credential(client: GatewayClient, value: dict, result: dict, *, recipient_key: Any,
                       controller_keys: dict, controller: str, controller_generation: int,
                       deadline: float | None = None) -> CredentialMaterial:
    """No cache. Ack uncertainty fails without returning the decrypted credential."""
    import time

    from jwcrypto import jwe

    from .gateway_auth import thumbprint, verify
    from .gateway_contract import correlation, number, strings

    fields(result, {"state", "receipt", "binding"})
    require(result["state"] == "READY", "credential-unavailable")
    binding = result["binding"]
    require(type(binding) is dict and all(binding.get(k) == v for k, v in correlation(value).items())
            and binding.get("epoch") == client.epoch and binding.get("worker") == client.principal
            and binding.get("parameters") == value["parameters"]
            and binding.get("controller") == controller
            and binding.get("controller_generation") == controller_generation
            and binding.get("recipient_jkt") == thumbprint(recipient_key.export_public(as_dict=True)),
            "credential-binding-mismatch")
    claims = verify(result["receipt"], controller_keys, controller, client.principal, lifetime=60)
    require(claims.get("binding") == binding and claims.get("state") == "READY"
            and claims["sub"] == controller, "credential-binding-mismatch")
    sealed = text(claims.get("sealed"), 32768)
    import hashlib
    require(claims.get("sealed_digest") == hashlib.sha256(sealed.encode()).hexdigest(),
            "sealed-credential-denied")
    envelope = jwe.JWE(algs=["RSA-OAEP-256", "A256GCM"])
    try:
        envelope.deserialize(sealed, key=recipient_key)
        plain = parse(envelope.payload)
    except (jwe.JWException, ValueError, TypeError):
        raise GatewayError("sealed-credential-denied") from None
    fields(plain, {"binding", "token", "expires_at", "source", "account", "audience", "scopes"})
    require(plain["binding"] == binding, "credential-binding-mismatch")
    parameters = value["parameters"]
    require(all(plain[k] == parameters[k] for k in ("source", "account", "audience", "scopes")),
            "credential-scope-mismatch")
    require(number(plain["expires_at"]) > time.time() and plain["expires_at"] <= claims["exp"],
            "credential-expired")
    material = CredentialMaterial(text(plain["token"], 16384), plain["expires_at"],
                                  plain["source"], plain["account"], plain["audience"], strings(plain["scopes"]))
    try:
        ack = (client.acknowledge(value["request_id"], digest(value), deadline=deadline)
               if deadline is not None else client.acknowledge(value["request_id"], digest(value)))
        require(ack.get("state") == "ACKED", "ack-uncertain")
    except GatewayError:
        raise GatewayError("ack-uncertain") from None
    require(material.expires_at > time.time(), "credential-expired")
    require(deadline is None or time.monotonic() <= deadline, "credential-unavailable")
    return material
