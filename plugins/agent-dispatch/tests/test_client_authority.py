"""Explicit authority is never selected by availability or local task contents."""

from __future__ import annotations

import pytest

from agent_dispatch import __main__ as core, config
from agent_dispatch.client import DispatchError
from agent_dispatch.client_authority import client_authority, default_authority
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue


@pytest.fixture(autouse=True)
def isolated_authority(monkeypatch):
    for name in (
        "TASK_AUTHORITY", "URL", "SHARED_URL", "SHARED_TOKEN", "SHARED_TOKEN_COMMAND",
        "TOKEN", "CONTROL_TOKEN", "CONTROL_TOKEN_COMMAND", "SHARED_CONTROL_TOKEN",
        "SHARED_CONTROL_TOKEN_COMMAND", "FEDERATION_ROLE", "NO_AUTOSTART",
        "TASK_ID", "TASK_URL", "TASK_TOKEN", "TASK_TOKEN_FILE", "TASK_WORKER_ID",
    ):
        monkeypatch.delenv("AGENT_DISPATCH_" + name, raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: False)
    monkeypatch.setattr(config, "_routing_url", lambda: "http://127.0.0.1:19847")


def args(*argv):
    return core.build_parser().parse_args([*argv, "list"])


def forbid(*_args, **_kwargs):
    pytest.fail("authority selection attempted a local probe, fallback or autostart")


def test_unconfigured_authority_preserves_legacy_routing():
    assert default_authority() == "legacy"
    assert client_authority(args()) == "legacy"


