"""Explicit synthetic issuer/CA/provider fixtures; never resolve host credentials."""

import copy
import datetime
import ipaddress
import json
import ssl
import time
import jwt
from http.server import ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID
from fleet_manager.gateway_auth import Signer, make_proof, thumbprint
from fleet_manager.gateway_client import CredentialMaterial, Discovery, GatewayClient
from fleet_manager.gateway_contract import GatewayConfig
from jwcrypto import jwk


class SyntheticScopedProvider:
    """Fixture only; never a real account or provider lookup."""

    async def acquire(self, request, *, timeout):
        return CredentialMaterial(
            "SYNTHETIC-SECRET-NEVER-PRODUCTION", int(time.time()) + 300,
            request["source"], request["account"], request["audience"],
            tuple(request["scopes"]),
        )


class BoundedTLSFixtureServer(ThreadingHTTPServer):
    request_queue_size = 4

    def __init__(self, address, handler, tls):
        self._tls = tls
        super().__init__(address, handler)

    def get_request(self):
        connection, address = self.socket.accept()
        connection.settimeout(3)
        try:
            return self._tls.wrap_socket(connection, server_side=True), address
        except (OSError, ssl.SSLError):
            connection.close()
            raise


def public(key, kid):
    pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    return jwk.JWK.from_pem(pem).export_public(as_dict=True) | {
        "kid": kid, "alg": "ES256" if isinstance(key, ec.EllipticCurvePrivateKey) else "EdDSA",
        "use": "sig"}


