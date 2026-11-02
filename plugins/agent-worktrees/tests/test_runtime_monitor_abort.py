from __future__ import annotations

import time
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from zdd import breadcrumb, diagnostics, publication_history, routing, routing_persistence
from zdd.cutover_lock import CutoverLock, CutoverLockedError

from agent_worktrees import runtime_monitor_abort as abort
from agent_worktrees import runtime_monitor_completion as completion
from agent_worktrees import runtime_monitor_recovery as owned_recovery
from agent_worktrees import runtime_monitor_retirements as retirements


@pytest.fixture
def interrupted(tmp_path, monkeypatch):
    directory = tmp_path / "status-monitor-routing"
    identities = {101: "1001", 202: "2002"}
    alive = set(identities)
    monkeypatch.setattr(diagnostics, "process_start_time", identities.get)
    monkeypatch.setattr(diagnostics, "_pid_alive", alive.__contains__)
    def observe(pid, start):
        if pid in identities:
            return identities[pid] != start
        if pid in alive:
            raise PermissionError("native original exit is unavailable")
        return True

    monkeypatch.setattr(abort.process_exit, "observe_original_exit", observe)
    monkeypatch.setattr(retirements.locks, "process_start_time", identities.get)
    old = routing.publish_active(
        directory, bind="127.0.0.1", port=41001, pid=101, process_start_time="1001",
    )
    witness = routing.publication_witness(directory, deadline=time.monotonic() + 1)
    predecessor = {"pid": 101, "start": "1001", "slot": None, "release_claim": None}
    retirements.reserve(tmp_path, predecessor)
    abort.cutover.control_token_path(tmp_path).write_text("synthetic-control-token", encoding="utf-8")
    record = breadcrumb.write_breadcrumb(
        directory, state="started", old=old.to_dict(), new_pid=202, new_port=41002,
        new_process_start_time="2002", publication_witness=witness, self_retirement=True, pid=303,
    )
    events = []

    def authority():
        with pytest.raises(CutoverLockedError):
            with CutoverLock(directory):
                pytest.fail("recovery dropped cutover custody")

    class Client:
        def __init__(self, url, **kwargs):
            self.port = int(url.rsplit(":", 1)[1])
            self.config_dir = kwargs.get("config_dir")
            self.guarded_predecessor = None

        def health(self):
            authority()
            pid = 101 if self.port == 41001 else 202
            return {
                "pid": pid, "status": "ready", "published": pid == 101,
                "runtime_generation": "old-generation",
                "capabilities": [abort.recovery.CAPABILITY],
                "worktrees": {"status": "ready"}, "repositories": {"admitting": True},
            }

        def undrain(self):
            authority()
            assert self.port == 41001
            self.guarded_predecessor.operation(directory, "http://127.0.0.1:41001")
            events.append("guarded-undrain")
            return {"draining": False}

    def native(pid, start, *, exit_timeout):
        authority()
        with pytest.raises(routing.RoutingLockTimeout):
            with routing.routing_lock(directory, deadline=time.monotonic() + 0.01):
                pytest.fail("passive disposal dropped native routing custody")
        assert (pid, start, exit_timeout) == (202, "2002", 1)
        events.append("terminate-passive")
        alive.remove(202)
        identities.pop(202)
        events.append("native-exit")
        return {"identity_verified": True, "killed": True, "exit_confirmed": True}

    monkeypatch.setattr(abort.cutover, "ControlClient", Client)
    monkeypatch.setattr(diagnostics, "terminate_pid_if_identity", native)
    return tmp_path, directory, old, record, predecessor, identities, alive, events


@pytest.mark.parametrize("candidate", ["absent", "passive", "dead-selected", "reused-selected"])
def test_departed_install_recovers_prepared_custody_without_retirement_grant(interrupted, candidate):
    root, directory, old, record, _predecessor, identities, alive, events = interrupted
    if candidate == "absent":
        record = breadcrumb.write_breadcrumb(
            directory, state="started", old=old.to_dict(), new_port=41002, pid=303,
            self_retirement=True, publication_witness=record["publication_witness"],
        )
    elif candidate in {"dead-selected", "reused-selected"}:
        routing.publish_active(
            directory, bind="127.0.0.1", port=41002, pid=202,
            process_start_time="2002", demote_existing=True,
        )
        if candidate == "dead-selected":
            identities.pop(202)
            alive.remove(202)
        else:
            identities[202] = "3003"
    with CutoverLock(directory):
        assert abort.recover_abandoned(root)
    assert breadcrumb.read_breadcrumb(directory) is None
    assert routing.read_active_endpoint(directory, verify_listener=False).pid == 101
    assert not retirements._records(root)
    assert events == (
        ["terminate-passive", "native-exit", "guarded-undrain"]
        if candidate == "passive" else ["guarded-undrain"]
    )


