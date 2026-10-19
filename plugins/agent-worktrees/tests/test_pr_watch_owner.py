"""Facade ownership and fail-closed cancellation contracts."""

from __future__ import annotations

import json
import io
import os
import sys

import pytest

from agent_worktrees import pr_watch_owner as owner
from agent_worktrees import registry_paths


@pytest.fixture(autouse=True)
def selected_payload(tmp_path, monkeypatch):
    root = tmp_path / "plugins" / "agent-worktrees"
    for name in ("agent-worktrees", "agent-pull-requests"):
        payload = root.parent / name
        (payload / "bin").mkdir(parents=True)
        (payload / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        suffix = ".cmd" if os.name == "nt" else ""
        (payload / "bin" / f"{name}{suffix}").touch()
    monkeypatch.setattr(registry_paths, "_payload_root", lambda: root)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    # Domain/receipt units model a verified worker; subprocess probes check the real kernel boundary.
    monkeypatch.setattr(owner, "require_contained_worker", lambda: None)
    return root


def executable(tmp_path, name):
    if name in ("owner", "worktrees"):
        plugin = "agent-pull-requests" if name == "owner" else "agent-worktrees"
        suffix = ".cmd" if os.name == "nt" else ""
        return str(tmp_path / "plugins" / plugin / "bin" / f"{plugin}{suffix}")
    path = tmp_path / name
    path.touch()
    return str(path)


def status(*, registrations=(), in_flight=()):
    return {
        "capabilities": sorted(owner._REQUIRED_CAPABILITIES),
        "registrations": list(registrations), "in_flight": list(in_flight),
    }


def registration():
    return {
        "repo": "project/repo", "number": 7, "subscriber_id": "pr-watch:worker",
        "registration_id": "generation", "worktrees_command": "attributable-worktrees",
    }


def test_subscribe_uses_only_owner_and_attributable_callback(tmp_path, monkeypatch, capsys):
    calls = []

    def run(command, arguments, **kwargs):
        calls.append((command, arguments, kwargs))
        return {
            "registered": True, "registration_id": "generation", "continuous": True,
            "notification_protocol": "acknowledged_notifications/v1",
            "snapshot_protocol": "agent-worktrees.pr-snapshot/v1",
        }

    monkeypatch.setattr(owner, "_run", run)
    peer, worktrees, bridge = (executable(tmp_path, name) for name in ("owner", "worktrees", "bridge"))
    assert owner.dispatch_owner(
        ["subscribe", "project/repo", "7", "--notify", "worker", "--bridge", bridge, "--json"],
        worktrees_command=worktrees, owner_command=peer,
    ) == 0
    assert json.loads(capsys.readouterr().out)["registration_id"] == "generation"
    assert len(calls) == 1
    assert calls[0][0] == peer
    args = calls[0][1]
    assert args[:2] == ["watch", "subscribe"]
    assert args[args.index("--worktrees-command") + 1] == worktrees
    assert "--continuous" in args
    assert "agent_worktrees.pr_watch_owner" in calls[0][2]["input_payload"]["argv"]
    assert not any(arg == "wait" for arg in args)


def test_stop_cancels_only_exact_generation_for_target_worktree(tmp_path, monkeypatch, capsys):
    calls = []
    initial = status(registrations=[
        registration(), {**registration(), "subscriber_id": "pr-watch:someone-else"},
    ])

    def run(command, arguments):
        calls.append(arguments)
        if arguments[:2] == ["watch", "unsubscribe"]:
            return {"unregistered": True, "registration_id": "generation", "in_flight": False}
        return initial if len(calls) == 1 else status()

    monkeypatch.setattr(owner, "_run", run)
    assert owner.dispatch_owner(
        ["stop", "--notify", "worker", "--json"],
        owner_command=executable(tmp_path, "owner"),
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"stopped": [{"repo": "project/repo", "pr": 7}], "still_running": []}
    assert len(calls) == 3
    assert calls[1][calls[1].index("--registration-id") + 1] == "generation"


def test_stop_refuses_inflight_callback_after_cancellation(tmp_path, monkeypatch, capsys):
    responses = iter([
        status(registrations=[registration()]),
        {"unregistered": True, "registration_id": "generation", "in_flight": True},
        status(in_flight=[registration()]),
    ])
    monkeypatch.setattr(owner, "_run", lambda *_: next(responses))
    assert owner.dispatch_owner(
        ["stop", "--notify", "worker", "--json"], owner_command=executable(tmp_path, "owner"),
    ) == 1
    assert json.loads(capsys.readouterr().out)["still_running"]


def test_stop_missing_owner_is_unknown_not_success(capsys, selected_payload):
    (selected_payload.parent / "agent-pull-requests" / "plugin.json").unlink()
    assert owner.dispatch_owner(["stop", "--notify", "worker", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["stopped"] == []
    assert result["still_running"][0]["state"] == "unknown"


def test_owner_rollover_cannot_turn_stop_into_success(tmp_path, monkeypatch, capsys):
    responses = iter([status(), {"registrations": [], "in_flight": []}])
    monkeypatch.setattr(owner, "_run", lambda *_: next(responses))
    assert owner.dispatch_owner(
        ["stop", "--notify", "worker", "--json"], owner_command=executable(tmp_path, "owner"),
    ) == 1
    assert json.loads(capsys.readouterr().out)["still_running"]


def test_discovery_uses_selected_sibling_without_path_lookup(tmp_path):
    worktrees, peer = owner._payload_commands(None, None)
    assert worktrees == executable(tmp_path, "worktrees")
    assert peer == executable(tmp_path, "owner")


def test_arbitrary_override_cannot_change_marketplace(tmp_path):
    foreign = executable(tmp_path, "foreign")
    with pytest.raises(ValueError, match="selected payload"):
        owner._payload_commands(None, foreign)


@pytest.mark.parametrize("context", ["", " ", "{}", "null"])
def test_explicit_cell_context_never_falls_back_to_legacy(context, monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context)
    with pytest.raises(ValueError, match="installation context"):
        owner._payload_commands(None, None)


def test_unrelated_facade_verbs_are_not_intercepted():
    assert owner.dispatch_owner(["snapshot", "project/repo", "7"]) is None


def fired_payload():
    return {
        "notification_protocol": "acknowledged_notifications/v1",
        "event_id": "stable-event", "registration_id": "generation",
        "subscriber_id": "pr-watch:worker", "repo": "project/repo", "number": 7,
        "transitions": ["gates_changed"], "timed_out": False,
        "snapshot": {
            "schema": "agent-worktrees.pr-snapshot", "version": 1,
            "repo": "project/repo", "pr": 7,
        },
    }


def admitted_receipt():
    return {
        "ok": True, "session_id": "live-head", "message_id": 12,
        "receipt_current": True, "receipt_error": None,
        "receipt": {
            "session_id": "live-head", "message_id": 12, "stage": "admitted",
            "admitted_at": 123.0,
        },
    }


@pytest.mark.parametrize("governed", [False, True])
def test_callback_reaches_real_main_from_neutral_cwd(tmp_path, monkeypatch, governed):
    from agent_worktrees import __main__ as main

    neutral = tmp_path / "owner-runtime"
    neutral.mkdir()
    monkeypatch.chdir(neutral)
    main.cfg.set_active_project(None)
    monkeypatch.setattr(main, "_resolve_active_project", lambda explicit: (None, None))
    bridge = executable(tmp_path, "bridge")
    if governed:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "test-context")
        monkeypatch.setattr(owner, "_cell_peer_prefix", lambda peer: ["canonical", peer])
    seen = []

    def run(command, arguments, **kwargs):
        seen.append((command, arguments, kwargs))
        return {"session_id": "live-head"} if "resolve" in arguments else admitted_receipt()

    monkeypatch.setattr(owner, "_run", run)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fired_payload())))
    assert main.main(["pr-watch", "deliver", "--contained-worker", "--notify", "worker", "--bridge", bridge]) == 0
    assert len(seen) == 2 and all(call[2]["owned"] for call in seen)
    assert seen[-1][1][1:3] == ["send", "worker"]
    assert all(call[0] == (["canonical", "agent-bridge"] if governed else bridge) for call in seen)


