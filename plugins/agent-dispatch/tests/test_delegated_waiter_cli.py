from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from agent_dispatch import delegated_waiter_cli as delegation, procutil
from agent_dispatch.delegated_waiter_contract import NOTIFICATIONS, PROTOCOL
from agent_dispatch.hibernation import RunSpec
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests._delegation_helpers import client_factory, identity, prepare, task

pytestmark = pytest.mark.contract("agent_dispatch.delegated_waiter.negotiation")


@pytest.fixture(autouse=True)
def stable_callback(monkeypatch):
    monkeypatch.setattr(delegation, "callback_prefix", lambda _: ["stable-host", "stable-entry"])


def args():
    return SimpleNamespace(pr_watch_repo="example/project", pr_watch_number=7,
                           pr_watch_timeout=300.0, pr_watch_until=None, shared=False,
                           token=None, control_token=None, url=None)

def test_failed_owner_spawn_is_explicit_unavailable(monkeypatch):
    monkeypatch.setattr(procutil, "run_background_capture", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="watch_owner_unavailable"):
        delegation.owner_request(["resolved-owner"], "health")


@pytest.fixture
def clean_context(monkeypatch):
    import os
    for key in tuple(os.environ):
        if key.startswith("AGENT_DISPATCH_") or key in {
            "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
            "GH_HOST", "GH_CONFIG_DIR",
            "AGENT_PULL_REQUESTS_HOME",
        }:
            monkeypatch.delenv(key)


@pytest.mark.parametrize("coordinator,owner", [
    (False, False), (False, True), (True, False), (True, True),
])
def test_capability_matrix(clean_context, monkeypatch, coordinator, owner):
    from contextlib import nullcontext
    client = SimpleNamespace(health=lambda: {"delegated_run_waiters": {
        "protocol": PROTOCOL if coordinator else "legacy", "coordinator_id": "queue",
    }})
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: ["attributable"])
    monkeypatch.setattr(delegation, "owner_request", lambda *_: {
        "pid": 3, "capabilities": [NOTIFICATIONS] if owner else [],
    })
    spec, prefix, reason = delegation.negotiate(args(), ("real-wait",), lambda _: nullcontext(client))
    assert bool(spec) == (coordinator and owner)
    assert (reason == "negotiated") == bool(spec)
    assert prefix is not None if spec else prefix is None


@pytest.mark.parametrize("capability", [None, [], "future"])
def test_malformed_coordinator_capability_falls_back(clean_context, monkeypatch, capability):
    from contextlib import nullcontext
    client = SimpleNamespace(health=lambda: {"delegated_run_waiters": capability})
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: ["attributable"])
    monkeypatch.setattr(delegation, "owner_request", lambda *_: pytest.fail("no owner probe"))
    assert delegation.negotiate(args(), ("real-wait",), lambda _: nullcontext(client)) == (
        None, None, "coordinator_protocol_unavailable",
    )


@pytest.mark.parametrize("health", [
    {"pid": 3, "capabilities": None},
    {"pid": 3, "capabilities": NOTIFICATIONS},
    {"pid": True, "capabilities": [NOTIFICATIONS]},
    {"pid": -1, "capabilities": [NOTIFICATIONS]},
])
def test_malformed_owner_health_falls_back(clean_context, monkeypatch, health):
    from contextlib import nullcontext
    client = SimpleNamespace(health=lambda: {"delegated_run_waiters": {
        "protocol": PROTOCOL, "coordinator_id": "queue",
    }})
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: ["attributable"])
    monkeypatch.setattr(delegation, "owner_request", lambda *_: health)
    assert delegation.negotiate(args(), ("real-wait",), lambda _: nullcontext(client)) == (
        None, None, "watch_protocol_unavailable",
    )


@pytest.mark.parametrize("option", [
    "token", "control_token", "url", "shared", "local",
    "task_token", "task_token_file", "task_scope_id", "task_url", "task_worker",
])
def test_explicit_context_falls_back_before_owner_probe(clean_context, monkeypatch, option):
    supplied = args()
    setattr(supplied, option, "sensitive-value")
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: pytest.fail("no probe"))
    assert delegation.negotiate(supplied, ("real-wait",), None)[2] == "caller_context_requires_process"


@pytest.mark.parametrize("name", [
    "AGENT_DISPATCH_TOKEN", "AGENT_DISPATCH_CONTROL_TOKEN",
    "AGENT_DISPATCH_SHARED_TOKEN_COMMAND", "AGENT_DISPATCH_CONTROL_TOKEN_COMMAND", "GH_TOKEN",
    "AGENT_DISPATCH_TASK_AUTHORITY", "AGENT_DISPATCH_TASK_URL",
    "AGENT_DISPATCH_TASK_ID", "AGENT_DISPATCH_TASK_WORKER_ID",
    "AGENT_DISPATCH_WSL_WINDOWS_CLIENT",
    "GH_HOST", "GH_CONFIG_DIR",
    "AGENT_PULL_REQUESTS_HOME",
])
def test_environment_credentials_select_process_without_copy(clean_context, monkeypatch, name):
    monkeypatch.setenv(name, "sensitive-value")
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: pytest.fail("no probe"))
    assert delegation.negotiate(args(), ("real-wait",), None) == (
        None, None, "caller_context_requires_process",
    )


