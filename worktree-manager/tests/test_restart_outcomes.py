"""Retained owner receipts are historical evidence, never retry authority."""

from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
import sys
import threading

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager.restart_attempts import RestartAttemptError, RestartAttempts
from worktree_manager.restart_evidence import PositiveEvidence, RecoveryJournal
from worktree_manager.restart_observer import HostEpoch


EPOCH = HostEpoch("linux", "environment-one", "boot-two")
STATES = ("not-reserved", "launch-uncertain", "mux-created", "seed-acknowledged")


def receipt(attempt, state):
    evidence = attempt["last_positive"]
    expected = {field: evidence[field] for field in (
        "project", "worktree_id", "worktree_path", "native_session_id",
        "worktree_incarnation", "head_revision", "owner_revision",
        "activation_ordinal", "source_identity",
    )}
    expected["target_epoch"] = attempt["target_epoch"]
    result = {
        "schema": "native-restart-launch", "version": 1,
        "request": {
            "request_id": attempt["attempt_id"], "expected": expected,
            "source_epoch": {key: evidence[key] for key in (
                "platform", "environment_id", "boot_id",
            )},
            "native_incarnation": evidence["native_incarnation"],
        },
        "state": state, "seed_acknowledged": False,
        "restored": False, "automatic_eligible": False,
    }
    if state == "seed-acknowledged":
        result.update(
            version=2, seed_acknowledged=True,
            acknowledgement={
                "native_session_id": evidence["native_session_id"],
                "target_epoch": attempt["target_epoch"],
                "source_identity": "native-source-v1:" + "b" * 64,
                "native_incarnation": "99:456", "session_incarnation": "$1:100",
                "head_revision": evidence["head_revision"],
                "owner_revision": evidence["owner_revision"] + 1,
                "activation_ordinal": evidence["activation_ordinal"] + 1,
                "status_reconciled": True,
            },
        )
    return result


@pytest.fixture
def frozen(tmp_path):
    journal = RecoveryJournal(tmp_path)
    journal.observe(PositiveEvidence(
        project="example", worktree_id="example-one", worktree_path="/example/worktree",
        native_session_id="00000000-0000-4000-8000-000000000001",
        native_incarnation="42:100", binding_proof="inuse-hold-v1", owner_revision=8,
        session_incarnation="mux-one", mapping_revision=1, platform="linux",
        environment_id=EPOCH.environment_id, boot_id="boot-one",
        observed_at=100.0, observation_sequence=1,
        source_identity="native-source-v1:" + "a" * 64,
        activation_ordinal=2, lifecycle_qualified=True,
        worktree_incarnation="0123456789abcdef", head_revision=17,
    ))
    attempts = RestartAttempts(tmp_path)
    attempt, = attempts.freeze(journal.snapshot(), EPOCH)
    return attempts, attempt


def frozen_payload(attempts):
    with sqlite3.connect(attempts.path) as connection:
        return connection.execute("SELECT payload FROM attempts").fetchone()[0]


