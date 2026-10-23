"""Tests for the Worktree Manager ``mux-status-v1`` wire client."""

from __future__ import annotations

import pytest

from work_coalescing_singleton import CoalescingServer
from zdd import routing

from agent_worktrees import mux_status_link


@pytest.fixture(autouse=True)
def isolated_start_attempts(monkeypatch):
    original = getattr(mux_status_link, "_invoke_manager_ensure", None)
    monkeypatch.setattr(mux_status_link, "_startup_after", {}, raising=False)
    monkeypatch.setattr(mux_status_link, "_invoke_manager_ensure",
                        lambda *args: pytest.fail("test must stub any Manager lifecycle command"),
                        raising=False)
    return original


def _selected_provider(monkeypatch, tmp_path):
    import json

    registry = tmp_path / "providers"
    registry.mkdir()
    root = tmp_path / "selected-manager"
    root.mkdir()
    command = (str(tmp_path / "declared-manager.exe"), "--declared-prefix")
    (registry / "worktree-manager.json").write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager",
        "command": list(command), "provider_root": str(root), "minimum_version": "0.1.0",
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", str(registry))
    monkeypatch.setenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", "worktree-manager")
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
    return root, command


def _connected_payload(path):
    return dict(project="project", worktree_id="one", worktree_path=str(path),
                mux_session="wt-one", session_incarnation="$1:100",
                mux_bin="psmux", attached_clients=1)


def test_explicit_sink_root_is_preserved_but_cannot_retarget_selected_startup(monkeypatch, tmp_path):
    _selected_provider(monkeypatch, tmp_path)
    override = tmp_path / "explicit-sink"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(override))
    assert mux_status_link._default_root() == override
    assert mux_status_link.push_discovery_via_daemon(_connected_payload(tmp_path)) == {
        "applied": False, "reason": "selected-manager-root-conflict",
    }


def test_endpoint_from_rendezvous_rejects_missing_or_malformed_fields():
    assert mux_status_link.endpoint_from_rendezvous(None) is None
    assert mux_status_link.endpoint_from_rendezvous({}) is None
    assert (
        mux_status_link.endpoint_from_rendezvous(
            {"manager_mux_endpoint": "bad", "manager_mux_token": "token"}
        )
        is None
    )
    assert (
        mux_status_link.endpoint_from_rendezvous(
            {"manager_mux_endpoint": "127.0.0.1:99999", "manager_mux_token": "token"}
        )
        is None
    )


def test_discovery_distinguishes_optional_absence_from_unavailable_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    assert mux_status_link.push_discovery_via_daemon({"mux_session": "wt-one"}) is None
    mux_status_link.lock_path().write_text("{}", encoding="utf-8")
    assert mux_status_link.push_discovery_via_daemon({"mux_session": "wt-one"}) == {
        "applied": False, "reason": "daemon-unavailable",
    }


