"""Frozen identities are durable evidence, never permission to launch."""

from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager.restart_evidence import PositiveEvidence, RecoveryJournal
from worktree_manager.restart_observer import HostEpoch


@pytest.fixture
def attempts_module():
    return importlib.import_module("worktree_manager.restart_attempts")


@pytest.fixture
def journal(tmp_path):
    result = RecoveryJournal(tmp_path)
    result.observe(PositiveEvidence(
        project="example", worktree_id="example-one", worktree_path="/example/worktree",
        native_session_id="00000000-0000-4000-8000-000000000001",
        native_incarnation="native-one", binding_proof="inuse-hold-v1", owner_revision=1,
        session_incarnation="mux-one", mapping_revision=1, platform="linux",
        environment_id="environment-one", boot_id="boot-one",
        observed_at=100.0, observation_sequence=1, source_identity="source-one",
        activation_ordinal=1, lifecycle_qualified=True,
        worktree_incarnation="0123456789abcdef", head_revision=17,
    ))
    return result


def test_absent_attempt_inspection_creates_nothing(tmp_path, attempts_module):
    assert attempts_module.RestartAttempts(tmp_path).snapshot() == []
    assert list(tmp_path.iterdir()) == []


def test_frozen_attempt_is_reused_without_overwriting_original_evidence(
    tmp_path, journal, attempts_module,
):
    attempts = attempts_module.RestartAttempts(tmp_path)
    epoch = HostEpoch("linux", "environment-one", "boot-two")
    first = attempts.freeze(journal.snapshot(), epoch)
    assert len(first) == 1
    assert first[0]["state"] == "frozen"
    assert first[0]["automatic_eligible"] is False
    assert first[0]["last_positive"]["head_revision"] == 17
    evidence = PositiveEvidence(**journal.snapshot()[0]["last_positive"])
    journal.observe(replace(evidence, observation_sequence=2, observed_at=200.0))
    assert attempts.freeze(journal.snapshot(), epoch) == first
    journal.observe(replace(
        evidence, boot_id="boot-two", native_incarnation="native-two",
        source_identity="source-two", mapping_revision=2, owner_revision=2,
        observation_sequence=3, observed_at=300.0,
    ))
    assert attempts.freeze(journal.snapshot(), epoch) == first
    assert attempts_module.RestartAttempts(tmp_path).snapshot() == first


@pytest.mark.parametrize("change,disposition", [
    ({"boot_id": "boot-two"}, "live"),
    ({"environment_id": "foreign"}, "live"),
    ({"platform": "wsl"}, "live"),
    ({"lifecycle_qualified": False}, "live"),
    ({"worktree_incarnation": None, "head_revision": None}, "live"),
    ({}, "intentional"),
    ({}, "unknown"),
])
def test_unqualified_closed_foreign_or_same_boot_rows_are_not_frozen(
    tmp_path, journal, attempts_module, change, disposition,
):
    row = journal.snapshot()[0]
    row["last_positive"].update(change)
    row["disposition"] = disposition
    if disposition == "intentional":
        row["detail"] = "operator-stop"
    assert attempts_module.RestartAttempts(tmp_path).freeze(
        [row], HostEpoch("linux", "environment-one", "boot-two"),
    ) == []
    assert not (tmp_path / "restart-recovery" / "attempts.sqlite3").exists()


def test_corrupt_or_foreign_schema_is_not_reset(tmp_path, attempts_module):
    attempts = attempts_module.RestartAttempts(tmp_path)
    attempts.path.parent.mkdir()
    attempts.path.write_bytes(b"not a database")
    original = attempts.path.read_bytes()
    with pytest.raises(attempts_module.RestartAttemptError, match="read"):
        attempts.snapshot()
    assert attempts.path.read_bytes() == original
    attempts.path.unlink()
    with sqlite3.connect(attempts.path) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(attempts_module.RestartAttemptError, match="schema"):
        attempts.snapshot()
    with pytest.raises(attempts_module.RestartAttemptError, match="schema"):
        attempts.freeze([], HostEpoch("linux", "environment-one", "boot-two"))


