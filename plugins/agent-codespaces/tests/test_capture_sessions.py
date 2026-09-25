"""Tests for the CodeSpaces non-destructive capture verb
(session-rescue-parity Phase 3): ``sessions.capture_codespace_sessions``,
its ``_capture_hold_reason`` gate, and the ``sync_codespace_sessions``
``lock=`` widening seam.
"""
from __future__ import annotations

from types import SimpleNamespace

from agent_codespaces import sessions
from agent_codespaces.lease import Lease
from agent_codespaces.lifecycle import CodespaceInfo


def _lease(name: str, *, worktree: str = "") -> Lease:
    return Lease(
        codespace=name, effort="some-effort", pid=1, host="h",
        acquired_at=0.0, heartbeat_at=0.0, worktree=worktree,
    )


def _info(name: str, *, display_name: str = "", state: str = "Available") -> CodespaceInfo:
    return CodespaceInfo(
        name=name, display_name=display_name or name, repository="o/r",
        branch="main", state=state, machine="basicLinux32gb",
    )


# --- _capture_hold_reason: the four holder shapes + the orphaned exception ---

def test_capture_hold_reason_free_when_no_hold(monkeypatch):
    import agent_codespaces.lease as lease_mod
    monkeypatch.setattr(lease_mod, "get_lease", lambda name: None)
    monkeypatch.setattr(
        "agent_codespaces.lifecycle._list_codespaces_under", lambda account: [_info("cs")],
    )
    import agent_codespaces.coordination as coordination
    monkeypatch.setattr(coordination, "list_leases", lambda: {})

    assert sessions._capture_hold_reason("cs", account=None) is None


def test_capture_hold_reason_defers_on_local_lease(monkeypatch):
    import agent_codespaces.lease as lease_mod
    monkeypatch.setattr(lease_mod, "get_lease", lambda name: _lease(name))

    reason = sessions._capture_hold_reason("cs", account=None)
    assert reason is not None
    assert "lease" in reason


def test_capture_hold_reason_proceeds_on_orphaned_claim(monkeypatch, tmp_path):
    """An orphaned #897 claim (owning worktree positively gone) is not a
    live hold -- session-rescue-parity Phase 1's recorded exception."""
    import agent_codespaces.lease as lease_mod
    gone_path = str(tmp_path / "does-not-exist")
    monkeypatch.setattr(lease_mod, "get_lease", lambda name: _lease(name, worktree=gone_path))
    monkeypatch.setattr(
        "agent_codespaces.lifecycle._list_codespaces_under", lambda account: [_info("cs")],
    )
    import agent_codespaces.coordination as coordination
    monkeypatch.setattr(coordination, "list_leases", lambda: {})

    assert sessions._capture_hold_reason("cs", account=None) is None


def test_capture_hold_reason_defers_on_beacon(monkeypatch):
    import agent_codespaces.lease as lease_mod
    monkeypatch.setattr(lease_mod, "get_lease", lambda name: None)
    monkeypatch.setattr(
        "agent_codespaces.lifecycle._list_codespaces_under",
        lambda account: [_info("cs", display_name="cs #a1b2")],
    )

    reason = sessions._capture_hold_reason("cs", account=None)
    assert reason is not None
    assert "beacon" in reason


def test_capture_hold_reason_defers_on_l2_hold(monkeypatch):
    import agent_codespaces.lease as lease_mod
    monkeypatch.setattr(lease_mod, "get_lease", lambda name: None)
    monkeypatch.setattr(
        "agent_codespaces.lifecycle._list_codespaces_under", lambda account: [_info("cs")],
    )
    import agent_codespaces.coordination as coordination
    l2 = SimpleNamespace(key="cs", holder="other/machine", live=True)
    monkeypatch.setattr(coordination, "list_leases", lambda: {"cs": l2})

    reason = sessions._capture_hold_reason("cs", account=None)
    assert reason is not None
    assert "L2" in reason


# --- capture_codespace_sessions: preflight, account binding, gating ---

def test_capture_defers_on_non_available_state(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: "acct",
    )
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.get_codespace_status_with_account",
        lambda name, account: (True, "Starting", "acct"),
    )

    res = sessions.capture_codespace_sessions("cs", account=None)

    assert res["ok"] is False
    assert res["deferred"] is True
    assert "not Available" in res["detail"]


def test_capture_defers_when_codespace_not_found(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: "acct",
    )
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.get_codespace_status_with_account",
        lambda name, account: (False, None, None),
    )

    res = sessions.capture_codespace_sessions("cs", account=None)

    assert res["ok"] is False
    assert res["deferred"] is True
    assert "not found" in res["detail"]