def test_discovery_uses_existing_authenticated_endpoint(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    seen = []
    server = CoalescingServer(
        lambda kind, payload: seen.append((kind, payload)) or {"applied": False, "reason": "incumbent"},
        linger_seconds=1, subscriber_ttl=3,
    )
    server.start()
    rendezvous = server.rendezvous()
    mux_status_link.lock_path().write_text(json.dumps({
        "manager_mux_endpoint": rendezvous["endpoint"],
        "manager_mux_token": rendezvous["token"],
    }), encoding="utf-8")
    payload = {"mux_session": "wt-one", "session_incarnation": "$1:100"}
    try:
        assert mux_status_link.push_discovery_via_daemon(payload) == {
            "applied": False, "reason": "incumbent",
        }
        assert seen == [("mux-discover-v1", payload)]
        assert server.subscriber_count() == 0
    finally:
        server.close()


def test_old_manager_unsupported_discovery_is_visibly_deferred(monkeypatch, tmp_path, caplog):
    import json
    from types import SimpleNamespace
    from agent_worktrees import __main__ as monitor
    from agent_worktrees import status_monitor_discovery as discovery

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    monkeypatch.setattr(discovery, "_retry_after", {}, raising=False)
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: [
        ("project", SimpleNamespace(worktree_id="one", worktree_path=str(tmp_path))),
    ])
    monkeypatch.setattr(discovery, "_session_path_matches", lambda *args: True)
    monkeypatch.setattr(discovery, "_register_standalone",
                        lambda *args: pytest.fail("unsupported Manager must not authorize fallback"))
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "empty-registry")
    monkeypatch.setattr(monitor, "_monitor_list_sessions",
                        lambda mux: {"wt-one": (1, "$1:100")})
    monkeypatch.setattr(monitor.cfg, "active_project", lambda: None)
    monkeypatch.setattr(monitor, "_monitor_mux_set",
                        lambda *args: pytest.fail("unsupported Manager must not paint directly"))

    def old_manager(kind, payload):
        raise ValueError(f"mux_daemon does not serve kind={kind!r}")

    server = CoalescingServer(old_manager, linger_seconds=1, subscriber_ttl=3)
    server.start()
    rendezvous = server.rendezvous()
    mux_status_link.lock_path().write_text(json.dumps({
        "manager_mux_endpoint": rendezvous["endpoint"],
        "manager_mux_token": rendezvous["token"],
    }), encoding="utf-8")
    try:
        assert monitor._monitor_sweep("mux", "token", "prefix", set()) == 0
        assert any("deferred" in record.message for record in caplog.records)
        assert server.subscriber_count() == 0
    finally:
        server.close()


@pytest.mark.parametrize("stale_token", [False, True])
def test_selected_dormant_manager_starts_at_its_registered_root(monkeypatch, tmp_path, stale_token):
    import json

    root, command = _selected_provider(monkeypatch, tmp_path)
    if stale_token:
        (root / "mux-daemon.token").write_text("old-token", encoding="utf-8")
    seen = []
    server = CoalescingServer(
        lambda kind, payload: seen.append(kind) or {"applied": True},
        linger_seconds=1, subscriber_ttl=3,
    )
    server.start()
    attempts = []

    def ensure(manifest, selected_root):
        attempts.append((manifest.command, selected_root))
        rendezvous = server.rendezvous()
        (root / "mux-daemon.lock").write_text(json.dumps({
            "manager_mux_endpoint": rendezvous["endpoint"],
            "manager_mux_token": rendezvous["token"],
        }), encoding="utf-8")
        return True

    monkeypatch.setattr(mux_status_link, "_invoke_manager_ensure", ensure)
    try:
        payload = _connected_payload(tmp_path)
        assert mux_status_link.push_discovery_via_daemon(payload)["applied"]
        assert attempts == [(command, root)]
        assert mux_status_link.push_status_via_daemon(dict(
            project="project", worktree_id="one", values={"@aw_seg": "status"},
            rendered_at="2026-10-22T12:00:00Z",
        ))["applied"]
        assert seen == ["mux-discover-v1", "mux-status-v1"]
    finally:
        server.close()


def test_unavailable_selected_start_is_shared_and_completion_bounded(monkeypatch, tmp_path):
    _selected_provider(monkeypatch, tmp_path)
    now = [100.0]
    monkeypatch.setattr(mux_status_link, "_now", lambda: now[0], raising=False)
    attempts = []

    def failed_start(*args):
        attempts.append(now[0])
        now[0] += 5.0
        return False

    monkeypatch.setattr(mux_status_link, "_invoke_manager_ensure", failed_start)
    payload = _connected_payload(tmp_path)
    assert not mux_status_link.push_discovery_via_daemon(payload)["applied"]
    now[0] = 124.0
    assert not mux_status_link.push_discovery_via_daemon(dict(
        payload, worktree_id="two", mux_session="wt-two",
    ))["applied"]
    assert attempts == [100.0]
    now[0] = 125.0
    assert not mux_status_link.push_discovery_via_daemon(payload)["applied"]
    assert attempts == [100.0, 125.0]


