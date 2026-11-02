"""Synthetic public-key proofs through the real current policy and TLS receiver."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path

from joserfc.jwk import ECKey
import pytest

from fleet_manager import planning
from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_connector import ServiceConnector
from fleet_manager.machine_identity import (
    MachineBinding, MachineIdentityVerifier, PROFILE, machine_headers, make_machine_proof,
)
from fleet_manager.runtime_identity import IdentityError
from gateway_service_transport_fixtures import Rig
from test_gateway_service_policy import Harness
from test_runtime_identity import AUDIENCE, NOW, Clock, TARGET


class MachineHarness(Harness):
    def __init__(self) -> None:
        super().__init__()
        self.document["version"] = 4
        self.document["identity"] = {
            "profile": PROFILE, "audience": AUDIENCE, "trust_valid_until": NOW + 86400}
        self.document["enrollment"] = {"mappings": [
            {"principal": principal, "kind": kind, "resource": resource,
             "key_source": principal, "expires_at": NOW + 600}
            for principal, kind, resource in (
                ("service-a", "service", "service-a"), ("service-b", "service", "service-b"),
                ("caller-a", "caller", "service-a"))]}


def test_real_proofs_fence_content_query_kind_resource_replay_capacity_and_restart() -> None:
    key = ECKey.generate_key("P-256")
    clock = Clock()
    binding = MachineBinding("machine-a", "caller", "service-a",
                             planning.canonical(key.as_dict(private=False)), NOW + 300)
    owner = MachineIdentityVerifier(
        audience=AUDIENCE, bindings=(binding,), trust_valid_until=NOW + 600,
        clock=clock, replay_capacity=1)
    params = dict(principal="machine-a", audience=AUDIENCE, kind="caller", resource="service-a",
                  method="POST", target_uri=TARGET + "?a=1", nonce=owner.nonce, body=b"intent", now=NOW)
    proof = make_machine_proof(key, **params)
    context = dict(method="POST", target_uri=TARGET + "?a=1", kind="caller", resource="service-a",
                   content_digest=hashlib.sha256(b"intent").hexdigest())
    for change in (
        {"target_uri": TARGET + "?a=2"}, {"kind": "service"}, {"resource": "service-b"},
        {"content_digest": hashlib.sha256(b"changed").hexdigest()},
    ):
        with pytest.raises(IdentityError):
            owner.verify("machine-a", proof, **(context | change))
    def admit(_: int) -> str:
        try:
            return owner.verify("machine-a", proof, **context).subject
        except IdentityError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(admit, range(8)))
    assert results.count("machine-a") == 1 and results.count("proof-replayed") == 7
    with pytest.raises(IdentityError, match="^resource-limit$"):
        owner.verify("machine-a", make_machine_proof(key, **params), **context)
    replacement = MachineIdentityVerifier(
        audience=AUDIENCE, bindings=(binding,), trust_valid_until=NOW + 600, clock=clock)
    with pytest.raises(IdentityError, match="^invalid-proof$"):
        replacement.verify("machine-a", proof, **context)
    clock.value -= 1
    with pytest.raises(IdentityError, match="^clock-rollback$"):
        owner.verify("machine-a", proof, **context)
    assert machine_headers("machine-a", proof)["Authorization"].endswith("machine-a")
    assert machine_headers("machine-a", proof)["Fleet-Machine-Proof"] == proof


def test_real_tls_connector_and_caller_bind_actual_body_and_query(tmp_path: Path) -> None:
    async def scenario() -> None:
        rig = Rig(tmp_path)
        rig.h.document["version"] = 4
        rig.h.document["identity"] = {
            "profile": PROFILE, "audience": AUDIENCE,
            "trust_valid_until": rig.h.clock.value + 86400}
        rig.h.document["enrollment"] = {"mappings": [
            {"principal": principal, "kind": kind, "resource": "service-a",
             "key_source": principal, "expires_at": rig.h.clock.value + 600}
            for principal, kind in (("service-a", "service"), ("caller-a", "caller"))]}
        try:
            await rig.start()
            connector = ServiceConnector(
                slot="service-a", gateway_origin=rig.origin, backend_origin=rig.origins[0],
                proof_key=rig.h.keys[1], gateway_ca=rig.ca,
                machine_principal="service-a", machine_audience=AUDIENCE)
            task = asyncio.create_task(connector.connect_once())
            rig.tasks.append(task)
            await asyncio.wait_for(connector.connected.wait(), 3)
            caller = ServiceCaller(
                gateway_origin=rig.origin, proof_key=rig.h.keys[3], gateway_ca=rig.ca,
                machine_principal="caller-a", machine_audience=AUDIENCE, machine_resource="service-a")
            response = await caller.request("POST", "/api/write?a=1", body=b"exact-body")
            assert response.status == 200
            assert rig.backends[0].requests[-1][1] == "/write?a=1"
            assert rig.backends[0].requests[-1][3] == b"exact-body"
            nonce = rig.state.authority.challenge()
            proof = make_machine_proof(
                rig.h.keys[3], principal="caller-a", audience=AUDIENCE, kind="caller",
                resource="service-a", method="POST", target_uri=rig.origin + "/api/write?a=1",
                nonce=nonce, body=b"exact-body")
            for path, body in (("/api/write?a=2", b"exact-body"), ("/api/write?a=1", b"changed")):
                async with rig.session.post(
                    rig.origin + path, data=body, headers=machine_headers("caller-a", proof),
                ) as refused:
                    assert refused.status == 401
            assert len(rig.backends[0].requests) == 1
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_machine_operations_require_separately_selected_admission_profile() -> None:
    from fleet_manager.runtime_admission import AdmissionAuthority, AdmissionError
    from test_runtime_admission import intent, policy

    key = ECKey.generate_key("P-256")
    clock = Clock()
    verifier = MachineIdentityVerifier(
        audience=AUDIENCE, trust_valid_until=NOW + 600, clock=clock,
        bindings=(MachineBinding(
            "principal-1", "caller", "service-a",
            planning.canonical(key.as_dict(private=False)), NOW + 300),))
    proof = make_machine_proof(
        key, principal="principal-1", audience=AUDIENCE, kind="caller", resource="service-a",
        method="POST", target_uri=TARGET, nonce=verifier.nonce, body=b"intent", now=NOW)
    identity = verifier.verify(
        "principal-1", proof, method="POST", target_uri=TARGET, kind="caller",
        resource="service-a", content_digest=hashlib.sha256(b"intent").hexdigest())
    legacy = AdmissionAuthority(snapshot=policy, clock=clock)
    with pytest.raises(AdmissionError, match="^unsupported-profile$"):
        legacy.authorize(identity, intent(), role="client", installation="client-a")
    selected = AdmissionAuthority(
        snapshot=lambda: policy(identity_profile=PROFILE, issuer=PROFILE), clock=clock)
    permit = selected.authorize(identity, intent(), role="client", installation="client-a")
    assert permit.enrollment.principal == "actor-a"