@pytest.mark.parametrize("unsafe", ["history", "unknown-exit", "foreign", "granted", "owner-alive"])
def test_unproven_or_granted_publication_keeps_witness_and_custody(interrupted, unsafe):
    root, directory, _old, record, predecessor, identities, alive, events = interrupted
    if unsafe == "history":
        monkey = routing.read_table(directory, strict=True)
        monkey[publication_history.KEY]["saturated"] = True
        routing_persistence.write(routing.routing_table_path(directory), monkey)
    elif unsafe == "unknown-exit":
        identities.pop(202)
    elif unsafe == "foreign":
        identities[404] = "4004"
        routing.publish_active(directory, bind="127.0.0.1", port=41004, pid=404, process_start_time="4004")
    elif unsafe == "granted":
        retirements.commit(root, predecessor)
    else:
        alive.add(303)
    with CutoverLock(directory):
        if unsafe in {"unknown-exit", "foreign"}:
            with pytest.raises(ValueError):
                abort.recover_abandoned(root)
        else:
            assert not abort.recover_abandoned(root)
    assert breadcrumb.read_breadcrumb(directory) == record
    assert retirements._records(root)
    assert not events


def test_first_history_durability_failure_retries_without_losing_passive_exit(interrupted, monkeypatch):
    root, directory, _old, record, _predecessor, _identities, _alive, events = interrupted
    write = routing_persistence.write
    def fail(*args, **kwargs):
        raise routing_persistence.PublicationCommitUncertain("storage")

    monkeypatch.setattr(routing_persistence, "write", fail)
    with CutoverLock(directory), pytest.raises(routing_persistence.PublicationCommitUncertain):
        abort.recover_abandoned(root)
    assert breadcrumb.read_breadcrumb(directory) == record
    assert events == ["terminate-passive", "native-exit"]
    assert retirements._records(root)[0][1]["state"] == "prepared"
    monkeypatch.setattr(routing_persistence, "write", write)
    with CutoverLock(directory):
        assert abort.recover_abandoned(root)
    assert events == ["terminate-passive", "native-exit", "guarded-undrain"]
    assert breadcrumb.read_breadcrumb(directory) is None


def test_surviving_predecessor_runs_guarded_recovery_after_selected_candidate_death(
    interrupted, monkeypatch,
):
    root, directory, _old, _record, _predecessor, identities, alive, events = interrupted
    routing.publish_active(
        directory, bind="127.0.0.1", port=41002, pid=202, process_start_time="2002", demote_existing=True,
    )
    identities.pop(202)
    alive.remove(202)
    monkeypatch.setattr(completion.os, "getpid", lambda: 101)
    completion.recover(root, promote=lambda: pytest.fail("dead candidate must not promote"), ready=lambda: False)
    assert events == ["guarded-undrain"]
    assert breadcrumb.read_breadcrumb(directory) is None


@pytest.mark.parametrize("failure", ["cancel", "terminal-write", "cleanup"])
@pytest.mark.parametrize("candidate", [True, False])
def test_restored_precommit_outcome_retains_retry_custody_across_cleanup_failure(
    interrupted, monkeypatch, failure, candidate,
):
    root, directory, _old, record, _predecessor, _identities, _alive, _events = interrupted
    if not candidate:
        record = breadcrumb.write_breadcrumb(
            directory, state="started", old=record["old"], new_port=record["new_port"],
            self_retirement=True, publication_witness=record["publication_witness"], pid=record["pid"],
        )
    original_cancel = retirements.cancel
    original_write = breadcrumb.write_breadcrumb
    original_clear = breadcrumb.clear_breadcrumb
    def fail(*args, **kwargs):
        raise OSError("cleanup unavailable")

    if failure == "cancel":
        monkeypatch.setattr(retirements, "cancel", fail)
    elif failure == "terminal-write":
        def write(path, **kwargs):
            if kwargs["state"] == "rolled_back":
                return fail()
            return original_write(path, **kwargs)

        monkeypatch.setattr(breadcrumb, "write_breadcrumb", write)
    else:
        monkeypatch.setattr(breadcrumb, "clear_breadcrumb", lambda path: False)
    with CutoverLock(directory), pytest.raises(OSError):
        abort.recover_abandoned(root)
    retained = breadcrumb.read_breadcrumb(directory)
    assert retained["pid"] == record["pid"]
    assert retained["state"] == ("rolled_back" if failure == "cleanup" else "started")
    monkeypatch.setattr(retirements, "cancel", original_cancel)
    monkeypatch.setattr(breadcrumb, "write_breadcrumb", original_write)
    monkeypatch.setattr(breadcrumb, "clear_breadcrumb", original_clear)
    with CutoverLock(directory):
        assert abort.recover_abandoned(root)
    assert breadcrumb.read_breadcrumb(directory) is None
    assert not retirements._records(root)