def test_capacity_failure_rolls_back_whole_freeze_without_losing_pending_attempts(
    tmp_path, journal, attempts_module, monkeypatch,
):
    monkeypatch.setattr(attempts_module, "_MAX_ATTEMPTS", 2)
    attempts = attempts_module.RestartAttempts(tmp_path)
    epoch = HostEpoch("linux", "environment-one", "boot-two")
    first = attempts.freeze(journal.snapshot(), epoch)
    rows = []
    for worktree_id in ("example-two", "example-three"):
        row = journal.snapshot()[0]
        row["last_positive"]["worktree_id"] = worktree_id
        rows.append(row)
    with pytest.raises(attempts_module.RestartAttemptError, match="limit"):
        attempts.freeze(rows, epoch)
    assert attempts.snapshot() == first


def test_concurrent_processes_freeze_one_attempt(tmp_path, journal, attempts_module):
    code = """
import json,sys
from pathlib import Path
from worktree_manager.restart_attempts import RestartAttempts
from worktree_manager.restart_evidence import RecoveryJournal
from worktree_manager.restart_observer import HostEpoch
root=Path(sys.argv[1])
print(json.dumps(RestartAttempts(root).freeze(
    RecoveryJournal(root).snapshot(), HostEpoch('linux','environment-one','boot-two'))))
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            **no_window_kwargs(),
        )
        for _ in range(2)
    ]
    results = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
    assert results[0] == results[1]
    assert len(results[0]) == 1
    assert attempts_module.RestartAttempts(tmp_path).snapshot() == results[0]


def test_explicit_request_freezes_before_ordinary_daemon_start(tmp_path, journal):
    from worktree_manager import source_config
    from worktree_manager.restart_notification_runtime import RestartNotificationRuntime
    from worktree_manager.restart_observer import RestartObserver

    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nautomatic_restore = true\n", encoding="utf-8",
    )
    runtime = RestartNotificationRuntime(
        tmp_path, RestartObserver(tmp_path, epoch=HostEpoch(
            "linux", "environment-one", "boot-two",
        )),
    )
    runtime.activate()
    assert runtime.status["attempts"]["state"] == "awaiting-owner-admission"
    assert len(runtime.status["attempts"]["ids"]) == 1
    assert runtime.status["automatic_eligible"] is False
    runtime.close()


@pytest.mark.parametrize("activate", [False, True])
def test_passive_and_default_off_never_freeze(tmp_path, journal, activate):
    from worktree_manager.restart_notification_runtime import RestartNotificationRuntime
    from worktree_manager.restart_observer import RestartObserver

    runtime = RestartNotificationRuntime(
        tmp_path, RestartObserver(tmp_path, epoch=HostEpoch(
            "linux", "environment-one", "boot-two",
        )),
    )
    if activate:
        runtime.activate()
    runtime.pump(admitted=False)
    assert not (tmp_path / "restart-recovery" / "attempts.sqlite3").exists()
    runtime.close()


def test_corrupt_attempts_are_a_json_blocker_even_when_automatic_mode_is_off(
    tmp_path, attempts_module, monkeypatch, capsys,
):
    from worktree_manager import restart_observer, self_install
    from worktree_manager.__main__ import main

    attempts = attempts_module.RestartAttempts(tmp_path)
    attempts.path.parent.mkdir()
    attempts.path.write_bytes(b"corrupt private attempt state")
    original = attempts.path.read_bytes()
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: HostEpoch(
        "linux", "environment-one", "boot-two",
    ))
    assert main(["mux-daemon", "recovery-evidence"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "attempt" in result["blocker"]
    assert result["automatic_eligible"] is False
    assert attempts.path.read_bytes() == original


def test_freeze_failure_does_not_disable_ordinary_mux_service(
    tmp_path, journal, attempts_module,
):
    from worktree_manager import mux_daemon, source_config

    attempts = attempts_module.RestartAttempts(tmp_path)
    attempts.path.write_bytes(b"corrupt private attempt state")
    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nautomatic_restore = true\n", encoding="utf-8",
    )
    runtime = mux_daemon.MuxDaemonRuntime(tmp_path / "mux.json", root=tmp_path)
    runtime.recovery_observer.epoch = HostEpoch("linux", "environment-one", "boot-two")
    try:
        runtime.restart_notifications.activate()
        runtime.start()
        assert runtime.server is not None and runtime.port is not None
        assert runtime.accepting_requests()
        assert "attempt" in runtime.health()["restart_recovery"]["blocker"]
    finally:
        runtime.shutdown()


def test_crash_after_insert_before_commit_preserves_prior_attempts(
    tmp_path, journal, attempts_module,
):
    attempts = attempts_module.RestartAttempts(tmp_path)
    epoch = HostEpoch("linux", "environment-one", "boot-two")
    original = attempts.freeze(journal.snapshot(), epoch)
    code = """
