"""Direct producer command routing, output, and safety-flag contracts."""

from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from agent_dispatch import __main__ as cli
from agent_dispatch import producers_cli
from agent_dispatch.client import DispatchError
from agent_dispatch.producers import emitter, schedule, webhook


def _args(*argv):
    return cli.build_parser().parse_args(list(argv))


@pytest.fixture
def client(monkeypatch):
    fake = Mock()
    monkeypatch.setattr(producers_cli, "_client", lambda args: nullcontext(fake))
    return fake


@pytest.mark.parametrize(
    "name",
    [
        "_cmd_schedule",
        "_cmd_emitter",
        "_cmd_webhook",
        "_cmd_reservations",
        "_parse_label_max_attempts",
    ],
)
def test_command_entry_points_are_directly_importable(name):
    assert callable(getattr(producers_cli, name))


@pytest.mark.parametrize(
    ("name", "args", "kwargs"),
    [
        ("_client", (argparse.Namespace(),), {}),
        ("_emit", ({"ok": True},), {}),
        ("_enrich", ({"id": "task"},), {"result": True}),
        ("_resolve_client_target", (argparse.Namespace(),), {}),
    ],
)
def test_proxy_resolves_current_main_attribute(monkeypatch, name, args, kwargs):
    proxy = getattr(producers_cli, name)
    for value in (object(), object()):
        target = Mock(return_value=value)
        monkeypatch.setattr(cli, name, target)
        assert proxy(*args, **kwargs) is value
        target.assert_called_once_with(*args, **kwargs)


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        (None, {}),
        (["review=2", "build=0"], {"review": 2, "build": 0}),
        ([" review = -1", "build=3", "review=4"], {"review": 4, "build": 3}),
    ],
)
def test_label_max_attempts(items, expected):
    assert producers_cli._parse_label_max_attempts(items) == expected


@pytest.mark.parametrize(
    ("items", "message"),
    [
        (["review"], "expects LABEL=N"),
        (["=2"], "expects LABEL=N"),
        (["review=abc"], "must be an integer"),
    ],
)
def test_label_max_attempts_rejects_invalid_policy(items, message):
    with pytest.raises(SystemExit, match=message):
        producers_cli._parse_label_max_attempts(items)


@pytest.mark.parametrize(
    ("argv", "method", "positional", "keywords", "result"),
    [
        (
            ["reservations", "list", "--task", "task", "--state", "failed", "--limit", "7"],
            "list_reservations",
            (),
            {"task_id": "task", "state": "failed", "limit": 7},
            [{"key": "reservation"}],
        ),
        (
            ["reservations", "defer", "reservation", "--detail", "deferred"],
            "defer_spawn",
            ("reservation",),
            {"detail": "deferred"},
            {"state": "deferred"},
        ),
        (
            ["reservations", "settle", "reservation"],
            "settle_spawn",
            ("reservation",),
            {"detail": None},
            {"state": "settled"},
        ),
        (
            [
                "reservations",
                "rearm",
                "task",
                "--permit",
                "--reason",
                "repaired",
                "--min-failures",
                "1",
            ],
            "rearm_spawn",
            ("task",),
            {"permitted": True, "reason": "repaired", "min_failures": 1},
            {"state": "rearmed"},
        ),
    ],
)
def test_reservations_route_and_emit(client, capsys, argv, method, positional, keywords, result):
    target = getattr(client, method)
    target.return_value = result
    assert producers_cli._cmd_reservations(_args(*argv)) == 0
    target.assert_called_once_with(*positional, **keywords)
    assert client.method_calls == [(method, positional, keywords)]
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize(
    ("flags", "force", "confirmed_absent"),
    [
        ([], False, False),
        (["--force"], True, False),
        (["--confirmed-absent"], False, True),
        (["--force", "--confirmed-absent"], True, True),
    ],
)
def test_reservations_fail_preserves_independent_safety_flags(
    client,
    capsys,
    flags,
    force,
    confirmed_absent,
):
    client.fail_spawn.return_value = {"state": "failed"}
    args = _args("reservations", "fail", "reservation", "--detail", "failed", *flags)
    assert producers_cli._cmd_reservations(args) == 0
    client.fail_spawn.assert_called_once_with(
        "reservation",
        detail="failed",
        force=force,
        confirmed_absent=confirmed_absent,
    )
    assert json.loads(capsys.readouterr().out) == {"state": "failed"}


def test_unknown_reservation_command_has_no_client_effects(client):
    assert producers_cli._cmd_reservations(argparse.Namespace(reservations_command="bogus")) == 2
    assert client.method_calls == []


