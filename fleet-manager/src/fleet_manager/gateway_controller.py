"""Authenticated subscribed controller. The external local provider retains custody."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from jwcrypto import jwe, jwk

from .gateway_auth import Signer, thumbprint, verify
from .gateway_client import CredentialMaterial, GatewayClient
from .gateway_contract import CREDENTIAL_ACTIONS, ForgeScope, GatewayError, encode, number, require
from .planning import digest


class CredentialProvider(Protocol):
    async def acquire(self, request: dict, *, timeout: float) -> CredentialMaterial | None:
        """Honor REAL audience/permission scope or return None (UNSUPPORTED).

        A request's source label does not downscope an opaque account token.
        Implementations must validate provider expiry/revocation and actual permissions.
        """
        ...


class RelaySource(Protocol):
    """Structural reuse of credential_relay.sources.CredentialSource, no host dependency."""
    def supports(self, action: str, fields: dict[str, str]) -> bool: ...
    async def resolve(self, action: str, fields: dict[str, str], *, timeout: float) -> str | None: ...


@dataclass(frozen=True)
class RelayPolicy:
    source: str
    account: str
    audience: str
    scopes: tuple[str, ...]
    action: str
    fields: dict[str, str]
    forge: ForgeScope | None = None


class RelayCredentialProvider:
    """Reuse an existing source only with explicit policy and provider-result verification.

    The verifier is the external custodian's audience/permission/revocation boundary.
    There is intentionally no default that relabels a broad relay token as scoped.
    """
    def __init__(self, source: RelaySource, policies: tuple[RelayPolicy, ...],
                 verify_material: Callable[[str, RelayPolicy], CredentialMaterial | None]):
        for policy in policies:
            require(policy.action in CREDENTIAL_ACTIONS, "unsupported-action")
            require(set(policy.fields) <= {"host", "resource", "tenant", "scope"}, "invalid-relay-fields")
            require(policy.forge is None or type(policy.forge) is ForgeScope, "invalid-forge-binding")
        self.source, self.policies, self.verify_material = source, policies, verify_material

    async def acquire(self, request: dict, *, timeout: float) -> CredentialMaterial | None:
        policy = next((p for p in self.policies if all(
            getattr(p, k) == (tuple(request[k]) if k == "scopes" else request[k])
            for k in ("source", "account", "audience", "scopes", "action"))
            and (p.forge.to_dict() if p.forge is not None else None) == request.get("forge")), None)
        if policy is None or not self.source.supports(policy.action, policy.fields):
            return None
        raw = await self.source.resolve(policy.action, dict(policy.fields), timeout=timeout)
        return None if raw is None else self.verify_material(raw, policy)


async def _provider_result(provider: CredentialProvider, parameters: dict,
                           timeout: float) -> CredentialMaterial | BaseException | None:
    async def acquire() -> CredentialMaterial | None:
        return await provider.acquire(parameters, timeout=timeout)

    return (await asyncio.gather(acquire(), return_exceptions=True))[0]


class Controller:
    """Blocking/foreground resident loop; deployment/lifetime belongs to the custodian."""
    def __init__(self, client: GatewayClient, provider: CredentialProvider, signer: Signer,
                 *, fleet: str, generation: int, lease_seconds: int = 10):
        require(2 <= lease_seconds <= 60)
        self.client, self.provider, self.signer = client, provider, signer
        self.fleet, self.generation, self.lease_seconds = fleet, generation, lease_seconds
        self.subscription: str | None = None

    def start(self) -> None:
        self.client.connect()
        self.subscription = self.client.subscribe(self.lease_seconds)["subscription"]

    def step(self) -> str:
        require(self.subscription is not None, "subscription-required")
        self.client.subscribe(self.lease_seconds, self.subscription)
        result = self.client.poll(self.subscription)
        if result["state"] == "IDLE":
            return "IDLE"
        claims = verify(result["dispatch"], self.client.discovery.jwks, self.client.discovery.issuer,
                        self.client.principal, lifetime=60)
        dispatch_deadline = min(claims["exp"], number(claims.get("deadline"), 0))
        require(claims["sub"] == self.client.discovery.gateway and claims.get("fleet") == self.fleet
                and claims.get("epoch") == self.client.epoch
                and claims.get("controller") == self.client.principal
                and claims.get("controller_generation") == self.generation
                and claims.get("subscription") == self.subscription, "dispatch-binding-mismatch")
        binding = {k: v for k, v in claims.items() if k not in
                   {"iss", "aud", "sub", "iat", "nbf", "exp", "jti",
                    "recipient_jwk", "deadline", "subscription"}}
        recipient = claims["recipient_jwk"]
        require(thumbprint(recipient) == binding["recipient_jkt"], "recipient-mismatch")
        remaining = dispatch_deadline - time.time()
        require(remaining > 0, "dispatch-expired")
        try:
            material = asyncio.run(asyncio.wait_for(
                _provider_result(self.provider, dict(binding["parameters"]), remaining),
                timeout=remaining))
        except (asyncio.TimeoutError, TimeoutError):
            # The acquisition budget is exhausted even if wall time moved back.
            return "UNKNOWN"
        else:
            if isinstance(material, BaseException):
                if not isinstance(material, Exception):
                    raise material
                material = None
                state = "UNAVAILABLE"
            else:
                state = "UNSUPPORTED" if material is None else "READY"
        # Acquisition can cross expiry; do not reuse its earlier clock sample.
        ttl = dispatch_deadline - int(time.time())
        if ttl <= 0:
            return "UNKNOWN"
        reply = {"binding": binding, "state": state}
        if material is not None:
            require(isinstance(material, CredentialMaterial), "provider-contract-invalid")
            parameters = binding["parameters"]
            forge = ForgeScope.load(parameters["forge"]) if "forge" in parameters else None
            supported = (all(getattr(material, k) == parameters[k] for k in ("source", "account", "audience"))
                         and material.scopes == tuple(parameters["scopes"])
                         and material.forge == forge
                         and type(material.expires_at) is int and time.time() < material.expires_at)
            if not supported:
                receipt = self.signer.sign({"binding": binding, "state": "UNSUPPORTED"}, binding["worker"], ttl,
                                           expires_at=dispatch_deadline)
                self.client.reply(self.subscription, binding["worker"], binding["request_id"], receipt)
                return "UNSUPPORTED"
            from .gateway_contract import text

            text(material.token, 16384)
            protected = encode({"alg": "RSA-OAEP-256", "enc": "A256GCM", "typ": "fleet-credential-v1",
                                "recipient": binding["recipient_jkt"], "binding": digest(binding)})
            effective_expiry = min(material.expires_at, dispatch_deadline)
            plain = encode({"binding": binding, "token": material.token, "expires_at": effective_expiry,
                            "source": material.source, "account": material.account,
                            "audience": material.audience, "scopes": list(material.scopes),
                            **({"forge": forge.to_dict()} if forge is not None else {})})
            envelope = jwe.JWE(plain, protected=protected, algs=["RSA-OAEP-256", "A256GCM"])
            envelope.add_recipient(jwk.JWK(**recipient))
            sealed = envelope.serialize(compact=True)
            ttl = min(ttl, effective_expiry - int(time.time()))
            reply |= {"sealed": sealed, "sealed_digest": hashlib.sha256(sealed.encode()).hexdigest()}
        receipt = self.signer.sign(reply, binding["worker"], ttl, expires_at=dispatch_deadline)
        self.client.reply(self.subscription, binding["worker"], binding["request_id"], receipt)
        return state

    def close(self) -> None:
        if self.subscription is not None:
            self.client.disconnect(self.subscription)
            self.subscription = None

    def serve(self, stop) -> str:
        """Stop must be externally owned; reconnect is explicit, never uncertain redelivery."""
        self.start()
        try:
            while not stop.wait(0.25):
                self.step()
        except GatewayError:
            return "UNKNOWN"
        finally:
            try:
                self.close()
            except GatewayError:
                pass  # Disconnect already fenced by the bounded subscription lease.
        return "STOPPED"