def test_selected_start_requires_positive_connected_demand(monkeypatch, tmp_path):
    _selected_provider(monkeypatch, tmp_path)
    assert not mux_status_link.push_discovery_via_daemon(
        dict(_connected_payload(tmp_path), attached_clients=0),
    )["applied"]


def test_reachable_old_selected_peer_is_not_restarted_for_unsupported_kind(monkeypatch, tmp_path):
    import json

    root, _ = _selected_provider(monkeypatch, tmp_path)

    def old_peer(kind, payload):
        raise ValueError(f"mux_daemon does not serve kind={kind!r}")

    server = CoalescingServer(old_peer, linger_seconds=1, subscriber_ttl=3)
    server.start()
    rendezvous = server.rendezvous()
    (root / "mux-daemon.lock").write_text(json.dumps({
        "manager_mux_endpoint": rendezvous["endpoint"],
        "manager_mux_token": rendezvous["token"],
    }), encoding="utf-8")
    try:
        result = mux_status_link.push_discovery_via_daemon(_connected_payload(tmp_path))
        assert result is not None and not result.get("applied")
        assert result.get("reason")
    finally:
        server.close()


def test_ensure_uses_declared_invocation_root_and_bounded_headless_custody(
    monkeypatch, tmp_path, isolated_start_attempts,
):
    import json
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(mux_status_link.sys, "platform", "win32")
    root, command = _selected_provider(monkeypatch, tmp_path)
    manifest, diagnostic = mux_status_link._selected_manager()
    assert not diagnostic
    events = []
    proc = SimpleNamespace(wait=lambda timeout: events.append(("wait", timeout)) or 0)
    job = SimpleNamespace(close=lambda: events.append(("close", None)))
    calls = []
    monkeypatch.setattr(mux_status_link, "spawn_sync_in_kill_on_close_job",
                        lambda argv, **kwargs: calls.append((argv, kwargs)) or (proc, job))
    assert isolated_start_attempts(manifest, root)
    argv, kwargs = calls[0]
    assert argv == [*command, "mux-daemon", "ensure"]
    assert kwargs["cwd"] == str(root)
    assert kwargs["env"]["WORKTREE_MANAGER_ROOT"] == str(root)
    assert json.loads(kwargs["env"]["WORKTREE_MANAGER_ENGINE_ARGV"])[1:] == ["-m", "agent_worktrees"]
    assert kwargs["require_job"] is True
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    for field, value in mux_status_link.no_window_kwargs().items():
        assert kwargs[field] == value
    assert events == [("wait", 10.0), ("close", None), ("wait", 5.0)]


def test_ensure_timeout_closes_owned_job_and_retains_startup_backoff(
    monkeypatch, tmp_path, isolated_start_attempts,
):
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(mux_status_link.sys, "platform", "win32")
    root, _ = _selected_provider(monkeypatch, tmp_path)
    manifest, _ = mux_status_link._selected_manager()
    events = []

    def wait(timeout):
        events.append(timeout)
        if timeout == 10.0:
            raise subprocess.TimeoutExpired("declared-manager", timeout)
        return 0

    monkeypatch.setattr(mux_status_link, "spawn_sync_in_kill_on_close_job", lambda *a, **kw: (
        SimpleNamespace(wait=wait), SimpleNamespace(close=lambda: events.append("closed")),
    ))
    monkeypatch.setattr(mux_status_link, "_invoke_manager_ensure", isolated_start_attempts)
    assert mux_status_link._ensure_selected_manager(manifest, root)["reason"] == "manager-startup-failed"
    assert events == [10.0, "closed", 5.0]
    assert mux_status_link._ensure_selected_manager(manifest, root)["reason"] == "manager-startup-deferred"


