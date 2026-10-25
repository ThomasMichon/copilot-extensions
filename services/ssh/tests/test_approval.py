from copy import deepcopy
import sqlite3

import pytest

from agent_ssh_service.config import ServiceConfig, SSHServiceError
from agent_ssh_service.store import DirectoryStore
from fleet_manager.planning import canonical
from fleet_manager.ssh_approval import EnrollmentResult

from conftest import enrollment, key


def selected(config, *, recovery=("recovery-owner",)):
    value = config.to_dict()
    value["enrollment_policy"] = {
        "mode": "peer-approval", "recovery_principals": list(recovery),
    }
    return ServiceConfig.from_dict(value)


def submit(store, index=0, **extra):
    result, changed = store.enroll(
        "principal-worker-a" if index == 0 else "principal-worker-b",
        {**enrollment(index), "enrollment_policy": "peer-approval", **extra},
    )
    assert not changed
    EnrollmentResult.parse(canonical(result))
    return result


def decision(pending, **extra):
    return {
        "requester": pending["candidate"]["requester"],
        "request_id": pending["request_id"], "fingerprint": pending["fingerprint"], **extra,
    }


def bootstrap(store):
    pending = submit(store)
    result, changed = store.approval.decide(
        "recovery-owner", decision(pending, confirm=True, reason="first-peer"),
        recovery=True,
    )
    assert changed and result["status"] == "active"
    return result


def test_pending_is_durable_but_not_directory_or_relay_admission(config):
    config = selected(config)
    store = DirectoryStore(config)
    before = store.snapshot()
    pending = submit(store)
    assert pending["status"] == "pending"
    assert pending["directory"] is None and pending["decision"] is None
    assert store.snapshot() == before
    assert store.relay_snapshot() == []
    restarted = DirectoryStore(config)
    assert restarted.approval.status("principal-worker-a", pending["request_id"]) == pending
    assert submit(restarted) == pending
    assert len(restarted.approval.pending("principal-worker-a")["requests"]) == 1


def test_explicit_recovery_then_authenticated_peer_approval_and_lost_reply(config):
    config = selected(config)
    store = DirectoryStore(config)
    first = bootstrap(store)
    second = submit(store, 1)
    active, changed = store.approval.decide("principal-worker-a", decision(second))
    assert changed and active["status"] == "active"
    assert active["decision"] == {"method": "peer", "principal": "principal-worker-a"}
    assert active["directory"]["directory"]["revision"] == first["directory"]["directory"]["revision"] + 1
    assert len(active["directory"]["directory"]["machines"]) == 2
    restarted = DirectoryStore(config)
    assert restarted.approval.decide("principal-worker-a", decision(second)) == (active, False)
    assert restarted.approval.status("principal-worker-b", second["request_id"]) == active
    assert restarted.approval.pending("principal-worker-a")["requests"] == []


@pytest.mark.parametrize("actor", ["principal-worker-b", "unknown", "recovery-owner"])
def test_pending_or_foreign_actor_cannot_approve(config, actor):
    store = DirectoryStore(selected(config))
    bootstrap(store)
    pending = submit(store, 1)
    before = store.snapshot()
    with pytest.raises(SSHServiceError):
        store.approval.decide(actor, decision(pending))
    assert store.snapshot() == before
    assert store.approval.status("principal-worker-b", pending["request_id"]) == pending


def test_enrolled_peer_cannot_self_approve(config):
    store = DirectoryStore(selected(config))
    original = bootstrap(store)
    with pytest.raises(SSHServiceError, match="self-approval-refused"):
        store.approval.decide("principal-worker-a", decision(original))


@pytest.mark.parametrize("extra", [
    {"confirm": False, "reason": "first-peer"},
    {"confirm": True, "reason": ""},
    {"confirm": True, "reason": "not\none-operation"},
])
def test_recovery_requires_explicit_confirmation_and_reason(config, extra):
    store = DirectoryStore(selected(config))
    pending = submit(store)
    with pytest.raises(SSHServiceError, match="explicit-recovery"):
        store.approval.decide("recovery-owner", decision(pending, **extra), recovery=True)
    assert store.snapshot()["directory"]["machines"] == []


def test_recovery_is_never_an_ordinary_machine_capability(config):
    store = DirectoryStore(selected(config))
    pending = submit(store)
    with pytest.raises(SSHServiceError, match="recovery-principal-not-admitted"):
        store.approval.decide(
            "principal-worker-a", decision(pending, confirm=True, reason="first-peer"),
            recovery=True,
        )
    assert store.snapshot()["directory"]["machines"] == []


def test_missing_recovery_owner_does_not_bootstrap_implicitly(config):
    store = DirectoryStore(selected(config, recovery=()))
    pending = submit(store)
    with pytest.raises(SSHServiceError):
        store.approval.decide(
            "recovery-owner", decision(pending, confirm=True, reason="first-peer"),
            recovery=True,
        )
    with pytest.raises(SSHServiceError):
        store.approval.decide("principal-worker-a", decision(pending))
    assert store.snapshot()["directory"]["revision"] == 1