def test_callback_acks_only_durable_live_admission_with_stable_identity(
    tmp_path, monkeypatch,
):
    payload = fired_payload()
    seen = []

    def run(command, arguments, **kwargs):
        seen.append((arguments, kwargs))
        return {"session_id": "live-head"} if "resolve" in arguments else admitted_receipt()

    monkeypatch.setattr(owner, "_run", run)
    for _ in range(2):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert owner.deliver_contained(["--notify", "worker", "--bridge", executable(tmp_path, "bridge")]) == 0
    for arguments, kwargs in seen:
        assert kwargs["owned"] is True
        assert kwargs["inherit_process_group"] is True
        if "send" in arguments:
            assert arguments[arguments.index("--idempotency-key") + 1] == "stable-event"
            assert arguments[arguments.index("--expected-session-id") + 1] == "live-head"
            assert arguments[arguments.index("--min-daemon-protocol") + 1] == "39"
            assert "--no-wait" in arguments
            assert arguments[-2:] == ["--prompt-file", "-"]
            assert "Human-owned gates remain human-owned" in kwargs["input_text"]


@pytest.mark.parametrize("returncode", [0, 1, -9])
def test_standalone_delivery_supervises_the_same_contained_worker(tmp_path, monkeypatch, returncode):
    seen = []
    body = json.dumps(fired_payload())
    monkeypatch.setattr(sys, "stdin", io.StringIO(body))

    def run(arguments, **kwargs):
        seen.append((arguments, kwargs))
        return SimpleNamespace(returncode=returncode)

    from types import SimpleNamespace
    monkeypatch.setattr(owner, "run_owned", run)
    monkeypatch.setattr(
        owner, "_run", lambda *_args, **_kwargs: pytest.fail("standalone entry ran an unsupervised peer"),
    )
    assert owner.deliver(
        ["--notify", "worker", "--bridge", executable(tmp_path, "bridge")],
    ) == (0 if returncode == 0 else 1)
    assert len(seen) == 1
    arguments, options = seen[0]
    assert arguments[:4] == [sys.executable, "-I", "-m", "agent_worktrees.pr_watch_owner"]
    assert arguments[4:6] == ["deliver", "--contained-worker"]
    assert options["input_text"] == body
    assert options["capture"] is False
    assert options.get("inherit_process_group", False) is False