def pem_key(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


class Fixtures:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(exist_ok=True)
        self.issuer = ed25519.Ed25519PrivateKey.generate()
        self.gateway = ed25519.Ed25519PrivateKey.generate()
        self.backend_key = ed25519.Ed25519PrivateKey.generate()
        self.proofs = {p: ed25519.Ed25519PrivateKey.generate() for p in ("alice", "bob", "worker", "controller")}
        self.access_issuer = ec.generate_private_key(ec.SECP256R1())
        self.identity_proofs = {p: ec.generate_private_key(ec.SECP256R1()) for p in self.proofs}
        self.recipient = jwk.JWK.generate(kty="RSA", size=2048)
        tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-loopback-fixture")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(tls_key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=1))
                .not_valid_after(now + datetime.timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]),
                               critical=False).sign(tls_key, hashes.SHA256()))
        (root / "tls.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root / "tls-key.pem").write_bytes(pem_key(tls_key))
        for name, key in {"issuer": self.issuer, "gateway": self.gateway,
                          "backend": self.backend_key, **self.proofs,
                          "identity-issuer": self.access_issuer,
                          **{f"identity-{p}": key for p, key in self.identity_proofs.items()}}.items():
            (root / f"{name}.pem").write_bytes(pem_key(key))
        (root / "recipient.json").write_text(self.recipient.export_private(), encoding="utf-8")
        self.keys = {"keys": [public(self.issuer, "issuer"), public(self.gateway, "gateway")]}
        peers = []
        for principal, role, sources, corpora in (
            ("alice", "query", ["source-a"], ["corpus-a"]),
            ("bob", "query", ["source-b"], ["corpus-b"]),
            ("worker", "index-worker", ["source-a"], ["corpus-a"]),
            ("controller", "credential-controller", ["source-a"], ["corpus-a"]),
        ):
            peers.append({
                "principal": principal, "role": role, "service": "index", "installation": "index-install",
                "generation": 1, "expires_at": int(time.time()) + 600, "sources": sources, "corpora": corpora,
                "accounts": ["account-a"], "audiences": ["ingestion-a"], "scopes": ["read"],
                "jobs": ["job-a"],
                "proof_jwk": public(self.identity_proofs[principal], "proof"),
                **({"receipt_jwk": public(self.proofs[principal], "proof")}
                   if principal == "controller" else {}),
                **({"recipient_jwk": self.recipient.export_public(as_dict=True)} if principal == "worker" else {}),
            })
        self.value = {
            "schema": "fleet-manager.gateway", "version": 1, "role": "gateway", "fleet": "fleet-a",
            "installation": "gateway-install", "issuer": "fixture-issuer", "audience": "gateway-a",
            "jwks": self.keys, "enrollments": peers, "revoked": [],
            "identity": {"profile": "jwt-pop-v1", "issuer": "https://issuer.fixture.invalid",
                         "jwks": {"keys": [public(self.access_issuer, "issuer")]},
                         "trust_valid_until": int(time.time()) + 86400},
            "backends": [{
                "identity": "backend-a", "service": "index", "installation": "index-install",
                "generation": 1, "expires_at": int(time.time()) + 600,
                "corpus": "corpus-a", "sources": ["source-a", "source-b"],
                "endpoint": "https://127.0.0.1:1", "ca": str(root / "tls.pem"),
                "issuer": "backend-issuer", "jwks": {"keys": [public(self.backend_key, "backend")]},
                "contract": "fleet-index-scoped-v1",
            }],
            "credential_rules": [{
                "worker": "worker", "controller": "controller", "source": "source-a",
                "account": "account-a", "audience": "ingestion-a", "scopes": ["read"],
                "action": "get-azure-token",
            }],
            "pending_limit": 8, "lease_seconds": 15,
            "tls": {"host": "127.0.0.1", "port": 0, "certificate": str(root / "tls.pem"),
                    "key": str(root / "tls-key.pem")},
            "signing_key": str(root / "gateway.pem"), "signing_kid": "gateway", "remote": False,
        }
        self.save()

    def save(self):
        (self.root / "config.json").write_text(json.dumps(self.value), encoding="utf-8")

    @property
    def config(self):
        return GatewayConfig.load(copy.deepcopy(self.value))

    @property
    def signer(self):
        return Signer(self.gateway, "gateway", "fixture-issuer", "gateway-install")

    @property
    def gateway_keys(self):
        return {"keys": [public(self.gateway, "gateway")]}

    def access(self, principal, overrides=None):
        peer = next(p for p in self.value["enrollments"] if p["principal"] == principal)
        payload = {k: peer[k] for k in ("role", "service", "installation", "generation",
                                      "sources", "corpora", "accounts", "audiences", "scopes", "jobs")}
        payload |= {"fleet": "fleet-a", "cnf": {"jkt": thumbprint(peer["proof_jwk"])}}
        payload.update(overrides or {})
        now = int(time.time())
        claims = payload | {"iss": self.value["identity"]["issuer"], "aud": "gateway-a",
                            "sub": principal, "iat": now, "nbf": now, "exp": now + 120,
                            "jti": f"fixture-{principal}-{time.time_ns()}"}
        claims.update(overrides or {})
        return jwt.encode(claims, self.access_issuer, algorithm="ES256",
                          headers={"kid": "issuer", "typ": "at+jwt"})

    def message(self, principal, route, payload, *, admission, epoch=None, access=None):
        access = access or self.access(principal)
        body = {"epoch": epoch, "payload": payload}
        return {"access": access, "body": body, "proof": make_proof(
            self.identity_proofs[principal], access, target_uri="https://127.0.0.1:443" + route,
            nonce=admission.verifier.nonce)}

    def client(self, principal, port):
        discovery = Discovery(f"https://127.0.0.1:{port}", str(self.root / "tls.pem"),
                              "fixture-issuer", "gateway-a", "gateway-install", self.gateway_keys)
        return GatewayClient(discovery, principal, self.identity_proofs[principal], lambda: self.access(principal))


def query(source=None, *, corpus="corpus-a", operation="index.search", request_id="query-1"):
    return {"request_id": request_id, "service_id": "index", "installation_id": "index-install",
            "generation": 1, "operation": operation,
            "parameters": {"q": "fixture", "limit": 10, "corpus": corpus,
                           **({"source": source} if source else {})}}


def credential_request(request_id="credential-1"):
    return {"request_id": request_id, "service_id": "index", "installation_id": "index-install",
            "generation": 1, "operation": "credential.get",
            "parameters": {"job": "job-a", "source": "source-a", "account": "account-a",
                           "audience": "ingestion-a", "scopes": ["read"], "action": "get-azure-token",
                           "recipient": "worker"}}