def test_exact_fingerprint_and_original_request_content_cannot_change(config):
    store = DirectoryStore(selected(config))
    bootstrap(store)
    pending = submit(store, 1)
    before = store.snapshot()
    with pytest.raises(SSHServiceError, match="fingerprint-mismatch"):
        store.approval.decide(
            "principal-worker-a", decision(pending, fingerprint="SHA256:" + "A" * 43))
    with pytest.raises(SSHServiceError, match="request-content-conflict"):
        submit(store, 1, public_key=key(25))
    assert store.snapshot() == before
    assert store.approval.status("principal-worker-b", pending["request_id"]) == pending


def test_competing_pending_identity_cannot_remain_approvable_after_activation(config):
    store = DirectoryStore(selected(config))
    original = submit(store)
    competing = submit(store, request_id="different-candidate", public_key=key(25))
    store.approval.decide(
        "recovery-owner", decision(original, confirm=True, reason="first-peer"), recovery=True)
    with pytest.raises(SSHServiceError, match="pending-identity-superseded"):
        store.approval.status("principal-worker-a", competing["request_id"])
    assert store.approval.pending("recovery-owner")["requests"] == []


@pytest.mark.parametrize("field,value", [
    ("generation", 2), ("alias", "renamed-worker"), ("user", "other-owner"),
])
def test_changed_binding_cannot_approve_a_stale_pending_identity(config, field, value):
    config = selected(config)
    store = DirectoryStore(config)
    bootstrap(store)
    pending = submit(store, 1)
    new = config.to_dict()
    new["revision"] += 1
    new["bindings"][1][field] = value
    current = DirectoryStore(ServiceConfig.from_dict(new))
    before = current.snapshot()
    with pytest.raises(SSHServiceError):
        current.approval.decide("principal-worker-a", decision(pending))
    assert current.snapshot() == before


def test_approver_generation_must_still_be_admitted(config):
    config = selected(config)
    store = DirectoryStore(config)
    bootstrap(store)
    pending = submit(store, 1)
    new = config.to_dict()
    new["revision"] += 1
    new["bindings"][0]["generation"] += 1
    current = DirectoryStore(ServiceConfig.from_dict(new))
    with pytest.raises(SSHServiceError, match="enrolled-approver-required"):
        current.approval.decide("principal-worker-a", decision(pending))
    assert current.approval.status("principal-worker-b", pending["request_id"])["status"] == "pending"


def test_completed_decision_survives_a_later_generation_without_claiming_active(config):
    config = selected(config)
    store = DirectoryStore(config)
    active = bootstrap(store)
    new = config.to_dict()
    new["revision"] += 1
    new["bindings"][0]["generation"] += 1
    current = DirectoryStore(ServiceConfig.from_dict(new))
    history = current.approval.status("principal-worker-a", active["request_id"])
    assert history["status"] == "superseded" and history["directory"] is None
    assert history["decision"] == active["decision"]
    assert history["fingerprint"] == active["fingerprint"]
    EnrollmentResult.parse(canonical(history))


def test_decision_and_activation_roll_back_together_if_caller_is_withdrawn(config):
    store = DirectoryStore(selected(config))
    bootstrap(store)
    pending = submit(store, 1)
    before, calls = store.snapshot(), 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SSHServiceError("withdrawn")

    with pytest.raises(SSHServiceError, match="withdrawn"):
        store.approval.decide("principal-worker-a", decision(pending), checkpoint=checkpoint)
    assert store.snapshot() == before
    assert store.approval.status("principal-worker-b", pending["request_id"]) == pending
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0] == 1


def test_enabling_optional_policy_preserves_an_existing_admitted_identity(config):
    store = DirectoryStore(config)
    before, _ = store.enroll("principal-worker-a", enrollment())
    new = selected(config).to_dict()
    new["revision"] += 1
    current = DirectoryStore(ServiceConfig.from_dict(new))
    retained = submit(current, request_id="existing-key-under-selected-policy")
    assert retained["status"] == "active"
    assert retained["decision"] == {"method": "retained", "principal": None}
    assert retained["directory"]["directory"]["machines"] == before["directory"]["machines"]
    with pytest.raises(SSHServiceError, match="new-generation"):
        submit(current, request_id="different-key", public_key=key(23))


def test_pending_listing_is_bounded_to_latest_candidate_per_declared_principal(config):
    store = DirectoryStore(selected(config))
    for index in range(35):
        submit(store, request_id=f"pending-{index}")
    pending = store.approval.pending("principal-worker-a")["requests"]
    assert [item["request_id"] for item in pending] == ["pending-34"]


@pytest.mark.parametrize("policy", [
    None, {"mode": "automatic", "recovery_principals": []},
    {"mode": "peer-approval", "recovery_principals": ["principal-worker-a"]},
    {"mode": "peer-approval", "recovery_principals": ["recovery", "recovery"]},
])
def test_invalid_or_overlapping_recovery_policy_is_refused(config, policy):
    value = deepcopy(config.to_dict())
    value["enrollment_policy"] = policy
    with pytest.raises((SSHServiceError, ValueError)):
        ServiceConfig.from_dict(value)
