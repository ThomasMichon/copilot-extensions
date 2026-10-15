"""Opt-in, fixed scoped backend ingress. No legacy API or lifecycle forwarding."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException, Request
from fleet_manager.gateway_auth import Signer, thumbprint
from fleet_manager.gateway_contract import (
    INDEX_CONTRACT,
    MAX_WIRE,
    Backend,
    GatewayError,
    encode,
    fields,
    parse,
    require,
    text,
    validate_jwks,
)
from fleet_manager.gateway_index import ExecutionScope, ScopedIndexBackend
from jwcrypto import jwk
from starlette.concurrency import run_in_threadpool

from .query_surface import hit_to_dict


@dataclass(frozen=True)
class FleetPolicy:
    backend: Backend
    fleet: str
    gateway_keys: dict
    gateway_issuer: str
    gateway_subject: str
    signing_key: Path
    signing_kid: str
    bindings: dict[str, str]
    host: str
    port: int
    certificate: str
    tls_key: str

    @classmethod
    def load(cls, path: Path, *, declared_sources: tuple[str, ...]) -> FleetPolicy:
        try:
            with path.open("rb") as stream:
                value = parse(stream.read(MAX_WIRE + 1))
        except OSError:
            raise GatewayError("fleet-policy-unavailable") from None
        fields(value, {
            "schema", "version", "backend", "fleet", "gateway_keys", "gateway_issuer",
            "gateway_subject", "signing_key", "signing_kid", "source_bindings", "listener",
        })
        require(value["schema"] == "agent-index.fleet-backend"
                and type(value["version"]) is int and value["version"] == 1)
        listener = fields(value["listener"], {"host", "port", "remote", "certificate", "key"})
        require(type(listener["remote"]) is bool)
        host = text(listener["host"])
        require(listener["remote"] or host in ("127.0.0.1", "::1", "localhost"))
        require(type(listener["port"]) is int and 1 <= listener["port"] <= 65535)
        backend = Backend.load(value["backend"], listener["remote"])
        require(backend.contract == INDEX_CONTRACT, "backend-unsupported")
        from urllib.parse import urlsplit

        origin = urlsplit(backend.endpoint)
        require(
            origin.hostname == host and origin.port == listener["port"],
            "listener-binding-mismatch",
        )
        validate_jwks(value["gateway_keys"])
        bindings = fields(value["source_bindings"], set(backend.sources))
        mapped = {}
        for grant, binding in bindings.items():
            fields(binding, {"declared_source", "family"})
            require(text(binding["declared_source"]) in declared_sources, "undeclared-source")
            family = text(binding["family"])
            require(":" in family and not family.endswith(":"), "invalid-stored-family")
            mapped[grant] = family
        return cls(
            backend, text(value["fleet"]), value["gateway_keys"],
            text(value["gateway_issuer"]), text(value["gateway_subject"]),
            Path(text(value["signing_key"])), text(value["signing_kid"]), mapped,
            host, listener["port"], text(listener["certificate"]), text(listener["key"]),
        )

    def signer(self) -> Signer:
        try:
            with self.signing_key.open("rb") as stream:
                raw = stream.read(16385)
            require(len(raw) <= 16384)
            key = serialization.load_pem_private_key(raw, password=None)
            require(isinstance(key, Ed25519PrivateKey))
            expected = next(
                (key for key in self.backend.jwks["keys"] if key["kid"] == self.signing_kid),
                None,
            )
            require(expected is not None)
            public = key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            require(thumbprint(jwk.JWK.from_pem(public).export_public(as_dict=True))
                    == thumbprint(expected), "backend-signing-key-mismatch")
            return Signer(key, self.signing_kid, self.backend.issuer, self.backend.identity)
        except (OSError, ValueError, TypeError):
            raise GatewayError("backend-signing-key-unavailable") from None


def build_fleet_app(policy: FleetPolicy, native_app: Any) -> FastAPI:
    """Only the two authenticated scoped routes, sharing native search/drain ownership."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def retrieve(scope: ExecutionScope, parameters: dict) -> dict:
        require(scope.corpus == policy.backend.corpus, "corpus-denied")
        require(bool(scope.sources) and all(s in policy.bindings for s in scope.sources))
        families = tuple(dict.fromkeys(policy.bindings[s] for s in scope.sources))
        require(native_app.state.promoted, "service-passive")
        with native_app.state.drain_gate.track_read():
            engine = native_app.state.get_search_engine()
            hits = engine.search_authorized(
                parameters["q"], families=families, limit=parameters["limit"],
                language=parameters.get("language"), repo=parameters.get("repo"),
            )
        payload = []
        for hit in hits:
            grant = next((s for s in scope.sources if hit.source == policy.bindings[s]
                          or hit.source.startswith(policy.bindings[s] + ":")), None)
            require(grant is not None, "scoped-store-contract-violation")
            payload.append(hit_to_dict(hit) | {
                "source": grant, "stored_source": hit.source, "corpus": scope.corpus,
            })
        return {"hits": payload}

    backend = ScopedIndexBackend(
        policy.backend, policy.signer(), gateway_keys=policy.gateway_keys,
        gateway_issuer=policy.gateway_issuer, gateway_subject=policy.gateway_subject,
        fleet=policy.fleet, retrieve=retrieve,
    )
    slots = threading.BoundedSemaphore(16)

    async def handle(request: Request, operation: Callable) -> dict:
        if request.url.scheme != "https":
            raise HTTPException(403, detail="tls-required")
        if not slots.acquire(blocking=False):
            raise HTTPException(503, detail="backend-unavailable")

        async def bounded_body() -> bytes:
            raw = bytearray()
            async for part in request.stream():
                if len(raw) + len(part) > MAX_WIRE:
                    raise HTTPException(413, detail="wire-limit")
                raw.extend(part)
            return bytes(raw)

        try:
            body = parse(await asyncio.wait_for(bounded_body(), timeout=5))
            result = await run_in_threadpool(operation, body)
            encode(result)
            return result
        except (GatewayError, ValueError, KeyError, TypeError):
            raise HTTPException(403, detail="backend-denied") from None
        except RuntimeError:
            raise HTTPException(503, detail="backend-unavailable") from None
        except (asyncio.TimeoutError, TimeoutError):
            raise HTTPException(408, detail="request-expired") from None
        finally:
            slots.release()

    @app.post("/v1/index/admit")
    async def admit(request: Request) -> dict:
        return await handle(request, backend.admit)

    @app.post("/v1/index/query")
    async def query(request: Request) -> dict:
        return await handle(request, backend.execute)

    return app