def test_selected_provider_cannot_turn_startup_into_a_path_lookup(monkeypatch, tmp_path):
    import json

    _selected_provider(monkeypatch, tmp_path)
    path = tmp_path / "providers" / "worktree-manager.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["command"] = ["worktree-manager"]
    path.write_text(json.dumps(document), encoding="utf-8")
    result = mux_status_link.push_discovery_via_daemon(_connected_payload(tmp_path))
    assert result == {"applied": False, "reason": "selected-manager-identity-unavailable"}


def test_posix_ensure_timeout_retires_its_owned_group_before_reaping(
    monkeypatch, tmp_path, isolated_start_attempts,
):
    import subprocess
    from types import SimpleNamespace

    root, _ = _selected_provider(monkeypatch, tmp_path)
    manifest, _ = mux_status_link._selected_manager()
    monkeypatch.setattr(mux_status_link.sys, "platform", "linux")
    events = []
    calls = []

    def wait(timeout):
        events.append(("wait", timeout))
        return 0

    proc = SimpleNamespace(wait=wait, poll=lambda: None, pid=12345)
    monkeypatch.setattr(
        mux_status_link, "spawn_sync_in_kill_on_close_job",
        lambda argv, **kwargs: calls.append(kwargs) or (proc, None),
    )
    def observe(*args, **kwargs):
        events.append(("observe", kwargs["timeout"]))
        raise subprocess.TimeoutExpired("declared-manager", kwargs["timeout"])

    monkeypatch.setattr(mux_status_link, "owned_process", SimpleNamespace(
        _require_posix_execution_support=lambda: None,
        _communicate_unreaped=observe,
        _retire_posix_group=lambda process: events.append(("kill-group", process.pid)),
    ))
    with pytest.raises(subprocess.TimeoutExpired):
        isolated_start_attempts(manifest, root)
    assert calls[0]["start_new_session"] is True
    assert events == [("observe", 10.0), ("kill-group", 12345), ("wait", 5.0)]


def test_posix_timeout_boundary_keeps_exited_leader_pinned_until_descendants_retire(
    monkeypatch, tmp_path, isolated_start_attempts,
):
    import subprocess
    from types import SimpleNamespace

    root, _ = _selected_provider(monkeypatch, tmp_path)
    manifest, _ = mux_status_link._selected_manager()
    monkeypatch.setattr(mux_status_link.sys, "platform", "linux")
    events = []
    descendants = [True]

    class Process:
        pid = 12345
        returncode = None

        def wait(self, timeout):
            if timeout == 10:
                raise subprocess.TimeoutExpired("declared-manager", timeout)
            events.append("reap")
            self.returncode = 0
            return 0

        def poll(self):
            events.append("poll-reaped")
            self.returncode = 0
            return 0

    process = Process()
    monkeypatch.setattr(mux_status_link, "spawn_sync_in_kill_on_close_job",
                        lambda *a, **kw: (process, None))

    def observe(*args, **kwargs):
        events.append("timeout-after-leader-exit")
        raise subprocess.TimeoutExpired("declared-manager", 10)

    def retire(proc):
        assert proc.returncode is None, "the original leader identity must still be pinned"
        descendants[0] = False
        events.append("retire")

    monkeypatch.setattr(mux_status_link, "owned_process", SimpleNamespace(
        _require_posix_execution_support=lambda: None,
        _communicate_unreaped=observe,
        _retire_posix_group=retire,
    ), raising=False)
    with pytest.raises(subprocess.TimeoutExpired):
        isolated_start_attempts(manifest, root)
    assert not descendants[0]
    assert "poll-reaped" not in events
    assert events.index("retire") < events.index("reap")


