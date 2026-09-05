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


def test_open_sends_configured_bearer_token(monkeypatch):
    captured = {}

    def urlopen(request, *, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return _Response({})

    monkeypatch.setattr(
        "agent_dispatch.bridge_remote.urllib.request.urlopen",
        urlopen,
    )

    response = LocalBridgeRemoteClient._open(
        "http://127.0.0.1:8080/health",
        "test-token",
        timeout=2.0,
    )
    response.close()

    assert captured == {
        "authorization": "Bearer " + "test-token",
        "timeout": 2.0,
    }


def test_read_and_mutating_operations_use_distinct_http_generations(monkeypatch):
    calls = []

    def request(_self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {}

    monkeypatch.setattr(LocalBridgeRemoteClient, "_request", request)
    client = LocalBridgeRemoteClient()

    client.session_status(
        "  HOST-A  ",
        "session-a",
        caller_id="agent-dispatch-fleet",
        timeout=8.0,
    )
    client.resolve_live_session("  HOST-A  ", "worktree-a", timeout=6.0)
    client.create_session(
        "  HOST-A  ",
        agent="task-worker",
        prompt="work",
        caller_id="fleet-task-a",
        timeout=120.0,
    )
    client.end_session("  HOST-A  ", "session-a", timeout=20.0)

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


def test_undecodable_auth_degrades_as_unavailable(tmp_path, monkeypatch):
    (tmp_path / "auth.yaml").write_bytes(b"\xff")
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", "http://127.0.0.1:1")

    with pytest.raises(RemoteBridgeUnavailable, match="authentication"):
        LocalBridgeRemoteClient(config_dir=tmp_path)._connection()


@pytest.mark.parametrize(
    "contents",
    [
        "- not-a-mapping\n",
        "token: [not-a-string]\n",
        "token: '   '\n",
    ],
)
def test_invalid_auth_shape_degrades_as_unavailable(
    tmp_path, monkeypatch, contents
):
    (tmp_path / "auth.yaml").write_text(contents, encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", "http://127.0.0.1:1")

    with pytest.raises(RemoteBridgeUnavailable, match="authentication"):
        LocalBridgeRemoteClient(config_dir=tmp_path)._connection()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com:8080",
        "http://user@127.0.0.1:8080",
        "file://127.0.0.1/tmp/bridge",
        "http://127.0.0.1:8080/bridge",
    ],
)
def test_explicit_base_url_must_be_loopback_http(
    tmp_path, monkeypatch, base_url
):
    (tmp_path / "auth.yaml").write_text("token: secret", encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", base_url)

    with pytest.raises(RemoteBridgeUnavailable, match="loopback HTTP or HTTPS"):
        LocalBridgeRemoteClient(config_dir=tmp_path)._connection()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8080/",
        "http://127.0.0.2:8080",
        "http://[::1]:8080",
    ],
)
def test_explicit_base_url_accepts_loopback_hosts(
    tmp_path, monkeypatch, base_url
):
    (tmp_path / "auth.yaml").write_text("token: secret", encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", base_url)

    assert LocalBridgeRemoteClient(config_dir=tmp_path)._connection() == (
        base_url.rstrip("/"),
        "secret",
    )


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


@pytest.mark.parametrize(
    "payload",
    [
        {"protocol_version": "invalid", "min_protocol_version": 1},
        {"protocol_version": 14, "min_protocol_version": []},
    ],
)
def test_invalid_health_versions_are_an_operation_error(monkeypatch, payload):
    client = LocalBridgeRemoteClient()
    monkeypatch.setattr(
        client,
        "_connection",
        lambda: ("http://127.0.0.1:1", "token"),
    )
    monkeypatch.setattr(
        client,
        "_open",
        lambda *_args, **_kwargs: _Response(payload),
    )

    with pytest.raises(RemoteBridgeOperationError, match="protocol versions"):
        client._request("GET", "/operation", timeout=1.0)
