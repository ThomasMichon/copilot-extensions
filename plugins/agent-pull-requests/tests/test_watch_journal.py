"""Durable transaction intent is not successful control or writer transfer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace

import pytest
from agent_procutil import no_window_kwargs
from zdd.cutover_lock import CutoverLock

from agent_pull_requests.watch_journal import WatchJournal, authorize_control
from agent_pull_requests import _installation_context as api
from test_watch_installation import _installation, _mutate, _policy
from test_watch_runtime import _complete, _instance

# Real canonical receipt/slot validation at every journal boundary is I/O-heavy.
pytestmark = pytest.mark.timeout(90)


def _manifest(runtime):
    return json.dumps({
        "schema_version": 3, "service": "agent-pull-requests",
        "source": {"plugin": "agent-pull-requests", "version": runtime.payload_version},
        "venv": str(runtime.runtime_root),
    }).encode()


def _route(instance):
    return json.dumps({"active": {
        "bind": "127.0.0.1", "port": 12345, "pid": instance.pid,
        "process_start_time": instance.start_time,
        "version": instance.runtime.runtime_version, "generation": 1,
    }}).encode()


@pytest.fixture
def cell(tmp_path):
    installation = _installation(tmp_path)
    old = _complete(installation)
    target = _complete(installation, version="2.0.0")
    return installation, _instance(old), target


@pytest.fixture
def passive(cell):
    installation, _, target = cell
    code = """
