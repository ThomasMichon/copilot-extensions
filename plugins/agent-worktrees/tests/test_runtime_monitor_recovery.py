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
    monkeypatch.setattr(diagnostics, "_pid_alive", lambda pid: pid in identities)
    monkeypatch.setattr(
        "zdd.process_exit.observe_original_exit", lambda pid, start: identities.get(pid) != start,
    )
    monkeypatch.setattr(recovery.locks, "process_start_time", identities.get)
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
    assert events == ([] if published else ["undrain-predecessor", "terminate-candidate"])


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


def test_rollback_preserves_route_token_when_native_birth_token_differs(owned, monkeypatch):
    root, route_root, record, handle, _client, _health, _events, _identities = owned
    monkeypatch.setattr(recovery.locks, "process_start_time", lambda pid: "123:456" if pid == 101 else None)
    assert recovery.recover_owned_publication(root, record, (101, "123:456"), [handle])
    restored = routing.read_active_endpoint(route_root, verify_listener=False)
    assert restored.pid == 101
    assert restored.process_start_time == "1001"


@pytest.mark.parametrize("old_state", ["busy", "exited", "reused"])
def test_commit_forward_does_not_wait_for_or_contact_the_predecessor(owned, monkeypatch, old_state):
    root, route_root, record, handle, client, health, events, identities = owned
    health[41002]["published"] = True
    client.drained = False
    if old_state == "exited":
        identities.pop(101)
    elif old_state == "reused":
        identities[101] = "different"
    original = client.health

    def candidate_only(self):
        assert self.port == 41002, "recovery contacted the predecessor"
        return original(self)

    monkeypatch.setattr(client, "health", candidate_only)
    assert recovery.recover_owned_publication(root, record, (101, "1001"), [handle])
    assert breadcrumb.read_breadcrumb(route_root)["state"] == "committed"
    assert events == [] and handle.poll() is None


@pytest.mark.parametrize("phase", ["started", "flipped"])
@pytest.mark.parametrize("grant_fails", [False, True])
def test_original_installer_completes_selected_async_candidate_without_legacy_rollback(
    owned, monkeypatch, phase, grant_fails,
):
    from agent_worktrees import runtime_monitor_retirements as retirements

    root, directory, original, handle, client, health, events, _identities = owned
    record = breadcrumb.write_breadcrumb(
        directory, state=phase, old=original["old"], new_port=41002, new_pid=202,
        new_process_start_time="2002", self_retirement=True,
    )
    monkeypatch.setattr(retirements, "_records", lambda path: [(root / "old.json", {
        "pid": 101, "start": "1001", "slot": None, "release_claim": None, "state": "prepared",
    })])

    def grant(*a, **k):
        events.append("grant")
        if grant_fails:
            raise OSError("grant unavailable")
        return True

    def promote(self):
        assert self.port == 41002
        events.append("promote")
        health[41002].update(
            published=True, worktrees={"status": "ready"}, repositories={"admitting": True},
        )
        return {"adopted": True}

    monkeypatch.setattr(retirements, "commit", grant)
    monkeypatch.setattr(client, "promote", promote, raising=False)
    if grant_fails:
        with pytest.raises(OSError, match="grant unavailable"):
            recovery.recover_owned_publication(root, record, (101, "1001"), [handle])
        assert breadcrumb.read_breadcrumb(directory)["state"] == "committed"
        assert events == ["grant"]
    else:
        assert recovery.recover_owned_publication(root, record, (101, "1001"), [handle]) == "committed"
        assert breadcrumb.read_breadcrumb(directory) is None
        assert events == ["grant", "promote"]
    assert handle.poll() is None
    table = routing.read_table(directory, strict=True)
    assert table["active"]["pid"] == 202 and table["previous"]["pid"] == 101
