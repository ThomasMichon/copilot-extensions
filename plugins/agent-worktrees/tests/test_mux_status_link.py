"""Tests for the Worktree Manager ``mux-status-v1`` wire client."""

from __future__ import annotations

from work_coalescing_singleton import CoalescingServer
from zdd import routing

from agent_worktrees import mux_status_link


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


def test_push_status_via_daemon_forwards_exact_rendered_values_and_releases_client():
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


def test_push_status_via_daemon_reports_daemon_unavailable_when_missing():
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
