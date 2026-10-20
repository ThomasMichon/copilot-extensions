"""Offline synthetic issuer/certificate preparation; not a production issuer."""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from importlib.metadata import distribution
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from joserfc import jwt
from joserfc.jwk import ECKey

ISSUER = "https://synthetic-issuer.example"
AUDIENCE = "synthetic-gateway"
ORIGIN = "https://gateway:8443"


def put(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def prepare(root: Path) -> dict:
    os.umask(0o077)
    for name in ("authority", "gateway", "tls", "a", "b", "probe"):
        (root / name).mkdir(mode=0o700)
    issuer = ECKey.generate_key("P-256")
    names = ("service-a", "service-b", "caller-a", "caller-limited")
    keys = {name: ECKey.generate_key("P-256") for name in names}
    now = int(time.time())
    put(root / "authority/issuer.json", issuer.as_dict(private=True))
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic Witness CA")])
    not_before = datetime.now(timezone.utc) - timedelta(minutes=1)
    not_after = datetime.now(timezone.utc) + timedelta(days=1)
    ca = (
        x509.CertificateBuilder().subject_name(ca_subject).issuer_name(ca_subject)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(not_before).not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                       critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    tls_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-gateway")])
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(ca_subject)
        .public_key(tls_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(not_before).not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("gateway")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(tls_key.public_key()),
                       critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert = certificate.public_bytes(serialization.Encoding.PEM)
    (root / "tls/cert.pem").write_bytes(cert)
    (root / "tls/key.pem").write_bytes(tls_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    for name, key in keys.items():
        access = jwt.encode(
            {"alg": "ES256", "typ": "at+jwt", "kid": "synthetic-1"},
            {"iss": ISSUER, "sub": name, "aud": AUDIENCE, "iat": now,
             "exp": now + 3600, "cnf": {"jkt": key.thumbprint()}}, issuer)
        directory = root / ("a" if name == "service-a" else "b")
        if name.startswith("caller"):
            directory = root / "probe"
        put(directory / (name + ".key.json"), key.as_dict(private=True))
        (directory / (name + ".token")).write_text(access, encoding="ascii")
        # Negative tests use explicitly synthetic service credentials, never host auth.
        if name.startswith("service"):
            put(root / "probe" / (name + ".key.json"), key.as_dict(private=True))
            (root / "probe" / (name + ".token")).write_text(access, encoding="ascii")
    for name in ("a", "b", "probe"):
        (root / name / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    document = {
        "schema": "fleet-manager.gateway-service-policy", "version": 1,
        "manifest": {
            "schema": "fleet-manager.gateway-manifest", "version": 1,
            "fleet": "synthetic-fleet", "gateway": "synthetic-gateway", "revision": 1,
            "roles": ["service", "assets"],
            "key_sources": [
                {"name": name, "provider": "owner-store", "reference": "keys/" + name}
                for name in names
            ],
            "slots": [
                {"name": "service-" + letter, "role": "service",
                 "principal": "service-" + letter, "key_source": "service-" + letter,
                 "routes": [
                     {"prefix": "/api/" + letter, "methods": ["GET"],
                      "capabilities": ["http", "response-stream"]},
                     {"prefix": "/ui/" + letter, "methods": ["GET"],
                      "capabilities": ["http"]},
                 ]} for letter in ("a", "b")
            ],
        },
        "identity": {
            "issuer": ISSUER, "audience": AUDIENCE,
            "jwks": {"keys": [{**issuer.as_dict(private=False), "kid": "synthetic-1"}]},
            "trust_valid_until": now + 3600,
        },
        "bindings": [
            {"provider": "owner-store", "reference": "keys/" + name,
             "jwk": key.as_dict(private=False)} for name, key in keys.items()
        ],
        "callers": [
            {"principal": "caller-a", "key_source": "caller-a",
             "slots": ["service-a", "service-b"], "methods": ["GET"]},
            {"principal": "caller-limited", "key_source": "caller-limited",
             "slots": ["service-a"], "methods": ["GET"]},
        ],
    }
    put(root / "gateway/policy.json", document)
    put(root / "gateway/listener.json", {
        "schema": "fleet-manager.gateway-service-listener", "version": 1,
        "fleet": "synthetic-fleet", "gateway": "synthetic-gateway",
        "host": "0.0.0.0", "port": 8443, "origin": ORIGIN,
        "certificate": "/tls/cert.pem", "key": "/tls/key.pem", "remote": True,
    })
    package = distribution("fleet-manager")
    direct = json.loads(package.read_text("direct_url.json") or "{}")
    if direct.get("dir_info", {}).get("editable") or not direct.get("url", "").endswith(".whl"):
        raise RuntimeError("fixture-requires-installed-wheel")
    return {"result": "pass", "synthetic": True, "distribution": package.version,
            "editable": False, "issuer_process": False,
            "machine": platform.machine(), "platform": sys.platform}


if __name__ == "__main__":
    try:
        print(json.dumps(prepare(Path(sys.argv[1]))))
    except Exception:
        # Offline key creation must never leak local variables in diagnostics.
        print(json.dumps({"result": "fail", "code": "synthetic-preparation-failed"}))
        raise SystemExit(1) from None