def test_central_default_never_probes_starts_or_fails_over_to_local(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://central:8787")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN", "central-reader")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_CONTROL_TOKEN", "central-control")
    monkeypatch.setattr(core, "has_live_local_coordinator", forbid)
    monkeypatch.setattr(core, "_lazy_start_coordinator", forbid)
    monkeypatch.setattr(core, "failover_machine", forbid)
    monkeypatch.setattr(core, "client_token", forbid)
    monkeypatch.setattr(core, "client_control_token", forbid)
    with core._client(args()) as client:
        assert client.base_url == "http://central:8787"
        assert client._http.headers["Authorization"].endswith("central-reader")
        assert client._control_token == "central-control"


def test_central_default_requires_its_own_authentication(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://central:8787")
    monkeypatch.setenv("AGENT_DISPATCH_TOKEN", "local-only-reader")
    monkeypatch.setattr(core, "_lazy_start_coordinator", forbid)
    monkeypatch.setattr(core, "failover_machine", forbid)
    with pytest.raises(DispatchError, match="requires a shared coordinator bearer"):
        core._client(args())


def test_central_default_without_endpoint_refuses_before_any_local_side_effect(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setattr(core, "_lazy_start_coordinator", forbid)
    monkeypatch.setattr(core, "failover_machine", forbid)
    with pytest.raises(SystemExit):
        core._client(args())


@pytest.mark.parametrize("selection", ["flag", "environment"])
def test_local_authority_ignores_remote_override_and_all_failover(monkeypatch, selection):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central" if selection == "flag" else "local")
    monkeypatch.setenv("AGENT_DISPATCH_URL", "http://remote-override:8787")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://central:8787")
    monkeypatch.setenv("AGENT_DISPATCH_TOKEN", "local-reader")
    monkeypatch.setenv("AGENT_DISPATCH_CONTROL_TOKEN", "local-control")
    monkeypatch.setattr(core, "has_live_local_coordinator", forbid)
    monkeypatch.setattr(core, "failover_machine", forbid)
    monkeypatch.setattr(core, "shared_token", forbid)
    monkeypatch.setattr(core, "shared_control_token", forbid)
    with core._client(args("--local") if selection == "flag" else args(), ensure=False) as client:
        assert client.base_url == "http://127.0.0.1:19847"
        assert client._http.headers["Authorization"].endswith("local-reader")
        assert client._control_token == "local-control"


@pytest.mark.parametrize("flags", [("--shared",), ("--url", "http://explicit:8787")])
def test_ambiguous_local_target_is_rejected_before_start(monkeypatch, flags):
    monkeypatch.setattr(core, "_lazy_start_coordinator", forbid)
    with pytest.raises(DispatchError, match="--local cannot be combined"):
        core._client(args("--local", *flags))


def test_bad_authority_fails_loud_before_start(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "centrla")
    monkeypatch.setattr(core, "_lazy_start_coordinator", forbid)
    with pytest.raises(DispatchError, match="must be local or central"):
        core._client(args())


def test_worker_and_producer_routes_retain_explicit_authority(monkeypatch):
    from agent_dispatch.producers_cli import _reexec_argv

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    assert core._spawn_route(args()) == " --shared"
    assert core._spawn_route(args("--local")) == " --local"
    assert _reexec_argv(args())[1:] == ["-m", "agent_dispatch", "--shared"]
    assert _reexec_argv(args("--local"))[1:] == ["-m", "agent_dispatch", "--local"]
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "local")
    assert _reexec_argv(args())[1:] == ["-m", "agent_dispatch", "--local"]


@pytest.mark.parametrize("flags,target", [([], "--shared"), (["--local"], "--local")])
def test_mcp_cli_preserves_target_and_separate_credentials(monkeypatch, flags, target):
    from agent_dispatch import mcp_server

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://central:8787")
    monkeypatch.setenv("AGENT_DISPATCH_URL", "http://foreign:8787")
    for name, value in {
        "TOKEN": "local-reader", "CONTROL_TOKEN": "local-control",
        "SHARED_TOKEN": "central-reader", "SHARED_CONTROL_TOKEN": "central-control",
    }.items():
        monkeypatch.setenv("AGENT_DISPATCH_" + name, value)
    monkeypatch.setenv("AGENT_DISPATCH_NO_AUTOSTART", "1")
    observed = []

    def serve(tools):
        with tools._client_factory() as client:
            observed.append((client.base_url, client._control_token,
                             client._http.headers["Authorization"]))

    monkeypatch.setattr(mcp_server, "serve_stdio", serve)
    with mcp_server._default_client() as default_client:
        assert default_client.base_url == "http://central:8787"
        assert default_client._control_token == "central-control"
    invocation = core.build_parser().parse_args([*flags, "mcp"])
    assert invocation.func(invocation) == 0
    expected = ("http://127.0.0.1:19847", "local-control", "local-reader") if target == "--local" else (
        "http://central:8787", "central-control", "central-reader"
    )
    assert observed[0][:2] == expected[:2]
    assert observed[0][2].endswith(expected[2])


def test_attention_keeps_local_override_in_action_and_page_prefix(monkeypatch):
    from types import SimpleNamespace
    from agent_dispatch.attention_cli import _effective_cli, _target_cli

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    invocation = args("--local")
    assert _target_cli(invocation) == ("agent-dispatch", "--local")
    assert _effective_cli(invocation, SimpleNamespace(base_url="http://local:8787")) == (
        "agent-dispatch", "--local"
    )


def test_ensure_supervisor_and_registration_children_keep_local_override(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from agent_dispatch import procutil, remote_dispatch, single_instance, supervise_cli
    from agent_dispatch.supervisor_daemon import ReconcileSummary, SupervisorDaemon

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _machine: False)
    monkeypatch.setattr(single_instance, "is_locked", lambda _path: False)
    monkeypatch.setattr(procutil, "resolve_own_runtime_python", lambda: "owned-python")
    spawned = []
    monkeypatch.setattr(procutil, "spawn_dispatch_daemon", lambda argv, **_kw: spawned.append(argv))
    invocation = core.build_parser().parse_args(["--local", "supervise", "register"])
    assert supervise_cli._ensure_supervisor_daemon(invocation, "host", "default")["ensured"]
    assert spawned == [["owned-python", "-m", "agent_dispatch", "--local",
                        "supervise", "serve", "--machine", "host", "--env", "default"]]

    commands = []
    launcher = SimpleNamespace(launch=lambda _reg, cmd: commands.append(cmd) or object())
    daemon = SupervisorDaemon(object(), "host", launcher=launcher, target_args=("--local",))
    monkeypatch.setattr(daemon, "_own_python", lambda: "owned-python")
    monkeypatch.setattr(daemon, "_materializer", lambda _reg: lambda *_a: str(tmp_path / "spec.json"))
    for kind, spec in (
        ("supervised-lane", {"repo": "example.com/acme/repo"}),
        ("schedule", {"schedules": []}),
        ("emitter", {"command": ["owned-command"]}),
    ):
        daemon._start({"id": kind, "kind": kind, "spec": spec}, ReconcileSummary(), bucket="started")
    assert len(commands) == 3
    assert all(cmd[:4] == ["owned-python", "-m", "agent_dispatch", "--local"] for cmd in commands)


def test_supervisor_serve_forwards_selected_authority_to_daemon(monkeypatch, tmp_path):
    import os
    from agent_dispatch import config, logging_setup, procutil, supervise_cli, supervisor_daemon
    from agent_dispatch.supervisor_daemon import supervisor_lease_scope
    from types import SimpleNamespace

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE", "0")
    monkeypatch.setenv("AGENT_DISPATCH_NO_AUTOSTART", "1")
    monkeypatch.setattr(procutil, "relocate_off_payload", lambda: None)
    monkeypatch.setattr(logging_setup, "configure_file_logging", lambda _name: None)
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    captured = []

    def daemon(*_args, **kwargs):
        captured.append(kwargs)

        def serve(**callbacks):
            callbacks["on_cycle_start"](1, 10.0)
            return 0

        return SimpleNamespace(serve=serve)

    monkeypatch.setattr(supervisor_daemon, "SupervisorDaemon", daemon)
    invocation = core.build_parser().parse_args([
        "--local", "supervise", "serve", "--machine", "host", "--no-declared", "--once",
    ])
    assert supervise_cli._cmd_supervise_serve(invocation) == 0
    assert captured[0]["target_args"] == ("--local",)
    status, error = supervise_cli._read_supervisor_runtime_status(supervisor_lease_scope("host", "default"))
    assert error is None and status["owner"] == {
        "pid": os.getpid(), "authority_target": supervise_cli._supervisor_authority_target(invocation),
    }
    with captured[0]["client_factory"]() as client:
        assert client.base_url == "http://127.0.0.1:19847"


@pytest.mark.parametrize("holder_flags,request_flags,conflict", [
    (["--shared"], ["--local"], True),
    (["--local"], ["--shared"], True),
    (["--local"], ["--local"], False),
    (["--shared"], ["--shared"], False),
    (["--url", "http://first:8787"], ["--url", "http://second:8787"], True),
])
def test_ensure_rejects_opposite_authority_under_the_real_singleton_lock(
    monkeypatch, tmp_path, capsys, holder_flags, request_flags, conflict,
):
    import os
    from types import SimpleNamespace
    from agent_dispatch import config, remote_dispatch, single_instance, supervise_cli
    from agent_dispatch.supervisor_daemon import supervisor_lease_scope

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _machine: False)
    monkeypatch.setattr(supervise_cli, "_spawn_supervisor_daemon_detached", forbid)
    parser = core.build_parser()
    holder = parser.parse_args([*holder_flags, "supervise", "serve"])
    request = parser.parse_args([*request_flags, "supervise", "register"])
    scope = supervisor_lease_scope("host", "default")
    lock_path = single_instance.lock_path_for(tmp_path, scope)
    with single_instance.SingleInstance(lock_path) as acquired:
        assert acquired
        supervise_cli._write_supervisor_cycle_start(
            scope, 1, 10.0, authority_target=supervise_cli._supervisor_authority_target(holder),
        )
        supervise_cli._write_supervisor_runtime_status(
            scope, SimpleNamespace(running=[], backing_off=[], dead=[]),
        )
        result = supervise_cli._ensure_supervisor_daemon(request, "host", "default")
        assert single_instance.read_holder_pid(lock_path) == os.getpid()
        assert single_instance.is_locked(lock_path)
    assert result["ensured"] is False
    assert result["reason"] == ("authority conflict" if conflict else "already running")
    assert ("registration is saved" in capsys.readouterr().err) is conflict


@pytest.mark.parametrize("receipt", ["absent", "old-pid", "no-target", "malformed"])
def test_explicit_ensure_refuses_unknown_lock_authority(monkeypatch, tmp_path, capsys, receipt):
    import json
    import os
    from agent_dispatch import config, remote_dispatch, single_instance, supervise_cli
    from agent_dispatch.supervisor_daemon import supervisor_lease_scope

    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _machine: False)
    monkeypatch.setattr(supervise_cli, "_spawn_supervisor_daemon_detached", forbid)
    invocation = core.build_parser().parse_args(["--local", "supervise", "register"])
    scope = supervisor_lease_scope("host", "default")
    lock_path = single_instance.lock_path_for(tmp_path, scope)
    with single_instance.SingleInstance(lock_path) as acquired:
        assert acquired
        if receipt != "absent":
            payload = {"owner": {"pid": os.getpid(), "authority_target": "0" * 64}}
            if receipt == "old-pid":
                payload["owner"]["pid"] += 1
            elif receipt == "no-target":
                payload["owner"].pop("authority_target")
            supervise_cli._supervisor_runtime_status_path(scope).write_text(
                "{" if receipt == "malformed" else json.dumps(payload), encoding="utf-8",
            )
        result = supervise_cli._ensure_supervisor_daemon(invocation, "host", "default")
    assert result["ensured"] is False and result["reason"] == "authority unavailable"
    assert "selected authority is not ensured" in capsys.readouterr().err


