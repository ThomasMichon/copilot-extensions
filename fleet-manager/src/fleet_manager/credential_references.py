"""Bounded external credential references; no issuer, ambient login or persistence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gateway_contract import MAX_WIRE, GatewayError, require, text


def read_credential_reference(path: Any) -> bytes:
    """Read one absolute operator-selected reference, including rotated file contents."""
    filename = Path(text(str(path) if isinstance(path, Path) else path, 2048)).expanduser()
    require(filename.is_absolute(), "absolute-reference-required")
    try:
        with filename.open("rb") as stream:
            raw = stream.read(MAX_WIRE + 1)
    except OSError:
        raise GatewayError("credential-reference-unavailable") from None
    require(len(raw) <= MAX_WIRE, "wire-limit")
    return raw


@dataclass(frozen=True)
class ServiceCredentials:
    """An external proof key and fresh access-token source for ServiceConnector.

    Loading is not enrollment, placement or proof of Gateway acceptance. Key
    rotation requires a new connector; access-token rotation is read on every
    admission. Neither token claims nor file labels grant local authority.
    """

    proof_key: Any
    access_token_file: Path

    async def access_token(self) -> str:
        raw = await asyncio.to_thread(read_credential_reference, self.access_token_file)
        try:
            return text(raw.decode("utf-8").strip(), 8192)
        except UnicodeError:
            raise GatewayError("credential-reference-unavailable") from None


def load_service_credentials(
    *, proof_key_file: Path, access_token_file: Path,
) -> ServiceCredentials:
    """Load an externally provisioned P-256 key, never a token from argv or environment."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey, SECP256R1

    filename = Path(text(str(access_token_file), 2048)).expanduser()
    require(filename.is_absolute(), "absolute-reference-required")
    try:
        proof = serialization.load_pem_private_key(
            read_credential_reference(proof_key_file), password=None,
        )
    except (ValueError, TypeError):
        raise GatewayError("credential-key-unavailable") from None
    require(isinstance(proof, EllipticCurvePrivateKey) and isinstance(proof.curve, SECP256R1),
            "credential-key-unavailable")
    return ServiceCredentials(proof, filename)
