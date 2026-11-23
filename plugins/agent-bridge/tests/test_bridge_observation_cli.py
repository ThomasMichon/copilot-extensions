"""Exact Bridge observations never hydrate, follow or replace a conversation."""

from __future__ import annotations

import copy
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge import __main__ as cli
from agent_bridge.client import BridgeClientError


def snapshot(session_id: str, *, idle: bool = True) -> dict[str, Any]:
    return {
        "identity": {
            "snapshot_session_id": session_id,
            "current_session_id": session_id,
            "native_session_id": "native-a",
            "successor_id": None,
        },
        "state": {"session_status": "idle" if idle else "running", "at_rest": idle},
    }


def client_for(
    monkeypatch: pytest.MonkeyPatch,
    *,
    represented: bool = False,
    payloads: list[dict[str, Any]] | None = None,
) -> tuple[SimpleNamespace, list[tuple[str, Any]]]:
    calls: list[tuple[str, Any]] = []
    session_id = "native-a" if represented else "managed-a"
    values = list(payloads or [snapshot(session_id)])

    def resolve(handle: str, **scope: Any) -> dict[str, Any]:
        calls.append(("resolve", (handle, scope)))
        return {
            "bridge": {
                "session_id": "native-a",
                "managed_session_id": None if represented else session_id,
                "registered_session_id": session_id if represented else None,
            },
        }

    def read(handle: str, **bounds: Any) -> dict[str, Any]:
        assert bounds.pop("require_native_identity") is True
        calls.append(("snapshot", (handle, bounds)))
        return copy.deepcopy(values.pop(0) if len(values) > 1 else values[0])

    client = SimpleNamespace(
        resolve_bridge=resolve,
        get_live_session=lambda handle: {"session_id": handle, "status": "live"},
        **{"get_live_result_snapshot" if represented else "get_result_snapshot": read},
    )

    def get_client(**kwargs: Any) -> SimpleNamespace:
        assert kwargs == {"ensure": False}
        return client

    monkeypatch.setattr(cli, "_get_client", get_client)
    monkeypatch.setattr(cli, "_sender_repo", lambda: cli._PROJECT_OVERRIDE or "demo")
    return client, calls


@pytest.mark.parametrize("represented", [False, True])
@pytest.mark.parametrize("handle", ["workspace-a", "native-a", "managed-a"])
def test_bridge_read_pins_exact_identity_and_common_bounds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    represented: bool,
    handle: str,
) -> None:
    _client, calls = client_for(monkeypatch, represented=represented)
    cli.main(
        [
            "--json",
            "read",
            handle,
            "--bridge",
            "--tail",
            "4",
            "--max-text-chars",
            "512",
            "--machine",
            "host-a",
            "--namespace",
            "container",
        ]
    )
    session_id = "native-a" if represented else "managed-a"
    assert calls == [
        (
            "resolve",
            (
                handle,
                {
                    "project": "demo",
                    "all_projects": False,
                    "machine": "host-a",
                    "namespace": "container",
                    "local_only": True,
                },
            ),
        ),
        ("snapshot", (session_id, {"max_items": 4, "max_text_chars": 512})),
    ]
    assert json.loads(capsys.readouterr().out) == snapshot(session_id)