def test_capture_fails_closed_with_no_account_and_no_binding(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: None,
    )

    res = sessions.capture_codespace_sessions("cs", account=None)

    assert res["ok"] is False
    assert res["deferred"] is True
    assert "no explicit account" in res["detail"]


def test_capture_uses_explicit_account_without_consulting_binding(monkeypatch):
    """An explicit ``account`` bypasses the binding lookup entirely -- the
    binding stub below would fail the test if it were ever called."""
    def _explode(name):
        raise AssertionError("bound_account should not be consulted")

    monkeypatch.setattr("agent_codespaces.account_binding.bound_account", _explode)
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.get_codespace_status_with_account",
        lambda name, account: (True, "Starting", account),
    )

    res = sessions.capture_codespace_sessions("cs", account="explicit-acct")

    assert res["deferred"] is True  # Starting -> preflight defers before any hold check


def test_capture_defers_on_active_hold_before_connecting(monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: "acct",
    )
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.get_codespace_status_with_account",
        lambda name, account: (True, "Available", "acct"),
    )
    monkeypatch.setattr(
        sessions, "_capture_hold_reason", lambda name, *, account: "held by someone",
    )

    def _explode(*a, **k):
        raise AssertionError("must never connect when held")

    monkeypatch.setattr(sessions, "_connect_with_retry", _explode)

    res = sessions.capture_codespace_sessions("cs", account=None)

    assert res["ok"] is False
    assert res["deferred"] is True
    assert res["detail"] == "held by someone"


def test_capture_liveness_gate_defers_active_session(monkeypatch):
    """A mid-write session (active liveness) is deferred, never captured --
    the CodeSpace-side peer of agent-containers' liveness-gate regression."""
    import ssh_manager

    class _FakeLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self, *a, **k):
            return self

        def release(self):
            pass

    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)
    monkeypatch.setattr(ssh_manager, "ConnectionManager", lambda *a, **k: object())
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: "acct",
    )
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.get_codespace_status_with_account",
        lambda name, account: (True, "Available", "acct"),
    )
    monkeypatch.setattr(sessions, "_capture_hold_reason", lambda name, *, account: None)

    async def _connect_ok(*a, **k):
        return None

    async def _disconnect_ok(*a, **k):
        return None

    async def _probe_active(manager, name, *, timeout):
        return sessions.SessionLiveness("active", ["s1"], [])

    def _pull_should_not_run(*a, **k):
        raise AssertionError("must never pull a mid-write session")

    monkeypatch.setattr(sessions, "_connect_with_retry", _connect_ok)
    monkeypatch.setattr(sessions, "_probe_codespace_liveness", _probe_active)
    monkeypatch.setattr(sessions, "_pull_tar_bytes", _pull_should_not_run)

    class _Manager:
        async def disconnect(self, name):
            return None

    monkeypatch.setattr(ssh_manager, "ConnectionManager", lambda *a, **k: _Manager())

    res = sessions.capture_codespace_sessions("cs", account=None)

    assert res["ok"] is False
    assert res["deferred"] is True
    assert "active" in res["detail"]


def test_capture_re_probes_after_pull_and_discards_on_became_active(monkeypatch):
    """Re-validates liveness AFTER the pull too -- a session that goes
    active during the pull is discarded, never staged/pushed."""
    import ssh_manager

    class _FakeLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self, *a, **k):
            return self

        def release(self):
            pass

    class _Manager:
        async def disconnect(self, name):
            return None

    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)
    monkeypatch.setattr(ssh_manager, "ConnectionManager", lambda *a, **k: _Manager())
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: "acct",
    )
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.get_codespace_status_with_account",
        lambda name, account: (True, "Available", "acct"),
    )
    monkeypatch.setattr(sessions, "_capture_hold_reason", lambda name, *, account: None)

    async def _connect_ok(*a, **k):
        return None

    probes = iter([
        sessions.SessionLiveness("idle", [], []),
        sessions.SessionLiveness("active", ["s1"], []),
    ])

    async def _probe_sequence(manager, name, *, timeout):
        return next(probes)

    async def _pull_ok(*a, **k):
        return b"not-empty"

    def _stage_should_not_run(*a, **k):
        raise AssertionError("must never stage/push a became-active capture")

    monkeypatch.setattr(sessions, "_connect_with_retry", _connect_ok)
    monkeypatch.setattr(sessions, "_probe_codespace_liveness", _probe_sequence)
    monkeypatch.setattr(sessions, "_pull_tar_bytes", _pull_ok)
    monkeypatch.setattr(sessions, "_stage_and_push", _stage_should_not_run)

    res = sessions.capture_codespace_sessions("cs", account=None)

    assert res["ok"] is False
    assert res["deferred"] is True
    assert "became" in res["detail"]


