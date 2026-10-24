"""Immutable service-profile validation; selected source custody is external.

This is the auth adapter's schema boundary, not provider/key discovery. The
common crypto engine owns both explicitly selected authentication profiles.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass

from . import planning
from .enrolled_identity import (
    EnrolledBinding, EnrolledIdentityVerifier, KINDS, MAX_AUDIENCES, MAX_ENROLLMENTS,
    PROFILE as ENROLLED_PROFILE,
)
from .gateway_contract import GatewayError, public_key
from .gateway_identity_provider import IdentityProvider, IdentityProviderError, prepare_identity
from .gateway_manifest import GatewayManifest, ManifestError, ServiceSlot
from .runtime_identity import IdentityError, IdentityVerifier, _text, _time

SCHEMA = "fleet-manager.gateway-service-policy"
MAX_BINDINGS = 128
MAX_CALLERS = 128
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


class ServicePolicyError(ValueError):
    """A static, value-free refusal; never includes credentials or config."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ServicePolicyError(code)


def fields(value: object, names: set[str]) -> dict[str, object]:
    require(isinstance(value, dict) and set(value) == names, "invalid-fields")
    assert isinstance(value, dict)
    return value


def items(value: object, maximum: int, *, minimum: int = 0) -> list[object]:
    require(isinstance(value, list) and minimum <= len(value) <= maximum,
            "collection-limit")
    assert isinstance(value, list)
    return value


def identifier(value: object) -> str:
    try:
        planning.identifier(value, "identifier")
    except planning.PlanError:
        raise ServicePolicyError("invalid-identifier") from None
    assert isinstance(value, str)
    return value


def _choices(value: object, allowed: set[str] | frozenset[str],
             maximum: int) -> tuple[str, ...]:
    selected = tuple(identifier(item) for item in items(value, maximum, minimum=1))
    require(len(set(selected)) == len(selected) and set(selected) <= allowed,
            "invalid-scope")
    return tuple(sorted(selected))


def _proof_key(value: object) -> str:
    require(isinstance(value, dict)
            and {"kty", "crv", "x", "y"} <= value.keys()
            and not value.keys() - {"kty", "crv", "x", "y", "kid"},
            "invalid-binding-key")
    assert isinstance(value, dict)
    try:
        public_key(value, "EC")
        for name in ("x", "y"):
            coordinate = value[name]
            require(isinstance(coordinate, str) and len(coordinate) == 43,
                    "invalid-binding-key")
            raw = base64.b64decode(coordinate + "=", altchars=b"-_", validate=True)
            require(len(raw) == 32 and base64.urlsafe_b64encode(raw).rstrip(
                b"=").decode("ascii") == coordinate, "invalid-binding-key")
        if "kid" in value:
            kid = value["kid"]
            require(isinstance(kid, str) and 0 < len(kid) <= 128
                    and all(32 < ord(char) < 127 for char in kid), "invalid-binding-key")
    except (GatewayError, ValueError, binascii.Error):
        raise ServicePolicyError("invalid-binding-key") from None
    try:
        from joserfc.errors import JoseError
        from joserfc.jwk import ECKey
    except ImportError:
        raise ServicePolicyError("unsupported-profile") from None
    try:
        key = ECKey.import_key(value)
        key.get_op_key("verify")  # Force maintained-library curve-point validation.
        return key.thumbprint()
    except (JoseError, ValueError, TypeError):
        raise ServicePolicyError("invalid-binding-key") from None


@dataclass(frozen=True)
class ResolvedKey:
    name: str
    provider: str
    reference: str
    thumbprint: str


@dataclass(frozen=True)
class CallerPolicy:
    principal: str
    key_source: str
    slots: tuple[str, ...]
    methods: tuple[str, ...]


