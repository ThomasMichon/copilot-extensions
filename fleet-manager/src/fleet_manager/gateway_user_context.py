"""Version-five caller composition; old service authentication is independent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from . import planning
from .bearer_identity import (
    MAX_CLAIMS, MAX_MAPPINGS, MAX_VALUES, PROFILE, BearerIdentityVerifier,
    ClaimRequirement, PrincipalMapping,
)
from .gateway_identity_provider import (
    IdentityProvider, IdentityProviderError, prepare_trust,
)
from .gateway_manifest import GatewayManifest, ManifestError
from .gateway_service_context import (
    MAX_CALLERS, METHODS, SCHEMA, ServiceContext, ServicePolicyError, ServiceVerifier,
    UserCallerPolicy, _choices, fields, identifier, items, parse_context, require,
)
from .runtime_identity import IdentityError


def parse_user_context(
    raw: bytes, clock: Callable[[], int], *,
    identity_provider: IdentityProvider | None,
    user_identity_provider: IdentityProvider | None,
) -> tuple[ServiceContext, ServiceVerifier | None, BearerIdentityVerifier]:
    """Validate a complete explicit publication without source or provider I/O."""
    try:
        root = fields(planning.decode(raw), {
            "schema", "version", "manifest", "service", "user_callers"})
        require(root["schema"] == SCHEMA and type(root["version"]) is int
                and root["version"] == 5, "unsupported-schema")
        manifest = GatewayManifest.load(root["manifest"])
        require(manifest.version == 2, "unsupported-schema")
        base, service_verifier = None, None
        remote_slots = tuple(slot for slot in manifest.slots if slot.key_source is not None)
        if root["service"] is not None:
            base, service_verifier = parse_context(
                planning.canonical(root["service"]), clock, identity_provider=identity_provider)
            require(
                (base.manifest.fleet, base.manifest.gateway, base.manifest.revision)
                == (manifest.fleet, manifest.gateway, manifest.revision)
                and base.manifest.slots == remote_slots
                and base.manifest.key_sources == manifest.key_sources,
                "service-manifest-mismatch")
        else:
            require(not remote_slots and not manifest.key_sources, "service-policy-required")
        selected = fields(root["user_callers"], {
            "identity", "required_claims", "principal_claim", "mappings", "callers"})
        identity = prepare_trust(
            selected["identity"], user_identity_provider, profile=PROFILE)
        required = []
        for value in items(selected["required_claims"], MAX_CLAIMS):
            entry = fields(value, {"claim", "values"})
            required.append(ClaimRequirement(
                entry["claim"], tuple(items(entry["values"], MAX_VALUES, minimum=1))))
        mappings = []
        for value in items(selected["mappings"], MAX_MAPPINGS):
            entry = fields(value, {"subject", "principal"})
            mappings.append(PrincipalMapping(entry["subject"], identifier(entry["principal"])))
        trust = identity["trust"]
        verifier = BearerIdentityVerifier(
            **trust, clock=clock, principal_claim=selected["principal_claim"],
            required_claims=tuple(required), mappings=tuple(mappings))
        callers = []
        slot_names = {slot.name for slot in manifest.slots}
        for value in items(selected["callers"], MAX_CALLERS):
            entry = fields(value, {"principal", "slots", "methods"})
            callers.append(UserCallerPolicy(
                identifier(entry["principal"]), _choices(entry["slots"], slot_names, 64),
                _choices(entry["methods"], METHODS, len(METHODS))))
        require(len({item.principal for item in callers}) == len(callers), "duplicate-caller")
        require(base is None or not ({item.principal for item in callers}
                                    & {item.principal for item in base.callers}),
                "ambiguous-caller")
        require(all(item.principal in {caller.principal for caller in callers}
                    for item in mappings), "ungranted-user")
        callers.sort(key=lambda item: item.principal)
        normalized_callers = [
            {"principal": item.principal, "slots": list(item.slots), "methods": list(item.methods)}
            for item in callers]
        user_trust = {
            "identity": identity, "principal_claim": selected["principal_claim"],
            "required_claims": sorted(
                [{"claim": item.claim, "values": sorted(item.values)} for item in required],
                key=planning.canonical),
            "mappings": sorted(
                [{"subject": item.subject, "principal": item.principal} for item in mappings],
                key=planning.canonical),
        }
        expires = trust["trust_valid_until"]
        if base is None:
            base = ServiceContext(
                manifest, "", "", expires, (), (), (), ())
        slot_fingerprints = base.slot_fingerprints + tuple(
            (slot.name, planning.digest(value))
            for slot, value in zip(manifest.slots, manifest.to_dict()["slots"])
            if slot.key_source is None)
        scopes = base.caller_fingerprints + tuple(
            (caller.principal, planning.digest(value))
            for caller, value in zip(callers, normalized_callers))
        normalized = {
            "schema": SCHEMA, "version": 5, "manifest": manifest.to_dict(),
            "service": base.digest if root["service"] is not None else None,
            "user_callers": dict(user_trust, callers=normalized_callers),
        }
        return replace(
            base, manifest=manifest, digest=planning.digest(normalized),
            slot_fingerprints=slot_fingerprints, caller_fingerprints=scopes,
            user_callers=tuple(callers), user_trust_digest=planning.digest(user_trust),
            user_trust_valid_until=expires,
        ), service_verifier, verifier
    except planning.PlanError:
        raise ServicePolicyError("invalid-json") from None
    except (IdentityError, ManifestError) as exc:
        raise ServicePolicyError(exc.code) from None
    except IdentityProviderError:
        raise ServicePolicyError("provider-trust-unavailable") from None