def test_contained_delivery_refuses_unknown_kernel_scope_without_fallback(tmp_path, monkeypatch):
    def refused():
        raise RuntimeError("unknown kernel containment")

    monkeypatch.setattr(owner, "require_contained_worker", refused)
    monkeypatch.setattr(
        owner, "run_owned", lambda *_args, **_kwargs: pytest.fail("refused worker started another root"),
    )
    monkeypatch.setattr(
        owner, "_run", lambda *_args, **_kwargs: pytest.fail("refused worker reached Bridge"),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fired_payload())))
    assert owner.dispatch([
        "deliver", "--contained-worker", "--notify", "worker", "--bridge", executable(tmp_path, "bridge"),
    ]) == 1


@pytest.mark.parametrize("change", [
    {"ok": False}, {"receipt_current": False}, {"receipt": None},
    {"receipt_error": "unavailable"}, {"session_id": "changed-head"}, {"message_id": True},
    {"receipt": {"session_id": "live-head", "message_id": 12, "stage": "accepted"}},
    {"receipt": {"session_id": "live-head", "message_id": 13, "stage": "admitted"}},
])
def test_callback_keeps_delivery_pending_without_valid_receipt(
    tmp_path, monkeypatch, change,
):
    response = {**admitted_receipt(), **change}
    monkeypatch.setattr(
        owner, "_run",
        lambda _command, arguments, **_kwargs:
        {"session_id": "live-head"} if "resolve" in arguments else response,
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fired_payload())))
    assert owner.deliver_contained(["--notify", "worker", "--bridge", executable(tmp_path, "bridge")]) == 1


def test_missing_live_session_never_creates_or_resumes_agent(tmp_path, monkeypatch):
    seen = []

    def run(command, arguments, **kwargs):
        seen.append(arguments)
        return {}

    monkeypatch.setattr(owner, "_run", run)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fired_payload())))
    assert owner.deliver_contained(["--notify", "worker", "--bridge", executable(tmp_path, "bridge")]) == 1
    assert len(seen) == 1
    assert seen[0][1:3] == ["live-sessions", "resolve"]


def test_real_callback_crosses_owned_bridge_cli_boundary(tmp_path, monkeypatch):
    script = tmp_path / "bridge.py"
    script.write_text(
        "import json,sys\n"
        "if 'resolve' in sys.argv:\n"
        " print(json.dumps({'session_id':'live-head'}))\n"
        "else:\n"
        " assert '--expected-session-id' in sys.argv\n"
        " assert '--idempotency-key' in sys.argv\n"
        " assert 'stable-event' in sys.stdin.read()\n"
        f" print({json.dumps(admitted_receipt())!r})\n",
        encoding="utf-8",
    )
    bridge = tmp_path / ("bridge.cmd" if os.name == "nt" else "bridge")
    bridge.write_text(
        f'@"{sys.executable}" "{script}" %*\n' if os.name == "nt" else
        f'#!{sys.executable}\nexec(compile(open({str(script)!r}).read(),{str(script)!r},"exec"))\n',
        encoding="utf-8",
    )
    if os.name != "nt":
        bridge.chmod(0o700)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fired_payload())))
    actual_run = owner.run_owned
    evidence = []

    def capture(*args, **kwargs):
        completed = actual_run(*args, **{**kwargs, "capture": True})
        evidence.append((completed.returncode, completed.stdout, completed.stderr))
        return completed

    monkeypatch.setattr(owner, "run_owned", capture)
    assert owner.deliver(["--notify", "worker", "--bridge", str(bridge)]) == 0, evidence


