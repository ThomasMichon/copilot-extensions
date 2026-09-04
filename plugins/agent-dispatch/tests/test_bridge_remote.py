"""Tests for the independent local Bridge remote-command adapter."""

from agent_dispatch.bridge_remote import LocalBridgeRemoteClient


def test_read_and_mutating_operations_use_distinct_http_generations(monkeypatch):
    calls = []

    def request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {}

    monkeypatch.setattr(LocalBridgeRemoteClient, "_request", request)
    client = LocalBridgeRemoteClient()

    client.session_status(
        "host-a",
        "session-a",
        caller_id="agent-dispatch-fleet",
        timeout=8.0,
    )
    client.resolve_live_session("host-a", "worktree-a", timeout=6.0)
    client.create_session(
        "host-a",
        agent="task-worker",
        prompt="work",
        caller_id="fleet-task-a",
        timeout=120.0,
    )

    assert calls[0][2]["required_protocol"] == 11
    assert calls[1][2]["required_protocol"] == 11
    assert "required_protocol" not in calls[2][2]
