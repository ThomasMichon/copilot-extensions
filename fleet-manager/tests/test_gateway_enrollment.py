"""Real delegation, durable source floors and original-operation recovery."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sqlite3

from joserfc.jwk import ECKey
import pytest

from fleet_manager import planning
from fleet_manager.gateway_enrollment import SCHEMA
from fleet_manager.gateway_service_policy import ServiceAuthority, ServicePolicyError
from fleet_manager.ledger import OperationLedger
from fleet_manager.operations import OperationIntent
from fleet_manager.machine_identity import make_machine_proof
from test_machine_identity import MachineHarness
from test_runtime_identity import AUDIENCE, NOW

DESTINATION = "https://gateway.example"
GRANT = "test-only-" + "g" * 33
assert len(GRANT) == 43


class EnrollmentHarness:
    def __init__(self, root: Path) -> None:
        self.h = MachineHarness()
        self.ledger = OperationLedger(
            root / "operations.sqlite", authority="authority-a", installation="installation-a",
            enable_enrollment=True)
        self.scope = {
            "source": "source-a", "account": "account-a", "container": "container-a",
            "harness": "harness-a", "gateway": "gateway-a", "destination": DESTINATION,
            "machine": "machine-a", "principal": "new-caller", "generation": 1,
            "slots": ["service-a"], "methods": ["GET", "POST"], "expires_at": NOW + 300,
        }
        self.delegation = {
            "id": "grant-a", "grant_sha256": hashlib.sha256(GRANT.encode()).hexdigest(),
            "grant_expires_at": NOW + 300,
            "scope": self.scope}
        self.document = {
            "schema": SCHEMA, "version": 1, "policy": self.h.document,
            "delegations": [self.delegation]}
        self.owner = self.restart()
        self.key = ECKey.generate_key("P-256")

    def restart(self) -> ServiceAuthority:
        owner = ServiceAuthority(
            fleet="fleet-a", gateway="gateway-a", clock=self.h.clock,
            source=lambda: planning.canonical(self.document), enrollment_journal=self.ledger)
        owner.reload()
        return owner

    def request(self, *, key=None, operation="operation-a", public_metadata=None, **changes):
        key = key or self.key
        binding = {
            "delegation": "grant-a", "delegation_digest": planning.digest(self.delegation),
            "scope": copy.deepcopy(self.scope), "public_key": key.as_dict(private=False)}
        binding["public_key"].update(public_metadata or {})
        intent = {
            "schema": "fleet-manager.operation-intent", "version": 1,
            "operation": operation, "authority": "authority-a", "principal": "new-caller",
            "account": "account-a", "target": "machine-a", "generation": 1,
            "command": "machine.enroll", "parameters": {"enrollment": planning.digest(binding)}}
        return planning.canonical({
            "intent": intent, "delegation": "grant-a", "scope": binding["scope"],
            "public_key": binding["public_key"], **changes})

    def enroll(self, raw=None, *, grant=GRANT, proof_body=None, signer=None):
        raw = raw if raw is not None else self.request()
        proof = make_machine_proof(
            signer or self.key, principal="new-caller", audience=AUDIENCE,
            kind="caller", resource="enrollment", method="POST",
            target_uri=DESTINATION + "/_fleet/enroll", nonce=self.owner.enrollment_challenge(),
            body=raw if proof_body is None else proof_body, now=self.h.clock.value)
        return self.owner.enroll(
            raw, grant=grant, proof=proof,
            target_uri=DESTINATION + "/_fleet/enroll", destination=DESTINATION)


def test_real_publication_repeat_restart_lost_reply_and_no_duplicate_key(tmp_path: Path) -> None:
    h = EnrollmentHarness(tmp_path)
    raw = h.request()
    first = h.enroll(raw)
    assert first["state"] == "published"
    assert h.enroll(raw) == first
    h.owner = h.restart()
    assert h.enroll(raw) == first
    with sqlite3.connect(h.ledger.path) as connection:
        assert connection.execute("SELECT count(*) FROM machine_enrollments").fetchone() == (1,)
        receipt = connection.execute("SELECT receipt FROM receipts").fetchone()[0]
    assert GRANT.encode() not in receipt
    proof = make_machine_proof(
        h.key, principal="new-caller", audience=AUDIENCE, kind="caller", resource="service-a",
        method="GET", target_uri=DESTINATION + "/api", nonce=h.owner.challenge(), body=b"", now=NOW)
    grant = h.owner.authenticate_caller(
        "new-caller", proof, method="GET", request_method="GET", slot="service-a",
        target_uri=DESTINATION + "/api", content_digest=hashlib.sha256(b"").hexdigest())
    assert h.owner.revalidate_caller(grant).principal == "new-caller"
    with pytest.raises(ServicePolicyError, match="^enrollment-journal-refused$"):
        h.enroll(h.request(operation="operation-b"))
    with pytest.raises(ServicePolicyError):
        h.enroll(h.request(key=ECKey.generate_key("P-256")))


@pytest.mark.parametrize("field", [
    "source", "account", "container", "harness", "gateway", "destination", "machine",
    "principal", "generation", "slots", "methods", "expires_at",
])
def test_wrong_scope_never_admits_or_echoes_grant(tmp_path: Path, field: str) -> None:
    h = EnrollmentHarness(tmp_path)
    scope = copy.deepcopy(h.scope)
    scope[field] = ["service-b"] if field in ("slots", "methods") else "wrong"
    with pytest.raises(ServicePolicyError) as caught:
        h.enroll(h.request(scope=scope))
    assert GRANT not in repr(caught.value)
    with sqlite3.connect(h.ledger.path) as connection:
        assert connection.execute("SELECT count(*) FROM operations").fetchone() == (0,)


def test_discovery_wrong_grant_content_forgery_expiry_and_revocation(tmp_path: Path) -> None:
    h = EnrollmentHarness(tmp_path)
    for kwargs in (
        {"grant": ""}, {"grant": "a" * 43}, {"proof_body": b"other-content"},
        {"signer": ECKey.generate_key("P-256")},
    ):
        with pytest.raises(ServicePolicyError):
            h.enroll(**kwargs)
    h.h.clock.value = NOW + 300
    with pytest.raises(ServicePolicyError, match="^enrollment-denied$"):
        h.enroll()
    h.document["delegations"] = []
    h.document["policy"]["manifest"]["revision"] += 1
    h.owner.reload()
    with pytest.raises(ServicePolicyError, match="^enrollment-denied$"):
        h.enroll()


def test_revoked_source_floor_survives_restart_and_cannot_fall_back(tmp_path: Path) -> None:
    h = EnrollmentHarness(tmp_path)
    h.enroll()
    old = copy.deepcopy(h.document)
    h.document["delegations"] = []
    h.document["policy"]["manifest"]["revision"] += 1
    h.owner.reload()
    h.owner = h.restart()
    assert all(c.principal != "new-caller" for c in h.owner._current()[0].callers)
    h.document = old
    with pytest.raises(ServicePolicyError, match="^enrollment-journal-refused$"):
        h.restart()
    with pytest.raises(planning.PlanError, match="unsupported operation ledger schema"):
        OperationLedger(h.ledger.path, authority="authority-a", installation="installation-a")


@pytest.mark.parametrize("failure", ["jwk-metadata", "capacity"])
def test_unpublishable_admission_rolls_back_every_durable_effect(tmp_path: Path, failure: str) -> None:
    h = EnrollmentHarness(tmp_path)
    if failure == "capacity":
        policy = h.document["policy"]
        for index in range(125):
            reference = "unused-" + str(index)
            policy["manifest"]["key_sources"].append({
                "name": reference, "provider": "owner-store", "reference": reference})
            policy["bindings"].append({
                "provider": "owner-store", "reference": reference,
                "jwk": h.h.keys[1].as_dict(private=False)})
        policy["manifest"]["revision"] += 1
        h.owner.reload()
        raw = h.request()
    else:
        raw = h.request(public_metadata={"alg": "ES256"})
    with pytest.raises(ServicePolicyError):
        h.enroll(raw)
    with sqlite3.connect(h.ledger.path) as connection:
        for table in ("operations", "outbox", "audit", "machine_enrollments"):
            assert connection.execute("SELECT count(*) FROM " + table).fetchone() == (0,)
    h.restart()


def test_rejected_owner_candidate_cannot_advance_accepted_floor(tmp_path: Path) -> None:
    h = EnrollmentHarness(tmp_path)
    valid = copy.deepcopy(h.document)
    h.document["policy"]["manifest"].update(fleet="different-fleet", revision=2)
    with pytest.raises(ServicePolicyError, match="^owner-mismatch$"):
        h.owner.reload()
    h.document = valid
    h.owner = h.restart()
    assert h.enroll()["state"] == "published"


def test_restart_settles_original_publication_without_reusing_expired_grant(tmp_path: Path) -> None:
    h = EnrollmentHarness(tmp_path)
    h.scope["expires_at"] = NOW + 600
    h.document["policy"]["manifest"]["revision"] += 1
    h.owner.reload()
    raw = h.request()
    intent = OperationIntent.from_dict(planning.decode(raw)["intent"])
    producer = h.owner._enrollment
    proof = make_machine_proof(
        h.key, principal="new-caller", audience=AUDIENCE,
        kind="caller", resource="enrollment", method="POST",
        target_uri=DESTINATION + "/_fleet/enroll", nonce=producer.nonce, body=raw, now=NOW)
    producer.enroll(
        raw, grant=GRANT, proof=proof,
        target_uri=DESTINATION + "/_fleet/enroll", destination=DESTINATION)
    assert h.ledger.inspect(intent)["state"] == "accepted"
    assert h.ledger.inspect(intent)["receipt"] is None
    h.h.clock.value = NOW + 301
    h.owner = h.restart()
    assert h.ledger.inspect(intent)["state"] == "outcome"
    assert h.ledger.inspect(intent)["receipt"]["status"] == "succeeded"
    with pytest.raises(ServicePolicyError, match="^enrollment-denied$"):
        h.enroll(raw)


def test_higher_source_revision_cannot_resurrect_withdrawn_machine_generation(tmp_path: Path) -> None:
    h = EnrollmentHarness(tmp_path)
    h.enroll()
    original = copy.deepcopy(h.document)
    h.document["delegations"] = []
    h.document["policy"]["manifest"]["revision"] += 1
    h.owner.reload()
    with sqlite3.connect(h.ledger.path) as connection:
        assert connection.execute(
            "SELECT withdrawn_revision FROM machine_enrollments").fetchone() == (2,)
    h.document = original
    h.document["policy"]["manifest"]["revision"] = 3
    h.owner = h.restart()
    assert all(c.principal != "new-caller" for c in h.owner._current()[0].callers)
    with pytest.raises(ServicePolicyError, match="^enrollment-journal-refused$"):
        h.enroll()


def test_expired_candidate_cannot_poison_accepted_policy_floor(tmp_path: Path) -> None:
    h = EnrollmentHarness(tmp_path)
    original = copy.deepcopy(h.document)
    h.document["policy"]["manifest"]["revision"] += 1
    h.document["policy"]["identity"]["trust_valid_until"] = NOW
    with pytest.raises(ServicePolicyError, match="^authority-unavailable$"):
        h.owner.reload()
    h.document = original
    h.owner = h.restart()
    assert h.enroll()["state"] == "published"