def test_outcome_upgrade_is_additive_durable_and_preserves_frozen_bytes(frozen):
    attempts, attempt = frozen
    original = frozen_payload(attempts)
    with sqlite3.connect(attempts.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    for state in STATES:
        value = receipt(attempt, state)
        result = attempts.record_outcome(attempt["attempt_id"], value)
        assert result["outcome"] == value
        assert result["state"] == "frozen"
        assert result["automatic_eligible"] is False
        assert frozen_payload(attempts) == original
        restarted, = RestartAttempts(attempts.path.parent.parent).snapshot()
        assert restarted == result
        assert attempts.record_outcome(attempt["attempt_id"], value) == result
    with sqlite3.connect(attempts.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM outcomes").fetchone() == (1,)


@pytest.mark.parametrize("state", STATES[:-1])
def test_stale_owner_never_downgrades_acknowledged_evidence(frozen, state):
    attempts, attempt = frozen
    retained = attempts.record_outcome(
        attempt["attempt_id"], receipt(attempt, "seed-acknowledged"),
    )
    with pytest.raises(RestartAttemptError, match="regress"):
        attempts.record_outcome(attempt["attempt_id"], receipt(attempt, state))
    assert attempts.snapshot() == [retained]


def test_conflicting_ack_execution_is_not_an_idempotent_observation(frozen):
    attempts, attempt = frozen
    value = receipt(attempt, "seed-acknowledged")
    retained = attempts.record_outcome(attempt["attempt_id"], value)
    changed = copy.deepcopy(value)
    changed["acknowledgement"]["native_incarnation"] = "100:789"
    with pytest.raises(RestartAttemptError, match="changed"):
        attempts.record_outcome(attempt["attempt_id"], changed)
    assert attempts.snapshot() == [retained]


@pytest.mark.parametrize("mutation", [
    {"restored": True}, {"automatic_eligible": True}, {"request": {}},
    {"version": True}, {"state": []}, {"seed_acknowledged": True},
])
def test_unconfirmed_receipt_cannot_migrate_or_rewrite_storage(frozen, mutation):
    attempts, attempt = frozen
    value = {**receipt(attempt, "mux-created"), **mutation}
    before = attempts.path.read_bytes()
    with pytest.raises(RestartAttemptError, match="receipt"):
        attempts.record_outcome(attempt["attempt_id"], value)
    assert attempts.path.read_bytes() == before
    assert attempts.snapshot() == [attempt]


@pytest.mark.parametrize("case", ["missing", "foreign", "corrupt", "empty"])
def test_outcome_write_never_initializes_or_repairs_unconfirmed_storage(tmp_path, case):
    attempts = RestartAttempts(tmp_path)
    if case != "missing":
        attempts.path.parent.mkdir()
        if case == "foreign":
            with sqlite3.connect(attempts.path) as connection:
                connection.execute("CREATE TABLE unrelated (value TEXT)")
        else:
            attempts.path.write_bytes(b"corrupt" if case == "corrupt" else b"")
    before = attempts.path.read_bytes() if attempts.path.exists() else None
    with pytest.raises(RestartAttemptError):
        attempts.record_outcome("00000000-0000-4000-8000-000000000005", {})
    assert (attempts.path.read_bytes() if attempts.path.exists() else None) == before


@pytest.mark.parametrize("corruption", [
    "blob", "orphan", "foreign-request", "oversize", "nested-json", "foreign-table",
])
def test_corrupt_retained_outcome_blocks_reads_and_writes_without_reset(frozen, corruption):
    attempts, attempt = frozen
    value = receipt(attempt, "mux-created")
    attempts.record_outcome(attempt["attempt_id"], value)
    with sqlite3.connect(attempts.path) as connection:
        if corruption == "foreign-table":
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        elif corruption == "orphan":
            connection.execute("UPDATE outcomes SET attempt_id=?", (
                "00000000-0000-4000-8000-000000000005",
            ))
        else:
            changed = copy.deepcopy(value)
            changed["request"]["expected"]["native_session_id"] = (
                "00000000-0000-4000-8000-000000000005"
            )
            payload = {
                "blob": sqlite3.Binary(b"invalid"),
                "foreign-request": json.dumps(changed),
                "oversize": " " * 32769,
                "nested-json": "[" * 15000 + "0" + "]" * 15000,
            }[corruption]
            connection.execute("UPDATE outcomes SET payload=?", (payload,))
    before = attempts.path.read_bytes()
    with pytest.raises(RestartAttemptError):
        attempts.snapshot()
    with pytest.raises(RestartAttemptError):
        attempts.record_outcome(attempt["attempt_id"], value)
    assert attempts.path.read_bytes() == before


def test_capacity_refuses_new_attempt_but_retains_existing_outcome(frozen, monkeypatch):
    from worktree_manager import restart_attempts

    attempts, attempt = frozen
    retained = attempts.record_outcome(
        attempt["attempt_id"], receipt(attempt, "seed-acknowledged"),
    )
    monkeypatch.setattr(restart_attempts, "_MAX_ATTEMPTS", 1)
    row = RecoveryJournal(attempts.path.parent.parent).snapshot()[0]
    row["last_positive"]["worktree_id"] = "example-two"
    with pytest.raises(RestartAttemptError, match="limit"):
        attempts.freeze([row], EPOCH)
    assert attempts.snapshot() == [retained]


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("migration", [False, True])
def test_process_crash_keeps_whole_old_or_whole_new_outcome(frozen, committed, migration):
    attempts, attempt = frozen
    previous = attempt if migration else attempts.record_outcome(
        attempt["attempt_id"], receipt(attempt, "launch-uncertain"),
    )
    value = receipt(attempt, "seed-acknowledged")
    code = """
import json,os,sqlite3,sys
from pathlib import Path
from worktree_manager import restart_attempts
original=sqlite3.connect
def connect(*args,**kwargs):
    result=original(*args,**kwargs)
    if sys.argv[4]=='False':
        result.set_trace_callback(lambda sql: os._exit(41) if sql=='COMMIT' else None)
    return result
restart_attempts.sqlite3.connect=connect
restart_attempts.RestartAttempts(Path(sys.argv[1])).record_outcome(sys.argv[2],json.loads(sys.argv[3]))
os._exit(42)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(attempts.path.parent.parent),
         attempt["attempt_id"], json.dumps(value), str(committed)],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert process.returncode == (42 if committed else 41), process.stderr
    retained, = attempts.snapshot(recover_existing=True)
    assert retained.get("outcome") == (value if committed else previous.get("outcome"))
    assert retained["last_positive"] == attempt["last_positive"]
    with sqlite3.connect(attempts.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (
            1 if migration and not committed else 2,
        )


def test_concurrent_owner_observations_never_replace_newer_receipt(frozen):
    attempts, attempt = frozen
    code = """
import json,sys
from pathlib import Path
from worktree_manager.restart_attempts import RestartAttemptError,RestartAttempts
try:
    RestartAttempts(Path(sys.argv[1])).record_outcome(sys.argv[2],json.loads(sys.argv[3]))
except RestartAttemptError as exc:
    if 'regress' not in str(exc):
        raise
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(attempts.path.parent.parent),
             attempt["attempt_id"], json.dumps(receipt(attempt, state))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            **no_window_kwargs(),
        )
        for state in ("mux-created", "seed-acknowledged")
    ]
    try:
        for process in processes:
            _, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
    retained, = attempts.snapshot()
    assert retained["outcome"] == receipt(attempt, "seed-acknowledged")


