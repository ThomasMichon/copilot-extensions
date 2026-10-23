"""Real file/key/token construction, with synthetic external credential material."""

import asyncio

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from fleet_manager.credential_references import (
    load_service_credentials, read_credential_reference,
)
from fleet_manager.gateway_contract import MAX_WIRE, GatewayError


def _key(path, key):
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))


def test_service_credentials_read_actual_rotated_token_not_a_cached_value(tmp_path):
    proof, access = tmp_path / "proof.pem", tmp_path / "access"
    _key(proof, ec.generate_private_key(ec.SECP256R1()))
    credentials = load_service_credentials(proof_key_file=proof, access_token_file=access)
    with pytest.raises(GatewayError, match="credential-reference-unavailable"):
        asyncio.run(credentials.access_token())
    access.write_text("synthetic-first\n")
    assert asyncio.run(credentials.access_token()) == "synthetic-first"
    access.write_text("synthetic-renewed\n")
    assert asyncio.run(credentials.access_token()) == "synthetic-renewed"
    assert credentials.proof_key.private_numbers()
    access.write_bytes(b"\xff")
    with pytest.raises(GatewayError, match="credential-reference-unavailable"):
        asyncio.run(credentials.access_token())
    access.write_bytes(b"x" * (MAX_WIRE + 1))
    with pytest.raises(GatewayError, match="wire-limit"):
        asyncio.run(credentials.access_token())


@pytest.mark.parametrize("key", [
    ec.generate_private_key(ec.SECP384R1()),
    rsa.generate_private_key(public_exponent=65537, key_size=2048),
    None,
])
def test_service_credentials_refuse_non_p256_or_invalid_keys(tmp_path, key):
    proof = tmp_path / "proof.pem"
    if key is None:
        proof.write_text("not a private key")
    else:
        _key(proof, key)
    with pytest.raises(GatewayError, match="credential-key-unavailable"):
        load_service_credentials(proof_key_file=proof, access_token_file=tmp_path / "access")


def test_reference_refuses_relative_and_missing_paths_without_echoing_values(tmp_path):
    with pytest.raises(GatewayError, match="absolute-reference-required"):
        read_credential_reference("relative")
    with pytest.raises(GatewayError) as error:
        read_credential_reference(tmp_path / "sensitive-reference")
    assert str(error.value) == "credential-reference-unavailable"


@pytest.mark.parametrize("value", ["", "x" * 8193, "token\nembedded"])
def test_service_token_refuses_empty_oversized_and_control_values(tmp_path, value):
    proof, access = tmp_path / "proof.pem", tmp_path / "access"
    _key(proof, ec.generate_private_key(ec.SECP256R1()))
    access.write_text(value)
    credentials = load_service_credentials(proof_key_file=proof, access_token_file=access)
    with pytest.raises(GatewayError):
        asyncio.run(credentials.access_token())