def test_supervisor_target_receipt_excludes_credentials():
    from agent_dispatch import supervise_cli

    parser = core.build_parser()
    plain = parser.parse_args(["--local", "supervise", "serve"])
    secrets = parser.parse_args([
        "--local", "--token", "reader-secret", "--control-token", "control-secret",
        "supervise", "serve",
    ])
    target = supervise_cli._supervisor_authority_target(secrets)
    assert target == supervise_cli._supervisor_authority_target(plain)
    assert len(target) == 64 and "reader-secret" not in target and "control-secret" not in target


@pytest.mark.parametrize("flags,first,second,reason", [
    (["--shared"], "http://writer-a:8787", "http://writer-b:8787", "authority conflict"),
    (["--shared"], "http://writer-a:8787/", "http://writer-a:8787", "already running"),
    (["--local"], "http://writer-a:8787", "http://writer-b:8787", "already running"),
])
def test_real_supervisor_lock_distinguishes_shared_writers_but_not_local_rendezvous(
    monkeypatch, tmp_path, flags, first, second, reason,
):
    from agent_dispatch import config, remote_dispatch, single_instance, supervise_cli
    from agent_dispatch.supervisor_daemon import supervisor_lease_scope

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _machine: False)
    monkeypatch.setattr(supervise_cli, "_spawn_supervisor_daemon_detached", forbid)
    invocation = core.build_parser().parse_args([*flags, "supervise", "register"])
    scope = supervisor_lease_scope("host", "default")
    with single_instance.SingleInstance(single_instance.lock_path_for(tmp_path, scope)) as acquired:
        assert acquired
        monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", first)
        monkeypatch.setenv("AGENT_DISPATCH_PORT", "18787")
        supervise_cli._write_supervisor_cycle_start(
            scope, 1, 10.0, authority_target=supervise_cli._supervisor_authority_target(invocation),
        )
        monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", second)
        monkeypatch.setenv("AGENT_DISPATCH_PORT", "28787")
        result = supervise_cli._ensure_supervisor_daemon(invocation, "host", "default")
    assert result["ensured"] is False and result["reason"] == reason