def runtime_for(root):
    from worktree_manager.restart_notification_runtime import RestartNotificationRuntime
    from worktree_manager.restart_observer import RestartObserver

    runtime = RestartNotificationRuntime(root, RestartObserver(root, epoch=EPOCH))
    runtime.activate()
    return runtime


def enable_outcome_observation(root):
    from worktree_manager.source_config import config_path

    config_path(root).write_text("[restart_recovery]\nautomatic_restore = true\n")


def owner_receipts(monkeypatch, attempt, states):
    from worktree_manager import engine_client

    calls = []

    def run(project, args, **kwargs):
        calls.append((project, args, kwargs))
        state = next(states)
        if isinstance(state, Exception):
            raise state
        result = receipt(attempt, state)
        assert json.loads(args[4]) == result["request"]
        return result

    monkeypatch.setattr(engine_client, "run_json", run)
    return calls


def test_active_opt_in_pump_durably_observes_exact_owner_receipt(frozen, monkeypatch):
    attempts, attempt = frozen
    root = attempts.path.parent.parent
    enable_outcome_observation(root)
    calls = owner_receipts(monkeypatch, attempt, iter(["seed-acknowledged"]))
    runtime = runtime_for(root)
    try:
        runtime.pump(admitted=True)
        retained, = RestartAttempts(root).snapshot()
        assert retained["outcome"] == receipt(attempt, "seed-acknowledged")
        status = runtime.status["outcome_observation"]
        assert status["attempt_id"] == attempt["attempt_id"]
        assert status["state"] == "seed-acknowledged"
        assert status["automatic_eligible"] is False
        assert len(calls) == 1
        assert calls[0][2]["environment"] == {
            "WORKTREE_NO_UPDATE": "1", "WORKTREE_NO_RECONCILE": "1",
        }
    finally:
        runtime.close()