@dataclass(frozen=True)
class ServiceContext:
    """No mutable JSON or verifier aliases cross this immutable boundary."""

    manifest: GatewayManifest
    digest: str
    trust_digest: str
    trust_valid_until: int
    keys: tuple[ResolvedKey, ...]
    callers: tuple[CallerPolicy, ...]
    slot_fingerprints: tuple[tuple[str, str], ...]
    caller_fingerprints: tuple[tuple[str, str], ...]

    def slot(self, name: str) -> ServiceSlot:
        result = next((item for item in self.manifest.slots if item.name == name), None)
        require(result is not None, "slot-unavailable")
        assert result is not None
        return result

    def key(self, name: str) -> ResolvedKey:
        return next(item for item in self.keys if item.name == name)

    def caller(self, principal: str) -> CallerPolicy:
        result = next((item for item in self.callers if item.principal == principal), None)
        require(result is not None, "caller-denied")
        assert result is not None
        return result

    def slot_fingerprint(self, name: str) -> str | None:
        return next((digest for slot, digest in self.slot_fingerprints if slot == name), None)

    def caller_fingerprint(self, principal: str) -> str | None:
        return next((digest for caller, digest in self.caller_fingerprints
                     if caller == principal), None)


def _enrollment(value: object, keys: tuple[ResolvedKey, ...], manifest: GatewayManifest,
                callers: list[CallerPolicy], audience: str
                ) -> tuple[dict[str, object], tuple[EnrolledBinding, ...]]:
    envelope = fields(value, {"allowed_audiences", "mappings"})
    audiences: list[str] = []
    for item in items(envelope["allowed_audiences"], MAX_AUDIENCES, minimum=1):
        if not isinstance(item, str) or not _text(item, 512):
            raise ServicePolicyError("invalid-enrollment")
        audiences.append(item)
    require(len(set(audiences)) == len(audiences) and audience in audiences,
            "invalid-enrollment")
    resolved = {key.name: key for key in keys}
    enrollments, normalized = [], []
    for value in items(envelope["mappings"], MAX_ENROLLMENTS):
        item = fields(value, {
            "subject", "client_id", "principal", "kind", "resource", "key_source", "expires_at",
        })
        subject, client, kind = item["subject"], item["client_id"], item["kind"]
        resource, expires = item["resource"], item["expires_at"]
        if (not isinstance(subject, str) or not _text(subject)
                or not isinstance(client, str) or not _text(client)
                or not isinstance(kind, str) or kind not in KINDS
                or not isinstance(resource, str) or resource != audience
                or type(expires) is not int or not _time(expires)):
            raise ServicePolicyError("invalid-enrollment")
        principal, source = identifier(item["principal"]), identifier(item["key_source"])
        require(source in resolved, "unresolved-key-source")
        if kind == "service":
            require(any(slot.principal == principal and slot.key_source == source
                        for slot in manifest.slots), "invalid-enrollment")
        else:
            require(any(caller.principal == principal and caller.key_source == source
                        for caller in callers), "invalid-enrollment")
        key = resolved[source]
        enrollments.append(EnrolledBinding(
            subject, client, principal, kind, resource, source, key.thumbprint, expires))
        normalized.append(dict(item, resolved_key={
            "provider": key.provider, "reference": key.reference, "thumbprint": key.thumbprint,
        }))
    return {
        "allowed_audiences": sorted(audiences),
        "mappings": sorted(normalized, key=planning.canonical),
    }, tuple(enrollments)