@pytest.mark.parametrize("flags,authority", [
    ([], "central"), (["--local"], "local"), (["--shared"], "central"),
    (["--url", "http://pinned:8787"], "pinned_url"),
])
def test_inspect_task_keeps_selected_writer_instead_of_peer_queue(monkeypatch, capsys, flags, authority):
    import json
    from contextlib import nullcontext
    from types import SimpleNamespace
    from agent_dispatch import remote_dispatch
    from agent_dispatch.client_authority import client_authority

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", forbid)
    monkeypatch.setattr(remote_dispatch, "browse_remote", forbid)
    observed = []

    def client(invocation, **_kwargs):
        observed.append(client_authority(invocation))
        return nullcontext(SimpleNamespace(
            get=lambda task_id: {"id": task_id}, events=lambda _task: [],
            progress_log=lambda _task: [], attachments=lambda _task: [],
        ))

    monkeypatch.setattr(core, "_client", client)
    invocation = core.build_parser().parse_args([*flags, "inspect-task", "owned-task", "--machine", "peer"])
    assert invocation.func(invocation) == 0
    assert observed == [authority]
    assert json.loads(capsys.readouterr().out)["task"]["id"] == "owned-task"


@pytest.mark.parametrize("flags", [[], ["--local"], ["--shared"], ["--url", "http://pinned:8787"]])
def test_mcp_remote_side_load_refuses_ssh_default_without_forwarding_credentials(monkeypatch, flags):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from agent_dispatch import mcp_server, remote_dispatch
    from agent_dispatch.client_authority import client_authority

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _owner: True)
    monkeypatch.setattr(remote_dispatch, "browse_remote", forbid)
    observed = []

    def client(invocation, **_kwargs):
        observed.append(client_authority(invocation))
        return nullcontext(SimpleNamespace(
            get_registration=lambda _id: {"id": "same-id", "machine": "peer", "env": "default"},
        ))

    def serve(tools):
        with pytest.raises(DispatchError, match="remote emitter side-load cannot preserve"):
            tools.emitter_side_load("same-id", "example/change")

    monkeypatch.setattr(core, "_client", client)
    monkeypatch.setattr(mcp_server, "serve_stdio", serve)
    invocation = core.build_parser().parse_args([*flags, "mcp"])
    assert invocation.func(invocation) == 0
    assert observed == [client_authority(invocation)]


