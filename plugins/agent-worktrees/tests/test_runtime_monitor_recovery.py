from __future__ import annotations

import os
import time

import pytest
from zdd import breadcrumb, diagnostics, routing, routing_persistence
from zdd.cutover_lock import CutoverLock, CutoverLockedError

from agent_worktrees import runtime_monitor_recovery as recovery


@pytest.fixture
def owned(tmp_path, monkeypatch):
    route_root = tmp_path / "status-monitor-routing"
    events = []
    identities = {101: "1001", 202: "2002"}
    health = {
        41001: {"pid": 101, "status": "ready", "published": True},
        41002: {"pid": 202, "status": "ready", "published": False},
    }
    monkeypatch.setattr(diagnostics, "process_start_time", identities.get)
    old = routing.publish_active(
        route_root, bind="127.0.0.1", port=41001, pid=101, process_start_time="1001",
    )
    witness = routing.publication_witness(route_root, deadline=time.monotonic() + 5)
    routing.publish_active(
        route_root, bind="127.0.0.1", port=41002, pid=202, process_start_time="2002",
        demote_existing=True,
    )
    record = breadcrumb.write_breadcrumb(
        route_root, state="publication-uncertain", old=old.to_dict(), new_port=41002,
        new_pid=202, publication_witness=witness, new_process_start_time="2002",
    )

    class Handle:
        pid = 202
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            assert_authority()
            events.append("terminate-candidate")
            self.returncode = 0

    def assert_authority():
        with pytest.raises(CutoverLockedError):
            with CutoverLock(route_root):
                pytest.fail("owner-led recovery released native transition authority")

    class Client:
        drained = True

        def __init__(self, url, **_kwargs):
            self.port = int(url.rsplit(":", 1)[1])

        def health(self):
            assert_authority()
            return health[self.port]

        def drain(self, **_kwargs):
            assert_authority()
            events.append("drain-predecessor")
            return {"drained": self.drained}

        def shutdown(self):
            assert_authority()
            events.append("shutdown-predecessor")

        def undrain(self):
            assert_authority()
            events.append("undrain-predecessor")
            return {"draining": False}

    monkeypatch.setattr(recovery, "ControlClient", Client)
    return tmp_path, route_root, record, Handle(), Client, health, events, identities


@pytest.mark.parametrize("published", [False, True])
def test_same_owner_recovers_uncertain_publication_under_native_authority(owned, published):
    root, route_root, record, handle, _client, health, events, _identities = owned
    health[41002]["published"] = published
    assert recovery.recover_owned_publication(root, record, (101, "1001"), [handle])
    active = routing.read_active_endpoint(route_root, verify_listener=False)
    terminal = breadcrumb.read_breadcrumb(route_root)
    assert active.pid == (202 if published else 101)
    assert terminal["state"] == ("committed" if published else "rolled_back")
    assert terminal["publication_witness"] == record["publication_witness"]
    assert terminal["new_process_start_time"] == record["new_process_start_time"]
    assert events == (
        ["drain-predecessor", "shutdown-predecessor"] if published else
        ["undrain-predecessor", "terminate-candidate"]
    )


def test_uncertain_republication_storage_failure_retains_owner_witness(owned, monkeypatch):
    root, route_root, record, handle, _client, _health, events, _identities = owned
    write = routing_persistence.write
    failure = routing_persistence.PublicationCommitUncertain("durability unavailable")

    def fail(*_args):
        raise failure

    monkeypatch.setattr(routing_persistence, "write", fail)
    with pytest.raises(routing_persistence.PublicationCommitUncertain) as raised:
        recovery.recover_owned_publication(root, record, (101, "1001"), [handle])
    assert raised.value is failure
    assert events == []
    assert handle.poll() is None
    assert breadcrumb.read_breadcrumb(route_root) == record
    monkeypatch.setattr(routing_persistence, "write", write)
    assert recovery.recover_owned_publication(root, record, (101, "1001"), [handle])


@pytest.mark.parametrize("invalid", ["owner", "candidate", "predecessor", "unknown", "route"])
def test_unattributable_or_corrupt_evidence_never_releases_custody(owned, invalid):
    root, route_root, record, handle, _client, _health, events, identities = owned
    if invalid == "owner":
        record["pid"] = os.getpid() + 1
    elif invalid == "candidate":
        identities[202] = "different"
    elif invalid == "predecessor":
        identities[101] = "different"
    elif invalid == "unknown":
        record["state"] = "unknown"
    else:
        routing.routing_table_path(route_root).write_text("{corrupt", encoding="utf-8")
    if invalid == "route":
        with pytest.raises(ValueError):
            recovery.recover_owned_publication(root, record, (101, "1001"), [handle])
    else:
        assert not recovery.recover_owned_publication(root, record, (101, "1001"), [handle])
    assert events == []
    assert handle.poll() is None


def test_commit_forward_requires_actual_predecessor_drain(owned):
    root, route_root, record, handle, client, health, events, _identities = owned
    health[41002]["published"] = True
    client.drained = False
    assert not recovery.recover_owned_publication(root, record, (101, "1001"), [handle])
    assert breadcrumb.read_breadcrumb(route_root) == record
    assert "shutdown-predecessor" not in events
    assert handle.poll() is None
    client.drained = True
    assert recovery.recover_owned_publication(root, record, (101, "1001"), [handle])
    assert events[-1] == "shutdown-predecessor"