@pytest.mark.parametrize("case", ["default-off", "passive", "draining"])
def test_unadmitted_or_disabled_pump_never_contacts_owner(frozen, monkeypatch, case):
    attempts, attempt = frozen
    root = attempts.path.parent.parent
    if case != "default-off":
        enable_outcome_observation(root)
    calls = owner_receipts(monkeypatch, attempt, iter([]))
    runtime = runtime_for(root)
    runtime._can_admit = lambda: case != "draining"
    try:
        runtime.pump(admitted=case != "passive")
        assert calls == []
        assert attempts.snapshot() == [attempt]
        with sqlite3.connect(attempts.path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    finally:
        runtime.close()


def test_receipt_polling_is_bounded_fair_and_skips_completed_ack(frozen, monkeypatch):
    from worktree_manager.restart_outcome_observer import RestartOutcomeObserver

    attempts, first = frozen
    root = attempts.path.parent.parent
    rows = RecoveryJournal(root).snapshot()
    rows[0]["last_positive"]["worktree_id"] = "example-two"
    both = attempts.freeze(rows, EPOCH)
    calls = []

    def inspect(attempt_id, current, epoch):
        calls.append(attempt_id)
        attempt = next(item for item in current if item["attempt_id"] == attempt_id)
        return receipt(attempt, "seed-acknowledged")

    monkeypatch.setattr("worktree_manager.restart_outcome_observer.inspect_launch", inspect)
    observer = RestartOutcomeObserver(root)
    observer.observe(EPOCH, can_admit=lambda: True, now=0)
    observer.observe(EPOCH, can_admit=lambda: True, now=19.99)
    assert calls == [both[0]["attempt_id"]]
    observer.observe(EPOCH, can_admit=lambda: True, now=20)
    observer.observe(EPOCH, can_admit=lambda: True, now=40)
    assert calls == [item["attempt_id"] for item in both]
    assert observer.status["reason"] == "no-unacknowledged-attempt"


def test_owner_unavailability_preserves_uncertainty_and_waits_before_retry(frozen, monkeypatch):
    from worktree_manager.engine_client import EngineError
    from worktree_manager.restart_outcome_observer import RestartOutcomeObserver

    attempts, attempt = frozen
    retained = attempts.record_outcome(
        attempt["attempt_id"], receipt(attempt, "launch-uncertain"),
    )
    calls = owner_receipts(monkeypatch, attempt, iter([EngineError("owner unavailable")]))
    observer = RestartOutcomeObserver(attempts.path.parent.parent)
    observer.observe(EPOCH, can_admit=lambda: True, now=0)
    observer.observe(EPOCH, can_admit=lambda: True, now=19.99)
    assert len(calls) == 1
    assert "owner unavailable" in observer.status["blocker"]
    assert observer.status["applied"] is False
    assert attempts.snapshot() == [retained]


def test_drain_between_owner_read_and_commit_cannot_persist(frozen, monkeypatch):
    from worktree_manager import mux_daemon

    attempts, attempt = frozen
    root = attempts.path.parent.parent
    enable_outcome_observation(root)
    runtime = mux_daemon.MuxDaemonRuntime(root / "mux.json", root=root)
    runtime.recovery_observer.epoch = EPOCH
    runtime.activate_restart_recovery()
    runtime.start()
    entered, release = threading.Event(), threading.Event()

    def inspect(*args):
        entered.set()
        assert release.wait(3)
        return receipt(attempt, "seed-acknowledged")

    monkeypatch.setattr("worktree_manager.restart_outcome_observer.inspect_launch", inspect)
    errors = []

    def pump():
        try:
            with runtime.loop_mutation():
                runtime.restart_notifications.pump(admitted=True)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=pump)
    worker.start()
    try:
        assert entered.wait(3)
        runtime.begin_drain()
        assert not runtime._is_drained()
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        assert not errors
        assert runtime._is_drained()
        assert attempts.snapshot() == [attempt]
        assert runtime.health()["restart_recovery"]["outcome_observation"]["reason"] == "admission-closed"
    finally:
        release.set()
        worker.join(3)
        runtime.shutdown()