import os,sys
from pathlib import Path
from worktree_manager.restart_attempts import RestartAttempts
from worktree_manager.restart_evidence import RecoveryJournal
from worktree_manager.restart_observer import HostEpoch
root=Path(sys.argv[1])
read=RestartAttempts._read
calls=0
def interrupted(connection):
    global calls
    rows=read(connection)
    calls+=1
    if calls==2:
        assert len(rows)==2
        print('inserted-before-commit',flush=True)
        os._exit(7)
    return rows
RestartAttempts._read=staticmethod(interrupted)
row=RecoveryJournal(root).snapshot()[0]
row['last_positive']['worktree_id']='example-two'
RestartAttempts(root).freeze([row],HostEpoch('linux','environment-one','boot-two'))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert process.returncode == 7, process.stderr
    assert process.stdout.strip() == "inserted-before-commit"
    assert attempts.snapshot(recover_existing=True) == original
    row = journal.snapshot()[0]
    row["last_positive"]["worktree_id"] = "example-two"
    assert len(attempts.freeze([row], epoch)) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and symlink fixture")
def test_attempts_are_private_and_symlinks_are_refused(tmp_path, journal, attempts_module):
    attempts = attempts_module.RestartAttempts(tmp_path)
    epoch = HostEpoch("linux", "environment-one", "boot-two")
    attempts.freeze(journal.snapshot(), epoch)
    assert attempts.path.stat().st_mode & 0o777 == 0o600
    assert attempts.path.parent.stat().st_mode & 0o777 == 0o700
    target = tmp_path / "retained.sqlite3"
    attempts.path.rename(target)
    attempts.path.symlink_to(target)
    original = target.read_bytes()
    with pytest.raises(attempts_module.RestartAttemptError):
        attempts.snapshot()
    with pytest.raises(attempts_module.RestartAttemptError):
        attempts.freeze(journal.snapshot(), epoch)
    assert target.read_bytes() == original


@pytest.mark.parametrize("field", ["payload", "attempt_id", "candidate_key"])
def test_blob_columns_block_recovery_but_not_mux_startup(
    tmp_path, journal, attempts_module, monkeypatch, capsys, field,
):
    from worktree_manager import mux_daemon, restart_observer, self_install, source_config
    from worktree_manager.__main__ import main

    attempts = attempts_module.RestartAttempts(tmp_path)
    epoch = HostEpoch("linux", "environment-one", "boot-two")
    attempts.freeze(journal.snapshot(), epoch)
    with sqlite3.connect(attempts.path) as connection:
        connection.execute(f"UPDATE attempts SET {field}=?", (sqlite3.Binary(b"invalid"),))
    with pytest.raises(attempts_module.RestartAttemptError, match="column"):
        attempts.snapshot()
    with pytest.raises(attempts_module.RestartAttemptError, match="column"):
        attempts.freeze(journal.snapshot(), epoch)
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: epoch)
    assert main(["mux-daemon", "recovery-evidence"]) == 1
    assert "column" in json.loads(capsys.readouterr().out)["blocker"]
    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nautomatic_restore = true\n", encoding="utf-8",
    )
    runtime = mux_daemon.MuxDaemonRuntime(tmp_path / "mux.json", root=tmp_path)
    try:
        runtime.restart_notifications.activate()
        runtime.start()
        assert runtime.server is not None and runtime.accepting_requests()
        assert "column" in runtime.health()["restart_recovery"]["blocker"]
    finally:
        runtime.shutdown()
    with sqlite3.connect(attempts.path) as connection:
        assert connection.execute(f"SELECT typeof({field}) FROM attempts").fetchone() == ("blob",)