@pytest.mark.parametrize(
    ("argv", "method", "positional", "keywords", "result"),
    [
        (["schedule", "list"], "list_schedules", (), {"include_paused": True}, [{"id": "job"}]),
        (["schedule", "list", "--active"], "list_schedules", (), {"include_paused": False}, []),
        (["schedule", "remove", "job"], "remove_schedule", ("job",), {}, {"removed": True}),
        (["schedule", "pause", "job"], "set_schedule_paused", ("job", True), {}, {"paused": True}),
        (
            ["schedule", "resume", "job"],
            "set_schedule_paused",
            ("job", False),
            {},
            {"paused": False},
        ),
        (["schedule", "lease-list"], "list_schedule_leases", (), {}, []),
        (
            ["schedule", "lease-show", "scope"],
            "get_schedule_lease",
            ("scope",),
            {},
            {"scope": "scope"},
        ),
        (
            [
                "schedule",
                "lease-acquire",
                "scope",
                "--holder",
                "worker",
                "--holder-session",
                "session",
                "--ttl",
                "60",
            ],
            "acquire_schedule_lease",
            ("scope", "worker"),
            {"holder_session": "session", "ttl": 60},
            {"holder": "worker"},
        ),
        (
            ["schedule", "lease-release", "scope", "--holder", "worker", "--force"],
            "release_schedule_lease",
            ("scope", "worker"),
            {"force": True},
            {"released": True},
        ),
    ],
)
def test_schedule_routes_and_emits(client, capsys, argv, method, positional, keywords, result):
    target = getattr(client, method)
    target.return_value = result
    assert producers_cli._cmd_schedule(_args(*argv)) == 0
    target.assert_called_once_with(*positional, **keywords)
    assert client.method_calls == [(method, positional, keywords)]
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["schedule", "tick"], "pass a SPEC path or --registry"),
        (["schedule", "serve"], "pass a SPEC path or --registry"),
        (["schedule", "serve", "--registry"], "--lease-scope and --holder are required"),
        (
            ["schedule", "serve", "--registry", "--lease-scope", "scope"],
            "--lease-scope and --holder are required",
        ),
        (
            ["schedule", "serve", "--registry", "--holder", "worker"],
            "--lease-scope and --holder are required",
        ),
    ],
)
def test_schedule_requires_spec_or_complete_registry_lease(client, monkeypatch, argv, message):
    serve = Mock()
    monkeypatch.setattr(schedule, "serve", serve)
    monkeypatch.setattr(schedule, "serve_registry", serve)
    with pytest.raises(SystemExit, match=message):
        producers_cli._cmd_schedule(_args(*argv))
    assert client.method_calls == []
    serve.assert_not_called()


def test_unknown_schedule_command_has_no_client_effects(client):
    with pytest.raises(SystemExit, match="unknown schedule command"):
        producers_cli._cmd_schedule(argparse.Namespace(schedule_command="bogus"))
    assert client.method_calls == []


@pytest.mark.parametrize("configured", [False, True])
def test_webhook_config_overrides_and_client_authority(client, monkeypatch, configured):
    config = {"existing": True, "url": "https://old.example", "coordinator_token": "old"}
    load = Mock(return_value=config.copy())
    serve = Mock()
    monkeypatch.setattr(webhook, "load_config", load)
    monkeypatch.setattr(webhook, "serve", serve)
    argv = (
        ["--url", "https://coordinator.example", "--token", "synthetic-token"]
        if configured
        else []
    )
    args = _args(*argv, "webhook", *(["--config", "webhook.json"] if configured else []))
    assert producers_cli._cmd_webhook(args) == 0
    expected = {**config, "url": args.url, "coordinator_token": args.token} if configured else {}
    serve.assert_called_once()
    assert serve.call_args.args == (expected,)
    assert serve.call_args.kwargs["host"] == args.host
    assert serve.call_args.kwargs["port"] == args.port
    if configured:
        with serve.call_args.kwargs["client_factory"]() as actual:
            assert actual is client
        load.assert_called_once_with("webhook.json")
    else:
        assert "client_factory" not in serve.call_args.kwargs
        load.assert_not_called()


def test_emitter_side_load_routes_scope_and_emits(client, monkeypatch, capsys):
    registration = {"kind": "emitter"}
    client.get_registration.return_value = registration
    result = {"created": [{"id": "task"}]}
    run = Mock(return_value=result)
    monkeypatch.setattr(emitter, "run_side_load", run)
    monkeypatch.setattr(producers_cli, "_registration_scope", lambda args: ("worker", "test"))
    assert producers_cli._cmd_emitter(_args("emitter", "side-load", "registration", "change")) == 0
    client.get_registration.assert_called_once_with("registration")
    run.assert_called_once_with(
        client,
        registration,
        "change",
        current_machine="worker",
        current_env="test",
    )
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize(
    "error", [emitter.EmitterError("cannot load"), DispatchError(409, "cannot load")]
)
def test_emitter_side_load_reports_error(client, monkeypatch, capsys, error):
    monkeypatch.setattr(producers_cli, "_registration_scope", lambda args: (None, None))
    monkeypatch.setattr(emitter, "run_side_load", Mock(side_effect=error))
    assert producers_cli._cmd_emitter(_args("emitter", "side-load", "registration", "change")) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == f"agent-dispatch emitter side-load: {error}"


def test_emitter_tick_loads_spec_routes_holder_and_emits(client, monkeypatch, capsys):
    spec = {"id": "emitter"}
    load = Mock(return_value=spec)
    result = {"created": [], "errors": []}
    run = Mock(return_value=result)
    monkeypatch.setattr(emitter, "load_spec", load)
    monkeypatch.setattr(emitter, "run_tick", run)
    assert (
        producers_cli._cmd_emitter(_args("emitter", "tick", "spec.json", "--holder", "worker"))
        == 0
    )
    load.assert_called_once_with("spec.json")
    run.assert_called_once_with(client, spec, holder="worker")
    assert json.loads(capsys.readouterr().out) == result


def test_unknown_emitter_command_refused(monkeypatch):
    monkeypatch.setattr(emitter, "load_spec", Mock(return_value={}))
    with pytest.raises(SystemExit, match="unknown emitter command"):
        producers_cli._cmd_emitter(argparse.Namespace(emitter_command="bogus", spec="spec.json"))
