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
    invocation = core.build_parser().parse_args(["claim-status", "legacy-task"])
    assert core._cmd_claim_status(invocation) == 1
    assert "original authority" in capsys.readouterr().err