def test_original_installer_consumes_verified_precommit_outcome(interrupted):
    root, directory, old, record, _predecessor, _identities, _alive, _events = interrupted
    record = breadcrumb.write_breadcrumb(
        directory, state=record["state"], old=old.to_dict(), new_pid=record["new_pid"],
        new_port=record["new_port"], new_process_start_time=record["new_process_start_time"],
        self_retirement=True, publication_witness=record["publication_witness"], pid=os.getpid(),
    )
    assert owned_recovery.recover_owned_publication(root, record, (old.pid, old.process_start_time), []) == "rolled_back"
    assert breadcrumb.read_breadcrumb(directory) is None


@pytest.mark.parametrize("owner", ["resident", "installer"])
def test_dead_committed_successor_never_revokes_grants_or_waits_for_old_work(interrupted, owner):
    root, directory, old, record, predecessor, identities, alive, events = interrupted
    retirements.commit(root, predecessor)
    routing.publish_active(
        directory, bind="127.0.0.1", port=41002, pid=202,
        process_start_time="2002", demote_existing=True,
    )
    identities.pop(202)
    alive.remove(202)
    record = breadcrumb.write_breadcrumb(
        directory, state="committed", old=old.to_dict(), new_pid=202, new_port=41002,
        new_process_start_time="2002", self_retirement=True,
        pid=os.getpid() if owner == "installer" else 303,
        publication_witness=record["publication_witness"],
    )
    if owner == "installer":
        assert owned_recovery.recover_owned_publication(root, record, (old.pid, old.process_start_time), []) == "committed"
    else:
        completion.recover(
            root, promote=lambda: pytest.fail("dead successor cannot promote"), ready=lambda: False,
        )
    assert breadcrumb.read_breadcrumb(directory) is None
    assert routing.read_table(directory, strict=True).get("active") is None
    assert retirements._records(root)[0][1]["state"] == "retiring"
    assert identities[101] == "1001" and not events
    assert retirements.retirement_only(root, {"pid": 101})
    identities[101] = "3003"
    assert not retirements.retirement_only(root, {"pid": 101})


@pytest.mark.parametrize("selected", [False, True])
def test_cold_interruption_can_settle_without_inventing_a_predecessor(tmp_path, monkeypatch, selected):
    directory = tmp_path / "status-monitor-routing"
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "2002" if pid == 202 else None)
    witness = routing.publication_witness(directory, deadline=time.monotonic() + 1)
    if selected:
        routing.publish_active(directory, bind="127.0.0.1", port=41002, pid=202, process_start_time="2002")
    record = breadcrumb.write_breadcrumb(
        directory, state="started", new_pid=202 if selected else None, new_port=41002,
        new_process_start_time="2002" if selected else None, publication_witness=witness,
        self_retirement=True, pid=303,
    )
    monkeypatch.setattr(retirements.locks, "process_start_time", lambda pid: None)
    monkeypatch.setattr(diagnostics, "_pid_alive", lambda pid: False)
    with CutoverLock(directory):
        assert abort.recover_abandoned(tmp_path)
    assert breadcrumb.read_breadcrumb(directory) is None
    assert routing.read_table(directory, strict=True).get("active") is None
    if selected:
        assert not publication_history.never_published(
            routing.read_table(directory, strict=True), record["publication_witness"], pid=202, port=41002,
        )


def test_publication_during_passive_health_cannot_authorize_disposal(interrupted, monkeypatch):
    root, directory, _old, record, _predecessor, _identities, _alive, events = interrupted
    client = abort.cutover.ControlClient
    health = client.health
    def publish(self):
        result = health(self)
        if self.port == 41002:
            routing.publish_active(
                directory, bind="127.0.0.1", port=41002, pid=202,
                process_start_time="2002", demote_existing=True,
            )
        return result

    monkeypatch.setattr(client, "health", publish)
    with CutoverLock(directory), pytest.raises(ValueError, match="changed before native termination"):
        abort.recover_abandoned(root)
    assert not events
    assert breadcrumb.read_breadcrumb(directory) == record
    assert retirements._records(root)[0][1]["state"] == "prepared"


@pytest.mark.parametrize("predecessor", ["dead", "reused", "unknown"])
def test_precommit_predecessor_exit_cancels_without_undrain(interrupted, predecessor):
    root, directory, _old, record, _claim, identities, alive, events = interrupted
    identities.pop(101)
    if predecessor == "dead":
        alive.remove(101)
    elif predecessor == "reused":
        identities[101] = "3003"
    with CutoverLock(directory):
        if predecessor == "unknown":
            with pytest.raises(ValueError, match="native exit is unavailable"):
                abort.recover_abandoned(root)
        else:
            assert abort.recover_abandoned(root)
    assert "guarded-undrain" not in events
    if predecessor != "unknown":
        assert breadcrumb.read_breadcrumb(directory) is None
        assert not retirements._records(root)
        assert routing.read_table(directory, strict=True).get("active") is None


