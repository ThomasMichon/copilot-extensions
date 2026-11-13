"""The named send form uses the same queue and receipt as positional callers."""

from __future__ import annotations

import json

import pytest

from agent_bridge import __main__ as cli
from agent_bridge.ordinary_send import normalize_arguments, remote_target, resolve_existing_session
from agent_bridge.send_outcome import SendRefused


def arguments(*words):
    return cli.build_parser().parse_args(["--json", "send", *words])


@pytest.mark.parametrize("words", [
    ["worker", "hello"],
    ["--agent", "worker", "--message", "hello"],
    ["worker", "--message", "hello"],
])
def test_named_and_positional_forms_select_the_same_message(monkeypatch, capsys, words):
    sent = []

    class Client:
        def resolve_live_session(self, target):
            assert target == "worker"
            return {"session_id": "native-worker"}

        def send_live_message(self, target, **body):
            sent.append((target, body))
            return {"session_id": target, "message_id": 17,
                    "receipt": {"stage": "admitted"}, "duplicate": False}

    monkeypatch.setattr(cli, "_get_client", Client)
    monkeypatch.setattr(cli, "_live_sender_label", lambda _: "sender")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _: None)
    cli._cmd_send(arguments(*words))
    result = json.loads(capsys.readouterr().out)
    assert result["message_id"] == 17 and result["outcome"] == "admitted"
    assert len(sent) == 1
    assert sent[0][0] == "native-worker" and sent[0][1]["body"] == "hello"


@pytest.mark.parametrize("words,reason", [
    (["--message", "hello"], "missing_target"),
    (["worker", "--agent", "other", "--message", "hello"], "conflicting_target"),
    (["worker", "hello", "--message", "other"], "conflicting_message"),
    (["--agent", "worker", "--prompt-file", "-", "--message", "hello"], "conflicting_message"),
])
def test_conflicting_inputs_refuse_before_connecting(monkeypatch, words, reason):
    monkeypatch.setattr(cli, "_get_client", lambda: pytest.fail("must not connect"))
    with pytest.raises(SendRefused) as refused:
        normalize_arguments(arguments(*words))
    assert refused.value.reason == reason


def test_machine_target_never_resolves_or_creates_locally(monkeypatch, capsys):
    sent = []

    class Client:
        def resolve_live_session(self, _target):
            pytest.fail("remote target must not resolve against the caller's registry")

        def send_remote_live_message(self, machine, target, **body):
            sent.append((machine, target, body))
            return {"session_id": "native-worker", "message_id": 19,
                    "receipt": {"stage": "admitted"}, "duplicate": False}

    monkeypatch.setattr(cli, "_get_client", Client)
    monkeypatch.setattr(cli, "_live_sender_label", lambda _: "sender")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _: None)
    cli._cmd_send(arguments("--machine", "host-b", "--agent", "worker",
                            "--message", "hello"))
    result = json.loads(capsys.readouterr().out)
    assert result["message_id"] == 19 and result["target"] == "native-worker"
    assert len(sent) == 1
    assert sent[0][:2] == ("host-b", "worker")
    assert sent[0][2]["message"] == "hello" and sent[0][2]["qualified_peer"] is False


def test_conflicting_machine_never_connects(monkeypatch):
    monkeypatch.setattr(cli, "_get_client", lambda: pytest.fail("must not connect"))
    args = arguments("--machine", "host-b", "--agent", "live:host-c:repo:worker",
                     "--message", "hello")
    normalize_arguments(args)
    with pytest.raises(SendRefused, match="conflicts"):
        remote_target(args)


@pytest.mark.parametrize("status", ["stopped", "starting", "created", "failed"])
def test_raw_send_never_resumes_or_creates_a_dormant_target(status):
    class Client:
        def get_session(self, target):
            return {"session_id": target, "status": status}

        def resume_session(self, *_a, **_kw):
            pytest.fail("raw send must not resume")

        def create_session(self, *_a, **_kw):
            pytest.fail("raw send must not create")

    with pytest.raises(SendRefused, match="explicitly resume"):
        resolve_existing_session(Client(), "worker")


def test_raw_send_refuses_ambiguous_live_agents():
    class Client:
        def get_session(self, _target):
            return {}

        def list_sessions(self):
            return [
                {"session_id": sid, "agent_name": "worker", "status": "idle"}
                for sid in ("one", "two")
            ]

    with pytest.raises(SendRefused) as refused:
        resolve_existing_session(Client(), "worker")
    assert refused.value.reason == "ambiguous_target"


def test_project_routed_machine_send_keeps_the_same_project_on_the_receiver(monkeypatch, capsys):
    sent = []

    class Client:
        def send_remote_live_message(self, machine, target, **body):
            sent.append((machine, target, body))
            return {"session_id": "receiver", "message_id": 17,
                    "receipt": {"stage": "admitted"}, "duplicate": False}

    monkeypatch.setattr(cli, "_get_client", Client)
    monkeypatch.setattr(cli, "_live_sender_label", lambda _a: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _a: None)
    args = cli.build_parser().parse_args([
        "--project", "example-repo", "--json", "send",
        "--machine", "peer-host", "--agent", "worker", "--message", "hello",
    ])
    cli._cmd_send(args)
    result = json.loads(capsys.readouterr().out)
    assert sent[0][2]["project"] == "example-repo"
    assert result["machine"] == "peer-host" and result["project"] == "example-repo"
    assert result["message_id"] == 17
