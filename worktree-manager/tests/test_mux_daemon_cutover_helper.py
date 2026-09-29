"""Rehearsals for the installer-driven mux-daemon cutover helper."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from work_coalescing_singleton import CoalescingServer
from zdd import breadcrumb, routing

from worktree_manager import mux_daemon
from worktree_manager import mux_daemon_cutover as mdc


class _Handle:
    def __init__(self, daemon):
        self.daemon = daemon
        self.pid = daemon.pid

    def terminate(self) -> None:
        self.daemon.force_terminate()

    def poll(self) -> int | None:
        return None if self.daemon.alive else 0


class _FakeMuxDaemon:
    def __init__(self, pid: int, *, token: str):
        self.pid = pid
        self.token = token
        self.port = None
        self.alive = True
        self.draining = False
        self.shutdown_requested = False
        self.promote_calls = 0
        self.server = None
        self.busy = threading.Event()

    def start(self, port: int) -> None:
        self.server = CoalescingServer(
            self._compute,
            linger_seconds=5.0,
            subscriber_ttl=30.0,
            bind_port=port,
            token=self.token,
        )
        self.server.start()
        self.port = port

    def _compute(self, kind: str, payload: dict) -> dict:
        if kind == mdc.health_kind():
            return {"status": "draining" if self.draining else "ready"}
        if kind == mdc.drain_kind():
            self.draining = True
            timeout = float(payload["timeout"])
            poll = max(0.01, float(payload["poll"]))
            deadline = time.time() + timeout
            while self.busy.is_set() and time.time() < deadline:
                time.sleep(poll)
            return {
                "drained": not self.busy.is_set(),
                "clean": not self.busy.is_set(),
                "forced": False,
                "busy_sessions": ["busy"] if self.busy.is_set() else [],
            }
        if kind == mdc.undrain_kind():
            self.draining = False
            return {"draining": False}
        if kind == mdc.shutdown_kind():
            self.shutdown_requested = True

            def _finish() -> None:
                time.sleep(0.2)
                self.alive = False
                if self.server is not None:
                    self.server.close()

            threading.Thread(target=_finish, daemon=True).start()
            return {"shutdown": True}
        if kind == mdc.adopt_kind():
            self.promote_calls += 1
            return {"adopted": True}
        if kind == mux_daemon.KIND:
            return {"applied": True}
        raise ValueError(kind)

    def force_terminate(self) -> None:
        if self.alive:
            self.alive = False
            if self.server is not None:
                self.server.close()


def _wait_for(predicate, *, timeout: float = 10.0, interval: float = 0.05, message: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(message)


def _mapping_entry() -> dict:
    return {
        "project": "proj",
        "worktree_id": "wt-1",
        "worktree_path": "/tmp/proj.worktrees/wt-1",
        "mux_session": "wt-1",
        "mux_bin": "psmux",
        "session_incarnation": "sess:1",
        "panes": [{"pane_id": "%1", "role": "head", "live": True}],
        "attached_clients": 1,
        "live": True,
        "mapping_revision": 1,
        "observed_at": "2026-09-28T00:00:00Z",
    }


def test_activate_after_update_cuts_over_and_converges(tmp_path, monkeypatch):
    route_dir = mdc.routing_dir(tmp_path)
    lock_path = mux_daemon.lock_path(tmp_path)
    route_dir.mkdir(parents=True)
    token = mdc.load_or_create_control_token(tmp_path)

    old = _FakeMuxDaemon(101, token=token)
    old.start(mdc.pick_free_port())
    first_new = _FakeMuxDaemon(202, token=token)
    second_new = _FakeMuxDaemon(303, token=token)
    daemons = {old.pid: old, first_new.pid: first_new, second_new.pid: second_new}
    pending = [first_new, second_new]

    routing.publish_active(route_dir, bind="127.0.0.1", port=old.port, pid=old.pid, version="old")
    lock_path.write_text(
        json.dumps(
            {
                "pid": old.pid,
                "manager_mux_endpoint": f"127.0.0.1:{old.port}",
                "manager_mux_token": token,
            }
        ),
        encoding="utf-8",
    )
    mux_daemon.register_mapping(_mapping_entry(), root=tmp_path)

    def _self_retire(daemon: _FakeMuxDaemon) -> None:
        while daemon.alive:
            active = routing.read_active_endpoint(route_dir, verify_listener=False)
            if active is not None and active.pid != daemon.pid and not daemon.busy.is_set():
                daemon.force_terminate()
                return
            time.sleep(0.05)

    threading.Thread(target=_self_retire, args=(old,), daemon=True).start()
    threading.Thread(target=_self_retire, args=(first_new,), daemon=True).start()

    old.busy.set()
    threading.Thread(target=lambda: (time.sleep(1.0), old.busy.clear()), daemon=True).start()

    def _spawn(slot, *, root: Path, port: int):
        del slot, root
        daemon = pending.pop(0)
        daemon.start(port)
        return _Handle(daemon)

    monkeypatch.setattr(mdc, "spawn_passive", _spawn)

    first_result: dict[str, object] = {}

    def _run_first() -> None:
        first_result.update(
            mdc.activate_after_update(root=tmp_path, slot=tmp_path / "slot-a", version="2.0.0")
        )

    first_thread = threading.Thread(target=_run_first, daemon=True)
    first_thread.start()

    first_pid = _wait_for(
        lambda: (
            (ep := routing.read_active_endpoint(route_dir, verify_listener=False))
            and ep.pid != old.pid
            and ep.pid
        ),
        message="first cutover never published an active route",
    )
    assert first_pid == first_new.pid
    assert old.alive, "the old daemon exited before the first successor served"

    second_result = mdc.activate_after_update(
        root=tmp_path, slot=tmp_path / "slot-b", version="3.0.0"
    )
    assert second_result["action"] == "cutover"
    assert second_result["result"]["ok"] is True

    first_thread.join(timeout=10)
    assert not first_thread.is_alive(), "first cutover never completed"
    assert first_result["action"] == "cutover"
    assert first_result["result"]["ok"] is True

    final_pid = _wait_for(
        lambda: routing.read_active_endpoint(route_dir, verify_listener=False).pid,
        message="second cutover never published the newest route",
    )
    assert final_pid == second_new.pid

    _wait_for(
        lambda: (not old.alive) and (not first_new.alive) and second_new.alive,
        timeout=10,
        message="superseded mux-daemon generations did not converge to one live daemon",
    )

    stored = mux_daemon.get_mapping("proj", "wt-1", root=tmp_path)
    assert stored is not None
    assert stored["live"] is True
    assert stored["mux_session"] == "wt-1"
    assert stored["mapping_revision"] == 1

    for daemon in daemons.values():
        daemon.force_terminate()


def test_activate_after_update_bootstraps_a_legacy_lock_only_daemon(tmp_path, monkeypatch):
    token = mdc.load_or_create_control_token(tmp_path)
    lock_path = mux_daemon.lock_path(tmp_path)
    old = _FakeMuxDaemon(101, token="legacy-token")
    old.start(mdc.pick_free_port())
    new = _FakeMuxDaemon(202, token=token)
    daemons = {old.pid: old, new.pid: new}

    lock_path.write_text(
        json.dumps(
            {
                "pid": old.pid,
                "manager_mux_endpoint": f"127.0.0.1:{old.port}",
                "manager_mux_token": "legacy-token",
            }
        ),
        encoding="utf-8",
    )

    def _spawn(slot, *, root: Path, port: int):
        del slot, root
        new.start(port)
        return _Handle(new)

    monkeypatch.setattr(mdc, "spawn_passive", _spawn)
    monkeypatch.setattr(
        mdc,
        "_terminate_mux_daemon_pid",
        lambda pid, *, root: (old.force_terminate(), True)[1] if pid == old.pid else False,
    )

    result = mdc.activate_after_update(root=tmp_path, slot=tmp_path / "slot", version="2.0.0")

    assert result["action"] == "legacy-cutover"
    assert result["result"]["ok"] is True
    assert routing.read_active_endpoint(mdc.routing_dir(tmp_path), verify_listener=False).pid == new.pid
    assert old.alive is False
    assert new.alive is True

    for daemon in daemons.values():
        daemon.force_terminate()


def test_activate_after_update_is_noop_when_already_at_target_version(tmp_path, monkeypatch):
    """A reconcile call with the already-active version must never spawn,
    drain, or even contend the cutover lock -- the fast path a launcher
    calling in on every session start (and every self-update check, whether
    or not the payload actually changed) relies on to stay cheap (#5344).
    """
    route_dir = mdc.routing_dir(tmp_path)
    route_dir.mkdir(parents=True)
    token = mdc.load_or_create_control_token(tmp_path)

    live = _FakeMuxDaemon(101, token=token)
    live.start(mdc.pick_free_port())
    routing.publish_active(route_dir, bind="127.0.0.1", port=live.port, pid=live.pid, version="1.2.3")

    def _spawn(*a, **k):
        raise AssertionError("spawn_passive must not be called for a no-op reconcile")

    monkeypatch.setattr(mdc, "spawn_passive", _spawn)

    result = mdc.activate_after_update(root=tmp_path, slot=tmp_path / "slot", version="1.2.3")

    assert result["action"] == "noop"
    assert result["reason"] == "already-active-version"
    assert result["route"]["pid"] == live.pid
    assert routing.read_active_endpoint(route_dir, verify_listener=False).pid == live.pid

    # The cutover lock must never even be touched for the lock-free fast
    # path: tmp_path starts without this file, so its continued absence is
    # the actual regression signal (a stale/released lock file left behind
    # by _acquire_cutover_lock would otherwise make a "try to acquire it"
    # check pass even though the lock WAS taken and released).
    assert not mdc._cutover_lock_path(tmp_path).exists()

    live.force_terminate()


def test_activate_after_update_runs_recovery_despite_stale_breadcrumb_at_target_version(
    tmp_path, monkeypatch
):
    """A stale (non-terminal) breadcrumb from a crashed prior cutover must
    never be masked by the same-version no-op path: the routed daemon can
    already be healthy and on the target version while a stranded old
    survivor or abandoned passive from that aborted attempt still needs
    recovery/reaping (the abandoned-passive gap `zdd.breadcrumb`'s module
    docstring describes; #5344 follow-up). Neither the lock-free pre-check
    nor the under-lock re-check may return "noop" until recovery has
    actually run."""
    route_dir = mdc.routing_dir(tmp_path)
    route_dir.mkdir(parents=True)
    token = mdc.load_or_create_control_token(tmp_path)

    live = _FakeMuxDaemon(101, token=token)
    live.start(mdc.pick_free_port())
    routing.publish_active(
        route_dir, bind="127.0.0.1", port=live.port, pid=live.pid, version="2.0.0"
    )
    # A breadcrumb left in a non-terminal state marks an aborted cutover --
    # as if the orchestrator that produced today's healthy "2.0.0" daemon had
    # crashed right after flipping but before ever reaching "committed".
    breadcrumb.write_breadcrumb(
        route_dir,
        state="draining",
        old={"bind": "127.0.0.1", "port": 12345},
        new_port=live.port,
        new_pid=live.pid,
    )

    def _spawn(*a, **k):
        raise AssertionError(
            "no real cutover is needed: the routed daemon already serves "
            "the target version -- only recovery/reaping should run"
        )

    monkeypatch.setattr(mdc, "spawn_passive", _spawn)

    result = mdc.activate_after_update(root=tmp_path, slot=tmp_path / "slot", version="2.0.0")

    # The stale breadcrumb must have forced the locked path -- recovery and
    # abandoned-passive reaping actually ran -- rather than short-circuiting
    # through either no-op shortcut before they could.
    assert "recovery" in result
    assert "passive_reap" in result
    assert result["action"] == "noop"
    assert result["reason"] == "already-active-version"

    live.force_terminate()


def test_activate_after_update_same_version_second_caller_converges_without_spawn(
    tmp_path, monkeypatch
):
    """A second caller targeting the *same* version as an in-flight cutover
    must not race its own spawn: its lock-free pre-check can still observe
    the pre-flip (old) version and fall through to the cutover lock, but
    once it acquires that lock the first caller has already published the
    target version -- so it must converge to a no-op there instead of
    spawning a second passive daemon for a version that is already active
    (#5344)."""
    route_dir = mdc.routing_dir(tmp_path)
    lock_path = mux_daemon.lock_path(tmp_path)
    route_dir.mkdir(parents=True)
    token = mdc.load_or_create_control_token(tmp_path)

    old = _FakeMuxDaemon(101, token=token)
    old.start(mdc.pick_free_port())
    new = _FakeMuxDaemon(202, token=token)

    routing.publish_active(route_dir, bind="127.0.0.1", port=old.port, pid=old.pid, version="old")
    lock_path.write_text(
        json.dumps(
            {
                "pid": old.pid,
                "manager_mux_endpoint": f"127.0.0.1:{old.port}",
                "manager_mux_token": token,
            }
        ),
        encoding="utf-8",
    )

    spawn_calls: list[int] = []
    spawned_and_started = threading.Event()
    release_first = threading.Event()

    def _spawn(slot, *, root: Path, port: int):
        del slot, root
        if spawn_calls:
            raise AssertionError(
                "spawn_passive must only be called once for a single target version"
            )
        spawn_calls.append(port)
        new.start(port)
        # Hold the first caller here -- spawned and healthy, but not yet
        # flipped -- so the second caller's own lock-free pre-check still
        # observes the pre-cutover (old) version and falls through to the
        # contended lock, exactly the race this test exercises.
        spawned_and_started.set()
        assert release_first.wait(timeout=10), "test never released the first caller"
        return _Handle(new)

    monkeypatch.setattr(mdc, "spawn_passive", _spawn)

    # Deterministic hand-off (no sleep-based timing): the first caller already
    # holds the cutover lock by the time the second caller is started below,
    # so the *second* ever call into ``_acquire_cutover_lock`` is provably the
    # second caller committing to the contended-lock path -- exactly the
    # moment it is safe to release the first caller and know the intended
    # race (second sees "old" pre-lock, blocks on the lock, then re-checks
    # post-lock) was actually exercised.
    real_acquire_lock = mdc._acquire_cutover_lock
    acquire_call_count = 0
    second_reached_lock = threading.Event()

    def _acquire_and_signal(root_arg, **kwargs):
        nonlocal acquire_call_count
        acquire_call_count += 1
        if acquire_call_count == 2:
            second_reached_lock.set()
        return real_acquire_lock(root_arg, **kwargs)

    monkeypatch.setattr(mdc, "_acquire_cutover_lock", _acquire_and_signal)

    first_result: dict[str, object] = {}

    def _run_first() -> None:
        first_result.update(
            mdc.activate_after_update(root=tmp_path, slot=tmp_path / "slot-a", version="2.0.0")
        )

    first_thread = threading.Thread(target=_run_first, daemon=True)
    first_thread.start()

    assert spawned_and_started.wait(timeout=10), "first caller never spawned"
    assert routing.read_active_endpoint(route_dir, verify_listener=False).version == "old", (
        "test setup invalid: the route must still show the pre-cutover version "
        "at the point the second caller's pre-lock check runs"
    )

    second_result: dict[str, object] = {}

    def _run_second() -> None:
        second_result.update(
            mdc.activate_after_update(root=tmp_path, slot=tmp_path / "slot-b", version="2.0.0")
        )

    second_thread = threading.Thread(target=_run_second, daemon=True)
    second_thread.start()

    # Block until the second caller has provably fallen through its own
    # lock-free pre-check (still "old", asserted above) and committed to
    # waiting on the contended cutover lock -- only then release the first
    # caller to proceed to the flip.
    assert second_reached_lock.wait(timeout=10), (
        "second caller never reached the contended cutover lock -- "
        "the pre-lock/under-lock race this test targets was not exercised"
    )
    release_first.set()

    first_thread.join(timeout=10)
    second_thread.join(timeout=10)
    assert not first_thread.is_alive(), "first cutover never completed"
    assert not second_thread.is_alive(), "second caller never converged"

    assert first_result["action"] == "cutover"
    assert first_result["result"]["ok"] is True
    assert second_result["action"] == "noop"
    assert second_result["reason"] == "already-active-version"
    assert len(spawn_calls) == 1, "second caller must not spawn a redundant passive"

    _wait_for(
        lambda: (not old.alive) and new.alive,
        timeout=10,
        message="old daemon never retired after the (only) real cutover",
    )

    old.force_terminate()
    new.force_terminate()
