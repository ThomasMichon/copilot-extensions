"""Pure checkpoint work count; signatures and native composition are separate."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
before = sys.path.copy()
try:
    sys.path.insert(0, str(ROOT / "fleet-manager/src"))
    from fleet_manager.komodo_repair_authorization import RepairAuthorization
    from fleet_manager.operations import OperationIntent
    from fleet_manager.runtime_admission import (
        AdmissionAuthority, AuthoritySnapshot, CallerIdentity, Enrollment, OperationGrant,
    )
    from fleet_manager.runtime_identity import VerifiedIdentity
finally:
    sys.path[:] = before


def test_one_producer_and_six_current_resolutions_per_completed_phase() -> None:
    now = [1000]
    resolved, produced = [], []
    request = OperationIntent.from_dict({
        "schema": "fleet-manager.operation-intent", "version": 1,
        "operation": "original-operation", "authority": "gateway-a",
        "principal": "actor-a", "account": "account-a", "target": "host-a",
        "generation": 1, "command": "manager.converge",
        "parameters": {"deployment": "e" * 64},
    })
    enrollment = Enrollment(
        "principal-1", "actor-a", "account-a", "operator", "client", "client-a", 1, "active",
        operations=(OperationGrant("manager.converge", "host-a", 1, request.request_digest),))
    snapshot = AuthoritySnapshot(
        "gateway-a", "fleet-a", "https://issuer.example", "gateway-a",
        1, 999, 2000, 2000, (enrollment,))
    admission = AdmissionAuthority(snapshot=lambda: snapshot, clock=lambda: now[0])
    resolution = SimpleNamespace(
        request=request, role="client", installation="client-a",
        refresh=lambda: resolved.append(now[0]))

    def identity(sequence: int) -> VerifiedIdentity:
        return VerifiedIdentity(
            snapshot.issuer, "principal-1", snapshot.audience, "k" * 43,
            now[0], 2000, now[0], hashlib.sha256(str(sequence).encode()).hexdigest())

    def produce(original: CallerIdentity, intent: OperationIntent, role: str,
                installation: str, action: str) -> VerifiedIdentity:
        assert not admission._issued
        assert (intent, role, installation, action) == (request, "client", "client-a", "succeeded")
        produced.append(now[0])
        return identity(len(produced))

    initial = identity(0)
    resolution.identity = initial
    permit = admission.authorize(
        initial, request, role="client", installation="client-a", checkpoint=resolution.refresh)
    owner = SimpleNamespace(admission=admission, fresh_identity=produce)
    authorization = RepairAuthorization(owner, resolution, initial, permit, "succeeded")
    authorization.revalidate()
    authorization.end()
    for _ in range(3):
        before_resolved, before_produced = len(resolved), len(produced)
        now[0] += 31
        authorization.begin()
        current = authorization.permit
        authorization.revalidate()
        now[0] += 10
        authorization.revalidate()
        authorization.end()
        assert len(resolved) - before_resolved == 6
        assert len(produced) - before_produced == 1
        assert authorization.permit.expires_at == current.expires_at == current.issued_at + 30
    authorization.abort()
    assert not admission._issued and permit.expires_at == permit.issued_at + 30
