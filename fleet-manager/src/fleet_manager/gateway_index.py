"""Named scoped index route and the later index consumer's pre-retrieval verifier."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .gateway_auth import AuthContext, Signer, verify
from .gateway_contract import (
    INDEX_CONTRACT,
    Backend,
    GatewayConfig,
    correlation,
    encode,
    fields,
    request,
    require,
    search,
    strings,
    text,
)
from .planning import digest


@dataclass(frozen=True)
class ExecutionScope:
    principal: str
    fleet: str
    service: str
    installation: str
    generation: int
    corpus: str
    sources: tuple[str, ...]
    request_id: str
    request_digest: str
    expires_at: int


def verify_execution(grant: str, value: dict, *, gateway_keys: dict, gateway_issuer: str,
                     gateway_subject: str, backend: Backend, fleet: str) -> ExecutionScope:
    """Consumer MUST call before seed lookup, vector retrieval, hydration or metadata."""
    value = request(value)
    parameters = search(value["parameters"])
    require(backend.contract == INDEX_CONTRACT and backend.expires_at > time.time(), "backend-unsupported")
    claims = verify(grant, gateway_keys, gateway_issuer, backend.identity, lifetime=30)
    require(claims["sub"] == gateway_subject and claims.get("contract") == INDEX_CONTRACT
            and claims.get("fleet") == fleet and claims.get("backend") == backend.identity,
            "backend-identity-mismatch")
    require(claims.get("correlation") == correlation(value), "backend-correlation-mismatch")
    require(value["operation"] == "index.search" and value["service_id"] == backend.service
            and value["installation_id"] == backend.installation
            and value["generation"] == backend.generation, "offer-generation-mismatch")
    scope = fields(claims.get("scope"), {"principal", "corpus", "sources"})
    text(scope["principal"])
    sources = strings(scope["sources"])
    require(scope["corpus"] == parameters["corpus"] == backend.corpus
            and set(sources) <= set(backend.sources), "backend-scope-denied")
    if "source" in parameters:
        require(parameters["source"] in sources, "backend-scope-denied")
    return ExecutionScope(scope["principal"], fleet, backend.service, backend.installation,
                          backend.generation, backend.corpus, sources, value["request_id"],
                          digest(value), claims["exp"])


class ScopedIndexBackend:
    """Consumer seam: authorize/admit BEFORE calling the scope-aware retrieval callback.

    The callback must push nonempty scope.sources into retrieval and hydration itself.
    A legacy/unscoped retrieval callback is NOT compatible with this interface.
    """
    def __init__(self, backend: Backend, signer: Signer, *, gateway_keys: dict,
                 gateway_issuer: str, gateway_subject: str, fleet: str,
                 retrieve: Callable[[ExecutionScope, dict], dict]):
        require(backend.contract == INDEX_CONTRACT, "backend-unsupported")
        self.backend, self.signer = backend, signer
        self.gateway_keys, self.gateway_issuer = gateway_keys, gateway_issuer
        self.gateway_subject, self.fleet, self.retrieve = gateway_subject, fleet, retrieve
        self._admissions: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()

    def _verify(self, body: dict) -> ExecutionScope:
        return verify_execution(body["grant"], body["request"], gateway_keys=self.gateway_keys,
                                gateway_issuer=self.gateway_issuer, gateway_subject=self.gateway_subject,
                                backend=self.backend, fleet=self.fleet)

    def _receipt(self, body: dict, scope: ExecutionScope, extra: dict) -> dict:
        value = {"contract": INDEX_CONTRACT, "correlation": correlation(body["request"]),
                 "scope": {"principal": scope.principal, "corpus": scope.corpus,
                           "sources": list(scope.sources)}, **extra}
        return {"receipt": self.signer.sign(value, self.gateway_subject,
                                            max(1, min(30, scope.expires_at - int(time.time()))),
                                            expires_at=scope.expires_at)}

    def admit(self, body: dict) -> dict:
        fields(body, {"request", "grant"})
        scope = self._verify(body)
        with self._lock:
            self._admissions = {k: v for k, v in self._admissions.items() if v[1] > time.time()}
            require(len(self._admissions) < 64, "backend-unavailable")
            admission = secrets.token_hex(16)
            self._admissions[admission] = (digest(body), scope.expires_at)
        return self._receipt(body, scope, {"state": "ADMITTED", "admission": admission})

    def execute(self, body: dict) -> dict:
        fields(body, {"request", "grant", "admission"})
        scope = self._verify(body)
        with self._lock:
            entry = self._admissions.pop(body["admission"], None)
            require(entry is not None and entry[1] > time.time()
                    and entry[0] == digest({"request": body["request"], "grant": body["grant"]}),
                    "backend-admission-unknown")
        result = self.retrieve(scope, body["request"]["parameters"])
        require(type(result) is dict, "backend-result-invalid")
        return self._receipt(body, scope, {"state": "SUCCEEDED", "result": result})


class IndexRouter:
    def __init__(self, config: GatewayConfig, signer: Signer):
        self.config, self.signer = config, signer

    def execute(self, context: AuthContext, payload: dict, *, deadline: float | None = None) -> dict:
        from .gateway_client import post

        value = request(payload)
        require(context.peer.role == "query", "role-denied")
        require(value["operation"] == "index.search", "unsupported-operation")
        parameters = search(value["parameters"])
        backend = next((b for b in self.config.backends if b.service == value["service_id"]), None)
        require(backend is not None and backend.contract == INDEX_CONTRACT, "backend-unsupported")
        require(backend.identity not in self.config.revoked, "offer-unavailable")
        require(backend.expires_at > time.time(), "offer-unavailable")
        require(value["installation_id"] == backend.installation
                and value["generation"] == backend.generation, "offer-generation-mismatch")
        peer = context.peer
        require(peer.service == backend.service and peer.installation == backend.installation
                and parameters["corpus"] == backend.corpus and backend.corpus in peer.corpora,
                "corpus-denied")
        sources = tuple(s for s in peer.sources if s in backend.sources)
        require(bool(sources), "source-denied")
        if "source" in parameters:
            require(parameters["source"] in sources, "source-denied")
            sources = (parameters["source"],)
        binding = correlation(value)
        scope = {"principal": peer.principal, "corpus": backend.corpus, "sources": list(sources)}
        grant = self.signer.sign(
            {"contract": INDEX_CONTRACT, "fleet": self.config.fleet, "backend": backend.identity,
             "correlation": binding, "scope": scope}, backend.identity,
            min(30, context.expires_at - int(time.time()), backend.expires_at - int(time.time())),
            expires_at=min(context.expires_at, backend.expires_at))
        body = {"request": value, "grant": grant}
        admission = post(backend.endpoint, "/v1/index/admit", body, backend.ca, deadline=deadline)
        fields(admission, {"receipt"})
        admitted = verify(admission["receipt"], backend.jwks, backend.issuer,
                          self.config.installation, lifetime=30, revoked=self.config.revoked)
        require(admitted["sub"] == backend.identity and admitted.get("contract") == INDEX_CONTRACT
                and admitted.get("correlation") == binding and admitted.get("scope") == scope
                and admitted.get("state") == "ADMITTED", "backend-admission-denied")
        admission_id = text(admitted.get("admission"))
        response = post(backend.endpoint, "/v1/index/query", body | {"admission": admission_id},
                        backend.ca, deadline=deadline)
        fields(response, {"receipt"})
        receipt = verify(response["receipt"], backend.jwks, backend.issuer,
                         self.config.installation, lifetime=30, revoked=self.config.revoked)
        require(receipt["sub"] == backend.identity and receipt.get("contract") == INDEX_CONTRACT
                and receipt.get("correlation") == binding and receipt.get("scope") == scope,
                "backend-receipt-mismatch")
        result = fields(receipt.get("result"), {"hits"})
        require(type(result["hits"]) is list and len(result["hits"]) <= parameters["limit"],
                "backend-result-invalid")
        # This is a consistency check, not authorization by post-filter.
        for hit in result["hits"]:
            require(type(hit) is dict and hit.get("source") in sources
                    and hit.get("corpus") == backend.corpus, "backend-contract-violation")
        encode(result)
        return {"state": "SUCCEEDED", **binding, "result": result}
