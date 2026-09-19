"""Tests for the reconcile-set reaper."""

from __future__ import annotations

from single_instance_lease import (
    reconcile_set_reap,
    superseded_pids_from_table,
)


def _collector():
    killed: list[int] = []
    return killed, killed.append


def test_reaps_strays_but_spares_active_and_self():
    killed, terminate = _collector()
    res = reconcile_set_reap(
        [10, 20, 30, 40],
        active_pid=20,
        self_pid=40,
        terminate=terminate,
        alive=lambda p: True,
    )
    assert set(res.reaped) == {10, 30}
    assert 20 in res.skipped and 40 in res.skipped
    assert killed == res.reaped
    assert res.ok


def test_dead_pids_are_skipped_not_terminated():
    killed, terminate = _collector()
    alive = {10: True, 30: False}
    res = reconcile_set_reap(
        [10, 30],
        active_pid=None,
        self_pid=999,
        terminate=terminate,
        alive=lambda p: alive.get(p, False),
    )
    assert res.reaped == [10]
    assert 30 in res.skipped
    assert killed == [10]


def test_verify_gate_defeats_pid_reuse():
    killed, terminate = _collector()
    # 30 is alive but is NOT our service anymore (pid reused) -> verify says no.
    res = reconcile_set_reap(
        [10, 30],
        active_pid=None,
        self_pid=999,
        terminate=terminate,
        alive=lambda p: True,
        verify=lambda p: p != 30,
    )
    assert res.reaped == [10]
    assert 30 in res.skipped
    assert killed == [10]


def test_terminate_failure_is_fail_soft():
    def terminate(pid):
        if pid == 10:
            raise PermissionError("nope")

    res = reconcile_set_reap(
        [10, 20],
        active_pid=None,
        self_pid=999,
        terminate=terminate,
        alive=lambda p: True,
    )
    assert res.reaped == [20]
    assert res.failed == [10]
    assert res.ok is False


def test_duplicate_and_invalid_pids_ignored():
    killed, terminate = _collector()
    res = reconcile_set_reap(
        [10, 10, 0, -5, 10],
        active_pid=None,
        self_pid=999,
        terminate=terminate,
        alive=lambda p: True,
    )
    assert res.reaped == [10]
    assert killed == [10]


def test_self_pid_defaults_to_current_process():
    import os

    _killed, terminate = _collector()
    res = reconcile_set_reap(
        [os.getpid(), 12345],
        active_pid=None,
        terminate=terminate,
        alive=lambda p: True,
    )
    assert os.getpid() in res.skipped
    assert res.reaped == [12345]


def test_superseded_pids_from_table_harvests_previous():
    table = {
        "active": {"pid": 100, "generation": 8, "port": 1, "bind": "127.0.0.1"},
        "previous": {"pid": 90, "generation": 7, "port": 2, "bind": "127.0.0.1"},
    }
    assert superseded_pids_from_table(table, active_pid=100) == {90}


def test_superseded_pids_from_table_includes_stale_active():
    # The recorded active pid differs from who the caller knows is live -> it is
    # a stale record and becomes a candidate.
    table = {
        "active": {"pid": 55, "generation": 6, "port": 1, "bind": "127.0.0.1"},
        "previous": {"pid": 44, "generation": 5, "port": 2, "bind": "127.0.0.1"},
    }
    assert superseded_pids_from_table(table, active_pid=100) == {55, 44}


def test_superseded_pids_from_table_no_active_pid_excludes_active():
    table = {
        "active": {"pid": 55, "generation": 6, "port": 1, "bind": "127.0.0.1"},
        "previous": {"pid": 44, "generation": 5, "port": 2, "bind": "127.0.0.1"},
    }
    # Without a known-live pid, the recorded active is trusted and excluded.
    assert superseded_pids_from_table(table) == {44}


def test_superseded_pids_from_table_handles_junk():
    assert superseded_pids_from_table(None) == set()
    assert superseded_pids_from_table({"active": "x", "previous": {"pid": "y"}}) == set()
