"""Tests for the Worktree Manager ``mux-status-v1`` wire client."""

from __future__ import annotations

from work_coalescing_singleton import CoalescingServer

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
