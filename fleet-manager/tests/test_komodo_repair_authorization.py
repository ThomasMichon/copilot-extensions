"""Pure fresh-proof and exact-handle controls; no native or persistence effects."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_manager.komodo_repair import KomodoRepairOwner
from fleet_manager.komodo_repair_authorization import RepairAuthorization
from fleet_manager.machine_identity import (
    MachineBinding, MachineIdentityVerifier, PROFILE, make_machine_proof,
)
from fleet_manager.planning import canonical
from fleet_manager.runtime_admission import AdmissionAuthority, AdmissionError, OperationGrant
from fleet_manager.runtime_identity import IdentityError
from test_komodo_declaration import intent
from test_runtime_admission import actor, policy
from test_runtime_identity import Clock
from test_runtime_identity import keys as keys


class CheckpointHarness:
    def __init__(self, keys: Any) -> None:
        self.keys, self.clock = keys, Clock()
        self.request = intent("retained-original", generation=1, deployment="e" * 64)
        self.binding = MachineBinding(
            "principal-1", "caller", "client-a", canonical(keys[1].as_dict(private=False)),
            self.clock() + 1000)
        self.verifier = MachineIdentityVerifier(
            audience="gateway-a", bindings=(self.binding,), clock=self.clock,
            trust_valid_until=self.clock() + 1000)
        self.current = policy(
            issuer=PROFILE, audience="gateway-a", identity_profile=PROFILE,
            enrollments=(actor(operations=(
                OperationGrant("manager.converge", "host-a", 1, self.request.request_digest),
            )),))
        self.produced = self.resolved = 0
        self.mode = ""
        self.resolution = SimpleNamespace(
            identity=None, request=self.request, role="client", installation="client-a",
            refresh=self.refresh)
        self.admission = AdmissionAuthority(snapshot=self.snapshot, clock=self.clock)
        self.owner = KomodoRepairOwner(
            resolve=lambda *args: None, admission=self.admission, authority="gateway-a",
            clock=self.clock, fresh_identity=self.produce)
        identity = self.identity()
        self.resolution.identity = identity
        permit = self.admission.authorize(
            identity, self.request, role="client", installation="client-a",
            checkpoint=self.refresh)
        self.authorization = RepairAuthorization(
            self.owner, self.resolution, identity, permit, "succeeded")

    def refresh(self) -> None:
        self.resolved += 1

    def snapshot(self) -> Any:
        if not self.verifier.current(self.resolution.identity):
            raise AdmissionError("invalid-key-binding")
        return self.current

    def identity(self, *, action: str = "succeeded", **changes: Any) -> Any:
        target = "https://gateway.example/repair/" + action
        body = self.request.encoded
        parameters = dict(
            principal="principal-1", audience="gateway-a", kind="caller",
            resource="client-a", method="POST", target_uri=target,
            nonce=self.verifier.nonce, body=body, now=self.clock())
        parameters.update(changes)
        proof = make_machine_proof(self.keys[1], **parameters)
        return self.verifier.verify(
            "principal-1", proof, method="POST", target_uri="https://gateway.example/repair/succeeded",
            kind="caller", resource="client-a", content_digest=hashlib.sha256(body).hexdigest())

    def produce(self, original: Any, request: Any, role: str,
                installation: str, action: str) -> Any:
        self.produced += 1
        assert not self.admission._issued
        assert (original.subject, request, role, installation, action) == (
            "principal-1", self.request, "client", "client-a", "succeeded")
        if self.mode == "unavailable":
            raise IdentityError("fixture-identity-unavailable")
        if self.mode == "reused":
            return original
        if self.mode == "acquisition-expiry":
            identity = self.identity()
            self.clock.value += 1001
            return identity
        changes = {
            "body": {"body": b"other"},
            "nonce": {"nonce": "x" * 43},
            "action": {"action": "failed"},
            "resource": {"resource": "other"},
        }.get(self.mode, {})
        identity = self.identity(**changes)
        if self.mode in ("subject", "audience", "issuer", "proof_key", "profile", "kind", "binding"):
            value = replace(self.binding, expires_at=self.binding.expires_at + 1) if (
                self.mode == "binding") else "other"
            identity = replace(identity, **{self.mode: value})
        return identity


def test_distinct_permits_outlive_total_without_extending_any_handle(keys: Any) -> None:
    harness = CheckpointHarness(keys)
    authorization = harness.authorization
    original = authorization.permit
    authorization.revalidate()
    authorization.end()
    for _ in range(5):
        retired = authorization.permit
        harness.clock.value += 31
        authorization.begin()
        current = authorization.permit
        assert current is not retired and current.expires_at == current.issued_at + 30
        assert original.expires_at == original.issued_at + 30
        with pytest.raises(AdmissionError, match="unknown-permit"):
            harness.admission.revalidate(retired, harness.request)
        harness.clock.value += 10
        authorization.revalidate()
        assert authorization.permit.expires_at == current.expires_at
        authorization.end()
    authorization.begin()
    authorization.complete()
    assert harness.produced == 6 and not harness.admission._issued
    assert harness.clock() > original.issued_at + 30
    assert original.expires_at == original.issued_at + 30


def test_one_phase_has_one_fresh_decision_and_two_current_boundaries(keys: Any) -> None:
    harness = CheckpointHarness(keys)
    authorization = harness.authorization
    authorization.revalidate()
    authorization.end()
    before = harness.resolved
    authorization.begin()
    authorization.revalidate()
    harness.clock.value += 10
    authorization.revalidate()
    authorization.end()
    assert harness.produced == 1 and harness.resolved - before == 6
    assert len(harness.admission._issued) == 1
    authorization.abort()
    assert not harness.admission._issued


@pytest.mark.parametrize("mode", [
    "unavailable", "reused", "acquisition-expiry", "body", "nonce", "action", "resource",
    "subject", "audience", "issuer", "proof_key", "profile", "kind", "binding",
])
def test_failed_acquisition_aborts_without_old_identity_or_permit_fallback(
    keys: Any, mode: str,
) -> None:
    harness = CheckpointHarness(keys)
    authorization = harness.authorization
    original = authorization.permit
    authorization.revalidate()
    authorization.end()
    harness.mode = mode
    with pytest.raises((IdentityError, AdmissionError)):
        authorization.begin()
    assert authorization.failed and not harness.admission._issued
    assert original.expires_at == original.issued_at + 30
    with pytest.raises(AdmissionError, match="repair-phase-unavailable"):
        authorization.begin()
    assert harness.produced == 1


@pytest.mark.parametrize("stage", ["phase", "completion"])
@pytest.mark.parametrize("change", ["expiry", "grant", "enrollment", "key", "policy"])
def test_withdrawal_inside_phase_aborts_before_any_fresh_acquisition(
    keys: Any, stage: str, change: str,
) -> None:
    harness = CheckpointHarness(keys)
    authorization = harness.authorization
    authorization.revalidate()
    authorization.end()
    authorization.begin()
    if change == "expiry":
        harness.clock.value += 31
    elif change == "grant":
        harness.current = replace(harness.current, revision=2, enrollments=(
            actor(operations=()),))
    elif change == "enrollment":
        harness.current = replace(harness.current, revision=2, enrollments=(
            replace(harness.current.enrollments[0], generation=2),))
    elif change == "key":
        harness.verifier.refresh(MachineIdentityVerifier(
            audience="gateway-a", bindings=(), clock=harness.clock,
            trust_valid_until=harness.binding.expires_at))
    else:
        harness.current = replace(harness.current, revision=2, valid_until=harness.clock())
    with pytest.raises(AdmissionError):
        (authorization.complete if stage == "completion" else authorization.revalidate)()
    assert authorization.failed and harness.produced == 1 and not harness.admission._issued
    authorization.end()
    with pytest.raises(AdmissionError, match="repair-phase-unavailable"):
        authorization.begin()


def test_fresh_enrollment_cannot_replace_original_actor_binding(keys: Any) -> None:
    harness = CheckpointHarness(keys)
    authorization = harness.authorization
    authorization.revalidate()
    authorization.end()
    enrolled = harness.current.enrollments[0]
    harness.current = replace(harness.current, revision=2, enrollments=(
        replace(enrolled, generation=2),))
    with pytest.raises(AdmissionError, match="repair-enrollment-changed"):
        authorization.begin()
    assert not harness.admission._issued and authorization.failed
    with pytest.raises(AdmissionError, match="identity-reused"):
        harness.admission.authorize(
            harness.resolution.identity, harness.request, role="client", installation="client-a")


def test_registration_is_code_only_and_baseline_remains_optional(keys: Any) -> None:
    harness = CheckpointHarness(keys)
    with pytest.raises(ValueError, match="code-registered"):
        KomodoRepairOwner(
            resolve=lambda *args: None, admission=harness.admission, authority="gateway-a",
            clock=harness.clock, fresh_identity="wire-selected-producer")
    baseline = KomodoRepairOwner(
        resolve=lambda *args: None, admission=harness.admission, authority="gateway-a",
        clock=harness.clock)
    assert baseline.fresh_identity is None