def test_missing_stable_entry_falls_back_before_probing(clean_context, monkeypatch):
    def missing(_installation):
        raise ValueError("stable entry unavailable")
    monkeypatch.setattr(delegation, "callback_prefix", missing)
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: pytest.fail("no probe"))
    assert delegation.negotiate(args(), ("real-wait",), None) == (
        None, None, "callback_entry_unavailable",
    )


def test_missing_optional_owner_and_no_path_lookup(clean_context, monkeypatch, tmp_path):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(procutil, "resolve_runtime_python", lambda _: None)
    monkeypatch.setattr(procutil.shutil, "which", lambda _: pytest.fail("no PATH"))
    assert procutil.agent_pull_requests_launch_prefix() is None
    assert delegation.negotiate(args(), ("real-wait",), None)[2] == "optional_owner_absent"


@pytest.mark.parametrize("failure", [
    "before_mutation", "after_mutation", "missing_echo", "arm_lost_ack",
    "subscribe_timeout", "subscribe_unavailable",
])
def test_subscription_uncertainty_never_spawns_second_owner(tmp_path, monkeypatch, failure):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = identity(queue)
    prepared = prepare(queue, task_id, spec)
    registered = []

    def request(argv, **_):
        assert "--acknowledged-notifications" in argv and "--notify-timeout" in argv
        callback = argv[argv.index("--notify-argv") + 1:]
        assert len(callback) == 3
        assert json.loads(bytes.fromhex(callback[-1].removeprefix("hex:")))["generation"] == 1
        assert "sensitive-value" not in json.dumps(argv)
        if failure == "before_mutation":
            raise OSError("redacted")
        if failure == "subscribe_unavailable":
            return None
        if failure == "subscribe_timeout":
            import subprocess
            raise subprocess.TimeoutExpired("redacted", 30)
        registered.append(True)
        if failure == "after_mutation":
            raise httpx.ReadTimeout("redacted")
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "registered": True, "notification_protocol": NOTIFICATIONS,
            **({"registration_id": "registration"} if failure != "missing_echo" else {}),
        }))

    monkeypatch.setattr(procutil, "run_background_capture", request)
    monkeypatch.setattr(procutil, "resolve_own_runtime_python", lambda: "own-python")
    factory = client_factory(queue)
    if failure == "arm_lost_ack":
        from contextlib import contextmanager

        @contextmanager
        def lost_ack(*a, **kw):
            with factory(*a, **kw) as client:
                arm = client.arm_delegated_waiter

                def fail(*args, **kwargs):
                    arm(*args, **kwargs)
                    raise httpx.ReadTimeout("lost ACK")
                client.arm_delegated_waiter = fail
                yield client
        chosen_factory = lost_ack
    else:
        chosen_factory = factory
    result = delegation.activate(args(), RunSpec(command=("real-wait",), task_id=task_id),
                                 prepared, ["owner-python"], chosen_factory)
    assert result["detached"] == (failure == "arm_lost_ack")
    assert len(queue.list_run_waiter_wakes(task_id)) == (0 if result["detached"] else 1)
    assert not queue.list_pending_run_waiters()


@pytest.mark.parametrize("authority", ["local", "central"])
def test_authority_context_falls_back_before_owner_probe(clean_context, monkeypatch, authority):
    monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", authority)
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: pytest.fail("no probe"))
    assert delegation.negotiate(args(), ("real-wait",), None) == (
        None, None, "caller_context_requires_process",
    )


def test_real_fallback_command_preserves_cli_credentials(clean_context, tmp_path, monkeypatch, capsys):
    from agent_dispatch import __main__ as cli, hibernation_claims, remote_dispatch
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    monkeypatch.setattr(cli, "_client", client_factory(queue))
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host")
    monkeypatch.setattr(hibernation_claims, "add_hibernation_claim", lambda *_a, **_kw: {"added": True})
    spawned = []
    monkeypatch.setattr(cli, "_spawn_detached_waiter", lambda spec: spawned.append(spec) or {"pid": 11})
    supplied = cli.build_parser().parse_args([
        "--token", "synthetic-token", "--control-token", "synthetic-control",
        "run", "--detach", "--task", task_id, "--resume", "host/wt",
        "--pr-watch-repo", "example/project", "--pr-watch-number", "7",
        "--", "actual-wait", "actual-argument",
    ])
    assert cli._cmd_run(supplied) == 0
    assert spawned[0].command == ("actual-wait", "actual-argument")
    output = json.loads(capsys.readouterr().out)
    assert output["delegation_fallback"] == "caller_context_requires_process"
    assert output["command"] == ["actual-wait", "actual-argument"]
    assert "synthetic-token" not in json.dumps(output)
    assert queue.list_pending_run_waiters()[0]["kind"] == "process"