@contextmanager
def fleet_ingress(policy: FleetPolicy | None, native_app: Any):
    """Same-process owned TLS ingress; native loopback API/engine remain unchanged."""
    if policy is None:
        yield
        return
    import time

    import uvicorn

    from .fleet_http import FleetHTTPProtocol
    from .fleet_tls import TLSAdmission

    config = uvicorn.Config(
        build_fleet_app(policy, native_app), host=policy.host, port=policy.port,
        http=FleetHTTPProtocol, ws="none",
        ssl_certfile=policy.certificate, ssl_keyfile=policy.tls_key,
        timeout_keep_alive=5, limit_concurrency=32,
        timeout_graceful_shutdown=5,
        proxy_headers=False,
        access_log=False, log_level="critical", lifespan="off",
    )
    config.load()
    require(config.ssl is not None, "fleet-ingress-tls-unavailable")
    admission = TLSAdmission(config.ssl)
    config.ssl = None
    config.http_protocol_class = admission.protocol
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="index-fleet-ingress")
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        require(server.started, "fleet-ingress-unavailable")
        yield
    finally:
        if thread.is_alive() and admission.loop is not None:
            admission.loop.call_soon_threadsafe(admission.close)
        server.should_exit = True
        thread.join(10)
        require(not thread.is_alive(), "fleet-ingress-shutdown-uncertain")