def test_push_status_via_daemon_forwards_exact_rendered_values_and_releases_client(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    observed = []

    def _compute(kind, payload):
        observed.append((kind, payload, server.subscriber_count()))
        return {"applied": True}

    server = CoalescingServer(_compute, linger_seconds=5.0, subscriber_ttl=30.0)
    server.start()
    try:
        rv = server.rendezvous()
        result = mux_status_link.push_status_via_daemon(
            {
                "project": "proj",
                "worktree_id": "wt-1",
                "values": {"@aw_ctx": "CTX", "@aw_seg": "SEG"},
                "rendered_at": "2026-09-26T12:00:00Z",
                "monitor_generation": "prefix:token",
            },
            lock_data={
                "manager_mux_endpoint": rv["endpoint"],
                "manager_mux_token": rv["token"],
            },
        )
    finally:
        server.close()

    assert result == {"applied": True}
    assert observed == [
        (
            mux_status_link.KIND,
            {
                "project": "proj",
                "worktree_id": "wt-1",
                "values": {"@aw_ctx": "CTX", "@aw_seg": "SEG"},
                "rendered_at": "2026-09-26T12:00:00Z",
                "monitor_generation": "prefix:token",
            },
            1,
        )
    ]
    assert server.subscriber_count() == 0


def test_push_status_via_daemon_reports_daemon_unavailable_when_missing(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    result = mux_status_link.push_status_via_daemon(
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_seg": "SEG"},
            "rendered_at": "2026-09-26T12:00:00Z",
            "monitor_generation": "prefix:token",
        },
        lock_data=None,
    )
    assert result == {"applied": False, "reason": "daemon-unavailable"}


def test_push_status_via_daemon_prefers_routed_endpoint(monkeypatch, tmp_path):
    token = "stable-token"
    observed = []

    def _compute(kind, payload):
        observed.append((kind, payload))
        return {"applied": True}

    server = CoalescingServer(
        _compute,
        linger_seconds=5.0,
        subscriber_ttl=30.0,
        token=token,
    )
    server.start()
    try:
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
        mux_status_link.control_token_path().write_text(token + "\n", encoding="utf-8")
        rv = server.rendezvous()
        host, _, port_s = str(rv["endpoint"]).partition(":")
        routing.publish_active(
            mux_status_link.routing_dir(),
            bind=host,
            port=int(port_s),
            pid=4321,
            version="test",
        )
        result = mux_status_link.push_status_via_daemon(
            {
                "project": "proj",
                "worktree_id": "wt-1",
                "values": {"@aw_ctx": "CTX"},
                "rendered_at": "2026-09-26T12:00:00Z",
                "monitor_generation": "prefix:token",
            },
            lock_data={
                "manager_mux_endpoint": "127.0.0.1:1",
                "manager_mux_token": "wrong-token",
            },
        )
    finally:
        server.close()

    assert result == {"applied": True}
    assert observed == [
        (
            mux_status_link.KIND,
            {
                "project": "proj",
                "worktree_id": "wt-1",
                "values": {"@aw_ctx": "CTX"},
                "rendered_at": "2026-09-26T12:00:00Z",
                "monitor_generation": "prefix:token",
            },
        )
    ]


def test_push_status_via_daemon_falls_back_to_lock_when_routed_token_missing(monkeypatch, tmp_path):
    token = "lock-token"
    observed = []

    def _compute(kind, payload):
        observed.append((kind, payload))
        return {"applied": True}

    server = CoalescingServer(
        _compute,
        linger_seconds=5.0,
        subscriber_ttl=30.0,
        token=token,
    )
    server.start()
    try:
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
        rv = server.rendezvous()
        host, _, port_s = str(rv["endpoint"]).partition(":")
        routing.publish_active(
            mux_status_link.routing_dir(),
            bind=host,
            port=int(port_s),
            pid=4321,
            version="test",
        )
        result = mux_status_link.push_status_via_daemon(
            {
                "project": "proj",
                "worktree_id": "wt-1",
                "values": {"@aw_ctx": "CTX"},
                "rendered_at": "2026-09-26T12:00:00Z",
                "monitor_generation": "prefix:token",
            },
            lock_data={
                "manager_mux_endpoint": rv["endpoint"],
                "manager_mux_token": token,
            },
        )
    finally:
        server.close()

    assert result == {"applied": True}
    assert len(observed) == 1
