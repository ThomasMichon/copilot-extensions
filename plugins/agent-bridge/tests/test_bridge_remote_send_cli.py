"""Explicit-host sends retain their admission and exact completed-turn identity."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge import __main__ as cli
from agent_bridge.client import BridgeClientError


def snapshot(index: int, text: str, **changes: Any) -> dict[str, Any]:
    return {
        "identity": {
            "snapshot_session_id": "managed-a", "native_session_id": "native-a",
            "current_session_id": "managed-a", "successor_id": None,
        },
        "latest_result": {
            "availability": "available",
            "value": {"turn_index": index, "text": text},
            "truncation": {"truncated": False},
        },
        "state": {"session_status": "running"},
        **changes,
    }


def wire(
    monkeypatch: pytest.MonkeyPatch, *, payloads: list[Any] | None = None,
    changes: dict[str, Any] | None = None, admission: Any = None, represented: bool = False,
) -> list[tuple[str, Any]]:
    calls: list[tuple[str, Any]] = []
    values = list(payloads or [snapshot(2, "previous turn"), snapshot(3, "selected result")])

    def resolve(handle: str, **scope: Any) -> dict[str, Any]:
        calls.append(("resolve", (handle, scope)))
        return {"bridge": {
            "status": "running", "session_id": "native-a",
            "managed_session_id": None if represented else "managed-a",
            "registered_session_id": "native-a" if represented else None,
            "route": {"host": "host-b"}, **(changes or {}),
        }}

    def read(host: str, **options: Any) -> Any:
        calls.append(("read", (host, options)))
        result = values.pop(0) if len(values) > 1 else values[0]
        if isinstance(result, BaseException):
            raise result
        return copy.deepcopy(result)

    def submit(host: str, **options: Any) -> dict[str, Any]:
        calls.append(("submit", (host, options)))
        if isinstance(admission, Exception):
            raise admission
        if represented:
            return {
                "version": 1, "host": host, "session_id": options["session_id"],
                "invocation_key": options["invocation_key"], "sender": options["sender"],
                "admission": "accepted", "message_id": 41,
            }
        return {
            "version": 1, "host": host, "session_id": "managed-a",
            "native_session_id": "native-a", "invocation_key": options["invocation_key"],
            "admission": "accepted", "queued": False, "turn_index": 3,
        }

    client = SimpleNamespace(
        resolve_bridge=resolve, get_remote_result_snapshot=read, submit_remote_native_prompt=submit,
        submit_remote_exact_live_prompt=submit,
    )
    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: client)
    monkeypatch.setattr(cli, "_sender_repo", lambda: "demo")
    monkeypatch.setattr(cli, "_caller_id_for", lambda _args: "caller")
    monkeypatch.setattr(
        "agent_bridge.session_targeting_cli._live_sender_label",
        lambda args: args.sender or "fixture-sender",
    )
    monkeypatch.setattr(cli, "_RECONNECT_BACKOFF", 0)
    return calls


@pytest.mark.parametrize("selection", [[], ["--bridge"]])
@pytest.mark.parametrize("as_json", [False, True])
def test_admission_only_mode_pins_host_owner_native_and_generated_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], as_json: bool,
    selection: list[str],
) -> None:
    calls = wire(monkeypatch, payloads=[AssertionError("admission-only read")])
    cli.main([
        *(["--json"] if as_json else []),
        "send", "workspace-a", "work", *selection, "--host", "host-b", "--no-wait",
    ])
    output = capsys.readouterr().out
    assert calls[0] == ("resolve", ("workspace-a", {
        "hosts": ["host-b"], "project": "demo", "all_projects": False,
        "machine": None, "namespace": None,
    }))
    assert [name for name, _value in calls] == ["resolve", "submit"]
    host, submitted = calls[1][1]
    assert host == "host-b" and submitted["session_id"] == "managed-a"
    assert submitted["native_session_id"] == "native-a"
    assert len(submitted["invocation_key"]) == 32
    if as_json:
        payload = json.loads(output)
        assert payload["outcome"] == "delivered" and payload["admission"] == "accepted"
        assert payload["invocation_key"] == submitted["invocation_key"]
        assert payload["bridge"]["host"] == "host-b"
    else:
        assert submitted["invocation_key"] in output and "--no-wait" in output


def test_text_send_waits_for_selected_completed_turn_without_printing_previous_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls = wire(monkeypatch, payloads=[
        snapshot(2, "previous preflight"),
        snapshot(2, "previous completed turn"),
        snapshot(3, "selected result"),
    ])
    cli.main(["send", "workspace-a", "work", "--bridge", "--host", "host-b"])
    output = capsys.readouterr()
    assert "selected result" in output.out and "previous" not in output.out
    assert output.err == ""
    assert [name for name, _value in calls] == ["resolve", "read", "submit", "read", "read"]
    reads = [value for name, value in calls if name == "read"]
    assert all(host == "host-b" and value["session_id"] == "managed-a"
               and value["native_session_id"] == "native-a" and value["represented"] is False
               for host, value in reads)
    assert [value["max_text_chars"] for _host, value in reads] == [256, 6000, 6000]


@pytest.mark.parametrize("fault", [
    "later-turn", "identity", "connection", "timeout", "partial", "origin", "interrupted",
])
def test_post_admission_observation_never_replays_or_becomes_unknown_admission(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fault: str,
) -> None:
    after: Any = snapshot(4, "foreign result")
    if fault == "identity":
        after = snapshot(3, "foreign result")
        after["identity"]["current_session_id"] = "successor"
    elif fault == "connection":
        after = BridgeClientError(503, "observation unavailable")
    elif fault == "interrupted":
        after = KeyboardInterrupt()
    elif fault == "timeout":
        after = snapshot(2, "foreign result")
        from agent_bridge import client_read_budget

        def expire(_seconds: float) -> None:
            raise client_read_budget.ReadDeadlineExpired("fixture budget")

        monkeypatch.setattr(client_read_budget, "pause", expire)
    elif fault == "partial":
        after = snapshot(3, "bounded result")
        after["latest_result"].update(availability="partial", truncation={"truncated": True})
    elif fault == "origin":
        after = snapshot(3, "foreign result")
        after["latest_result"] = {"availability": "unsupported_for_target"}
        after["state"] = {"session_status": "idle", "at_rest": True}
    calls = wire(monkeypatch, payloads=[snapshot(2, "preflight"), after])
    cli.main(["send", "workspace-a", "work", "--bridge", "--host", "host-b"])
    output = capsys.readouterr()
    assert "foreign result" not in output.out and "UNKNOWN" not in output.err
    assert len([item for item in calls if item[0] == "submit"]) == 1
    if fault == "partial":
        assert "bounded result" in output.out
        assert "partial" in output.err and "truncated" in output.err
    else:
        assert "remains accepted" in output.err and "do not resend" in output.err


def test_text_result_preflight_refuses_before_prompt_when_provider_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls = wire(monkeypatch, payloads=[BridgeClientError(426, "prior result provider")])
    with pytest.raises(SystemExit) as error:
        cli.main(["send", "workspace-a", "work", "--bridge", "--host", "host-b"])
    assert error.value.code == 69
    assert "prior result provider" in capsys.readouterr().err
    assert [name for name, _value in calls] == ["resolve", "read"]


def test_remote_feed_expansion_cannot_be_silently_ignored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_get_client", lambda **_kw: pytest.fail("invalid feed mode reached client"))
    with pytest.raises(SystemExit) as error:
        cli.main([
            "--json", "send", "workspace-a", "work", "--bridge", "--host", "host-b",
            "--expand", "tools",
        ])
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "bridge_send_options"


def test_remote_hosting_requirement_refuses_before_intermediary_or_receiver(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_get_client", lambda **_kw: pytest.fail("hosting requirement reached peer"))
    with pytest.raises(SystemExit) as error:
        cli.main([
            "--json", "send", "workspace-a", "work", "--bridge", "--host", "host-b",
            "--require-agent-hosting",
        ])
    assert error.value.code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["reason"] == "bridge_send_options"
    assert "end-to-end" in output["error"] and "--require-agent-hosting" in output["error"]


@pytest.mark.parametrize("no_wait", [False, True])
@pytest.mark.parametrize("fault", ["lost-response", "malformed-refusal"])
def test_text_unknown_retains_generated_key_and_original_binding(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_wait: bool, fault: str,
) -> None:
    calls = wire(monkeypatch, admission=BridgeClientError(502, {
        "code": (
            "native_prompt_admission_unknown" if fault == "lost-response" else "native_prompt_refused"
        ),
    }))
    with pytest.raises(SystemExit) as error:
        cli.main([
            "send", "workspace-a", "work", "--bridge", "--host", "host-b",
            *(["--no-wait"] if no_wait else []),
        ])
    assert error.value.code == 70
    output = capsys.readouterr()
    submitted = [value for name, value in calls if name == "submit"]
    assert len(submitted) == 1
    key = submitted[0][1]["invocation_key"]
    assert len(key) == 32
    assert not output.out
    assert all(value in output.err for value in ("host-b", "managed-a", "native-a", key))


@pytest.mark.parametrize("outcome,code", [
    ("refused_busy", 75), ("refused_conflict", 65), ("refused_unavailable", 69),
])
def test_known_native_refusal_retains_remote_binding_and_original_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], outcome: str, code: int,
) -> None:
    calls = wire(monkeypatch, admission=BridgeClientError(409, {
        "code": "native_prompt_refused",
        "refusal": {
            "outcome": outcome, "reason": "fixture_reason", "error": "fixture refusal",
            "retryable": False,
        },
    }))
    with pytest.raises(SystemExit) as error:
        cli.main([
            "--json", "send", "workspace-a", "work", "--bridge", "--host", "host-b",
            "--idempotency-key", "original-key",
        ])
    assert error.value.code == code
    output = json.loads(capsys.readouterr().out)
    assert output["outcome"] == outcome and output["bridge"]["host"] == "host-b"
    assert output["bridge"]["managed_session_id"] == "managed-a"
    assert output["idempotency_key"] == "original-key"
    assert [name for name, _value in calls] == ["resolve", "submit"]


@pytest.mark.parametrize("changes", [
    {"route": {"host": "other-host"}},
    {"managed_session_id": None, "registered_session_id": "other-native"},
    {"managed_session_id": None, "retained_managed_session_id": "managed-a"},
])
def test_remote_selection_never_uses_another_host_or_unexpanded_owner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], changes: dict[str, Any],
) -> None:
    calls = wire(monkeypatch, changes=changes)
    with pytest.raises(SystemExit) as error:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge", "--host", "host-b"])
    assert error.value.code == 69
    assert json.loads(capsys.readouterr().out)["outcome"] == "refused_unavailable"
    assert [name for name, _value in calls] == ["resolve"]


@pytest.mark.parametrize("host,bridge", [("", True), (" ", True), ("host-b", False)])
def test_invalid_host_mode_stops_before_client(
    monkeypatch: pytest.MonkeyPatch, host: str, bridge: bool,
) -> None:
    monkeypatch.setattr(cli, "_get_client", lambda **_kw: pytest.fail("invalid host reached client"))
    with pytest.raises(SystemExit, match="host"):
        cli.main([
            "send", "workspace-a", "work", "--host", host,
            *(["--bridge"] if bridge else ["--legacy-selector"]),
        ])


def test_admission_unknown_preserves_generated_key_and_original_remote_binding(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls = wire(monkeypatch, admission=BridgeClientError(502, {
        "code": "native_prompt_admission_unknown",
    }))
    with pytest.raises(SystemExit) as error:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge", "--host", "host-b"])
    assert error.value.code == 70
    output = json.loads(capsys.readouterr().out)
    assert output["outcome"] == "unknown" and output["retryable"] is False
    assert output["bridge"] == {
        "host": "host-b", "session_id": "native-a", "managed_session_id": "managed-a",
        "registered_session_id": None,
    }
    assert output["idempotency_key"] == calls[1][1][1]["invocation_key"]


@pytest.mark.parametrize("as_json,no_wait", [(True, False), (False, False), (False, True)])
@pytest.mark.parametrize("explicit", [False, True])
def test_registered_send_reports_only_admission_with_exact_sender_and_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    as_json: bool, no_wait: bool, explicit: bool,
) -> None:
    calls = wire(monkeypatch, represented=True, payloads=[AssertionError("inbox must not read results")])
    cli.main([
        *(["--json"] if as_json else []),
        "send", "workspace-a", "work", "--bridge", "--host", "host-b",
        *(["--no-wait"] if no_wait else []),
        *(["--sender", "controller", "--idempotency-key", "original-key"] if explicit else []),
    ])
    assert [name for name, _value in calls] == ["resolve", "submit"]
    host, submitted = calls[1][1]
    key = submitted["invocation_key"]
    sender = "controller" if explicit else "fixture-sender"
    assert (key == "original-key") if explicit else (len(key) == 32)
    assert (host, submitted) == ("host-b", {
        "session_id": "native-a", "sender": sender, "invocation_key": key, "prompt": "work",
    })
    output = capsys.readouterr()
    if as_json:
        value = json.loads(output.out)
        assert value["outcome"] == "accepted" and value["accepted"] is True
        assert value["delivered"] is None and value["duplicate"] is None
        assert value["message_id"] == 41 and value["idempotency_key"] == key
        assert value["bridge"] == {
            "host": "host-b", "session_id": "native-a", "managed_session_id": None,
            "registered_session_id": "native-a", "sender": sender,
        }
    else:
        assert all(item in output.out for item in ("host-b/native-a", sender, key, "unconfirmed"))
        assert "do not resend" in output.out


@pytest.mark.parametrize("as_json", [False, True])
def test_registered_unknown_retains_original_sender_key_and_inbox(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], as_json: bool,
) -> None:
    calls = wire(monkeypatch, represented=True, admission=BridgeClientError(502, {
        "code": "exact_live_prompt_admission_unknown",
    }))
    with pytest.raises(SystemExit) as error:
        cli.main([
            *(["--json"] if as_json else []),
            "send", "workspace-a", "work", "--bridge", "--host", "host-b",
        ])
    assert error.value.code == 70
    assert [name for name, _value in calls] == ["resolve", "submit"]
    output = capsys.readouterr()
    key = calls[1][1][1]["invocation_key"]
    if as_json:
        value = json.loads(output.out)
        assert value["outcome"] == "unknown" and value["retryable"] is False
        assert value["bridge"]["sender"] == "fixture-sender"
        assert value["bridge"]["registered_session_id"] == "native-a"
        assert value["idempotency_key"] == key
    else:
        assert not output.out
        assert all(item in output.err for item in ("host-b", "native-a", "fixture-sender", key))


def test_registered_reply_address_refuses_before_prompt_submission(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    calls = wire(monkeypatch, represented=True)
    with pytest.raises(SystemExit) as error:
        cli.main([
            "--json", "send", "workspace-a", "work", "--bridge", "--host", "host-b",
            "--reply-to", "controller",
        ])
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "bridge_send_options"
    assert [name for name, _value in calls] == ["resolve"]