@pytest.mark.parametrize("represented", [False, True])
def test_bridge_wait_uses_the_same_conversation_until_observed_idle(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    represented: bool,
) -> None:
    session_id = "native-a" if represented else "managed-a"
    _client, calls = client_for(
        monkeypatch,
        represented=represented,
        payloads=[snapshot(session_id, idle=False), snapshot(session_id)],
    )
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    cli.main(["wait", "workspace-a", "--bridge", "--all-projects", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result == {"settled": True, "reason": "idle", "snapshot": snapshot(session_id)}
    assert calls[0][1][1]["all_projects"] is True
    assert calls[0][1][1]["project"] is None
    assert calls[1:] == [
        ("snapshot", (session_id, {"max_items": 1, "max_text_chars": 256})),
        ("snapshot", (session_id, {"max_items": 1, "max_text_chars": 256})),
    ]
    assert len(sleeps) == 1


@pytest.mark.parametrize("status", [404, 409, 426, 503])
@pytest.mark.parametrize("command", ["read", "wait"])
def test_failed_bridge_selection_never_falls_back_to_legacy_resolution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: int,
    command: str,
) -> None:
    client, calls = client_for(monkeypatch)

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise BridgeClientError(status, "fixture-selection-refused")

    client.resolve_bridge = refuse
    with pytest.raises(SystemExit) as exc:
        cli.main([command, "workspace-a", "--bridge"])
    assert exc.value.code == 1
    assert calls == []
    assert "fixture-selection-refused" in capsys.readouterr().err


def test_metadata_only_bridge_does_not_hydrate_a_control_record(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, calls = client_for(monkeypatch)
    client.resolve_bridge = lambda *_args, **_kwargs: {"bridge": {"session_id": "native-a"}}
    with pytest.raises(SystemExit) as exc:
        cli.main(["read", "workspace-a", "--bridge"])
    assert exc.value.code == 1
    assert not calls
    assert "bridge_observation_unavailable" in capsys.readouterr().err


def test_bridge_read_keeps_retained_managed_history_without_resuming(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, calls = client_for(monkeypatch)
    client.resolve_bridge = lambda *_args, **_kwargs: {
        "bridge": {"session_id": "native-a", "retained_managed_session_id": "managed-a"},
    }
    cli.main(["--json", "read", "workspace-a", "--bridge"])
    assert calls == [("snapshot", ("managed-a", {"max_items": None, "max_text_chars": None}))]
    assert json.loads(capsys.readouterr().out) == snapshot("managed-a")


def test_bridge_read_rejects_retargeted_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client_for(monkeypatch, payloads=[snapshot("replacement")])
    with pytest.raises(SystemExit) as exc:
        cli.main(["read", "workspace-a", "--bridge"])
    assert exc.value.code == 1
    assert "bridge_observation_identity_changed" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["read", "wait"])
def test_bridge_observation_rejects_native_recreation_under_same_managed_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    replacement = snapshot("managed-a")
    replacement["identity"]["native_session_id"] = "native-b"
    replacement["latest_result"] = {"value": "replacement content must not be printed"}
    payloads = (
        [snapshot("managed-a", idle=False), replacement] if command == "wait" else [replacement]
    )
    _client, calls = client_for(monkeypatch, payloads=payloads)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", command, "workspace-a", "--bridge"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "bridge_observation_identity_changed" in captured.err
    assert len(calls) == (3 if command == "wait" else 2)


@pytest.mark.parametrize("command", ["read", "wait"])
@pytest.mark.parametrize("bridge", [False, True])
def test_explicit_project_is_consumed_only_by_bridge_observations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    bridge: bool,
) -> None:
    _client, calls = client_for(monkeypatch)
    argv = ["--project", "other-project", "--json", command, "workspace-a"]
    if bridge:
        cli.main([*argv, "--bridge"])
        assert calls[0][1][1]["project"] == "other-project"
        assert json.loads(capsys.readouterr().out)
    else:
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 2
        assert not calls
        assert "not meaningful" in capsys.readouterr().err


def test_bridge_wait_reports_successor_without_following_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    value = snapshot("managed-a")
    value["identity"].update(current_session_id="managed-b", successor_id="managed-b")
    _client, calls = client_for(monkeypatch, payloads=[value])
    cli.main(["wait", "workspace-a", "--bridge", "--json"])
    assert json.loads(capsys.readouterr().out)["reason"] == "identity_changed"
    assert len(calls) == 2


def test_expired_represented_registration_is_not_reported_as_idle(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, _calls = client_for(monkeypatch, represented=True)
    client.get_live_session = lambda handle: {"session_id": handle, "status": "expired"}
    cli.main(["wait", "workspace-a", "--bridge", "--json"])
    assert json.loads(capsys.readouterr().out)["reason"] == "expired"


def test_bridge_wait_timeout_does_not_claim_settlement(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, calls = client_for(monkeypatch, payloads=[snapshot("managed-a", idle=False)])
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=1))
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def consume_budget(_seconds: float) -> None:
        clock[0] = 2.0

    monkeypatch.setattr(time, "sleep", consume_budget)
    cli.main(["wait", "workspace-a", "--bridge", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["settled"] is False
    assert result["reason"] == "timeout"
    assert len(calls) == 2


@pytest.mark.parametrize("phase", ["resolution", "registration", "snapshot"])
def test_bridge_wait_stops_at_deadline_between_blocking_reads(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase: str,
) -> None:
    client, calls = client_for(monkeypatch, represented=True)
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=1))
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    method = {
        "resolution": "resolve_bridge",
        "registration": "get_live_session",
        "snapshot": "get_live_result_snapshot",
    }[phase]
    original = getattr(client, method)

    def expired(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        clock[0] = 1.0
        return result

    setattr(client, method, expired)
    cli.main(["wait", "workspace-a", "--bridge", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result == {"settled": False, "reason": "timeout", "snapshot": None}
    assert len(calls) == (2 if phase == "snapshot" else 1)


def test_bridge_wait_caps_backoff_and_does_not_start_another_poll(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _client, calls = client_for(monkeypatch, payloads=[snapshot("managed-a", idle=False)])
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=0.1))
    clock = [0.0]
    sleeps = []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(time, "sleep", sleep)
    cli.main(["wait", "workspace-a", "--bridge", "--json"])
    assert json.loads(capsys.readouterr().out)["reason"] == "timeout"
    assert sleeps == [0.1]
    assert len(calls) == 2


@pytest.mark.parametrize("phase", ["client", "capability", "protocol", "result", "retry"])
def test_bridge_wait_shares_deadline_with_real_http_and_protocol_requests(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase: str,
) -> None:
    from email.message import Message
    import io
    import urllib.error
    import urllib.request

    from agent_bridge.client import BridgeClient
    from agent_bridge.protocol import HTTP_PROTOCOL_MIN_SUPPORTED, HTTP_PROTOCOL_VERSION

    bridge = BridgeClient("http://fixture.invalid", "fixture-token")
    client, _calls = client_for(monkeypatch)
    monkeypatch.setattr(bridge, "resolve_bridge", client.resolve_bridge)

    def get_client(**kwargs: Any) -> BridgeClient:
        assert kwargs == {"ensure": False}
        if phase == "client":
            bridge.assert_client_supported()
        return bridge

    monkeypatch.setattr(cli, "_get_client", get_client)
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=1))
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    requests = []

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr(time, "sleep", sleep)

    class Response(io.BytesIO):
        status = 200

    def urlopen(request: Any, *, timeout: float) -> Response:
        requests.append((request.full_url, timeout))
        if request.full_url.endswith("/health"):
            index = len(requests)
            clock[0] += (
                1 if (phase, index) in {("client", 1), ("capability", 1), ("protocol", 2)} else 0.2
            )
            return Response(
                json.dumps(
                    {
                        "protocol_version": HTTP_PROTOCOL_VERSION,
                        "min_protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
                        "capabilities": {"result_native_identity": 1},
                    }
                ).encode()
            )
        if phase == "retry":
            clock[0] += 0.2
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "merging",
                Message(),
                io.BytesIO(b'{"detail":"merging"}'),
            )
        clock[0] += 1
        return Response(json.dumps(snapshot("managed-a")).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    cli.main(["wait", "workspace-a", "--bridge", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result == {"settled": False, "reason": "timeout", "snapshot": None}
    expected = {"client": [1.0], "capability": [1.0], "protocol": [1.0, 0.8]}.get(
        phase,
        [1.0, 0.8, 0.6],
    )
    assert [timeout for _url, timeout in requests] == pytest.approx(expected)
    assert clock[0] >= 1


@pytest.mark.parametrize(
    "argv",
    [
        ["read", "workspace-a", "--bridge", "--range", "1:2"],
        ["read", "workspace-a", "--bridge", "--event", "0"],
        ["read", "workspace-a", "--bridge", "--since", "0"],
        ["read", "workspace-a", "--bridge", "--message-id", "0"],
        ["wait", "workspace-a", "--bridge", "--all-attention"],
        ["wait", "workspace-a", "--bridge", "--attention", "input_required"],
        ["wait", "workspace-a", "--bridge", "--position", "position-a"],
        ["read", "workspace-a", "--all-projects"],
        ["wait", "workspace-a", "--machine", "host-a"],
    ],
)
def test_bridge_options_never_silently_change_legacy_meaning(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    _client, calls = client_for(monkeypatch)
    with pytest.raises(SystemExit, match="bridge"):
        cli.main(argv)
    assert not calls