def test_default_mcp_side_load_checks_current_authority_at_the_operation(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from agent_dispatch import mcp_server, remote_dispatch

    monkeypatch.delenv("AGENT_DISPATCH_TASK_AUTHORITY", raising=False)
    tools = mcp_server.DispatchTools(client_factory=lambda: nullcontext(SimpleNamespace(
        get_registration=lambda _id: {"id": "same-id", "machine": "peer"},
    )))
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda _owner: True)
    monkeypatch.setattr(remote_dispatch, "browse_remote", forbid)
    with pytest.raises(DispatchError, match="cannot preserve the selected authority"):
        tools.emitter_side_load("same-id", "example/change")


@pytest.mark.parametrize("route,expected", [
    ("local", "http://foreign:8787"), ("strict-local", "http://127.0.0.1:19847"),
])
def test_script_route_distinguishes_legacy_from_strict_local(monkeypatch, route, expected):
    from agent_dispatch.script_worker import _client_for_route

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_URL", "http://foreign:8787")
    monkeypatch.setenv("AGENT_DISPATCH_TOKEN", "local-reader")
    with _client_for_route(route) as client:
        assert client.base_url == expected
        assert client._http.headers["Authorization"].endswith("local-reader")


@pytest.mark.parametrize("flags,selector,prefix", [
    (["--local"], "--local", ""), ([], "--shared", "SHARED_"),
])
def test_detached_waiter_keeps_selected_writer_without_secrets_in_argv(
    monkeypatch, flags, selector, prefix,
):
    from types import SimpleNamespace
    from agent_dispatch import execution_cli, hibernation, procutil

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_URL", "http://foreign:8787")
    invocation = core.build_parser().parse_args([
        *flags, "--token", "explicit-reader", "--control-token", "explicit-control",
        "run", "--resume", "owned-worktree", "--", "owned-wait",
    ])
    monkeypatch.setattr(execution_cli, "_DETACHED_CLI_ARGS", invocation)
    monkeypatch.setattr(procutil, "resolve_own_runtime_python", lambda: "owned-python")
    observed = []
    monkeypatch.setattr(
        execution_cli.subprocess, "Popen",
        lambda argv, **kwargs: observed.append((argv, kwargs)) or SimpleNamespace(pid=123),
    )
    spec = hibernation.RunSpec(command=("owned-wait",), resume_worktree="owned-worktree")
    execution_cli._spawn_detached_waiter(spec)
    argv, kwargs = observed[0]
    assert argv[:5] == ["owned-python", "-m", "agent_dispatch", selector, "run"]
    assert "explicit-reader" not in argv and "explicit-control" not in argv
    assert kwargs["env"]["AGENT_DISPATCH_" + prefix + "TOKEN"] == "explicit-reader"
    assert kwargs["env"]["AGENT_DISPATCH_" + prefix + "CONTROL_TOKEN"] == "explicit-control"


@pytest.mark.parametrize("authority,url,token", [
    ("central", "http://central:8787", "central-reader"),
    ("local", "http://127.0.0.1:19847", "local-reader"),
])
def test_picker_board_and_relay_follow_the_same_environment_authority(
    monkeypatch, authority, url, token,
):
    from agent_dispatch import board_cli, board_relay

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", authority)
    monkeypatch.setenv("AGENT_DISPATCH_URL", "http://foreign:8787")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://central:8787")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN", "central-reader")
    monkeypatch.setenv("AGENT_DISPATCH_TOKEN", "local-reader")
    assert board_cli._endpoint() == url
    assert board_cli._token() == token
    with board_relay._build_client(args()) as client:
        assert client.base_url == url
        assert client._http.headers["Authorization"].endswith(token)


