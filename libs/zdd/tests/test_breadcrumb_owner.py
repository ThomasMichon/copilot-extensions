"""Original installer birth is immutable evidence, not the current PID occupant."""

import json
import os

import pytest

from zdd import breadcrumb, diagnostics


def test_capture_native_current_owner_birth(tmp_path):
    expected = diagnostics.process_start_time(os.getpid())
    assert expected is not None
    record = breadcrumb.write_breadcrumb(tmp_path, state="started")
    assert record["owner_process_start_time"] == expected
    assert breadcrumb.read_breadcrumb(tmp_path) == record
    assert breadcrumb.owner_exited(record) is False


@pytest.mark.parametrize("explicit_pid", [False, True])
def test_phase_writes_preserve_original_birth_despite_pid_reuse(
    tmp_path, monkeypatch, explicit_pid,
):
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "100")
    initial = breadcrumb.write_breadcrumb(tmp_path, state="started")
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "200")
    for state in ("started", "flipped", "committed"):
        options = {"pid": os.getpid()} if explicit_pid else {}
        record = breadcrumb.write_breadcrumb(
            tmp_path, state=state, started_at=initial["started_at"], **options,
        )
        assert record["owner_process_start_time"] == "100"
        assert breadcrumb.owner_exited(record) is True


def test_recovery_with_foreign_pid_preserves_original_birth(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "100")
    original = breadcrumb.write_breadcrumb(
        tmp_path, state="started", new_pid=202, new_port=61002,
    )
    original_pid = original["pid"]
    monkeypatch.setattr(os, "getpid", lambda: original_pid + 1)

    def forbidden(pid):
        pytest.fail("foreign recovery must not sample a new birth for the original owner")

    monkeypatch.setattr(diagnostics, "process_start_time", forbidden)
    outcome = breadcrumb.recover_stale_cutover(tmp_path, lambda url: None)
    assert outcome["recovered"] is False
    recovered = breadcrumb.read_breadcrumb(tmp_path)
    assert recovered["state"] == "rolled_back"
    assert recovered["pid"] == original_pid
    assert recovered["owner_process_start_time"] == "100"


@pytest.mark.parametrize("legacy", [False, True])
def test_foreign_pid_never_acquires_current_occupants_birth(tmp_path, monkeypatch, legacy):
    foreign_pid = os.getpid() + 1
    if legacy:
        breadcrumb.breadcrumb_path(tmp_path).write_text(
            json.dumps({"pid": foreign_pid, "state": "started", "started_at": "original"}),
            encoding="utf-8",
        )

    def forbidden(pid):
        pytest.fail("foreign PID birth must not be inferred retroactively")

    monkeypatch.setattr(diagnostics, "process_start_time", forbidden)
    record = breadcrumb.write_breadcrumb(
        tmp_path, state="committed", pid=foreign_pid,
        started_at="original" if legacy else None,
    )
    assert record["owner_process_start_time"] is None


@pytest.mark.parametrize("initial_birth", [None, "invalid"])
def test_missing_initial_birth_stays_unknown_across_phases(tmp_path, monkeypatch, initial_birth):
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: initial_birth)
    initial = breadcrumb.write_breadcrumb(tmp_path, state="started")
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "200")
    record = breadcrumb.write_breadcrumb(
        tmp_path, state="flipped", started_at=initial["started_at"],
    )
    assert record["owner_process_start_time"] is None
    assert breadcrumb.owner_exited(record) is None


def test_new_local_attempt_captures_fresh_birth(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "100")
    breadcrumb.write_breadcrumb(tmp_path, state="committed")
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "200")
    record = breadcrumb.write_breadcrumb(tmp_path, state="started")
    assert record["owner_process_start_time"] == "200"


@pytest.mark.parametrize("recorded,current,alive,expected", [
    ("100", "100", True, False),
    ("100", "100", False, True),
    ("100", "100", None, None),
    ("100", "200", True, True),
    ("100:2", "100:3", True, True),
    ("100", None, False, True),
    ("100", None, True, None),
    ("100", "invalid", True, None),
    (None, None, False, True),
    (None, "200", True, None),
    (None, None, True, None),
    ("invalid", "200", True, None),
    (100, "200", True, None),
])
def test_owner_exit_requires_positive_native_evidence(
    monkeypatch, recorded, current, alive, expected,
):
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: current)
    monkeypatch.setattr(diagnostics, "_pid_alive", lambda pid: alive)
    record = {"pid": 123}
    if recorded is not None:
        record["owner_process_start_time"] = recorded
    assert breadcrumb.owner_exited(record) is expected


@pytest.mark.parametrize("record", [None, {}, {"pid": True}, {"pid": -1}, {"pid": "123"}])
def test_malformed_owner_is_unknown(record):
    assert breadcrumb.owner_exited(record) is None


@pytest.mark.parametrize("probe", ["process_start_time", "_pid_alive"])
def test_owner_probe_failure_retains_uncertain_witness(tmp_path, monkeypatch, probe):
    record = breadcrumb.write_breadcrumb(tmp_path, state="started")
    before = breadcrumb.breadcrumb_path(tmp_path).read_bytes()
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: None)

    def fail(pid):
        raise OSError("fixture native identity unavailable")

    monkeypatch.setattr(diagnostics, probe, fail)
    assert breadcrumb.owner_exited(record) is None
    assert breadcrumb.breadcrumb_path(tmp_path).read_bytes() == before


def test_readable_matching_birth_with_failed_liveness_is_unknown(monkeypatch):
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "100")

    def fail(pid):
        raise OSError("fixture native liveness unavailable")

    monkeypatch.setattr(diagnostics, "_pid_alive", fail)
    assert breadcrumb.owner_exited({"pid": 123, "owner_process_start_time": "100"}) is None