def test_successful_handoff_successor_crash_preserves_granted_fallback(interrupted):
    from agent_worktrees.runtime_monitor_identity import routed_identity

    root, directory, old, _record, predecessor, identities, alive, events = interrupted
    retirements.commit(root, predecessor)
    routing.publish_active(
        directory, bind="127.0.0.1", port=41002, pid=202, process_start_time="2002", demote_existing=True,
    )
    breadcrumb.clear_breadcrumb(directory)
    identities.pop(202)
    alive.remove(202)
    assert routed_identity(root, require_async_retirement=True) is None
    with CutoverLock(directory):
        assert not abort.recover_abandoned(root)
    assert routing.read_table(directory, strict=True).get("active") is None
    assert routed_identity(root, require_async_retirement=True) is None
    assert retirements.retirement_only(root, old.to_dict())
    assert retirements._records(root)[0][1]["state"] == "retiring"
    assert not events


@pytest.mark.parametrize("accepted", [False, True])
def test_darwin_passive_cancellation_uses_pinned_exit_and_exact_cooperative_operation(
    interrupted, monkeypatch, accepted,
):
    root, directory, _old, record, _claim, identities, alive, events = interrupted
    from agent_worktrees import runtime_monitor_preparation as preparation

    client = abort.cutover.ControlClient
    health = client.health
    def capable(self):
        body = health(self)
        body["capabilities"].append(preparation.CAPABILITY)
        return body

    def request(self, action, payload):
        assert action == preparation.ACTION
        assert payload == {
            "pid": 202, "start": "2002", "port": 41002,
            "started_at": record["started_at"], "publication_witness": record["publication_witness"],
        }
        events.append("cooperative-cancel")
        if accepted:
            identities.pop(202)
            alive.remove(202)
        return {"shutdown": accepted}

    @contextmanager
    def native(pid, start):
        assert (pid, start) == (202, "2002")
        events.append("native-reference")
        yield SimpleNamespace(wait=lambda timeout: 202 not in alive)

    monkeypatch.setattr(abort, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(client, "health", capable)
    monkeypatch.setattr(client, "_request", request, raising=False)
    monkeypatch.setattr(abort.process_exit, "open_process", native)
    monkeypatch.setattr(
        diagnostics, "terminate_pid_if_identity", lambda *a, **k: pytest.fail("Darwin must not signal by PID"),
    )
    with CutoverLock(directory):
        assert abort.recover_abandoned(root) is accepted
    assert events[:2] == ["native-reference", "cooperative-cancel"]
    assert (breadcrumb.read_breadcrumb(directory) is None) is accepted


@pytest.mark.parametrize("birth", ["2002", None])
def test_reused_installer_pid_does_not_keep_recovery_alive(interrupted, birth):
    root, directory, _old, record, _claim, identities, alive, _events = interrupted
    routing_persistence.write(
        breadcrumb.breadcrumb_path(directory), {**record, "owner_process_start_time": "1001"},
    )
    alive.add(303)
    if birth is not None:
        identities[303] = birth
    with CutoverLock(directory):
        assert abort.recover_abandoned(root) is (birth is not None)
    assert (breadcrumb.read_breadcrumb(directory) is None) is (birth is not None)


@pytest.mark.parametrize("reused", [False, True])
def test_native_passive_identity_refusal_never_restores_or_cancels_custody(
    interrupted, monkeypatch, reused,
):
    root, directory, _old, record, _claim, identities, alive, events = interrupted
    table = routing.read_table(directory, strict=True)
    client = abort.cutover.ControlClient
    health = client.health
    def changed_after_health(self):
        body = health(self)
        if self.port == 41002 and reused:
            identities[202] = "3003"
        return body

    def refuse(pid, captured, *, exit_timeout):
        assert (pid, captured, exit_timeout) == (202, "2002", 1)
        assert identities[pid] == ("3003" if reused else captured)
        events.append("native-refusal")
        return {"killed": False, "identity_verified": False}

    monkeypatch.setattr(client, "health", changed_after_health)
    monkeypatch.setattr(diagnostics, "terminate_pid_if_identity", refuse)
    with CutoverLock(directory):
        assert not abort.recover_abandoned(root)
    assert events == ["native-refusal"]
    assert 202 in alive
    assert routing.read_table(directory, strict=True) == table
    assert breadcrumb.read_breadcrumb(directory) == record
    assert retirements._records(root)[0][1]["state"] == "prepared"