import json, os, sys
from pathlib import Path
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_runtime import WatchInstance
from agent_pull_requests.watch_journal import authorize_control
print(os.getpid(), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request["operation"] == "exit":
        break
    installation = WatchInstallation.capture(
        **{key: Path(value) for key, value in request["installation"].items()},
        environment={},
    )
    instance = WatchInstance.from_evidence(request["instance"], installation, environment={})
    try:
        authorize_control(
            installation, instance, operation=request["operation"],
            transaction_id=request["transactionId"], authorization=request["authorization"],
            environment={},
        )
        print("accepted", flush=True)
    except PermissionError:
        print("refused", flush=True)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    try:
        assert child.stdout is not None
        actual_pid = int(child.stdout.readline().strip())
        yield child, _instance(target, pid=actual_pid)
    finally:
        if child.poll() is None:
            assert child.stdin is not None
            child.stdin.write('{"operation":"exit"}\n')
            child.stdin.flush()
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()


def _begin(cell, guard, *, live=True):
    installation, predecessor, target = cell
    return WatchJournal.begin(
        installation, guard, target=target, target_manifest=_manifest(target),
        predecessor=predecessor if live else None, environment={},
    )


def _authorize(installation, instance, journal, expected_operation, **overrides):
    args = {
        "operation": expected_operation, "transaction_id": journal.transaction_id,
        "authorization": journal.authorization, "environment": {}, "held_guard": journal.guard,
    }
    args.update(overrides)
    authorize_control(installation, instance, **args)


def _select(installation, target, *, route=None):
    for name in ("current-version", "last-known-good"):
        (installation.plugin_root / name).write_text(target.runtime_version + "\n", encoding="utf-8")
    (installation.plugin_root / "deploy-manifest.json").write_bytes(_manifest(target))
    if route is not None:
        (installation.run_root / "active.json").write_bytes(route)


def _publish(cell, journal, passive):
    installation, _, target = cell
    _, successor = passive
    route = _route(successor)
    journal.bind_successor(successor, route=route)
    for phase in ("draining", "released", "read-ready", "publishing"):
        journal.advance(phase)
    _select(installation, target, route=route)
    return successor


def _ready(instance, *, effects=False):
    return {
        "instance": instance.evidence(), "writerHeld": True,
        "readReady": True, "effectsEnabled": effects,
    }

def _request_receiver(passive, installation, journal, operation):
    child, successor = passive
    assert child.stdin is not None and child.stdout is not None
    child.stdin.write(json.dumps({
        "installation": {
            key: str(getattr(installation, key)) for key in (
                "receipt", "plugin_root", "payload_root", "durable_home", "os_profile",
            )
        }, "instance": successor.evidence(), "operation": operation,
        "transactionId": journal.transaction_id, "authorization": journal.authorization,
    }) + "\n")
    child.stdin.flush()
    return child.stdout.readline().strip()


def test_cold_commit_and_recovery_are_metadata_only(cell):
    installation, _, target = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        assert not installation.state_root.exists()
        with pytest.raises(PermissionError, match="not coherent"):
            journal.commit()
        _select(installation, target)
        journal.commit()
        recovered = WatchJournal.recover(installation, guard, environment={})
        assert recovered.transaction_id == journal.transaction_id
        assert recovered.authorization != journal.authorization
        with pytest.raises(PermissionError, match="forward-only"):
            recovered.rollback()
        recovered.finish()
    assert not installation.state_root.exists()
    assert not (installation.run_root / "active.json").exists()


@pytest.mark.parametrize("phase", ["prepared", "passive", "draining", "released", "read-ready", "publishing"])
def test_precommit_crash_recovery_preserves_prior_custody(cell, passive, phase):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        if phase != "prepared":
            journal.bind_successor(passive[1], route=_route(passive[1]))
            for step in ("draining", "released", "read-ready", "publishing"):
                if journal._document["phase"] == phase:
                    break
                journal.advance(step)
        recovered = WatchJournal.recover(installation, guard, environment={})
        assert recovered._document["phase"] == journal._document["phase"]
        assert recovered.authorization != journal.authorization
        recovered.rollback()
        assert recovered._document["phase"] == "rolling-back"
        assert recovered._document["prior"] == journal._document["prior"]
    assert not installation.state_root.exists()


def test_shared_breadcrumb_clear_cannot_erase_owner_commit(cell, passive):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        successor = _publish(cell, journal, passive)
        journal.commit(read_health=lambda: _ready(successor))
        breadcrumb = installation.run_root / "cutover.json"
        breadcrumb.write_text("{}", encoding="utf-8")
        breadcrumb.unlink()
        recovered = WatchJournal.recover(installation, guard, environment={})
        assert recovered._document["phase"] == "committed"
        with pytest.raises(PermissionError, match="forward-only"):
            recovered.rollback()
        with pytest.raises(PermissionError, match="not authorized"):
            _authorize(installation, predecessor, recovered, "resume")
        with pytest.raises(PermissionError, match="retirement"):
            recovered.finish(read_health=lambda: _ready(successor, effects=True))


def test_receiver_authorization_runs_in_actual_successor_process(cell, passive):
    installation, _, _ = cell
    _, successor = passive

    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(successor, route=_route(successor))
        assert _request_receiver(passive, installation, journal, "adopt") == "refused"
        journal.advance("draining")
        journal.advance("released")
        assert _request_receiver(passive, installation, journal, "adopt") == "accepted"
        assert _request_receiver(passive, installation, journal, "activate") == "refused"
        assert _request_receiver(passive, installation, journal, "resume") == "refused"


def test_capability_without_native_controller_guard_is_refused(cell, passive):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(passive[1], route=_route(passive[1]))
        journal.advance("draining")
        journal.advance("released")
        assert _request_receiver(passive, installation, journal, "adopt") == "accepted"
    assert _request_receiver(passive, installation, journal, "adopt") == "refused"
    assert passive[0].poll() is None


def test_live_native_controller_identity_with_foreign_guard_holder_is_refused(cell, passive):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(passive[1], route=_route(passive[1]))
        journal.advance("draining")
        journal.advance("released")
        _mutate(installation.run_root / "watch-owner-transaction.json", lambda value: value.update(
            controller={"pid": passive[1].pid, "startTime": passive[1].start_time},
        ))
        assert _request_receiver(passive, installation, journal, "adopt") == "refused"
        assert guard.held and passive[0].poll() is None


def test_recovery_rotates_capability_before_new_controller_actions(cell, passive):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(passive[1], route=_route(passive[1]))
        journal.advance("draining")
        recovered = WatchJournal.recover(installation, guard, environment={})
        with pytest.raises(PermissionError, match="transaction authorization"):
            _authorize(installation, predecessor, journal, "drain")
        _authorize(installation, predecessor, recovered, "drain")


@pytest.mark.parametrize("override", [
    {"authorization": ""}, {"authorization": "0" * 64},
    {"transaction_id": "foreign"}, {"operation": "activate"}, {"operation": "unknown"},
])
def test_random_transaction_authorization_and_phase_are_required(cell, passive, override):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(passive[1], route=_route(passive[1]))
        journal.advance("draining")
        _authorize(installation, predecessor, journal, "drain")
        with pytest.raises(PermissionError):
            _authorize(installation, predecessor, journal, "drain", **override)


def test_matching_native_process_without_exact_instance_is_refused(cell, passive):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(passive[1], route=_route(passive[1]))
        journal.advance("draining")
        foreign = replace(predecessor, instance_id=passive[1].instance_id)
        with pytest.raises(PermissionError, match="not authorized"):
            _authorize(installation, foreign, journal, "drain")


def test_rollback_requires_captured_successor_exit_before_predecessor_resume(cell, passive):
    installation, predecessor, _ = cell
    child, successor = passive
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(successor, route=_route(successor))
        journal.advance("draining")
        journal.advance("released")
        journal.rollback()
        with pytest.raises(PermissionError, match="not authorized"):
            _authorize(installation, predecessor, journal, "resume")
        with successor.capture_exit(
            installation, read_health=lambda: {"instance": successor.evidence()}, environment={},
        ) as reference:
            with pytest.raises(TimeoutError, match="has not exited"):
                journal.record_exit("successor", reference, timeout=0)
            assert child.stdin is not None
            child.stdin.write('{"operation":"exit"}\n')
            child.stdin.flush()
            journal.record_exit("successor", reference, timeout=5)
            assert child.wait(timeout=5) == 0
        _authorize(installation, predecessor, journal, "resume")
        journal.finish(read_health=lambda: _ready(predecessor, effects=True))
        assert journal._document["phase"] == "rolled-back"


def test_live_exit_reference_is_not_foreign_retirement_evidence(cell, passive):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        journal.bind_successor(passive[1], route=_route(passive[1]))
        journal.rollback()
        with predecessor.capture_exit(
            installation, read_health=lambda: {"instance": predecessor.evidence()}, environment={},
        ) as reference:
            with pytest.raises(PermissionError, match="another instance"):
                journal.record_exit("successor", reference, timeout=0)


@pytest.mark.parametrize("field", ["current-version", "last-known-good", "deploy-manifest.json", "active.json"])
def test_incoherent_selection_cannot_authorize_commit(cell, passive, field):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        successor = _publish(cell, journal, passive)
        root = installation.run_root if field == "active.json" else installation.plugin_root
        (root / field).write_text("{}", encoding="utf-8")
        with pytest.raises(PermissionError, match="not coherent"):
            journal.commit(read_health=lambda: _ready(successor))
        assert journal._document["phase"] == "publishing"


@pytest.mark.parametrize("field", ["writerHeld", "readReady", "effectsEnabled"])
def test_commit_refuses_unready_or_already_effectful_successor(cell, passive, field):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        successor = _publish(cell, journal, passive)
        health = _ready(successor)
        health[field] = not health[field]
        with pytest.raises(PermissionError, match="effect-free"):
            journal.commit(read_health=lambda: health)


@pytest.mark.parametrize("boundary", ["commit", "finish", "bind"])
def test_journal_validates_runtime_after_actual_health_before_persisting(cell, passive, boundary):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        if boundary == "commit":
            instance = _publish(cell, journal, passive)
            action = lambda health: journal.commit(read_health=health)
        elif boundary == "finish":
            instance = predecessor
            journal.rollback()
            action = lambda health: journal.finish(read_health=health)
        else:
            instance = predecessor
            action = lambda health: journal.bind_endpoint(
                "predecessor", ("127.0.0.1", 12345, "test-owned" * 4), read_health=health,
            )
        path = installation.run_root / "watch-owner-transaction.json"
        before = path.read_bytes()
        reads = []

        def health():
            reads.append(True)
            (instance.runtime.runtime_root / api.RUNTIME_SLOT_COMPLETION_FILE).unlink()
            return _ready(instance, effects=boundary == "finish")

        with pytest.raises(api.InstallationContextError):
            action(health)
        assert reads == [True]
        assert path.read_bytes() == before


def test_governance_disable_blocks_commit_recovery_and_control(cell, passive):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        successor = _publish(cell, journal, passive)
        _policy(installation.os_profile, False)
        for action in (
            lambda: journal.commit(read_health=lambda: _ready(successor)),
            lambda: WatchJournal.recover(installation, guard, environment={}),
            lambda: _authorize(installation, predecessor, journal, "drain"),
        ):
            with pytest.raises(PermissionError, match="governance"):
                action()


def test_wrong_guard_or_released_guard_cannot_write_journal(cell, tmp_path):
    installation, _, _ = cell
    with CutoverLock(tmp_path / "foreign") as guard:
        with pytest.raises(PermissionError, match="installation cutover guard"):
            _begin(cell, guard)
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
    with pytest.raises(RuntimeError):
        journal.rollback()


def test_existing_journal_never_becomes_absence_or_second_transaction(cell):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        with pytest.raises(FileExistsError, match="reconciliation"):
            _begin(cell, guard)
        assert WatchJournal.recover(installation, guard, environment={}).transaction_id == journal.transaction_id
        (installation.run_root / "watch-owner-transaction.json").write_text("broken", encoding="utf-8")
        with pytest.raises(ValueError):
            WatchJournal.recover(installation, guard, environment={})

def test_owner_journal_mutation_can_revalidate_inside_borrowed_shared_callback(cell):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        with guard.borrow():
            journal.rollback()
            assert guard.held
        assert WatchJournal.recover(installation, guard, environment={})._document["phase"] == "rolling-back"


@pytest.mark.parametrize("scope", ["process", "thread"])
def test_journal_refuses_inherited_or_foreign_execution_before_guard_access(cell, scope):
    import threading

    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        journal._execution = (
            os.getpid() + 1 if scope == "process" else os.getpid(),
            threading.Thread() if scope == "thread" else threading.current_thread(),
        )
        with pytest.raises(PermissionError, match="another controller execution"):
            journal.rollback()
        assert guard.held


def test_journal_duplicate_fields_and_receipt_advance_refuse(cell):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        path = installation.run_root / "watch-owner-transaction.json"
        original = path.read_bytes()
        path.write_bytes(b'{"schema":1,' + original[1:])
        with pytest.raises(ValueError, match="duplicate"):
            WatchJournal.recover(installation, guard, environment={})
        path.write_bytes(original)
        _mutate(installation.receipt, lambda value: value.update(generation=2))
        with pytest.raises(PermissionError, match="receipts changed"):
            journal.rollback()


def test_linked_journal_refuses_without_adopting_foreign_bytes(cell, tmp_path):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        _begin(cell, guard, live=False)
        path = installation.run_root / "watch-owner-transaction.json"
        other = tmp_path / "foreign-journal.json"
        path.rename(other)
        try:
            path.symlink_to(other)
        except OSError:
            other.rename(path)
            pytest.skip("host does not permit symlink creation")
        with pytest.raises(PermissionError, match="non-link"):
            WatchJournal.recover(installation, guard, environment={})


def test_durable_ambiguous_commit_is_recovered_forward_only(cell, monkeypatch):
    from agent_pull_requests import watch_journal

    installation, _, target = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        _select(installation, target)
        write = watch_journal.write_private

        def uncertain(path, document, **kwargs):
            write(path, document, **kwargs)
            raise OSError("test-owned lost durability acknowledgement")

        monkeypatch.setattr(watch_journal, "write_private", uncertain)
        with pytest.raises(OSError, match="acknowledgement"):
            journal.commit()
        monkeypatch.setattr(watch_journal, "write_private", write)
        recovered = WatchJournal.recover(installation, guard, environment={})
        assert recovered._document["phase"] == "committed"
        with pytest.raises(PermissionError, match="forward-only"):
            recovered.rollback()


def test_postcommit_route_drift_blocks_retirement(cell, passive):
    installation, predecessor, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard)
        successor = _publish(cell, journal, passive)
        journal.commit(read_health=lambda: _ready(successor))
        _authorize(installation, predecessor, journal, "retire")
        _mutate(installation.run_root / "active.json", lambda value: value["active"].update(pid=os.getpid()))
        with pytest.raises(PermissionError, match="not coherent"):
            _authorize(installation, predecessor, journal, "retire")