def parse_context(raw: bytes, clock: Callable[[], int], *,
                  identity_provider: IdentityProvider | None = None,
                  ) -> tuple[ServiceContext, IdentityVerifier]:
    """Validate a complete bounded candidate, including real keys, off-lock."""
    try:
        decoded = planning.decode(raw)
        names = {
            "schema", "version", "manifest", "identity", "bindings", "callers",
        }
        if isinstance(decoded, dict) and type(decoded.get("version")) is int \
                and decoded["version"] == 3:
            names.add("enrollment")
        root = fields(decoded, names)
        require(root["schema"] == SCHEMA and type(root["version"]) is int
                and root["version"] in (1, 2, 3), "unsupported-schema")
        manifest = GatewayManifest.load(root["manifest"])
        if root["version"] == 1:
            identity = fields(root["identity"], {"issuer", "audience", "jwks", "trust_valid_until"})
            jwks = fields(identity["jwks"], {"keys"})
            signing_keys = items(jwks["keys"], 16, minimum=1)
            normalized_identity = dict(identity, jwks={
                "keys": sorted(signing_keys, key=planning.canonical),
            })
            verifier = IdentityVerifier(**normalized_identity, clock=clock)
            trust_valid_until = identity["trust_valid_until"]
            assert type(trust_valid_until) is int
        else:
            normalized_identity, verifier, trust_valid_until = prepare_identity(
                root["identity"], identity_provider, clock,
                profile=ENROLLED_PROFILE if root["version"] == 3 else "jwt-pop-v1")
        mappings: dict[tuple[str, str], tuple[str, dict[str, object]]] = {}
        declared = {(item.provider, item.reference) for item in manifest.key_sources}
        for item in items(root["bindings"], MAX_BINDINGS):
            binding = fields(item, {"provider", "reference", "jwk"})
            provider, reference = binding["provider"], binding["reference"]
            require(isinstance(provider, str) and isinstance(reference, str),
                    "invalid-binding")
            assert isinstance(provider, str) and isinstance(reference, str)
            pair = (provider, reference)
            require(pair in declared, "unreferenced-binding")
            require(pair not in mappings, "duplicate-binding")
            thumbprint = _proof_key(binding["jwk"])
            assert isinstance(binding["jwk"], dict)
            mappings[pair] = (thumbprint, binding["jwk"])
        require(set(mappings) == declared, "unresolved-key-source")
        keys = tuple(ResolvedKey(item.name, item.provider, item.reference,
                                 mappings[item.provider, item.reference][0])
                     for item in manifest.key_sources)
        callers = []
        slot_names = {slot.name for slot in manifest.slots}
        source_names = {key.name for key in keys}
        for item in items(root["callers"], MAX_CALLERS):
            caller = fields(item, {"principal", "key_source", "slots", "methods"})
            principal, source = identifier(caller["principal"]), identifier(caller["key_source"])
            require(source in source_names, "unresolved-key-source")
            callers.append(CallerPolicy(principal, source,
                                        _choices(caller["slots"], slot_names, 64),
                                        _choices(caller["methods"], METHODS, len(METHODS))))
        require(len({item.principal for item in callers}) == len(callers), "duplicate-caller")
        callers.sort(key=lambda item: item.principal)
        normalized_callers = [
            {"principal": item.principal, "key_source": item.key_source,
             "slots": list(item.slots), "methods": list(item.methods)} for item in callers
        ]
        manifest_value = manifest.to_dict()
        normalized = {
            "schema": SCHEMA, "version": root["version"], "manifest": manifest_value,
            "identity": normalized_identity, "callers": normalized_callers,
            "bindings": [{"provider": provider, "reference": reference, "jwk": value[1]}
                         for (provider, reference), value in sorted(mappings.items())],
        }
        trust_epoch = normalized_identity
        if root["version"] == 3:
            trust = normalized_identity["trust"]
            assert isinstance(trust, dict)
            issuer, audience, expires = trust["issuer"], trust["audience"], trust["trust_valid_until"]
            assert isinstance(issuer, str) and isinstance(audience, str) and type(expires) is int
            enrollment, enrollments = _enrollment(
                root["enrollment"], keys, manifest, callers, audience)
            normalized["enrollment"] = enrollment
            audiences = enrollment["allowed_audiences"]
            assert isinstance(audiences, list)
            verifier = EnrolledIdentityVerifier(
                issuer=issuer, audience=audience, jwks=trust["jwks"],
                trust_valid_until=expires, clock=clock, allowed_audiences=tuple(audiences),
                enrollments=enrollments)
            trust_epoch = {"identity": normalized_identity, "enrollment": enrollment}
        key_by_name = {key.name: key for key in keys}

        def key_content(name: str) -> dict[str, str]:
            key = key_by_name[name]
            return {"name": key.name, "provider": key.provider, "reference": key.reference,
                    "thumbprint": key.thumbprint}

        slots = tuple((slot.name, planning.digest({
            "slot": value, "key": key_content(slot.key_source),
        })) for slot, value in zip(manifest.slots, items(manifest_value["slots"], 64)))
        scopes = tuple((caller.principal, planning.digest({
            "caller": value, "key": key_content(caller.key_source),
        })) for caller, value in zip(callers, normalized_callers))
        return ServiceContext(manifest, planning.digest(normalized),
                              planning.digest(trust_epoch),
                              trust_valid_until, keys, tuple(callers),
                              slots, scopes), verifier
    except planning.PlanError:
        raise ServicePolicyError("invalid-json") from None
    except ManifestError as exc:
        raise ServicePolicyError(exc.code) from None
    except IdentityError as exc:
        raise ServicePolicyError(exc.code) from None
    except IdentityProviderError:
        raise ServicePolicyError("provider-trust-unavailable") from None
