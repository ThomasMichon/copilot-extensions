"""Offline throwaway credentials for the fixed real-provider experiment."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from joserfc.jwk import ECKey

ISSUER = "https://issuer.fixture.test:9091"
AUDIENCE = "https://resource.fixture.test"
CLIENT = "qualification-client"
SIGNING_KID = "qualification-es256"


def private_write(path: Path, data: bytes) -> None:
    """Exclusive creation, including the public fixture, without a global umask."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def put(path: Path, value: object) -> None:
    private_write(path, (json.dumps(value, sort_keys=True) + "\n").encode())


def pem(key: object) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def client_digest(secret: str, salt: bytes) -> str:
    encode = lambda value: base64.b64encode(value).decode("ascii").rstrip("=").replace("+", ".")
    derived = hashlib.pbkdf2_hmac("sha512", secret.encode("ascii"), salt, 310000)
    return "$pbkdf2-sha512$310000$" + encode(salt) + "$" + encode(derived)


def prepare(root: Path) -> dict:
    for name in ("provider", "client"):
        (root / name).mkdir(mode=0o700)
    provider, client = root / "provider", root / "client"
    ca_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Qualification Fixture CA")])
    now = datetime.now(timezone.utc)
    ca = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
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
    for directory, hostname in ((provider, "issuer.fixture.test"), (client, "localhost")):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
            .issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        private_write(directory / "cert.pem", cert.public_bytes(serialization.Encoding.PEM))
        private_write(directory / "key.pem", pem(key).encode("ascii"))
        private_write(directory / "ca.pem", ca.public_bytes(serialization.Encoding.PEM))
    client_secret = secrets.token_urlsafe(48)
    proof_key = ECKey.generate_key("P-256")
    put(client / "inputs.json", {
        "client_secret": client_secret, "proof_key": proof_key.as_dict(private=True),
    })
    user_hash = Argon2id(
        salt=secrets.token_bytes(16), length=32, iterations=1, lanes=1, memory_cost=8192,
    ).derive_phc_encoded(secrets.token_bytes(32))
    put(provider / "users.yml", {"users": {
        "fixture": {"displayname": "Fixture User", "password": user_hash,
                    "email": "fixture@example.test", "groups": []},
    }})
    put(provider / "configuration.yml", {
        "server": {"address": "tcp://0.0.0.0:9091",
                   "disable_healthcheck": True,
                   "tls": {"certificate": "/config/cert.pem", "key": "/config/key.pem"}},
        "ntp": {"disable_startup_check": True},
        "log": {"level": "error", "format": "json"},
        "authentication_backend": {"file": {"path": "/config/users.yml"}},
        "access_control": {"default_policy": "deny", "rules": [
            {"domain": "issuer.fixture.test", "policy": "one_factor"},
        ]},
        "session": {"secret": secrets.token_urlsafe(48), "cookies": [
            {"domain": "fixture.test", "authelia_url": ISSUER},
        ]},
        "storage": {"encryption_key": secrets.token_urlsafe(48),
                    "local": {"path": "/state/authelia.sqlite3"}},
        "notifier": {"filesystem": {"filename": "/state/notification.txt"}},
        "identity_validation": {"reset_password": {"jwt_secret": secrets.token_urlsafe(48)}},
        "identity_providers": {"oidc": {
            "hmac_secret": secrets.token_urlsafe(48),
            "jwks": [
                {"key_id": "qualification-rs256", "algorithm": "RS256", "use": "sig",
                 "key": pem(rsa.generate_private_key(public_exponent=65537, key_size=2048))},
                {"key_id": SIGNING_KID, "algorithm": "ES256", "use": "sig",
                 "key": pem(ec.generate_private_key(ec.SECP256R1()))},
            ],
            "clients": [{
                "client_id": CLIENT, "client_name": "Protocol Qualification",
                "client_secret": client_digest(client_secret, secrets.token_bytes(16)),
                "public": False, "authorization_policy": "one_factor",
                "audience": [AUDIENCE], "requested_audience_mode": "explicit",
                "scopes": ["fixture.read"], "grant_types": ["client_credentials"],
                "response_types": ["code"], "token_endpoint_auth_method": "client_secret_post",
                "access_token_signed_response_alg": "ES256",
                "access_token_signed_response_key_id": SIGNING_KID,
            }],
        }},
    })
    return {"result": "prepared", "credentials": "synthetic-files-only"}


if __name__ == "__main__":
    try:
        result = prepare(Path(sys.argv[1]))
    except (OSError, ValueError, TypeError):
        print(json.dumps({"result": "fail", "code": "fixture-preparation-failed"}))
        raise SystemExit(1) from None
    print(json.dumps(result))
