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
                "managed_session_id": None if represented else session_id,
                "registered_session_id": session_id if represented else None,
            },
        }

    def read(handle: str, **bounds: Any) -> dict[str, Any]:
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
    monkeypatch.setattr(cli, "_sender_repo", lambda: "demo")
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
        "bridge": {"retained_managed_session_id": "managed-a"},
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
    client_for(monkeypatch, payloads=[snapshot("managed-a", idle=False)])
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=1))
    clock = iter([0.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    cli.main(["wait", "workspace-a", "--bridge", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["settled"] is False
    assert result["reason"] == "timeout"


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
