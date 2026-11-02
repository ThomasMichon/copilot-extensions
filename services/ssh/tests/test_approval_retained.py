import sqlite3

import pytest

from agent_ssh_service.config import ServiceConfig, SSHServiceError
from agent_ssh_service.store import DirectoryStore

from conftest import enrollment
from test_approval import bootstrap, decision, selected, submit


def round_trip(config):
    peer = selected(config)
    pending_store = DirectoryStore(peer)
    pending = submit(pending_store)
    automatic = peer.to_dict()
    automatic["revision"] += 1
    automatic.pop("enrollment_policy")
    automatic_store = DirectoryStore(ServiceConfig.from_dict(automatic))
    automatic_store.enroll(
        "principal-worker-a", {**enrollment(), "request_id": "automatic-admission"})
    peer = peer.to_dict()
    peer["revision"] = automatic["revision"] + 1
    return DirectoryStore(ServiceConfig.from_dict(peer)), pending


@pytest.mark.parametrize("first_read", ["status", "resubmit"])
def test_retained_policy_round_trip_records_a_stable_supersedable_decision(config, first_read):
    store, pending = round_trip(config)
    if first_read == "status":
        retained = store.approval.status("principal-worker-a", pending["request_id"])
    else:
        retained = submit(store)
    assert retained["status"] == "active" and retained["decision"]["method"] == "retained"
    second = submit(store, 1)
    store.approval.decide("principal-worker-a", decision(second))
    restarted = DirectoryStore(store.config)
    assert restarted.approval.status("principal-worker-a", pending["request_id"]) == retained
    later = store.config.to_dict()
    later["revision"] += 1
    later["bindings"][0]["generation"] += 1
    advanced = DirectoryStore(ServiceConfig.from_dict(later))
    superseded = advanced.approval.status("principal-worker-a", pending["request_id"])
    assert superseded["status"] == "superseded"
    assert superseded["decision"] == retained["decision"]


def test_identical_future_request_alias_preserves_the_original_public_candidate_decision(config):
    store = DirectoryStore(selected(config))
    first = bootstrap(store)
    other = submit(store, 1)
    store.approval.decide("principal-worker-a", decision(other))
    alias = submit(store, request_id="same-candidate-new-correlation")
    assert alias["request_id"] != first["request_id"]
    assert alias["fingerprint"] == first["fingerprint"]
    assert alias["decision"] == first["decision"]


def test_retained_observation_rolls_back_when_its_status_caller_is_withdrawn(config):
    store, pending = round_trip(config)
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SSHServiceError("withdrawn")

    with pytest.raises(SSHServiceError, match="withdrawn"):
        store.approval.status(
            "principal-worker-a", pending["request_id"], checkpoint=checkpoint)
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "SELECT decision FROM enrollment_pending WHERE principal=? AND request_id=?",
            ("principal-worker-a", pending["request_id"]),
        ).fetchone()[0] is None
    assert store.approval.status("principal-worker-a", pending["request_id"])["status"] == "active"