def test_capture_succeeds_on_idle_session(monkeypatch):
    import ssh_manager

    class _FakeLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self, *a, **k):
            return self

        def release(self):
            pass

    class _Manager:
        async def disconnect(self, name):
            return None

    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)
    monkeypatch.setattr(ssh_manager, "ConnectionManager", lambda *a, **k: _Manager())
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: "acct",
    )
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.get_codespace_status_with_account",
        lambda name, account: (True, "Available", "acct"),
    )
    monkeypatch.setattr(sessions, "_capture_hold_reason", lambda name, *, account: None)

    async def _connect_ok(*a, **k):
        return None

    async def _probe_idle(manager, name, *, timeout):
        return sessions.SessionLiveness("idle", [], [])

    async def _pull_ok(*a, **k):
        return b"not-empty"

    monkeypatch.setattr(sessions, "_connect_with_retry", _connect_ok)
    monkeypatch.setattr(sessions, "_probe_codespace_liveness", _probe_idle)
    monkeypatch.setattr(sessions, "_pull_tar_bytes", _pull_ok)
    monkeypatch.setattr(
        sessions, "_stage_and_push",
        lambda tar_bytes, name, *, verbose: {"ok": True, "session_count": 3, "detail": "pushed"},
    )

    res = sessions.capture_codespace_sessions("cs", account=None)

    assert res["ok"] is True
    assert res["deferred"] is False
    assert res["session_count"] == 3


def test_capture_target_busy_defers(monkeypatch):
    import ssh_manager

    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: "acct",
    )
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.get_codespace_status_with_account",
        lambda name, account: (True, "Available", "acct"),
    )
    monkeypatch.setattr(sessions, "_capture_hold_reason", lambda name, *, account: None)

    class _BusyLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self, *a, **k):
            raise ssh_manager.TargetBusyError(
                "cs", SimpleNamespace(pid=1, op="ssh", age_seconds=1.0),
            )

    monkeypatch.setattr(ssh_manager, "TargetLock", _BusyLock)

    res = sessions.capture_codespace_sessions("cs", account=None)

    assert res["ok"] is False
    assert res["deferred"] is True
    assert "busy" in res["detail"]


# --- sync_codespace_sessions: the lock= widening seam ---

def test_sync_reuses_a_passed_lock_and_never_releases_it(monkeypatch):
    """When a caller passes an already-acquired lock, sync_codespace_sessions
    must not acquire or release its own -- the widened caller keeps holding
    it across its own subsequent destructive action."""
    import ssh_manager

    released = []

    class _CallerLock:
        def acquire(self, *a, **k):
            raise AssertionError("must not acquire when a lock is passed in")

        def release(self):
            released.append(True)

    async def _connect_ok(*a, **k):
        return None

    async def _pull_none(*a, **k):
        return None

    monkeypatch.setattr(ssh_manager, "ConnectionManager", lambda *a, **k: SimpleNamespace(
        disconnect=lambda name: _noop(),
    ))
    monkeypatch.setattr(sessions, "_connect_with_retry", _connect_ok)
    monkeypatch.setattr(sessions, "_pull_tar_bytes", _pull_none)

    res = sessions.sync_codespace_sessions("cs", lock=_CallerLock())

    assert res["ok"] is True
    assert released == []  # sync_codespace_sessions never released the caller's lock


def test_capture_defers_while_a_destructive_caller_holds_the_widened_lock(monkeypatch):
    """Contention regression (Phase 3's own item): a capture attempted while
    a destructive caller holds the (widened) SSH target lock across its
    sync+act sequence must defer -- never interleave with it.

    ``TargetLock`` is re-entrant for the SAME pid, so simulating this with a
    literal same-process ``acquire()`` would not exercise the busy path at
    all; write the lock file directly with a genuinely different, live pid
    (this process's own parent) as the holder, exactly as a real second
    process's destructive caller would leave it.
    """
    import os

    import ssh_manager

    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: "acct",
    )
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.get_codespace_status_with_account",
        lambda name, account: (True, "Available", "acct"),
    )
    monkeypatch.setattr(sessions, "_capture_hold_reason", lambda name, *, account: None)

    other_lock = ssh_manager.TargetLock("contended-cs", op="codespace-lifecycle")
    other_lock.path.parent.mkdir(parents=True, exist_ok=True)
    other_lock.path.write_text(
        (
            '{{"pid": {}, "op": "codespace-lifecycle", "target": "contended-cs", '
            '"started_at": 0.0, "host": ""}}'
        ).format(os.getppid()),
        encoding="utf-8",
    )
    try:
        res = sessions.capture_codespace_sessions("contended-cs", account=None)
    finally:
        other_lock.path.unlink(missing_ok=True)

    assert res["ok"] is False
    assert res["deferred"] is True
    assert "busy" in res["detail"]


async def _noop():
    return None