def test_standalone_worker_imports_helper_and_attests_real_kernel(tmp_path, monkeypatch):
    import threading
    import time
    from agent_procutil import owned_process

    ready, proceed = tmp_path / "worker.json", tmp_path / "proceed"
    probe = f"""
import json,os,pathlib,time
from agent_procutil.owned_process import require_contained_worker
ready=pathlib.Path({str(ready)!r});temporary=ready.with_suffix('.tmp')
temporary.write_text(json.dumps({{'pid':os.getpid()}}));temporary.replace(ready)
proceed=pathlib.Path({str(proceed)!r})
deadline=time.monotonic()+4
while not proceed.exists() and time.monotonic()<deadline: time.sleep(.01)
require_contained_worker()
print('owned-kernel-confirmed')
"""
    captured = {}
    spawn = owned_process.spawn_sync_in_kill_on_close_job

    def capture(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        captured.update(process=process, job=job)
        return process, job

    monkeypatch.setattr(owned_process, "spawn_sync_in_kill_on_close_job", capture)
    results, failures = [], []

    def run():
        try:
            results.append(owner.run_owned([sys.executable, "-I", "-c", probe], timeout=5))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=run)
    try:
        thread.start()
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), failures
        pid = json.loads(ready.read_text())["pid"]
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            from agent_procutil import (
                _build_job_extended_limit_info, _configure_job_teardown_api, _kernel32,
            )

            kernel = _kernel32()
            _configure_job_teardown_api(kernel)
            handle = captured["job"].handle
            info = _build_job_extended_limit_info()()
            returned = ctypes.c_uint32()
            assert kernel.QueryInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned),
            )
            flags = info.BasicLimitInformation.LimitFlags
            assert flags & 0x2000 and not flags & 0x1800

            class Members(ctypes.Structure):
                _fields_ = [
                    ("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                    ("pids", ctypes.c_size_t * 256),
                ]

            members = Members()
            assert kernel.QueryInformationJobObject(
                handle, 3, ctypes.byref(members), ctypes.sizeof(members), ctypes.byref(returned),
            )
            assert pid in set(members.pids[:members.count])
        proceed.write_text("go")
        thread.join(8)
        assert not thread.is_alive()
        assert not failures
        assert results[0].returncode == 0, (results[0].stdout, results[0].stderr)
        assert results[0].stdout.strip() == "owned-kernel-confirmed"
    finally:
        proceed.write_text("go")
        thread.join(8)
        assert not thread.is_alive()


def test_cell_subscription_and_callback_use_canonical_peers(
    tmp_path, selected_payload, monkeypatch, capsys,
):
    from agent_worktrees import _peer_launch, peer_launch_adapter

    own = {
        "payloadRoot": str(selected_payload),
        "pluginRoot": str(tmp_path / "cell" / "plugins" / "agent-worktrees"),
        "installReceipt": str(tmp_path / "cell" / "plugins" / "agent-worktrees" / "install.json"),
    }
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", own["installReceipt"])
    monkeypatch.setattr(peer_launch_adapter, "validate_context", lambda: dict(own))
    peers = []

    def prefix(caller, root, context, peer):
        assert caller == "agent-worktrees"
        assert str(root) == own["pluginRoot"]
        assert context == own["installReceipt"]
        peers.append(peer)
        return [sys.executable, "-I", "canonical-bootstrap", peer]

    monkeypatch.setattr(_peer_launch, "launch_prefix", prefix)
    seen = []

    def run(command, arguments, **kwargs):
        seen.append((command, arguments, kwargs))
        return {
            "registered": True, "registration_id": "generation", "continuous": True,
            "notification_protocol": "acknowledged_notifications/v1",
            "snapshot_protocol": "agent-worktrees.pr-snapshot/v1",
        }

    monkeypatch.setattr(owner, "_run", run)
    assert owner.dispatch([
        "subscribe", "project/repo", "7", "--notify", "worker",
        "--bridge", executable(tmp_path, "bridge"), "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["registered"]
    assert peers == ["agent-pull-requests", "agent-worktrees"]
    assert seen[0][0][-1] == "agent-pull-requests"
    callback = seen[0][2]["input_payload"]["argv"]
    assert callback[:4] == [sys.executable, "-I", "canonical-bootstrap", "agent-worktrees"]
    assert callback[4:6] == ["pr-watch", "deliver"]
    assert "--contained-worker" in callback


def test_cell_delivery_rebinds_bridge_without_using_raw_catalog_hint(
    tmp_path, selected_payload, monkeypatch,
):
    from agent_worktrees import _peer_launch, peer_launch_adapter

    own = {
        "payloadRoot": str(selected_payload), "pluginRoot": str(tmp_path / "cell-owner"),
        "installReceipt": str(tmp_path / "owned-receipt"),
    }
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", own["installReceipt"])
    monkeypatch.setattr(peer_launch_adapter, "validate_context", lambda: own)
    monkeypatch.setattr(
        _peer_launch, "launch_prefix",
        lambda caller, root, context, peer: [sys.executable, "-I", "canonical-bootstrap", peer],
    )
    calls = []

    def run(command, arguments, **kwargs):
        calls.append((command, arguments))
        return {"session_id": "live-head"} if "resolve" in arguments else admitted_receipt()

    monkeypatch.setattr(owner, "_run", run)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fired_payload())))
    assert owner.dispatch([
        "deliver", "--contained-worker", "--notify", "worker", "--bridge", "missing-legacy-hint",
    ]) == 0
    assert len(calls) == 2
    assert all(command[-1] == "agent-bridge" for command, _ in calls)


