"""Synthetic issuer/CA plus a real two-family Lance store; inference only is a fixture."""

from __future__ import annotations

import datetime
import ipaddress
import json
import socket
import time

import numpy as np
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID
from fleet_manager.gateway_auth import Signer
from fleet_manager.gateway_contract import correlation
from jwcrypto import jwk

from agent_index.chunking.base import Chunk
from agent_index.fleet_backend import FleetPolicy
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.search.engine import SearchEngine
from agent_index.server import build_app
from agent_index.store.source_layout import initialize_source_store


def public(key, kid):
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return jwk.JWK.from_pem(pem).export_public(as_dict=True) | {
        "kid": kid, "alg": "EdDSA", "use": "sig",
    }


class BackendFixture:
    def __init__(self, root):
        self.root = root
        self.gateway_key = ed25519.Ed25519PrivateKey.generate()
        self.backend_key = ed25519.Ed25519PrivateKey.generate()
        self.gateway_keys = {"keys": [public(self.gateway_key, "gateway")]}
        self.backend_keys = {"keys": [public(self.backend_key, "backend")]}
        tls = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-index-ca")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(tls.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]), critical=False).sign(tls, hashes.SHA256())
        )
        (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        for label, key in (("tls", tls), ("backend", self.backend_key)):
            (root / f"{label}.pem").write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.origin = f"https://127.0.0.1:{port}"
        self.value = {
            "schema": "agent-index.fleet-backend", "version": 1,
            "backend": {
                "identity": "index-node", "service": "index", "installation": "index-cell",
                "generation": 3, "expires_at": int(time.time()) + 600, "corpus": "corpus-a",
                "sources": ["docs", "other", "empty"], "endpoint": self.origin,
                "ca": str(root / "cert.pem"), "issuer": "index-issuer",
                "jwks": self.backend_keys, "contract": "fleet-index-scoped-v1",
            },
            "fleet": "fleet-a", "gateway_keys": self.gateway_keys,
            "gateway_issuer": "gateway-issuer", "gateway_subject": "gateway-cell",
            "signing_key": str(root / "backend.pem"), "signing_kid": "backend",
            "source_bindings": {
                "docs": {"declared_source": "git:alpha", "family": "git:alpha"},
                "other": {"declared_source": "git:beta", "family": "git:beta"},
                "empty": {"declared_source": "git:missing", "family": "git:missing"},
            },
            "listener": {
                "host": "127.0.0.1", "port": port, "remote": False,
                "certificate": str(root / "cert.pem"), "key": str(root / "tls.pem"),
            },
        }
        self.save()
        self.profile = ModelProfile("fixture", "fixture-inference-only", dim=2)
        self.store = initialize_source_store(IndexConfig(
            data_dir=root / "data", model_profiles={"fixture": self.profile},
        ))
        self.vector_table = self.store.get_vector_table(self.profile.model_id)
        self.chunks = [
            self.chunk("git:alpha", "needle allowed alpha", 1),
            self.chunk("git:alpha:commits", "needle allowed commit", 2),
            self.chunk("git:alphabet", "needle forbidden prefix", 3),
            *[self.chunk("git:beta", f"needle forbidden beta {i}", i + 4) for i in range(80)],
        ]
        vectors = np.array([[0, 1], [0.1, 0.9], *[[1, 0]] * 81], dtype=np.float32)
        self.store.upsert(self.profile.model_id, self.chunks, vectors)
        self.factory_calls = []

        class InferenceFixture:
            def embed_query(self, query):
                return np.array([1, 0], dtype=np.float32)

        self.engine = SearchEngine({"fixture": InferenceFixture()}, self.store)
        self.native = build_app(passive=True)
        self.native.state.promoted = True
        self.native.state.get_search_engine = self.get_engine

    @staticmethod
    def chunk(source, content, number):
        return Chunk(content, f"docs/{number}.md", "text", "markdown", 1, 1, source)

    def save(self):
        (self.root / "policy.json").write_text(json.dumps(self.value), encoding="utf-8")

    @property
    def policy(self):
        return FleetPolicy.load(
            self.root / "policy.json",
            declared_sources=("git:alpha", "git:beta", "git:missing"),
        )

    def get_engine(self):
        self.factory_calls.append(True)
        return self.engine

    def body(self, sources=("docs",), *, selector=None, changes=None):
        value = {
            "request_id": "request-1", "service_id": "index", "installation_id": "index-cell",
            "generation": 3, "operation": "index.search",
            "parameters": {"q": "needle", "limit": 10, "corpus": "corpus-a"},
        }
        if selector is not None:
            value["parameters"]["source"] = selector
        value.update(changes or {})
        grant = Signer(
            self.gateway_key, "gateway", "gateway-issuer", "gateway-cell",
        ).sign({
            "contract": "fleet-index-scoped-v1", "fleet": "fleet-a", "backend": "index-node",
            "correlation": correlation(value),
            "scope": {"principal": "alice", "corpus": "corpus-a", "sources": list(sources)},
        }, "index-node", 30)
        return {"request": value, "grant": grant}