def test_disk_failure_rolls_back_first_outcome_and_schema_migration(frozen, monkeypatch):
    from worktree_manager import restart_attempts

    attempts, attempt = frozen
    original_connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith("INSERT INTO outcomes"):
                raise sqlite3.OperationalError("fixture disk full")
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(
        restart_attempts.sqlite3, "connect",
        lambda *a, **k: original_connect(*a, **k, factory=FailingConnection),
    )
    with pytest.raises(RestartAttemptError, match="disk full"):
        attempts.record_outcome(attempt["attempt_id"], receipt(attempt, "mux-created"))
    assert attempts.snapshot() == [attempt]
    with original_connect(attempts.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='outcomes'",
        ).fetchall() == []


def test_outcome_budget_is_checked_before_any_schema_migration(frozen, monkeypatch):
    from worktree_manager import restart_attempts

    attempts, attempt = frozen
    value = receipt(attempt, "seed-acknowledged")
    monkeypatch.setattr(
        restart_attempts, "_MAX_PAYLOAD_BYTES",
        len(json.dumps(value, allow_nan=False, sort_keys=True).encode("utf-8")) - 1,
    )
    before = attempts.path.read_bytes()
    with pytest.raises(RestartAttemptError, match="receipt exceeds"):
        attempts.record_outcome(attempt["attempt_id"], value)
    assert attempts.path.read_bytes() == before
    assert attempts.snapshot() == [attempt]


def test_ordinary_inspection_reports_retained_ack_without_owner_io_or_writes(frozen, monkeypatch):
    from worktree_manager import engine_client, restart_observer
    from worktree_manager.restart_capabilities import inspect_recovery

    attempts, attempt = frozen
    retained = attempts.record_outcome(
        attempt["attempt_id"], receipt(attempt, "seed-acknowledged"),
    )
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: EPOCH)
    monkeypatch.setattr(
        engine_client, "run_json",
        lambda *a, **k: pytest.fail("ordinary inspection contacted an owner"),
    )
    before = attempts.path.read_bytes()
    result = inspect_recovery(attempts.path.parent.parent)
    assert result["attempts"] == [retained]
    assert result["automatic_eligible"] is False
    assert attempts.path.read_bytes() == before


def test_historical_outcome_observer_never_rebinds_frozen_target(frozen, monkeypatch):
    from worktree_manager.restart_outcome_observer import RestartOutcomeObserver

    attempts, attempt = frozen
    calls = owner_receipts(monkeypatch, attempt, iter(["seed-acknowledged"]))
    observer = RestartOutcomeObserver(attempts.path.parent.parent)
    observer.observe(
        HostEpoch(EPOCH.platform, EPOCH.environment_id, "boot-three"),
        can_admit=lambda: True, now=0,
    )
    assert len(calls) == 1
    retained, = attempts.snapshot()
    assert retained["outcome"]["request"]["expected"]["target_epoch"] == vars(EPOCH)
    assert retained["outcome"]["acknowledgement"]["target_epoch"] == vars(EPOCH)


def test_stale_owner_regression_is_visible_without_erasing_mux_receipt(frozen, monkeypatch):
    from worktree_manager.restart_outcome_observer import RestartOutcomeObserver

    attempts, attempt = frozen
    retained = attempts.record_outcome(attempt["attempt_id"], receipt(attempt, "mux-created"))
    owner_receipts(monkeypatch, attempt, iter(["launch-uncertain"]))
    observer = RestartOutcomeObserver(attempts.path.parent.parent)
    observer.observe(EPOCH, can_admit=lambda: True, now=0)
    assert "regress" in observer.status["blocker"]
    assert attempts.snapshot() == [retained]