def test_truncated_attempt_database_is_not_reinitialized(tmp_path, journal, attempts_module):
    attempts = attempts_module.RestartAttempts(tmp_path)
    epoch = HostEpoch("linux", "environment-one", "boot-two")
    assert attempts.freeze(journal.snapshot(), epoch)
    attempts.path.write_bytes(b"")
    with pytest.raises(attempts_module.RestartAttemptError, match="uninitialized"):
        attempts.snapshot()
    with pytest.raises(attempts_module.RestartAttemptError, match="uninitialized"):
        attempts.freeze(journal.snapshot(), epoch)
    assert attempts.path.read_bytes() == b""


@pytest.mark.parametrize("passive", [False, True])
def test_daemon_activation_freezes_inside_drain_mutation_boundary(
    tmp_path, monkeypatch, passive,
):
    from worktree_manager import mux_daemon

    original = mux_daemon.MuxDaemonRuntime
    activations = []

    def runtime(*args, **kwargs):
        result = original(*args, **kwargs)
        activate = result.restart_notifications.activate

        def guarded():
            assert result._loop_mutation_active
            activations.append(True)
            activate()

        result.restart_notifications.activate = guarded
        if passive:
            result.promote()
        return result

    monkeypatch.setattr(mux_daemon, "MuxDaemonRuntime", runtime)
    monkeypatch.setattr(mux_daemon.mux_daemon_cutover, "active_generation_for_pid", lambda *_a, **_k: 1)
    assert mux_daemon.run_daemon_foreground(tmp_path, passive=passive, max_iterations=1) == 0
    assert activations == [True]


def test_drain_does_not_report_idle_while_freezing(tmp_path, monkeypatch):
    from worktree_manager import mux_daemon

    runtime = mux_daemon.MuxDaemonRuntime(tmp_path / "mux.json", root=tmp_path)
    entered, release = threading.Event(), threading.Event()
    runtime.start()
    assert runtime.server is not None

    def freeze():
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(runtime.restart_notifications, "activate", freeze)
    worker = threading.Thread(target=runtime.activate_restart_recovery)
    worker.start()
    try:
        assert entered.wait(3)
        runtime.begin_drain()
        assert runtime._is_drained() is False
    finally:
        release.set()
        worker.join(3)
        runtime.shutdown()
    assert not worker.is_alive()
    assert runtime._is_drained()


def test_draining_generation_does_not_start_a_freeze(tmp_path, monkeypatch):
    from worktree_manager import mux_daemon

    runtime = mux_daemon.MuxDaemonRuntime(tmp_path / "mux.json", root=tmp_path)
    runtime.begin_drain()
    monkeypatch.setattr(
        runtime.restart_notifications, "activate",
        lambda: pytest.fail("draining generation started restart freezing"),
    )
    runtime.activate_restart_recovery()
    assert runtime._is_drained()
    runtime.shutdown()


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_missing_database_with_retained_sidecar_is_not_recreated(
    tmp_path, journal, attempts_module, suffix,
):
    attempts = attempts_module.RestartAttempts(tmp_path)
    sidecar = attempts.path.with_name(attempts.path.name + suffix)
    sidecar.write_bytes(b"retained unconfirmed transaction")
    with pytest.raises(attempts_module.RestartAttemptError, match="sidecar"):
        attempts.snapshot()
    with pytest.raises(attempts_module.RestartAttemptError, match="sidecar"):
        attempts.freeze(journal.snapshot(), HostEpoch("linux", "environment-one", "boot-two"))
    assert not attempts.path.exists()
    assert sidecar.read_bytes() == b"retained unconfirmed transaction"


