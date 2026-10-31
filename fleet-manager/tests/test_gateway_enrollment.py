"""Real delegation, durable source floors and original-operation recovery."""

from __future__ import annotations

import copy
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
from threading import Event, Thread
import time

from joserfc.jwk import ECKey
import pytest

from fleet_manager import planning
from fleet_manager.gateway_enrollment import SCHEMA
from fleet_manager.gateway_enrollment import EnrollmentPolicy
from fleet_manager.gateway_service_policy import ServiceAuthority, ServicePolicyError
from fleet_manager.gateway_service_server import Listener, ServiceRuntime
from fleet_manager import gateway_service_server
from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_connector import ServiceConnector
from fleet_manager.ledger import OperationLedger
from fleet_manager.operations import OperationIntent
from fleet_manager.machine_identity import make_machine_proof
from test_machine_identity import MachineHarness
from test_runtime_identity import AUDIENCE, NOW
from gateway_service_transport_fixtures import Rig

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


def test_clock_crossing_publication_rolls_back_floor_and_withdrawal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = EnrollmentHarness(tmp_path)
    h.enroll()
    original = copy.deepcopy(h.document)
    h.document["policy"]["manifest"]["revision"] += 1
    h.document["policy"]["identity"]["trust_valid_until"] = NOW + 1
    h.document["delegations"] = []
    compose = EnrollmentPolicy._compose

    def crossing(owner, records):
        candidate = compose(owner, records)
        h.h.clock.value = NOW + 1
        return candidate

    with monkeypatch.context() as patch:
        patch.setattr(EnrollmentPolicy, "_compose", crossing)
        with pytest.raises(ServicePolicyError, match="^authority-unavailable$"):
            h.owner.reload()
    with sqlite3.connect(h.ledger.path) as connection:
        assert connection.execute("SELECT revision FROM enrollment_floors").fetchone() == (1,)
        assert connection.execute("SELECT withdrawn_revision FROM machine_enrollments").fetchone() == (None,)
    h.document = original
    h.owner = h.restart()
    assert any(c.principal == "new-caller" for c in h.owner._current()[0].callers)


def test_settlement_failure_delivers_installed_context_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        h = EnrollmentHarness(tmp_path)
        h.enroll()
        listener = Listener("fleet-a", "gateway-a", "127.0.0.1", 0, DESTINATION, "", "")
        runtime = ServiceRuntime(h.owner, listener, 1)
        await runtime.reload()
        previous = runtime.context
        h.document["policy"]["manifest"]["revision"] += 1
        h.document["delegations"] = []

        def failed(*args) -> None:
            raise sqlite3.OperationalError("synthetic receipt storage failure")

        with monkeypatch.context() as patch:
            patch.setattr(EnrollmentPolicy, "reconcile_publication", failed)
            await runtime.reload()
        assert runtime.context is h.owner._current()[0]
        assert runtime.context.digest != previous.digest
        assert runtime.last_error == h.owner.status().last_error == "enrollment-settlement-pending"
        assert "service-enrollment-settlement-pending" in caplog.text
        await runtime.reload()
        assert runtime.last_error is h.owner.status().last_error is None

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["reload", "challenge", "enroll"])
def test_contended_journal_does_not_hold_serving_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    async def run() -> None:
        h = EnrollmentHarness(tmp_path)
        raw = h.request()
        proof = make_machine_proof(
            h.key, principal="new-caller", audience=AUDIENCE, kind="caller",
            resource="enrollment", method="POST", target_uri=DESTINATION + "/_fleet/enroll",
            nonce=h.owner.enrollment_challenge(), body=raw, now=NOW)
        entered, done = Event(), Event()
        transaction = h.ledger._transaction

        @contextmanager
        def observed(*args, **kwargs):
            entered.set()
            with transaction(*args, **kwargs) as connection:
                yield connection

        monkeypatch.setattr(h.ledger, "_transaction", observed)
        failures = []

        def blocked() -> None:
            try:
                if operation == "reload":
                    h.owner.reload()
                elif operation == "challenge":
                    h.owner.enrollment_challenge()
                else:
                    h.owner.enroll(
                        raw, grant=GRANT, proof=proof,
                        target_uri=DESTINATION + "/_fleet/enroll", destination=DESTINATION)
            except Exception as exc:
                failures.append(exc)
            finally:
                done.set()

        with sqlite3.connect(h.ledger.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            worker = Thread(target=blocked)
            worker.start()
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                start = time.monotonic()
                for _ in range(3):
                    assert h.owner.challenge()
                    await asyncio.sleep(0.01)
                assert time.monotonic() - start < 0.5
                assert not done.is_set()
            finally:
                connection.rollback()
                await asyncio.to_thread(worker.join, 6)
        assert done.is_set()
        assert not failures

    asyncio.run(run())


@pytest.mark.parametrize("same_generation", [False, True])
def test_reversed_reload_completion_cannot_restore_old_context_or_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_generation: bool,
) -> None:
    async def run() -> None:
        h = EnrollmentHarness(tmp_path)
        earlier = replace(h.owner.reload(), settlement_error="enrollment-settlement-pending")
        if not same_generation:
            h.document["policy"]["manifest"]["revision"] += 1
        later = h.owner.reload()
        runtime = ServiceRuntime(
            h.owner, Listener("fleet-a", "gateway-a", "127.0.0.1", 0, DESTINATION, "", ""), 1)
        responses = [asyncio.get_running_loop().create_future() for _ in range(2)]
        calls = []

        async def reversed_result(function):
            index = len(calls)
            calls.append(function)
            return await responses[index]

        monkeypatch.setattr(gateway_service_server, "settled", reversed_result)
        first = asyncio.create_task(runtime.reload())
        second = asyncio.create_task(runtime.reload())
        await asyncio.sleep(0)
        assert len(calls) == 2
        responses[1].set_result(later)
        await second
        responses[0].set_result(earlier)
        await first
        assert runtime.context is later.snapshot.context
        assert runtime.last_error is None

    asyncio.run(run())