@pytest.mark.parametrize("authority", ["legacy", "local", "central"])
def test_spawn_fallback_secrets_only_in_environment(clean_context, monkeypatch, authority):
    from agent_dispatch import execution_cli
    captured = {}
    supplied = args()
    supplied.token = "synthetic-token"
    supplied.control_token = "synthetic-control"
    if authority != "legacy":
        monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", authority)
    monkeypatch.setattr(execution_cli, "_DETACHED_CLI_ARGS", supplied)
    monkeypatch.setattr(procutil, "resolve_own_runtime_python", lambda: "own-python")

    def spawn(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return SimpleNamespace(pid=9)
    monkeypatch.setattr(execution_cli.subprocess, "Popen", spawn)
    execution_cli._spawn_detached_waiter(RunSpec(command=("actual-wait",)))
    shared = "SHARED_" if authority == "central" else ""
    assert captured["env"][f"AGENT_DISPATCH_{shared}TOKEN"] == "synthetic-token"
    assert captured["env"][f"AGENT_DISPATCH_{shared}CONTROL_TOKEN"] == "synthetic-control"
    assert ("--local" in captured["argv"]) == (authority == "local")
    assert ("--shared" in captured["argv"]) == (authority == "central")
    assert "synthetic-token" not in json.dumps(captured["argv"])
    assert "synthetic-control" not in json.dumps(captured["argv"])


def test_legacy_prepare_omits_all_new_fields():
    from agent_dispatch.client import DispatchClient
    sent = []

    def strict_legacy(request):
        body = json.loads(request.content)
        assert set(body) == {"worker_id", "host", "reason", "resume_worktree", "command"}
        sent.append(body)
        return httpx.Response(200, json={"generation": 1, "state": "preparing"})

    with DispatchClient("http://isolated", transport=httpx.MockTransport(strict_legacy)) as client:
        client.prepare_run_waiter("task", worker_id="worker", host="host", reason="wait",
                                  resume_worktree="host/wt", command=["actual-wait"])
    assert len(sent) == 1


@pytest.mark.parametrize("committed", [False, True])
def test_ambiguous_prepare_never_subscribes_or_spawns(clean_context, tmp_path, monkeypatch, capsys, committed):
    from contextlib import contextmanager
    from agent_dispatch import __main__ as cli, remote_dispatch
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = identity(queue)
    factory = client_factory(queue)

    @contextmanager
    def uncertain(*a, **kw):
        with factory(*a, **kw) as client:
            preparation = client.prepare_run_waiter

            def fail(*args, **kwargs):
                if committed:
                    preparation(*args, **kwargs)
                raise httpx.ReadTimeout("lost acknowledgement")
            client.prepare_run_waiter = fail
            yield client
    monkeypatch.setattr(cli, "_client", uncertain)
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host")
    monkeypatch.setattr(delegation, "negotiate", lambda *_: (spec, ["owner"], "negotiated"))
    monkeypatch.setattr(delegation, "activate", lambda *_: pytest.fail("no second owner"))
    monkeypatch.setattr(cli, "_spawn_detached_waiter", lambda *_: pytest.fail("no process"))
    supplied = cli.build_parser().parse_args([
        "run", "--detach", "--task", task_id, "--resume", "host/wt",
        "--pr-watch-repo", "example/project", "--pr-watch-number", "7", "--", "actual-wait",
    ])
    assert cli._cmd_run(supplied) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == "ambiguous_prepare"
    assert bool(report["waiter"]) == committed
    assert len(queue.list_pending_run_waiters()) == int(committed)


def test_callback_resolves_command_backed_auth_without_persisting_it(monkeypatch):
    from agent_dispatch import config, delegated_waiter_callback
    # Synthetic resolver seams: no actual credential store or command is used.
    monkeypatch.setattr(config, "client_url", lambda: "http://127.0.0.1:1")
    monkeypatch.setattr(config, "client_token", lambda: "synthetic-read-token")
    monkeypatch.setattr(config, "client_control_token", lambda: "synthetic-control-token")
    captured = []

    def create(*args, **kwargs):
        captured.append((args, kwargs))
        return object()
    monkeypatch.setattr("agent_dispatch.client.DispatchClient", create)
    delegated_waiter_callback.callback_client(args(), ensure=False)
    assert captured == [(("http://127.0.0.1:1",), {
        "token": "synthetic-read-token", "control_token": "synthetic-control-token", "timeout": 3,
    })]