@pytest.mark.parametrize("malformation", ["timestamp-overflow", "nested-json"])
def test_malformed_json_blocks_without_aborting_mux_startup(
    tmp_path, journal, attempts_module, monkeypatch, capsys, malformation,
):
    from worktree_manager import mux_daemon, restart_observer, self_install, source_config
    from worktree_manager.__main__ import main

    attempts = attempts_module.RestartAttempts(tmp_path)
    epoch = HostEpoch("linux", "environment-one", "boot-two")
    attempts.freeze(journal.snapshot(), epoch)
    with sqlite3.connect(attempts.path) as connection:
        value = json.loads(connection.execute("SELECT payload FROM attempts").fetchone()[0])
        value["last_positive"]["observed_at"] = 10 ** 400
        payload = (
            json.dumps(value) if malformation == "timestamp-overflow"
            else "[" * 15000 + "0" + "]" * 15000
        )
        connection.execute("UPDATE attempts SET payload=?", (payload,))
    with pytest.raises(attempts_module.RestartAttemptError):
        attempts.snapshot()
    with pytest.raises(attempts_module.RestartAttemptError):
        attempts.freeze(journal.snapshot(), epoch)
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: epoch)
    assert main(["mux-daemon", "recovery-evidence"]) == 1
    assert "attempt" in json.loads(capsys.readouterr().out)["blocker"]
    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nautomatic_restore = true\n", encoding="utf-8",
    )
    runtime = mux_daemon.MuxDaemonRuntime(tmp_path / "mux.json", root=tmp_path)
    try:
        runtime.restart_notifications.activate()
        runtime.start()
        assert runtime.server is not None and runtime.accepting_requests()
        assert "attempt" in runtime.health()["restart_recovery"]["blocker"]
    finally:
        runtime.shutdown()
    with sqlite3.connect(attempts.path) as connection:
        assert connection.execute("SELECT payload FROM attempts").fetchone() == (payload,)


@pytest.mark.parametrize("malformation", ["timestamp-overflow", "nested-json"])
def test_source_json_corruption_remains_a_recovery_evidence_error(
    tmp_path, journal, monkeypatch, capsys, malformation,
):
    from worktree_manager import mux_daemon, restart_observer, self_install
    from worktree_manager.__main__ import main
    from worktree_manager.restart_evidence import RecoveryEvidenceError

    with sqlite3.connect(journal.path) as connection:
        value = json.loads(connection.execute("SELECT payload FROM journal").fetchone()[0])
        value["last_positive"]["observed_at"] = 10 ** 400
        payload = (
            json.dumps(value) if malformation == "timestamp-overflow"
            else "[" * 15000 + "0" + "]" * 15000
        )
        connection.execute("UPDATE journal SET payload=?", (payload,))
    with pytest.raises(RecoveryEvidenceError):
        journal.snapshot()
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: HostEpoch(
        "linux", "environment-one", "boot-two",
    ))
    assert main(["mux-daemon", "recovery-evidence"]) == 1
    assert "evidence" in json.loads(capsys.readouterr().out)["blocker"]
    runtime = mux_daemon.MuxDaemonRuntime(tmp_path / "mux.json", root=tmp_path)
    try:
        runtime.restart_notifications.activate()
        runtime.start()
        assert runtime.server is not None and runtime.accepting_requests()
        assert runtime.recovery_observer.startup_result["blocker"]
    finally:
        runtime.shutdown()
    with sqlite3.connect(journal.path) as connection:
        assert connection.execute("SELECT payload FROM journal").fetchone() == (payload,)