@pytest.mark.parametrize("changes_during_read", [False, True])
def test_bounded_read_compares_each_stat_api_metadata_with_itself(tmp_path, monkeypatch, changes_during_read):
    from types import SimpleNamespace
    from agent_pull_requests.watch_journal import _regular_bytes

    path = tmp_path / "metadata.json"
    path.write_bytes(b"test-owned metadata")
    fstat = os.fstat
    observations = []

    def different_ctime(descriptor):
        observed = fstat(descriptor)
        observations.append(True)
        return SimpleNamespace(
            st_dev=observed.st_dev, st_ino=observed.st_ino, st_size=observed.st_size,
            st_mtime_ns=observed.st_mtime_ns, st_mode=observed.st_mode,
            st_ctime_ns=observed.st_ctime_ns + 100 + (
                1 if changes_during_read and len(observations) > 1 else 0
            ),
        )

    monkeypatch.setattr(os, "fstat", different_ctime)
    if changes_during_read:
        with pytest.raises(PermissionError, match="changed during"):
            _regular_bytes(path)
    else:
        assert _regular_bytes(path) == b"test-owned metadata"


def test_oversized_recovery_document_refuses_before_replacing_prior_journal(tmp_path):
    from agent_pull_requests.watch_journal import _persist

    path = tmp_path / "journal.json"
    path.write_bytes(b"prior custody")
    with pytest.raises(ValueError, match="bounded recovery size"):
        _persist(path, {"oversized": "x" * 1_048_576})
    assert path.read_bytes() == b"prior custody"