def test_actual_daemon_loop_observes_under_active_lease_and_mutation_fence(frozen, monkeypatch):
    from worktree_manager import mux_daemon, restart_observer

    attempts, attempt = frozen
    root = attempts.path.parent.parent
    enable_outcome_observation(root)
    calls = owner_receipts(monkeypatch, attempt, iter(["seed-acknowledged"]))
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: EPOCH)
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: None)
    monkeypatch.setattr(mux_daemon.AttachedClientObserver, "observe", lambda *a: None)
    monkeypatch.setattr(restart_observer.RestartObserver, "observe", lambda *a: None)
    original_runtime = mux_daemon.MuxDaemonRuntime
    events = []

    def create_runtime(*args, **kwargs):
        runtime = original_runtime(*args, **kwargs)
        original_pump = runtime.restart_notifications.pump

        def pump(*, admitted):
            assert runtime._loop_mutation_active and admitted
            assert runtime.accepting_requests() and not runtime.passive
            events.append("observing")
            original_pump(admitted=admitted)

        runtime.restart_notifications.pump = pump
        return runtime

    original_release = mux_daemon._release_daemon_lease

    def release(lease):
        assert attempts.snapshot()[0]["outcome"]["seed_acknowledged"]
        events.append("lease-release")
        original_release(lease)

    monkeypatch.setattr(mux_daemon, "MuxDaemonRuntime", create_runtime)
    monkeypatch.setattr(mux_daemon, "_release_daemon_lease", release)
    assert mux_daemon.run_daemon_foreground(root, max_iterations=1) == 0
    assert events == ["observing", "lease-release"]
    assert len(calls) == 1


def test_startup_at_full_capacity_still_advances_existing_owner_receipt(frozen, monkeypatch):
    attempts, _ = frozen
    root = attempts.path.parent.parent
    journal = RecoveryJournal(root)
    template = journal.snapshot()[0]
    rows = []
    for index in range(4095):
        row = copy.deepcopy(template)
        row["last_positive"]["worktree_id"] = f"retained-{index}"
        rows.append(row)
    full = attempts.freeze(rows, EPOCH)
    assert len(full) == 4096
    attempt = full[0]
    attempts.record_outcome(attempt["attempt_id"], receipt(attempt, "launch-uncertain"))
    evidence = {**template["last_positive"], "worktree_id": "new-after-capacity"}
    journal.observe(PositiveEvidence(**evidence))
    enable_outcome_observation(root)
    calls = owner_receipts(monkeypatch, attempt, iter(["seed-acknowledged"]))
    runtime = runtime_for(root)
    try:
        assert "limit reached" in runtime.status["blocker"]
        runtime.pump(admitted=True)
        retained = attempts.snapshot()
        assert len(retained) == 4096
        assert retained[0]["outcome"] == receipt(attempt, "seed-acknowledged")
        assert {item["attempt_id"] for item in retained} == {item["attempt_id"] for item in full}
        assert len(calls) == 1
        assert runtime.status["outcome_observation"]["state"] == "seed-acknowledged"
        assert "limit reached" in runtime.status["blocker"]
    finally:
        runtime.close()


def test_unconfirmed_startup_evidence_cannot_admit_historical_observation(frozen, monkeypatch):
    attempts, attempt = frozen
    root = attempts.path.parent.parent
    RecoveryJournal(root).path.write_bytes(b"corrupt startup evidence")
    enable_outcome_observation(root)
    calls = owner_receipts(monkeypatch, attempt, iter([]))
    runtime = runtime_for(root)
    try:
        assert runtime.status["blocker"]
        runtime.pump(admitted=True)
        assert calls == []
        assert attempts.snapshot() == [attempt]
    finally:
        runtime.close()
