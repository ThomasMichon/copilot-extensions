"""Delegated first touch composed by the existing Gateway policy/journal owner."""

from __future__ import annotations

from collections.abc import Callable
import copy
import hashlib
import hmac
import re

from . import enrollment_journal, planning
from .gateway_service_context import (
    METHODS, ServiceContext, ServiceVerifier, fields, identifier, items, parse_context, require,
)
from .ledger import OperationLedger
from .machine_identity import MachineBinding, MachineIdentityVerifier
from .operations import OperationIntent
from .runtime_identity import _https, _public_key, _time

SCHEMA = "fleet-manager.gateway-enrollment-policy"
SCOPE_FIELDS = {
    "source", "account", "container", "harness", "gateway", "destination", "machine",
    "principal", "generation", "slots", "methods", "expires_at",
}

def enrollment_scope(value: object) -> dict:
    """Validate source-independent scope before any first-touch state is created."""
    scope = fields(value, SCOPE_FIELDS)
    for name in SCOPE_FIELDS - {"destination", "slots", "methods", "expires_at", "generation"}:
        identifier(scope[name])
    require(_https(scope["destination"])
            and scope["destination"].rstrip("/") == scope["destination"], "invalid-delegation")
    require(_time(scope["expires_at"]) and type(scope["generation"]) is int
            and 0 < scope["generation"] < 2**53, "invalid-delegation")
    slots = items(scope["slots"], 64, minimum=1)
    methods = items(scope["methods"], len(METHODS), minimum=1)
    require(all(isinstance(s, str) for s in slots)
            and len(set(slots)) == len(slots), "invalid-scope")
    for slot in slots:
        identifier(slot)
    require(all(isinstance(m, str) for m in methods)
            and len(set(methods)) == len(methods) and set(methods) <= METHODS, "invalid-scope")
    return scope


