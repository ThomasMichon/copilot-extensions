"""Tests for the independent local Bridge remote-command adapter."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_dispatch.bridge_remote import (
    LocalBridgeRemoteClient,
    RemoteBridgeOperationError,
    RemoteBridgeUnavailable,
)


class _Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def close(self) -> None:
        pass


_MACHINES_PATH = "/api/v1/machines"


class _RawResponse(_Response):
    def read(self) -> bytes:
        payload: bytes = self._payload
        return payload


def test_open_sends_configured_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def urlopen(request: Any, *, timeout: float) -> _Response:
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


def test_read_and_mutating_operations_use_distinct_http_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(
        _self: LocalBridgeRemoteClient, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        if path != _MACHINES_PATH:
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
    assert calls[2][2]["required_protocol"] == 14
    assert calls[3][0:2] == (
        "POST",
        "/api/v1/remote/host-a/sessions/session-a/end",
    )


def test_create_session_requires_a_higher_protocol_for_a_charter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A charter overlay (rendered as copilot_args) is only honored by a newer
    daemon -- gate on that capability so an older one fails closed instead of
    silently dropping the charter."""
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(
        _self: LocalBridgeRemoteClient, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        if path != _MACHINES_PATH:
            calls.append((method, path, kwargs))
        return {}

    monkeypatch.setattr(LocalBridgeRemoteClient, "_request", request)
    client = LocalBridgeRemoteClient()

    client.create_session(
        "host-a",
        agent="task-worker",
        prompt="work",
        caller_id="fleet-task-a",
        timeout=120.0,
        charter="cab-charter",
    )

    assert calls[0][2]["required_protocol"] == 18
    assert calls[0][2]["body"]["copilot_args"] == ["--agent", "cab-charter"]


def test_malformed_auth_degrades_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "auth.yaml").write_text("token: [", encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", "http://127.0.0.1:1")

    with pytest.raises(RemoteBridgeUnavailable, match="authentication"):
        LocalBridgeRemoteClient(config_dir=tmp_path)._connection()


def test_undecodable_auth_degrades_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contents: str
) -> None:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_url: str
) -> None:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_url: str
) -> None:
    (tmp_path / "auth.yaml").write_text("token: secret", encoding="utf-8")
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", base_url)

    assert LocalBridgeRemoteClient(config_dir=tmp_path)._connection() == (
        base_url.rstrip("/"),
        "secret",
    )


def test_health_probe_shares_operation_timeout_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    calls: list[tuple[str, float]] = []

    def open_response(url: str, _token: str, **kwargs: Any) -> _Response:
        calls.append((url, kwargs["timeout"]))
        if url.endswith("/health"):
            return _Response(
                {"protocol_version": 14, "min_protocol_version": 1}
            )
        return _Response({"session_id": "session-a"})

    monkeypatch.setattr(client, "_open", open_response)

    result = client._request("GET", "/operation", timeout=1.0)

    assert result == {"session_id": "session-a"}
    assert [url for url, _timeout in calls] == [
        "http://127.0.0.1:1/health",
        "http://127.0.0.1:1/operation",
    ]
    assert [timeout for _url, timeout in calls] == pytest.approx([0.8, 0.6])


@pytest.mark.parametrize("payload", [b"not-json", b"[]"])
def test_invalid_health_response_is_an_operation_error(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
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
def test_invalid_health_versions_are_an_operation_error(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
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


def _registry_request(
    machines: Any, calls: list[tuple[str, str]]
) -> Callable[..., dict[str, Any]]:
    def request(
        _self: LocalBridgeRemoteClient, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append((method, path))
        if path == _MACHINES_PATH:
            return {"machines": machines}
        return {}

    return request


def _machine(key: str, *aliases: str) -> dict[str, Any]:
    return {
        "key": key,
        "ssh_environments": [
            {"name": "windows", "alias": alias} for alias in aliases
        ],
    }


def test_remote_operations_target_the_registered_key_for_an_ssh_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fleet pool names hosts by SSH alias, but the carrier requires the
    registered key of an independent execution space; translate a unique
    alias once and reuse it for the session's later operations."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        LocalBridgeRemoteClient,
        "_request",
        _registry_request([_machine("machine-key-a", "host-alias")], calls),
    )
    client = LocalBridgeRemoteClient()

    client.create_session(
        " HOST-ALIAS ", agent="task-worker", prompt="work",
        caller_id="fleet-task-a", timeout=120.0,
    )
    client.session_status("host-alias", "s-1", caller_id="fleet", timeout=8.0)

    assert calls == [
        ("GET", _MACHINES_PATH),
        ("POST", "/api/v1/remote/machine-key-a/sessions"),
        ("GET", "/api/v1/remote/machine-key-a/sessions/s-1/status?caller_id=fleet"),
    ]


@pytest.mark.parametrize(
    "machines",
    [
        [_machine("host-alias", "other"), _machine("machine-key-a", "host-alias")],
        [_machine("key-a", "host-alias"), _machine("key-b", "host-alias")],
        [_machine("key-a", "elsewhere")],
        "not-a-list",
    ],
    ids=["exact-key-wins", "ambiguous-alias", "unknown-alias", "bad-registry"],
)
def test_remote_operations_keep_non_unique_hosts_unchanged(
    monkeypatch: pytest.MonkeyPatch, machines: Any
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        LocalBridgeRemoteClient, "_request", _registry_request(machines, calls)
    )

    LocalBridgeRemoteClient().end_session("host-alias", "s-1", timeout=5.0)

    assert calls[-1] == ("POST", "/api/v1/remote/host-alias/sessions/s-1/end")


def test_remote_operations_keep_an_environment_alias_of_a_multi_environment_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The carrier selects one SSH environment of a multi-environment machine
    only by its exact alias; the machine key is ambiguous there. Each
    environment alias must reach the carrier unchanged."""
    machine = {
        "key": "machine-key-a",
        "ssh_environments": [
            {"name": "linux", "alias": "host-linux"},
            {"name": "windows", "alias": "host-windows"},
        ],
    }
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        LocalBridgeRemoteClient, "_request", _registry_request([machine], calls)
    )
    client = LocalBridgeRemoteClient()

    client.create_session(
        "host-linux", agent="task-worker", prompt="work",
        caller_id="fleet-task-a", timeout=120.0,
    )
    client.end_session("HOST-WINDOWS", "s-1", timeout=5.0)

    assert [call for call in calls if call[1] != _MACHINES_PATH] == [
        ("POST", "/api/v1/remote/host-linux/sessions"),
        ("POST", "/api/v1/remote/host-windows/sessions/s-1/end"),
    ]


def test_unreadable_registry_keeps_the_alias_without_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def request(
        _self: LocalBridgeRemoteClient, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(path)
        if path == _MACHINES_PATH:
            raise RemoteBridgeOperationError("registry unavailable")
        return {}

    monkeypatch.setattr(LocalBridgeRemoteClient, "_request", request)
    client = LocalBridgeRemoteClient()

    client.end_session("host-alias", "s-1", timeout=5.0)
    client.end_session("host-alias", "s-2", timeout=5.0)

    assert calls == [
        _MACHINES_PATH,
        "/api/v1/remote/host-alias/sessions/s-1/end",
        _MACHINES_PATH,
        "/api/v1/remote/host-alias/sessions/s-2/end",
    ]