def test_central_picker_board_keeps_its_stdlib_only_cold_import(monkeypatch):
    import subprocess
    import sys
    from agent_procutil import no_window_kwargs

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://central:8787")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN", "central-reader")
    result = subprocess.run([
        sys.executable, "-c",
        "import sys;from agent_dispatch import board_cli;"
        "assert board_cli._endpoint()=='http://central:8787';"
        "assert board_cli._token()=='central-reader';assert 'httpx' not in sys.modules",
    ], capture_output=True, text=True, timeout=10, **no_window_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("authority", ["central", "local"])
def test_same_cell_peer_filter_keeps_ambiguous_callback_fail_closed(monkeypatch, authority):
    from agent_dispatch.peer_launch import peer_environment

    context = {
        "pluginId": "agent-dispatch", "installReceipt": "receipt",
        "payloadRoot": "payload", "pluginRoot": "runtime",
    }
    filtered = peer_environment(context, {"AGENT_DISPATCH_TASK_AUTHORITY": authority})
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", filtered["AGENT_DISPATCH_TASK_AUTHORITY"])
    monkeypatch.setattr(core, "_client", forbid)
    invocation = core.build_parser().parse_args(["claim-status", "legacy-unqualified-task"])
    with pytest.raises(DispatchError, match="original authority"):
        core._cmd_claim_status(invocation)


@pytest.mark.parametrize("role", ["coordinator", "standby", "peeer", ""])
def test_central_mode_local_server_cannot_promote_its_independent_database(tmp_path, monkeypatch, role):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://central:8787")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", role)
    queue = TaskQueue(tmp_path / "local.db")
    with pytest.raises(DispatchError, match="must be a peer or satellite"):
        create_app(queue, enable_mcp=False)


@pytest.mark.parametrize("role", ["peer", "satellite"])
def test_central_mode_presence_only_local_server_is_admitted(tmp_path, monkeypatch, role):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://central:8787")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", role)
    queue = TaskQueue(tmp_path / "local.db")
    assert create_app(queue, enable_mcp=False) is not None


@pytest.mark.parametrize("authority", ["central", "local"])
@pytest.mark.parametrize("role", ["coordinator", "standby"])
def test_manual_federation_cannot_override_presence_only_authority(monkeypatch, role, authority):
    from agent_dispatch.federation_runner import FederationRunner

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", authority)
    with pytest.raises(DispatchError, match="must be a peer or satellite"):
        FederationRunner(object(), "local-peer", role=role)


@pytest.mark.parametrize("role", ["coordinator", "standby"])
def test_local_only_server_cannot_advertise_itself_as_a_fleet_writer(tmp_path, monkeypatch, role):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "local")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", role)
    with pytest.raises(DispatchError, match="must be a peer or satellite"):
        create_app(TaskQueue(tmp_path / "local.db"), enable_mcp=False)


def test_local_only_server_needs_no_central_service(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "local")
    assert create_app(TaskQueue(tmp_path / "local.db"), enable_mcp=False) is not None


@pytest.mark.parametrize("flags", [[], ["--shared"], ["--local"], ["--url", "http://explicit:8787"]])
def test_explicit_authority_cannot_create_in_a_different_machine_queue(monkeypatch, flags):
    from agent_dispatch import remote_dispatch

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setattr(core, "_scope_repo", lambda _args: "example.com/acme/repo")
    monkeypatch.setattr(remote_dispatch, "is_cross_machine", lambda _args: True)
    monkeypatch.setattr(core, "_dispatch_cross_machine", forbid)
    invocation = core.build_parser().parse_args([*flags, "create", "task", "--spawn"])
    with pytest.raises(DispatchError, match="cannot switch to another machine's queue"):
        core._cmd_create(invocation)


@pytest.mark.parametrize("command", ["list", "inbox"])
def test_explicit_authority_browse_does_not_select_a_peer_database(monkeypatch, command):
    from agent_dispatch import remote_dispatch, tracking

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setattr(core, "_scope_repo", lambda _args: "example.com/acme/repo")
    monkeypatch.setattr(remote_dispatch, "is_peer_machine", forbid)
    monkeypatch.setattr(core, "_browse_peer", forbid)
    monkeypatch.setattr(core, "_enrich", lambda value: value)
    monkeypatch.setattr(tracking, "enrich_tasks", lambda value: value)
    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def list(self, **kwargs):
            calls.append(kwargs)
            return []

    monkeypatch.setattr(core, "_client", lambda _args: Client())
    invocation = core.build_parser().parse_args([command, "--machine", "remote-worker"])
    assert invocation.func(invocation) == 0
    assert len(calls) == 1