def test_enrollment_preserves_unrelated_live_channel_request_and_replay_fence(tmp_path: Path) -> None:
    async def run() -> None:
        rig = Rig(tmp_path)
        h = EnrollmentHarness(tmp_path)
        h.h.clock.value = rig.h.clock.value
        h.scope["expires_at"] = rig.h.clock.value + 600
        h.delegation["grant_expires_at"] = rig.h.clock.value + 300
        h.document["policy"]["manifest"]["revision"] += 1
        rig.h.document = h.document
        try:
            await rig.start(enrollment_journal=h.ledger)
            h.scope["destination"] = rig.origin
            h.document["policy"]["manifest"]["revision"] += 1
            rig.save()
            await rig.state.reload()
            h.owner = rig.state.authority
            connector = ServiceConnector(
                slot="service-a", gateway_origin=rig.origin, backend_origin=rig.origins[0],
                proof_key=h.h.keys[1], gateway_ca=rig.ca,
                machine_principal="service-a", machine_audience=AUDIENCE)
            task = asyncio.create_task(connector.connect_once())
            rig.tasks.append(task)
            await asyncio.wait_for(connector.connected.wait(), 3)
            channel = rig.state.channels["service-a"]
            nonce = h.owner.challenge()
            proof = make_machine_proof(
                h.h.keys[3], principal="caller-a", audience=AUDIENCE, kind="caller",
                resource="service-a", method="GET", target_uri=rig.origin + "/api",
                nonce=nonce, body=b"", now=rig.h.clock.value)
            arguments = dict(method="GET", request_method="GET", slot="service-a",
                             target_uri=rig.origin + "/api",
                             content_digest=hashlib.sha256(b"").hexdigest())
            grant = h.owner.authenticate_caller("caller-a", proof, **arguments)
            caller = ServiceCaller(
                gateway_origin=rig.origin, proof_key=h.h.keys[3], gateway_ca=rig.ca,
                machine_principal="caller-a", machine_audience=AUDIENCE,
                machine_resource="service-a")
            request = asyncio.create_task(caller.request("GET", "/api/hold"))
            rig.tasks.append(request)
            await asyncio.wait_for(rig.backends[0].started.wait(), 3)
            raw = h.request()
            enrollment_proof = make_machine_proof(
                h.key, principal="new-caller", audience=AUDIENCE, kind="caller",
                resource="enrollment", method="POST", target_uri=rig.origin + "/_fleet/enroll",
                nonce=await asyncio.to_thread(h.owner.enrollment_challenge),
                body=raw, now=rig.h.clock.value)
            async with rig.session.post(
                rig.origin + "/_fleet/enroll", data=raw,
                headers={"Fleet-Enrollment-Grant": GRANT, "Fleet-Machine-Proof": enrollment_proof},
            ) as response:
                assert response.status == 200
            await rig.state.reload()
            assert h.owner.challenge() == nonce
            assert rig.state.channels["service-a"] is channel and not channel.closed
            assert h.owner.revalidate_caller(grant).principal == "caller-a"
            with pytest.raises(ServicePolicyError, match="^proof-replayed$"):
                h.owner.authenticate_caller("caller-a", proof, **arguments)
            rig.backends[0].release.set()
            assert (await asyncio.wait_for(request, 3)).status == 200
            h.document["policy"]["enrollment"]["mappings"] = [
                mapping for mapping in h.document["policy"]["enrollment"]["mappings"]
                if mapping["resource"] != "service-a"]
            h.document["policy"]["manifest"]["revision"] += 1
            rig.save()
            await rig.state.reload()
            assert channel.closed
            with pytest.raises(ServicePolicyError):
                h.owner.revalidate_caller(grant)
        finally:
            await rig.close()

    asyncio.run(run())