class EnrollmentPolicy:
    """No key issuer, sync provider, external effects or independent registry."""

    def __init__(self, value: object, ledger: OperationLedger,
                 clock: Callable[[], int]) -> None:
        root = fields(value, {"schema", "version", "policy", "delegations"})
        require(root["schema"] == SCHEMA and type(root["version"]) is int
                and root["version"] == 1, "unsupported-enrollment-schema")
        policy = root["policy"]
        require(isinstance(policy, dict) and policy.get("version") == 4,
                "unsupported-profile")
        self._policy = planning.canonical(policy)
        # Validate the base policy before accepting any source floor.
        context, _ = parse_context(self._policy, clock)
        self.fleet = context.manifest.fleet
        self.trust_valid_until = context.trust_valid_until
        self.gateway = context.manifest.gateway
        self.revision = context.manifest.revision
        require(self.revision < (2**53 - 1024) // 1024, "revision-limit")
        self.digest = planning.digest(root)
        self._ledger, self._clock = ledger, clock
        require(ledger._version == 5, "enrollment-journal-required")
        self._delegations: dict[str, bytes] = {}
        for value in items(root["delegations"], 128):
            item = fields(value, {"id", "grant_sha256", "grant_expires_at", "scope"})
            name = identifier(item["id"])
            require(name not in self._delegations, "duplicate-delegation")
            require(isinstance(item["grant_sha256"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", item["grant_sha256"]) is not None,
                    "invalid-delegation")
            require(_time(item["grant_expires_at"]), "invalid-delegation")
            scope = enrollment_scope(item["scope"])
            require(scope["gateway"] == self.gateway, "invalid-delegation")
            require(set(scope["slots"]) <= {s.name for s in context.manifest.slots},
                    "invalid-scope")
            require(scope["principal"] not in {c.principal for c in context.callers},
                    "principal-already-configured")
            require("machine-" + name not in {k.name for k in context.keys},
                    "key-source-already-configured")
            self._delegations[name] = planning.canonical(item)
        identity = policy["identity"]
        self._proofs = MachineIdentityVerifier(
            audience=identity["audience"], bindings=(),
            trust_valid_until=context.trust_valid_until, clock=clock)

    @property
    def nonce(self) -> str:
        return self._proofs.nonce

    def publication(self, eligible: Callable[[ServiceContext], None] | None = None
                    ) -> tuple[ServiceContext, ServiceVerifier]:
        """Validate then durably accept floors in the same journal transaction."""
        def validate(records: tuple[dict, ...]) -> tuple[ServiceContext, ServiceVerifier]:
            candidate = self._compose(records)
            if eligible is not None:
                eligible(candidate[0])
            return candidate

        return enrollment_journal.publish(
            self._ledger, gateway=self.gateway, revision=self.revision,
            fingerprint=self.digest, validate=validate, included=self._included)

    def _compose(self, records: tuple[dict, ...]) -> tuple[ServiceContext, ServiceVerifier]:
        require(len(records) <= 128, "enrollment-limit")
        policy = planning.decode(self._policy)
        policy["manifest"]["revision"] = self.revision * 1024 + len(records)
        for record in records:
            if not self._included(record):
                continue
            scope = record["scope"]
            name = "machine-" + record["delegation"]
            source = {"name": name, "provider": "operation-journal", "reference": name}
            policy["manifest"]["key_sources"].append(source)
            policy["bindings"].append({
                "provider": source["provider"], "reference": name, "jwk": record["public_key"]})
            policy["callers"].append({
                "principal": scope["principal"], "key_source": name,
                "slots": scope["slots"], "methods": scope["methods"]})
            for resource in scope["slots"]:
                policy["enrollment"]["mappings"].append({
                    "principal": scope["principal"], "key_source": name,
                    "kind": "caller", "resource": resource, "expires_at": scope["expires_at"]})
        return parse_context(planning.canonical(policy), self._clock)

    def _included(self, record: dict) -> bool:
        declaration = self._delegations.get(record["delegation"])
        if declaration is None:
            return False
        delegation = planning.decode(declaration)
        return (delegation["scope"] == record["scope"]
                and planning.digest(delegation) == record["delegation_digest"])

    def reconcile_publication(self, context: ServiceContext) -> None:
        """The serving owner calls this only after installing this exact context."""
        def validate(records: tuple[dict, ...]) -> None:
            candidate, _ = self._compose(records)
            require(candidate.digest == context.digest, "publication-changed")

        enrollment_journal.reconcile(
            self._ledger, gateway=self.gateway, revision=self.revision,
            fingerprint=self.digest, validate=validate, included=self._included)

    def enroll(self, raw: bytes, *, grant: str, proof: str,
               target_uri: str, destination: str) -> tuple[OperationIntent, str]:
        request = fields(planning.decode(raw), {"intent", "delegation", "scope", "public_key"})
        name = identifier(request["delegation"])
        declaration = self._delegations.get(name)
        require(declaration is not None, "enrollment-denied")
        assert declaration is not None
        delegation = planning.decode(declaration)
        require(isinstance(grant, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", grant) is not None,
                "enrollment-denied")
        require(hmac.compare_digest(hashlib.sha256(grant.encode("ascii")).hexdigest(),
                                    delegation["grant_sha256"]), "enrollment-denied")
        scope = delegation["scope"]
        now = self._clock()
        require(_time(now) and now < min(scope["expires_at"], delegation["grant_expires_at"]),
                "enrollment-denied")
        require(request["scope"] == scope and scope["destination"] == destination,
                "enrollment-denied")
        key = _public_key(request["public_key"])
        binding = {
            "delegation": name, "delegation_digest": planning.digest(delegation),
            "scope": copy.deepcopy(scope), "public_key": request["public_key"],
        }
        intent = OperationIntent.from_dict(request["intent"])
        require(intent.to_dict()["parameters"] == {"enrollment": planning.digest(binding)},
                "enrollment-intent-mismatch")
        enrolled = MachineBinding(
            scope["principal"], "caller", "enrollment",
            planning.canonical(request["public_key"]), scope["expires_at"])
        self._proofs._verify_binding(
            enrolled, key, proof, method="POST", target_uri=target_uri,
            content_digest=hashlib.sha256(raw).hexdigest())
        enrollment_journal.admit(
            self._ledger, intent, gateway=self.gateway, revision=self.revision,
            fingerprint=self.digest, binding=binding, thumbprint=key.thumbprint(),
            validate=self._compose)
        return intent, planning.digest(binding)