def test_local_installer_start_and_stop_never_target_central(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://central:8787")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN", "central-reader")
    monkeypatch.setenv("AGENT_DISPATCH_TOKEN", "local-reader")
    monkeypatch.setenv("AGENT_DISPATCH_CONTROL_TOKEN", "local-control")
    monkeypatch.setattr(config, "has_live_local_coordinator", lambda: True)
    started = []
    stopped = []
    monkeypatch.setattr(core, "_lazy_start_coordinator", lambda: started.append(True))

    class Client:
        def __init__(self, url, **kwargs):
            stopped.append((url, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def shutdown(self):
            stopped.append("shutdown")

    monkeypatch.setattr(core, "DispatchClient", Client)
    assert core._cmd_ensure_coordinator(args()) == 0
    assert started == [True]
    assert core._cmd_stop_coordinator(args()) == 0
    assert stopped == [
        ("http://127.0.0.1:19847", {"token": "local-reader", "control_token": "local-control"}),
        "shutdown",
    ]


def test_task_only_worker_keeps_its_pin_and_rejects_local_override(monkeypatch):
    from agent_dispatch.task_worker_cli import task_worker

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
    for name, value in {
        "TASK_ID": "own-task", "TASK_TOKEN": "own-signed-token", "TASK_URL": "http://pinned:8787",
        "TASK_WORKER_ID": "own-worker",
    }.items():
        monkeypatch.setenv("AGENT_DISPATCH_" + name, value)
    assert task_worker(args()).url == "http://pinned:8787"
    with pytest.raises(DispatchError, match="cannot be combined"):
        task_worker(args("--local"))


@pytest.mark.parametrize("authority,flag", [("local", "--local"), ("central", "--shared")])
def test_task_references_and_registered_callbacks_preserve_explicit_authority(monkeypatch, authority, flag):
    import json
    from pathlib import Path

    from agent_dispatch.client_authority import qualify_task, require_explicit_claim_authority

    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", authority)
    task = qualify_task(args(), {"id": "task-1", "status": "queued"})
    assert task["claim_ref"] == f"dispatch-{authority}-task:task-1"
    assert task["task_authority"] == authority
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "claim-providers" / f"dispatch-{authority}-task.json").read_text())
    assert manifest["namespace"] == f"dispatch-{authority}-task"
    assert manifest["status_command"] == ["agent-dispatch", flag]
    assert manifest["no_self_provision_env"] == "AGENT_DISPATCH_NO_SELFPROVISION"
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central" if authority == "local" else "local")
    assert client_authority(args(flag)) == authority
    require_explicit_claim_authority(args(flag))


def test_legacy_reference_output_is_unchanged():
    from agent_dispatch.client_authority import qualify_task

    task = {"id": "task-1", "status": "queued"}
    assert qualify_task(args(), task) is task


@pytest.mark.parametrize("authority", ["central", "local"])
def test_unqualified_claim_callback_never_reports_a_different_writer_absent(monkeypatch, authority, capsys):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", authority)
    monkeypatch.setattr(core, "_client", forbid)
    assert core.main(["claim-status", "legacy-task"]) == 1
    error = capsys.readouterr().err
    assert "original authority" in error
    assert "coordinator error" not in error


@pytest.mark.parametrize("kind", ["container", "network"])
@pytest.mark.parametrize("state", ["absent", "present", "inventory-failed"])
def test_partial_docker_setup_cleanup_requires_confirmed_absence(monkeypatch, kind, state):
    from types import SimpleNamespace

    from tests import test_central_authority_docker as proof

    def docker(*argv):
        if "rm" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="resource was never created")
        return SimpleNamespace(
            returncode=1 if state == "inventory-failed" else 0,
            stdout='"owned-resource"\n' if state == "present" else "",
            stderr="inventory unavailable" if state == "inventory-failed" else "",
        )

    monkeypatch.setattr(proof, "_docker", docker)
    if state == "absent":
        proof._remove_owned(kind, "owned-resource")
    else:
        with pytest.raises(AssertionError, match="inventory failed|cleanup failed"):
            proof._remove_owned(kind, "owned-resource")