def test_generated_callback_keeps_bridge_peers_in_outer_posix_group(
    tmp_path, monkeypatch, capsys,
):
    from agent_procutil import owned_process
    from types import SimpleNamespace

    peer, worktrees, bridge = (
        executable(tmp_path, name) for name in ("owner", "worktrees", "bridge")
    )
    monkeypatch.setattr(owner, "_payload_commands", lambda *_: (worktrees, peer))
    monkeypatch.setattr(owned_process, "os", SimpleNamespace(
        name="posix", getpid=lambda: 1234, getpgrp=lambda: 1234,
        getsid=lambda _pid: 1234, killpg=lambda *_: pytest.fail("member swept the outer group"),
        kill=lambda *_: None, waitid=lambda *_: SimpleNamespace(si_pid=5678),
        P_PID=1, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000,
    ))
    monkeypatch.setattr(owned_process, "signal", SimpleNamespace(
        SIGKILL=9, SIGCHLD=17, SIG_DFL=0, getsignal=lambda _: 0,
    ))
    actual_run = owner._run
    registered = []
    launches = []

    def route(command, arguments, **kwargs):
        if command == peer:
            registered.append(kwargs["input_payload"]["argv"])
            return {
                "registered": True, "registration_id": "generation", "continuous": True,
                "notification_protocol": "acknowledged_notifications/v1",
                "snapshot_protocol": "agent-worktrees.pr-snapshot/v1",
            }
        return actual_run(command, arguments, **kwargs)

    class Process:
        pid = 5678
        returncode = None
        stdin = stdout = stderr = None

        def __init__(self, arguments):
            self.args = arguments

        def wait(self, **_kwargs):
            self.returncode = 0
            return self.returncode

    def spawn(arguments, **kwargs):
        launches.append((arguments, kwargs))
        return Process(arguments), None

    def communicate(process, **_kwargs):
        response = {"session_id": "live-head"} if "resolve" in process.args else admitted_receipt()
        return json.dumps(response), ""

    monkeypatch.setattr(owned_process, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(owned_process, "_communicate_unreaped", communicate)
    monkeypatch.setattr(owner, "_run", route)
    assert owner.dispatch([
        "subscribe", "project/repo", "7", "--notify", "worker", "--bridge", bridge, "--json",
    ]) == 0
    capsys.readouterr()
    callback = registered[0]
    arguments = callback[callback.index("agent_worktrees.pr_watch_owner") + 1:]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(fired_payload())))
    assert owner.dispatch(arguments) == 0
    assert len(launches) == 2
    assert all(kwargs.get("start_new_session") is not True for _argv, kwargs in launches)
