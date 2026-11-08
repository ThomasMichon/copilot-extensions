"""Bounded credential consumption by an explicitly enrolled worker."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .credential_references import read_credential_reference as _file
from .gateway_client import CredentialMaterial, Discovery, GatewayClient, consume_credential
from .gateway_contract import (
    ForgeScope, GatewayError, credential, fields, number, parse, require, text, validate_jwks,
)
from .planning import digest


class CredentialWorker:
    def __init__(
        self, client: GatewayClient, *, service: str, installation: str, generation: int,
        recipient_key: Any, controller: str, controller_generation: int, controller_keys: dict,
    ):
        self.client = client
        self.service, self.installation = text(service), text(installation)
        self.generation = number(generation)
        self.recipient_key = recipient_key
        self.controller = text(controller)
        self.controller_generation = number(controller_generation)
        validate_jwks(controller_keys)
        self.controller_keys = controller_keys

    def acquire(self, parameters: dict, *, timeout: float = 30) -> CredentialMaterial:
        require(type(timeout) in (float, int) and 0 < timeout <= 60, "invalid-deadline")
        parameters = credential(parameters)
        require(parameters["recipient"] == self.client.principal, "recipient-denied")
        deadline = time.monotonic() + timeout
        if self.client.epoch is None:
            self.client.connect(deadline=deadline)
        value = {
            "request_id": secrets.token_hex(16), "service_id": self.service,
            "installation_id": self.installation, "generation": self.generation,
            "operation": "credential.get", "parameters": dict(parameters),
        }
        require(time.monotonic() < deadline, "credential-unavailable")
        submitted = self.client.request_credential(value, deadline=deadline)
        fields(submitted, {"state", "request_id", "request_digest", "expires_at"})
        require(
            submitted["state"] == "PENDING" and submitted["request_id"] == value["request_id"]
            and submitted["request_digest"] == digest(value), "credential-binding-mismatch",
        )
        expiry = number(submitted["expires_at"])
        while time.monotonic() < deadline and time.time() < expiry:
            result = self.client.credential_result(value["request_id"], digest(value), deadline=deadline)
            if result.get("state") == "READY":
                material = consume_credential(
                    self.client, value, result, recipient_key=self.recipient_key,
                    controller_keys=self.controller_keys, controller=self.controller,
                    controller_generation=self.controller_generation,
                    deadline=deadline,
                )
                require(material.expires_at > time.time(), "credential-expired")
                require(time.monotonic() <= deadline, "credential-unavailable")
                return material
            fields(result, {"state"})
            require(result["state"] in ("PENDING", "DISPATCHED"), "credential-unavailable")
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise GatewayError("credential-unavailable")


def _configured_provider(
    path: Path, *, source: str, account: str, forge: ForgeScope | None = None,
    timeout: float = 30,
) -> Callable[[], CredentialMaterial]:
    from jwcrypto import jwk
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey, SECP256R1

    value = parse(_file(str(path)))
    fields(value, {
        "schema", "version", "discovery", "principal", "service", "installation", "generation",
        "proof_key", "access_token_file", "recipient_key", "controller", "controller_generation",
        "controller_keys", "bindings",
    })
    require(value["schema"] == "fleet-manager.credential-worker"
            and type(value["version"]) is int and value["version"] == 1)
    bindings = value["bindings"]
    require(type(bindings) is list and 0 < len(bindings) <= 128)
    matches = []
    identities = set()
    for binding in bindings:
        credential(binding)
        if forge is None:
            require("forge" not in binding, "typed-forge-consumer-required")
            target = None
        else:
            require("forge" in binding, "typed-forge-policy-required")
            target = ForgeScope.load(binding["forge"])
        identity = (binding["source"], binding["account"], target)
        require(identity not in identities, "ambiguous-provider-policy")
        identities.add(identity)
        require(binding["recipient"] == value["principal"], "recipient-denied")
        if identity == (source, account, forge):
            matches.append(binding)
    require(len(matches) == 1, "provider-policy-denied")
    parameters = matches[0]
    try:
        proof = serialization.load_pem_private_key(_file(value["proof_key"]), password=None)
        recipient = jwk.JWK.from_pem(_file(value["recipient_key"]))
    except (ValueError, TypeError):
        raise GatewayError("credential-key-unavailable") from None
    require(isinstance(proof, EllipticCurvePrivateKey) and isinstance(proof.curve, SECP256R1),
            "credential-key-unavailable")
    require(recipient.has_private and recipient.get("kty") == "RSA"
            and 2048 <= recipient.get_op_key("decrypt").key_size <= 4096,
            "credential-key-unavailable")
    discovery_path = Path(text(value["discovery"], 2048)).expanduser()
    require(discovery_path.is_absolute(), "absolute-reference-required")

    def access() -> str:
        try:
            return text(_file(value["access_token_file"]).decode("utf-8").strip(), 16384)
        except UnicodeError:
            raise GatewayError("credential-reference-unavailable") from None

    client = GatewayClient(Discovery.load(discovery_path), text(value["principal"]), proof, access)
    worker = CredentialWorker(
        client, service=value["service"], installation=value["installation"],
        generation=value["generation"], recipient_key=recipient, controller=value["controller"],
        controller_generation=value["controller_generation"], controller_keys=value["controller_keys"],
    )

    def acquire() -> CredentialMaterial:
        return worker.acquire(parameters, timeout=timeout)

    return acquire


def token_provider(path: Path, *, source: str, account: str) -> Callable[[], str]:
    """Legacy index consumption; never discard a forge credential's typed binding."""
    acquire = _configured_provider(path, source=source, account=account)
    return lambda: acquire().token


def credential_provider(
    path: Path, *, source: str, account: str, forge: ForgeScope, timeout: float = 30,
) -> Callable[[], CredentialMaterial]:
    """Load an exact typed forge binding; retain scope, auth type and expiry on renewal."""
    require(isinstance(forge, ForgeScope), "typed-forge-policy-required")
    return _configured_provider(path, source=source, account=account, forge=forge, timeout=timeout)
