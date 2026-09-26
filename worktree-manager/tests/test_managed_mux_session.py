"""Tests for the Step-3 managed mux activation/deactivation bundle."""

from __future__ import annotations

from pathlib import Path

from work_coalescing_singleton.server import CoalescingServer

from worktree_manager import managed_mux_link, managed_mux_session, mux_daemon


def _write_monitor_lock(path: Path, server: CoalescingServer) -> None:
    path.write_text(
        (
            "{"
            f"\"managed_mux_endpoint\":\"{server.rendezvous()['endpoint']}\","
            f"\"managed_mux_token\":\"{server.rendezvous()['token']}\""
            "}"
        ),
        encoding="utf-8",
    )


def test_activate_managed_session_registers_and_pushes_live_observation(tmp_path, monkeypatch):
    observed: list[dict] = []

    def _compute(kind: str, payload: dict) -> dict:
        assert kind == managed_mux_link.KIND
        observed.append(dict(payload))
        return {"applied": True, "revision": payload["mapping_revision"]}

    server = CoalescingServer(_compute)
    server.start()
    try:
        lock = tmp_path / "status-monitor.lock"
        _write_monitor_lock(lock, server)
        monkeypatch.setattr(managed_mux_link, "lock_path", lambda root=None: lock)
        monkeypatch.setattr(managed_mux_link, "ensure_status_monitor_running", lambda: True)
        monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda root=None: True)
        monkeypatch.setattr(
            managed_mux_session,
            "_session_metadata",
            lambda mux_bin, mux_session: {
                "attached_clients": 2,
                "session_incarnation": "session-1:100",
                "panes": [{"pane_id": "%1", "role": "head", "live": True}],
            },
        )

        result = managed_mux_session.activate_managed_session(
            "proj",
            "wt-1",
            str(tmp_path / "worktree"),
            "wt-1",
            "tmux",
            root=tmp_path,
        )

        assert result["daemon_running"] is True
        assert result["mapping"] == {"applied": True, "revision": 1}
        assert result["observation"] == {"applied": True, "revision": 1}
        stored = mux_daemon.get_mapping("proj", "wt-1", root=tmp_path)
        assert stored is not None
        assert stored["mux_session"] == "wt-1"
        assert stored["mapping_revision"] == 1
        assert stored["mux_bin"] == "tmux"
        assert observed == [
            {
                "project": "proj",
                "worktree_id": "wt-1",
                "worktree_path": str(tmp_path / "worktree"),
                "mux_session": "wt-1",
                "session_incarnation": "session-1:100",
                "panes": [{"pane_id": "%1", "role": "head", "live": True}],
                "attached_clients": 2,
                "live": True,
                "observed_at": observed[0]["observed_at"],
                "mapping_revision": 1,
            }
        ]
    finally:
        server.close()


def test_deactivate_managed_session_tombstones_and_pushes_live_false(tmp_path, monkeypatch):
    observed: list[dict] = []

    def _compute(kind: str, payload: dict) -> dict:
        assert kind == managed_mux_link.KIND
        observed.append(dict(payload))
        return {"applied": True, "revision": payload["mapping_revision"]}

    server = CoalescingServer(_compute)
    server.start()
    try:
        lock = tmp_path / "status-monitor.lock"
        _write_monitor_lock(lock, server)
        monkeypatch.setattr(managed_mux_link, "lock_path", lambda root=None: lock)
        monkeypatch.setattr(managed_mux_link, "ensure_status_monitor_running", lambda: True)
        mux_daemon.register_mapping(
            {
                "project": "proj",
                "worktree_id": "wt-1",
                "worktree_path": str(tmp_path / "worktree"),
                "mux_session": "wt-1",
                "mux_bin": "tmux",
                "session_incarnation": "session-1:100",
                "panes": [{"pane_id": "%1", "role": "head", "live": True}],
                "attached_clients": 1,
                "mapping_revision": 1,
                "live": True,
                "observed_at": "2026-09-26T00:00:00Z",
            },
            root=tmp_path,
        )

        result = managed_mux_session.deactivate_managed_session(
            "proj",
            "wt-1",
            "wt-1",
            root=tmp_path,
        )

        assert result["mapping"] == {"applied": True, "revision": 2}
        assert result["observation"] == {"applied": True, "revision": 2}
        stored = mux_daemon.get_mapping("proj", "wt-1", root=tmp_path)
        assert stored is not None
        assert stored["live"] is False
        assert stored["mapping_revision"] == 2
        assert observed == [
            {
                "project": "proj",
                "worktree_id": "wt-1",
                "worktree_path": str(tmp_path / "worktree"),
                "mux_session": "wt-1",
                "session_incarnation": "session-1:100",
                "panes": [{"pane_id": "%1", "role": "head", "live": True}],
                "attached_clients": 0,
                "live": False,
                "observed_at": observed[0]["observed_at"],
                "mapping_revision": 2,
            }
        ]
    finally:
        server.close()


def test_activate_mapping_revision_allocation_is_race_free_under_concurrency(tmp_path):
    """Two "concurrent" activations for the same worktree with different
    session identities must never both be allocated the same revision
    (Copilot review finding on PR #3829): the previous
    get-current-then-register-separately shape let two callers both read
    the same current snapshot and both compute (and register at) an
    identical next revision, silently letting a stale/delayed activation
    win over a newer one. ``activate_mapping`` allocates the revision inside
    the same locked critical section that reads the current entry, so two
    callers racing on the interprocess lock must be serialized into two
    strictly increasing revisions, one per distinct identity, and the
    persisted mapping must match whichever payload actually won revision 2."""
    import threading

    payload_a = {
        "project": "proj",
        "worktree_id": "wt-1",
        "worktree_path": str(tmp_path / "a"),
        "mux_session": "wt-1-a",
        "mux_bin": "tmux",
        "session_incarnation": "session-a:100",
        "panes": [],
        "attached_clients": 0,
        "live": True,
        "observed_at": "2026-09-26T00:00:00Z",
    }
    payload_b = {
        **payload_a,
        "worktree_path": str(tmp_path / "b"),
        "mux_session": "wt-1-b",
        "session_incarnation": "session-b:200",
    }
    by_session = {"wt-1-a": payload_a, "wt-1-b": payload_b}

    barrier = threading.Barrier(2)
    results: list[dict] = []
    results_lock = threading.Lock()

    def _activate(payload: dict) -> None:
        barrier.wait(timeout=5)
        result = mux_daemon.activate_mapping(payload, root=tmp_path)
        with results_lock:
            results.append((payload["mux_session"], result))

    threads = [
        threading.Thread(target=_activate, args=(payload_a,)),
        threading.Thread(target=_activate, args=(payload_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(results) == 2
    revisions = sorted(result["revision"] for _, result in results)
    assert revisions == [1, 2], f"expected two strictly distinct revisions, got {revisions}"
    winner_session = next(session for session, result in results if result["revision"] == 2)
    stored = mux_daemon.get_mapping("proj", "wt-1", root=tmp_path)
    assert stored is not None
    assert stored["mapping_revision"] == 2
    assert stored["mux_session"] == winner_session
    assert stored["worktree_path"] == by_session[winner_session]["worktree_path"]

