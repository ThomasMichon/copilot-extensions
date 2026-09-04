"""Tests for the independent local Bridge remote-command adapter."""

import json

import pytest

from agent_dispatch.bridge_remote import (
    LocalBridgeRemoteClient,
    RemoteBridgeOperationError,
    RemoteBridgeUnavailable,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def close(self):
        pass


class _RawResponse(_Response):
    def read(self):
        return self._payload


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
    client.end_session("host-a", "session-a", timeout=20.0)

    assert calls[0][2]["required_protocol"] == 11
    assert calls[1][2]["required_protocol"] == 11
    assert "required_protocol" not in calls[2][2]
    assert calls[3][0:2] == (
        "POST",
        "/api/v1/remote/host-a/sessions/session-a/end",
    )


def test_malformed_auth_degrades_as_unavailable(tmp_path, monkeypatch):
    (tmp_path / "auth.yaml").write_text("token: [", encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", "http://127.0.0.1:1")

    with pytest.raises(RemoteBridgeUnavailable, match="authentication"):
        LocalBridgeRemoteClient(config_dir=tmp_path)._connection()


def test_health_probe_shares_operation_timeout_budget(monkeypatch):
    client = LocalBridgeRemoteClient()
    monkeypatch.setattr(
        client,
        "_connection",
        lambda: ("http://127.0.0.1:1", "token"),
    )
    clock = iter([100.0, 100.2, 100.4])
    monkeypatch.setattr(
        "agent_dispatch.bridge_remote.time.monotonic",
        lambda: next(clock),
    )
    calls = []

    def open_response(url, _token, **kwargs):
        calls.append((url, kwargs["timeout"]))
        if url.endswith("/health"):
            return _Response(
                {"protocol_version": 14, "min_protocol_version": 1}
            )
        return _Response({"session_id": "session-a"})

    monkeypatch.setattr(client, "_open", open_response)

    result = client._request("GET", "/operation", timeout=1.0)

    assert result == {"session_id": "session-a"}
    assert calls == [
        ("http://127.0.0.1:1/health", pytest.approx(0.8)),
        ("http://127.0.0.1:1/operation", pytest.approx(0.6)),
    ]


@pytest.mark.parametrize("payload", [b"not-json", b"[]"])
def test_invalid_health_response_is_an_operation_error(monkeypatch, payload):
    client = LocalBridgeRemoteClient()
    monkeypatch.setattr(
        client,
        "_connection",
        lambda: ("http://127.0.0.1:1", "token"),
    )
    monkeypatch.setattr(
        client,
        "_open",
        lambda *_args, **_kwargs: _RawResponse(payload),
    )

    with pytest.raises(RemoteBridgeOperationError, match="health"):
        client._request("GET", "/operation", timeout=1.0)
