"""Tests for ``agent-containers copilot <name> --detach/--stop``."""

from __future__ import annotations

import argparse
import contextlib
import json
import types
import dataclasses
import sqlite3
import subprocess
from venue_copilot.refs import deliver_note as _real_deliver_note

import pytest
import venue_copilot
from venue_copilot import detached as venue_detached
from ssh_manager import forward_keeper as shared_forward_keeper
from ssh_manager import keeper_holds as shared_keeper_holds

from agent_containers import copilot_detach as detach
from agent_containers import forward_keeper
from agent_containers.config import ContainersConfig, FleetConfig, RESTRICTED_PROFILE
from tests._hosting_owner import policy_reply


@pytest.fixture
def synthetic_keeper_processes(monkeypatch):
    calls = []

    def spawn(argv, *, instance, popen, **kwargs):
        calls.append(instance)
        return popen(argv, **kwargs)

    monkeypatch.setattr(forward_keeper, "spawn_instance", spawn)
    return calls


def _args(**kw):
    base = dict(
        name="repo-1",
        worktree_id=None,
        driver="orchestrator",
        seed="do the task",
        seed_file=None,
        copilot_args=["--no-ask-user"],
        register_timeout=0.0,
        ensure_mux=True,
        no_relay=False,
        force=False,
        detach=True,
        stop=False,
        dry_run=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _prepared(kwargs):
    if kwargs.get("prepared_context") is not None:
        kwargs["prepared_context"].update({
            "workspace": "/workspaces/repo", "plugin_context": {
                "version": 1, "cwd": "/workspaces/repo", "plugins": [{"projected_source": "helper@local"}],
                "logical_payloads": {"helper@local": "/live/helper"},
            },
        })
    return []


def test_pending_delivery_roundtrips_in_existing_keeper_state(monkeypatch, tmp_path):
    import os
    import time
    from ssh_manager.forward_keeper import KeeperStore
    store = KeeperStore(tmp_path)
    monkeypatch.setattr(forward_keeper, "_STORE", store)
    store.write("synthetic", {
        "pid": os.getpid(), "holds": {"scope": {"mux": "wt-scope", "updated_at": time.time()}},
    })
    pending = {"version": 1, "status": "unknown", "operation": "operation", "session_id": "native"}
    forward_keeper.record_pending_delivery("synthetic", "scope", pending)
    forward_keeper._holds().write_self_state("synthetic", {"connected": True}, pid=os.getpid())
    assert forward_keeper.read_state("synthetic")["pending_deliveries"]["scope"] == pending
    forward_keeper.record_pending_delivery("synthetic", "scope", None)
    assert forward_keeper.read_state("synthetic").get("pending_deliveries", {}) == {}


@pytest.mark.parametrize("cause", ["dead", "changed-forward"])
@pytest.mark.parametrize("replacement_fails", [False, True])
def test_scope_a_unknown_delivery_survives_scope_b_keeper_replacement_and_failure(
    monkeypatch, tmp_path, cause, replacement_fails,
):
    import time
    from ssh_manager.forward_keeper import KeeperStore
    from venue_copilot.refs import reconcile_owned_delivery, settle_deferred_seed

    queue = sqlite3.connect(tmp_path / "queue.sqlite")
    queue.execute("CREATE TABLE messages (key TEXT PRIMARY KEY, body TEXT)")
    attempts, settlements = [], []
    lost = [True]
    def send(argv, **kwargs):
        key = argv[argv.index("--idempotency-key") + 1]
        attempts.append((list(argv), kwargs["input"]))
        queue.execute("INSERT OR IGNORE INTO messages VALUES (?,?)", (key, kwargs["input"]))
        queue.commit()
        if lost[0]:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({
            "accepted": True, "session_id": "native-A", "message_id": 1, "duplicate": True,
            "receipt": {"session_id": "native-A", "stage": "admitted"},
        }))
    context = {"version": 1, "cwd": "/workspace/A", "plugins": [], "logical_payloads": {}}
    result = _real_deliver_note(
        "native-A", "task A", run=send, operation="operation-A", prepared_context=context,
    )
    assert result.status == "unknown"
    receipt = {"seed": {"text": "task A", "handoff_id": "bridge-operation-A"}}
    pending = {
        **result.recovery(), "envelope": {"prompt": "task A", "plugin_context": context},
        "creation_receipt": receipt, "remote_env": "/private/launch-input-A", "reservation_id": "reservation-A",
    }
    store = KeeperStore(tmp_path / "keepers")
    monkeypatch.setattr(forward_keeper, "_STORE", store)
    store.write("shared", {
        "keeper_protocol": forward_keeper._KEEPER_PROTOCOL, "pid": 987654321,
        "container": "shared", "venue_port": 4000, "relay_port": 4001, "host_relay_port": 4002,
        "holds": {"scope-A": {"mux": "wt-A", "updated_at": time.time()}},
        # Exercise migration of the unreleased, embedded workflow format.
        "pending_deliveries": {"scope-A": pending},
    })
    monkeypatch.setattr(store, "alive", lambda name: cause == "changed-forward")
    stopped = []
    def stop(name):
        assert forward_keeper.pending_delivery_path(name).is_file()
        stopped.append(name)
        store.remove(name)
        return cause == "changed-forward"
    monkeypatch.setattr(store, "stop", stop)
    def spawn(argv, env, payload, **kwargs):
        assert forward_keeper.pending_delivery_path("shared").is_file()
        assert "pending_deliveries" not in (store.read("shared") or {})
        assert "scope-A" in forward_keeper.read_state("shared")["holds"]
        if replacement_fails:
            raise OSError("replacement process creation failed")
        return {**payload, "pid": 987654322}
    monkeypatch.setattr(forward_keeper, "spawn_keeper", spawn)
    def replace():
        return forward_keeper.ensure_running(
            "shared", venue_port=5000 if cause == "changed-forward" else 4000,
            mux="wt-B", hold_id="scope-B", mux_alive=lambda mux: True,
        )
    if replacement_fails:
        with pytest.raises(OSError, match="replacement process"):
            replace()
    else:
        assert replace()["started"] is True
    assert stopped == ["shared"]
    loaded = forward_keeper.read_state("shared")["pending_deliveries"]["scope-A"]
    assert loaded == pending
    assert set(forward_keeper.read_state("shared")["holds"]) == {"scope-A", "scope-B"}
    assert not settlements
    assert forward_keeper.stop_keeper(
        "shared", hold_id="scope-B", mux_alive=lambda mux: True,
    ) is False
    assert set(forward_keeper.read_state("shared")["holds"]) == {"scope-A"}
    assert forward_keeper.read_state("shared")["pending_deliveries"]["scope-A"] == pending
    # Ordinary process-state removal also cannot erase an unresolved workflow.
    store.stop("shared")
    assert forward_keeper.read_state("shared")["pending_deliveries"]["scope-A"] == pending
    lost[0] = False
    admitted = reconcile_owned_delivery(loaded, run=send)
    assert admitted.status == "admitted" and admitted.message_id == 1
    assert settle_deferred_seed(
        lambda command, stdin: settlements.append((command, stdin)) or (0, "", ""),
        "worktree-A", context["cwd"], receipt,
    )
    assert len(settlements) == 1 and "bridge-seed-settle" in settlements[0][0]
    assert queue.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert all(value == attempts[0] for value in attempts)
    # Recovery may finish after the disposable keeper state has disappeared.
    forward_keeper.record_pending_delivery("shared", "scope-A", None)
    assert forward_keeper.read_state("shared") is None
    assert not forward_keeper.pending_delivery_path("shared").exists()
    queue.close()


def test_preparation_runs_under_the_existing_keeper_before_native_launch(seams, monkeypatch):
    prepared = []

    def prepare(args, target, **kwargs):
        assert seams.keeper
        assert kwargs["remote_env"]
        assert kwargs["relay_port"] == 9857
        prepared.append(True)
        return _prepared(kwargs)

    monkeypatch.setattr("agent_containers.plugin_preparation.prepare_plugins", prepare)
    assert detach.cmd_detach(
        _args(), require_live_relay_port=lambda: 41235, relay_healthy=lambda port: True,
    ) == 0
    assert prepared == [True]
    command = next(value for value in seams.run if "agent-worktrees embody" in value)
    assert "--seed " not in command and "--bridge-seed-operation r1" in command
    assert len(seams.messages) == 1
    assert seams.messages[0][2]["prepared_context"]["plugins"] == [{"projected_source": "helper@local"}]


def test_owned_readiness_failure_is_not_a_success_or_an_ordinary_ref_send(seams, monkeypatch, capsys):
    attempted = []
    from venue_copilot.refs import owned_result
    def refuse(sid, note, **kwargs):
        attempted.append(kwargs)
        return dataclasses.replace(owned_result(
            False, session_id=sid, operation=kwargs["operation"], note=note,
            context=kwargs["prepared_context"],
        ), status="refused")
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        refuse)
    assert detach.cmd_detach(
        _args(), require_live_relay_port=lambda: 41235, relay_healthy=lambda port: True,
    ) == 1
    assert len(attempted) == 1 and attempted[0]["min_daemon_protocol"] == 41
    assert attempted[0]["prepared_context"]["version"] == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert any("tmux kill-session" in value for value in seams.run)


def test_admitted_seed_lost_response_keeps_mux_keeper_inputs_and_pending_recovery(
    seams, monkeypatch, capsys, tmp_path,
):
    queue = sqlite3.connect(tmp_path / "admitted.sqlite")
    queue.execute("CREATE TABLE messages (key TEXT PRIMARY KEY, body TEXT)")
    attempts = []
    lost = [True]
    def run(argv, **kwargs):
        key = argv[argv.index("--idempotency-key") + 1]
        attempts.append((list(argv), kwargs["input"]))
        queue.execute("INSERT OR IGNORE INTO messages VALUES (?,?)", (key, kwargs["input"]))
        queue.commit()
        if lost[0]:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({
            "accepted": True, "session_id": "sid-42", "message_id": 1, "duplicate": True,
            "receipt": {"session_id": "sid-42", "stage": "admitted"},
        }))
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        lambda sid, note, **kwargs: _real_deliver_note(
                            sid, note, run=run, **{k: v for k, v in kwargs.items() if k != "run"}))
    args = _args()
    assert detach.cmd_detach(args, require_live_relay_port=lambda: 41235,
                             relay_healthy=lambda port: True) == 75
    report = json.loads(capsys.readouterr().out)
    assert report["pending"] is True and report["pending_delivery"]["status"] == "unknown"
    assert queue.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert not any("tmux kill-session" in command for command in seams.run)
    assert seams.stop_keeper == [] and seams.release == []
    pending = seams.pending["anchor-repo@repo-1"]
    assert pending["remote_env"] not in seams.cleaned
    native_launches = sum("agent-worktrees embody" in value for value in seams.run)
    monkeypatch.setattr(forward_keeper, "read_state", lambda name: {
        "pending_deliveries": seams.pending,
    })
    def deny_new_launch(*args, **kwargs):
        raise RuntimeError("expose_agent: false must not block retained delivery settlement")
    monkeypatch.setattr("agent_containers.hosting_policy.require_container_hosting", deny_new_launch)
    assert detach.cmd_detach(args, require_live_relay_port=lambda: 41235,
                             relay_healthy=lambda port: True) == 75
    capsys.readouterr()
    lost[0] = False
    assert detach.cmd_detach(args, require_live_relay_port=lambda: 41235,
                             relay_healthy=lambda port: True) == 0
    assert json.loads(capsys.readouterr().out)["message_id"] == 1
    assert seams.pending == {} and pending["remote_env"] in seams.cleaned
    assert sum("agent-worktrees embody" in value for value in seams.run) == native_launches
    assert all(value == attempts[0] for value in attempts)
    assert queue.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    queue.close()


def test_vanished_pending_delivery_rechecks_hosting_before_new_launch(seams, monkeypatch, capsys):
    snapshots = iter([
        {"pending_deliveries": {"anchor-repo@repo-1": {"status": "admitted"}}},
        {},
    ])
    monkeypatch.setattr(forward_keeper, "read_state", lambda _name: next(snapshots))
    def deny(*args, **kwargs):
        raise RuntimeError("expose_agent: false")
    monkeypatch.setattr("agent_containers.hosting_policy.require_container_hosting", deny)
    assert detach.cmd_detach(
        _args(), require_live_relay_port=lambda: 41235, relay_healthy=lambda port: True,
    ) == 1
    assert "expose_agent: false" in json.loads(capsys.readouterr().out)["error"]
    assert seams.run == [] and seams.stop_keeper == []


@pytest.mark.parametrize("failure", [None, "spawn", "probe"])
def test_unadmitted_pending_seed_restores_dead_keeper_before_native_readiness(
    seams, monkeypatch, capsys, failure,
):
    import time

    attempts, events, messages = [], [], {}
    connected = [False]
    def send(argv, **kwargs):
        attempts.append((list(argv), kwargs["input"]))
        if not connected[0]:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        events.append("send")
        key = argv[argv.index("--idempotency-key") + 1]
        messages.setdefault(key, kwargs["input"])
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({
            "accepted": True, "session_id": "sid-42", "message_id": 1,
            "receipt": {"session_id": "sid-42", "stage": "admitted"},
        }))
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        lambda sid, note, **kwargs: _real_deliver_note(
                            sid, note, run=send, **{k: v for k, v in kwargs.items() if k != "run"}))
    args = _args()
    assert detach.cmd_detach(args, require_live_relay_port=lambda: 41235,
                             relay_healthy=lambda port: True) == 75
    capsys.readouterr()
    assert not messages
    pending = seams.pending["anchor-repo@repo-1"]
    assert pending["daemon_port"] == 41234 and pending["mux_session"] == "wt-anchor-repo"
    monkeypatch.setattr(forward_keeper, "read_state", lambda name: {
        "pending_deliveries": seams.pending,
    })
    current_failure = [failure]
    def restore(name, **kwargs):
        events.append("restore")
        assert kwargs["venue_port"] == pending["daemon_port"]
        assert kwargs["mux"] == pending["mux_session"]
        if current_failure[0] == "spawn":
            raise OSError("replacement keeper failed")
        connected[0] = current_failure[0] != "probe"
        return {"hold_added": False, "hold_updated_at": 100}
    monkeypatch.setattr(forward_keeper, "ensure_running", restore)
    original_run = detach.subprocess.run
    def run(argv, **kwargs):
        if "curl -fsS" in argv[-1]:
            events.append("probe")
            return types.SimpleNamespace(returncode=0 if connected[0] else 1, stdout="", stderr="")
        return original_run(argv, **kwargs)
    monkeypatch.setattr(detach.subprocess, "run", run)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    launches = sum("agent-worktrees embody" in command for command in seams.run)
    if failure:
        before = len(attempts)
        assert detach.cmd_detach(args, require_live_relay_port=lambda: 41235,
                                 relay_healthy=lambda port: True) == 75
        assert json.loads(capsys.readouterr().out)["pending"] is True
        assert len(attempts) == before and not messages
        assert seams.pending and seams.stop_keeper == []
        current_failure[0] = None
        events.clear()
    assert detach.cmd_detach(args, require_live_relay_port=lambda: 41235,
                             relay_healthy=lambda port: True) == 0
    assert json.loads(capsys.readouterr().out)["message_id"] == 1
    assert events == ["restore", "probe", "send"]
    assert len(messages) == 1 and seams.pending == {}
    assert seams.release == [("anchor-repo@repo-1", "r1")]
    assert sum("agent-worktrees embody" in command for command in seams.run) == launches
    assert all(attempt == attempts[0] for attempt in attempts)


def test_confirmed_seed_recovery_only_settles_without_restoring_a_dead_native_forward(
    seams, monkeypatch, capsys,
):
    from venue_copilot.refs import owned_result

    def admit(sid, note, **kwargs):
        return dataclasses.replace(owned_result(
            False, session_id=sid, operation=kwargs["operation"],
            note=note, context=kwargs["prepared_context"],
        ), status="admitted", message_id=17)
    monkeypatch.setattr("venue_copilot.refs.deliver_note", admit)
    receipt = {"seed": {"text": "creation task", "handoff_id": "bridge-r1"}}
    original_run = detach.subprocess.run
    fail_settlement = [True]
    def run(argv, **kwargs):
        if "agent-worktrees embody" in argv[-1]:
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({
                "ok": True, "created": True, "bridge_seed_deferred": True,
                "bridge_seed_operation": "r1", "deferred_creation_seed": receipt,
                "session": "wt-anchor-repo",
            }), stderr="")
        if "bridge-seed-settle" in argv[-1] and fail_settlement[0]:
            return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"settlement unavailable")
        return original_run(argv, **kwargs)
    monkeypatch.setattr(detach.subprocess, "run", run)
    args = _args()
    assert detach.cmd_detach(args, require_live_relay_port=lambda: 41235,
                             relay_healthy=lambda port: True) == 75
    capsys.readouterr()
    assert seams.pending["anchor-repo@repo-1"]["status"] == "admitted"
    monkeypatch.setattr(forward_keeper, "read_state", lambda name: {
        "pending_deliveries": seams.pending,
    })
    monkeypatch.setattr(forward_keeper, "ensure_running",
                        lambda *a, **kw: pytest.fail("the native session has exited"))
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        lambda *a, **kw: pytest.fail("confirmed admission must not be resent"))
    fail_settlement[0] = False
    assert detach.cmd_detach(args, require_live_relay_port=lambda: 41235,
                             relay_healthy=lambda port: True) == 0
    assert json.loads(capsys.readouterr().out)["message_id"] == 17
    assert seams.pending == {}
    assert seams.release == [("anchor-repo@repo-1", "r1")]


@pytest.mark.parametrize("child_alive", [False, True])
@pytest.mark.parametrize("failure", [None, "copy", "install"])
def test_detached_live_ledger_requires_preparation_before_remote_decision(
    seams, monkeypatch, capsys, child_alive, failure,
):
    events = []
    monkeypatch.setattr(venue_copilot, "live_session_for", lambda *a, **kw: {
        "status": "live", "session_id": "sid-42", "venue": {"target": "repo-1"},
    })

    def prepare(args, target, **kwargs):
        events.append("prepare")
        assert args.stage_plugins == [{"source": "sample@synthetic", "enable": False}]
        if failure:
            raise RuntimeError(f"plugin {failure} failed")
        _prepared(kwargs)

    original = detach.subprocess.run

    def run(argv, **kwargs):
        if "agent-worktrees embody" in argv[-1]:
            events.append("reuse" if child_alive else "create")
            return types.SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                "ok": True, "created": not child_alive, "resumed": child_alive,
                "bridge_seed_deferred": True, "bridge_seed_operation": "r1",
            }))
        return original(argv, **kwargs)

    monkeypatch.setattr("agent_containers.plugin_preparation.prepare_plugins", prepare)
    monkeypatch.setattr(detach.subprocess, "run", run)
    assert detach.cmd_detach(
        _args(seed=None, stage_plugins=[{"source": "sample@synthetic", "enable": False}]),
        require_live_relay_port=lambda: 41235, relay_healthy=lambda port: True,
    ) == (1 if failure else 0)
    assert events == (["prepare"] if failure else ["prepare", "reuse" if child_alive else "create"])
    if not failure:
        out = json.loads(capsys.readouterr().out)
        assert out["created"] is not child_alive and out["resumed"] is child_alive
    if child_alive:
        assert not any("tmux kill-session" in command for command in seams.run)


def _target(profile: str = "trusted"):
    config = ContainersConfig(forward_gh_token=False, relay_enabled=True, relay_port=9857,
                              copilot_auth_mode="legacy")
    fleet = FleetConfig(
        repo="example/repo", workspace_folder="/workspaces/repo", exec_user="vscode"
    )
    return types.SimpleNamespace(
        name="repo-1",
        container_id="a" * 64,
        config=config,
        fleet=fleet,
        actual_profile=profile,
        user="vscode",
        workspace_folder="/workspaces/repo",
    )


class _FakeLock:
    instances: list["_FakeLock"] = []

    def __init__(self, target, *, op):
        self.target = target
        self.op = op
        self.released = False
        _FakeLock.instances.append(self)

    def acquire(self, *, force=False):
        self.force = force

    def release(self):
        self.released = True


@pytest.fixture
def seams(monkeypatch):
    monkeypatch.setattr("agent_containers.plugin_preparation.prepare_plugins", lambda *a, **kw: _prepared(kw))
    # Hermetic: never read the developer's own ~/.copilot/settings.json model.
    monkeypatch.setattr(detach, "with_supervisor", lambda venue, ref=None: dict(venue))
    monkeypatch.setattr(detach, "model_copilot_args", lambda existing: [])
    calls = types.SimpleNamespace(
        run=[],
        environments={},
        reserve=[],
        release=[],
        keeper=[],
        stop_keeper=[],
        cleaned=[],
        deregister=[],
        remote_env=[],
        shims=[],
        messages=[],
        pending={},
        admissions=[],
    )
    target = _target()
    def policy_run(argv, **kwargs):
        calls.admissions.append(argv)
        return policy_reply(
            argv, container_id=target.container_id, workspace=target.workspace_folder,
        )
    monkeypatch.setattr("agent_containers.hosting_policy.subprocess", types.SimpleNamespace(
        run=policy_run, SubprocessError=subprocess.SubprocessError,
    ))
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        lambda sid, note, **kwargs: calls.messages.append((sid, note, kwargs)) or True)
    import agent_containers.config as config_mod
    import agent_containers.resolver as resolver_mod
    import agent_containers.ssh_transport as ssh_transport
    import ssh_manager

    monkeypatch.setattr(config_mod, "load_config", lambda: target.config)
    monkeypatch.setattr(resolver_mod, "resolve_live_exec_target", lambda name, config=None: target)
    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)
    _FakeLock.instances.clear()
    monkeypatch.setattr(venue_detached, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(venue_detached, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(
        venue_detached,
        "reserve_with_retry",
        lambda scope, venue, **kw: (
            calls.reserve.append((scope, venue)) or {"reservation_id": "r1"}
        ),
    )
    monkeypatch.setattr(
        venue_detached,
        "await_claim",
        lambda scope, rid, timeout: "sid-42",
    )
    monkeypatch.setattr(
        venue_detached,
        "release_cli_mode",
        lambda scope, reservation_id=None: calls.release.append((scope, reservation_id)) or 1,
    )
    monkeypatch.setattr(venue_copilot, "release_cli_mode", venue_detached.release_cli_mode)
    monkeypatch.setattr(
        venue_copilot,
        "live_session_for",
        lambda handle: {
            "session_id": "sid-42",
            "venue": {"target": "repo-1"},
        },
    )
    monkeypatch.setattr(
        venue_detached,
        "deregister_live_session",
        lambda sid: calls.deregister.append(sid) or True,
    )
    monkeypatch.setattr(
        ssh_transport, "prepare_ssh_config", lambda name, user: types.SimpleNamespace()
    )
    monkeypatch.setattr(ssh_transport, "build_ssh_command", lambda cfg, cmd, **kw: ["ssh", cmd])
    monkeypatch.setattr(
        ssh_transport, "container_environment", lambda name, user: {"PATH": "/bin"}
    )
    monkeypatch.setattr(
        ssh_transport,
        "write_remote_env",
        lambda name, user, values: calls.remote_env.append(values) or "/tmp/env",
    )
    monkeypatch.setattr(
        ssh_transport,
        "cleanup_remote_env",
        lambda name, user, path: calls.cleaned.append(path),
    )
    monkeypatch.setattr(
        "agent_containers.container_shims.ensure_agent_worktrees",
        lambda *a, **k: calls.shims.append(("ensure", a, k)),
    )
    monkeypatch.setattr(
        "agent_containers.container_shims.ensure_agent_worktrees_workspace_registered",
        lambda *a, **k: calls.shims.append(("register", a, k)),
    )
    monkeypatch.setattr(
        "agent_containers.container_shims.deploy",
        lambda *a, **k: calls.shims.append(("deploy", a, k)),
    )
    monkeypatch.setattr(
        "agent_containers.container_shims.git_credential_environment",
        lambda: {
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    monkeypatch.setattr("agent_containers.relay_provider.token_for", lambda name: "relay-token")
    monkeypatch.setattr(
        forward_keeper,
        "ensure_running",
        lambda *a, **k: calls.keeper.append((a, k))
        or {"started": True, "hold_added": True, "state": {"pid": 123}},
    )
    monkeypatch.setattr(
        forward_keeper,
        "stop_keeper",
        lambda name, **kw: calls.stop_keeper.append((name, kw.get("hold_id"))) or True,
    )
    monkeypatch.setattr(forward_keeper, "read_state", lambda name: None)
    def pending(name, hold_id, value):
        if value is None:
            calls.pending.pop(hold_id, None)
        else:
            calls.pending[hold_id] = value
    monkeypatch.setattr(forward_keeper, "record_pending_delivery", pending)
    created = json.dumps(
        {
            "ok": True,
            "created": True,
            "resumed": False,
            "seed_submitted": False, "bridge_seed_deferred": True, "bridge_seed_operation": "r1",
            "session": "wt-anchor-repo",
        }
    )

    def fake_run(argv, **kwargs):
        command = argv[-1]
        calls.run.append(command)
        calls.environments[command] = kwargs.get("env")
        if "curl -fsS" in command:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "agent-worktrees embody" in command:
            return types.SimpleNamespace(returncode=0, stdout=created, stderr="")
        if "tmux kill-session" in command:
            return types.SimpleNamespace(returncode=0, stdout="STOPPED\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(detach.subprocess, "run", fake_run)
    return calls


def test_detach_success_provisions_credentials_starts_keeper_and_reports_handle(seams, capsys):
    rc = detach.cmd_detach(
        _args(),
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda port: port == 61234,
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["session_id"] == "sid-42"
    assert out["scope_id"] == "anchor-repo@repo-1"
    assert out["venue"] == {
        "kind": "container",
        "target": "repo-1",
        "mux_session_name": "wt-anchor-repo",
    }
    assert out["commands"]["attach"] == "agent-containers copilot repo-1"
    assert out["commands"]["stop"] == "agent-containers copilot repo-1 --stop"
    assert seams.reserve == [("anchor-repo@repo-1", out["venue"])]
    assert seams.keeper[0][1].items() >= {
        "venue_port": 41234,
        "mux": "wt-anchor-repo",
        "hold_id": "anchor-repo@repo-1",
        "relay_port": 9857,
        "host_relay_port": 61234,
    }.items()
    assert [item[0] for item in seams.shims] == ["ensure", "register", "deploy"]
    assert seams.shims[1] == (
        "register",
        ("repo-1",),
        {"user": "vscode", "workspace_folder": "/workspaces/repo"},
    )
    assert "auth.yaml" in seams.run[0] and "active.json" in seams.run[0]
    launch = next(cmd for cmd in seams.run if "agent-worktrees embody" in cmd)
    assert "cd /workspaces/repo" in launch
    assert "--bridge-scope-id anchor-repo@repo-1" in launch
    assert "--copilot-arg=--no-ask-user" in launch
    assert "--json" in launch
    assert seams.release == [("anchor-repo@repo-1", "r1")]
    assert _FakeLock.instances[0].released is True
    assert "GH_TOKEN" not in seams.remote_env[0]


def test_detach_ssh_excludes_inference_token_but_preserves_repository_auth(seams, monkeypatch):
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "fake-inference-token")
    monkeypatch.setenv("GH_TOKEN", "fake-repository-token")

    assert detach.cmd_detach(
        _args(),
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda port: True,
    ) == 0

    launch = next(command for command in seams.run if "agent-worktrees embody" in command)
    environment = seams.environments[launch]
    assert "COPILOT_GITHUB_TOKEN" not in environment
    assert environment["GH_TOKEN"] == "fake-repository-token"


def test_detach_explicit_legacy_preserves_existing_container_login(seams, monkeypatch):
    import agent_containers.ssh_transport as transport

    monkeypatch.setattr(
        transport, "container_environment",
        lambda name, user: {"PATH": "/bin", "COPILOT_GITHUB_TOKEN": "fake-container-login"},
    )
    assert detach.cmd_detach(
        _args(),
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda port: True,
    ) == 0
    assert seams.remote_env[0]["COPILOT_GITHUB_TOKEN"] == "fake-container-login"


def test_detach_forwards_the_host_github_token_when_enabled(seams, monkeypatch, capsys):
    import agent_containers.resolver as resolver_mod

    target = resolver_mod.resolve_live_exec_target("repo-1")
    target.config.forward_gh_token = True
    monkeypatch.setattr(resolver_mod, "host_gh_token", lambda: "gho_host")
    rc = detach.cmd_detach(
        _args(),
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda p: True,
    )
    assert rc == 0
    assert seams.remote_env[0]["GH_TOKEN"] == "gho_host"
    assert seams.remote_env[0]["LC_GIT_CREDENTIAL_RELAY_TOKEN"] == "relay-token"
    assert all("gho_host" not in cmd for cmd in seams.run)  # staged over stdin, never argv
    assert "gho_host" not in capsys.readouterr().out


def test_detach_forwards_dispatch_reachback_environment(seams, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_DISPATCH_URL", "http://host.docker.internal:50087")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN", "shared-token")
    monkeypatch.setenv("AGENT_DISPATCH_CONTROL_TOKEN", "control-token")

    rc = detach.cmd_detach(
        _args(),
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda p: True,
    )

    assert rc == 0
    assert seams.remote_env[0]["AGENT_DISPATCH_URL"] == "http://host.docker.internal:50087"
    assert seams.remote_env[0]["AGENT_DISPATCH_SHARED_TOKEN"] == "shared-token"
    assert "AGENT_DISPATCH_CONTROL_TOKEN" not in seams.remote_env[0]
    assert "shared-token" not in capsys.readouterr().out


def test_detach_without_relay_still_prepares_agent_worktrees_and_workspace(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "agent_containers.container_shims.ensure_agent_worktrees",
        lambda *a, **k: seen.append(("ensure", a, k)),
    )
    monkeypatch.setattr(
        "agent_containers.container_shims.ensure_agent_worktrees_workspace_registered",
        lambda *a, **k: seen.append(("register", a, k)),
    )
    monkeypatch.setattr(
        "agent_containers.resolver.host_gh_token",
        lambda: None,
        raising=False,
    )

    env, relay_port, host_relay_port = detach._launch_env(
        _args(no_relay=True),
        _target(),
        require_live_relay_port=lambda: (_ for _ in ()).throw(AssertionError("no relay")),
        relay_healthy=lambda p: (_ for _ in ()).throw(AssertionError("no relay")),
    )

    assert relay_port is None and host_relay_port is None
    assert seen == [
        ("ensure", ("repo-1",), {"user": "vscode"}),
        (
            "register",
            ("repo-1",),
            {"user": "vscode", "workspace_folder": "/workspaces/repo"},
        ),
    ]


def test_launch_env_does_not_stage_inference_auth(monkeypatch):
    import agent_containers.resolver as resolver_mod

    monkeypatch.setattr(
        "agent_containers.container_shims.ensure_agent_worktrees",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "agent_containers.container_shims.ensure_agent_worktrees_workspace_registered",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(resolver_mod, "host_gh_token", lambda: None, raising=False)
    monkeypatch.setattr(
        resolver_mod,
        "host_copilot_cli_token",
        lambda account: "gho_inference_only" if account == "inference-user" else None,
    )
    target = _target()
    target.fleet.copilot_cli_account = "inference-user"

    env, _relay_port, _host_relay_port = detach._launch_env(
        _args(no_relay=True),
        target,
        require_live_relay_port=lambda: (_ for _ in ()).throw(AssertionError("no relay")),
        relay_healthy=lambda p: (_ for _ in ()).throw(AssertionError("no relay")),
    )

    assert "COPILOT_GITHUB_TOKEN" not in env
    assert "GH_TOKEN" not in env


def test_actual_detached_launch_fails_closed_when_source_is_missing(monkeypatch):
    import agent_containers.resolver as resolver_mod

    monkeypatch.setattr(resolver_mod, "host_copilot_cli_token", lambda account: None)
    target = _target()
    target.config.copilot_auth_mode = "host-copilot"
    target.fleet.copilot_cli_account = "inference-user"

    with pytest.raises(RuntimeError, match="copilot-login"):
        detach._ContainerAdapter(
            name="repo-1", ssh_config=None, hold_id="scope", remote_env=None,
            relay_port=None, host_relay_port=None, target=target,
        ).launch(
            "agent-worktrees embody --detach", timeout=30,
        )


def test_detach_fails_before_launch_when_the_forwarded_token_is_missing(
    seams, monkeypatch, capsys
):
    import agent_containers.resolver as resolver_mod

    target = resolver_mod.resolve_live_exec_target("repo-1")
    target.config.forward_gh_token = True
    monkeypatch.setattr(resolver_mod, "host_gh_token", lambda: None)
    rc = detach.cmd_detach(
        _args(),
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda p: True,
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "signed out" in captured.err
    failure = json.loads(captured.out)
    assert (
        failure["ok"] is False
        and "launch_detail" not in failure
        and "reservation_ttl" not in failure
    )
    assert not any("agent-worktrees embody" in cmd for cmd in seams.run)
    assert _FakeLock.instances[0].released is True


def test_restricted_container_is_refused(monkeypatch, capsys):
    import agent_containers.config as config_mod
    import agent_containers.resolver as resolver_mod

    target = _target(RESTRICTED_PROFILE)
    monkeypatch.setattr(config_mod, "load_config", lambda: target.config)
    monkeypatch.setattr(resolver_mod, "resolve_live_exec_target", lambda name, config=None: target)
    rc = detach.cmd_detach(
        _args(), require_live_relay_port=lambda: 1, relay_healthy=lambda p: True
    )
    assert rc == 1
    assert "restricted" in capsys.readouterr().err


def test_missing_daemon_port_fails_before_keeper(seams, monkeypatch):
    monkeypatch.setattr(venue_detached, "resolve_daemon_port", lambda: None)
    rc = detach.cmd_detach(
        _args(), require_live_relay_port=lambda: 1, relay_healthy=lambda p: True
    )
    assert rc == 1
    assert seams.keeper == []


def test_unsubmitted_seed_on_registered_session_is_delivered_over_bridge(seams, monkeypatch, capsys):
    from venue_copilot import refs as venue_refs

    unseeded = json.dumps({"ok": True, "created": True, "seed_submitted": False,
                          "bridge_seed_deferred": True, "bridge_seed_operation": "r1"})
    sent = []
    monkeypatch.setattr(venue_refs, "deliver_note", lambda sid, note, **kw: sent.append((sid, note)) or True)

    def fake_run(argv, **kwargs):
        cmd = argv[-1]
        seams.run.append(cmd)
        if "agent-worktrees embody" in cmd:
            return types.SimpleNamespace(returncode=0, stdout=unseeded, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(detach.subprocess, "run", fake_run)
    rc = detach.cmd_detach(
        _args(), require_live_relay_port=lambda: 61234, relay_healthy=lambda p: True
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["seed_delivery"] == "bridge"
    assert out["seeded"] is True
    assert sent == [("sid-42", "do the task")]
    assert "repo-1" not in seams.stop_keeper
    assert not any("tmux kill-session" in cmd for cmd in seams.run)


def test_unsubmitted_seed_without_registration_stops_keeper_and_kills_created_mux(seams, monkeypatch):
    unseeded = json.dumps({"ok": True, "created": True, "seed_submitted": False})
    monkeypatch.setattr(venue_detached, "await_claim", lambda scope, rid, timeout: None)

    def fake_run(argv, **kwargs):
        cmd = argv[-1]
        seams.run.append(cmd)
        if "agent-worktrees embody" in cmd:
            return types.SimpleNamespace(returncode=0, stdout=unseeded, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(detach.subprocess, "run", fake_run)
    rc = detach.cmd_detach(
        _args(), require_live_relay_port=lambda: 61234, relay_healthy=lambda p: True
    )
    assert rc == 1
    assert ("repo-1", "anchor-repo@repo-1") in seams.stop_keeper
    assert any("tmux kill-session" in cmd for cmd in seams.run)


def test_rejoin_does_not_start_a_second_keeper(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        forward_keeper,
        "ensure_running",
        lambda *a, **k: {"started": False, "state": {"pid": 123}},
    )
    resumed = json.dumps({"ok": True, "created": False, "resumed": True,
                         "bridge_seed_deferred": True, "bridge_seed_operation": "r1"})

    def fake_run(argv, **kwargs):
        cmd = argv[-1]
        seams.run.append(cmd)
        if "agent-worktrees embody" in cmd:
            return types.SimpleNamespace(returncode=0, stdout=resumed, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(detach.subprocess, "run", fake_run)
    rc = detach.cmd_detach(
        _args(), require_live_relay_port=lambda: 61234, relay_healthy=lambda p: True
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["resumed"] is True
    assert seams.stop_keeper == []


def test_stop_kills_mux_stops_keeper_and_deregisters(seams, capsys):
    rc = detach.cmd_stop(_args(stop=True, detach=False))
    assert rc == 0
    assert any("tmux kill-session" in cmd for cmd in seams.run)
    assert seams.stop_keeper == [("repo-1", "anchor-repo@repo-1")]
    assert seams.release == [("anchor-repo@repo-1", None)]
    assert seams.deregister == ["sid-42"]
    assert json.loads(capsys.readouterr().out)["deregistered"] == "sid-42"


def test_stop_hold_mux_error_still_stops_releases_and_deregisters(
    seams, monkeypatch, caplog
):
    monkeypatch.setattr(
        shared_keeper_holds.KeeperHoldStore,
        "hold_mux",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("pending delete")),
    )
    caplog.set_level("WARNING", logger="ssh-manager.keeper_holds")

    rc = detach.cmd_stop(_args(stop=True, detach=False))

    assert rc == 0
    assert "Could not read forward-keeper hold for repo-1/anchor-repo@repo-1" in caplog.text
    assert any("tmux kill-session" in cmd for cmd in seams.run)
    assert seams.release == [("anchor-repo@repo-1", None)]
    assert seams.deregister == ["sid-42"]


def test_stop_keeper_error_does_not_skip_release_or_deregister(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        forward_keeper,
        "stop_keeper",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lock busy")),
    )

    rc = detach.cmd_stop(_args(stop=True, detach=False))

    assert rc == 0
    assert "[WARN] could not update the forward keeper" in capsys.readouterr().err
    assert seams.release == [("anchor-repo@repo-1", None)]
    assert seams.deregister == ["sid-42"]


def test_stop_keeper_os_error_does_not_skip_release_or_deregister(
    seams, monkeypatch, capsys
):
    monkeypatch.setattr(
        forward_keeper,
        "stop_keeper",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("pending delete")),
    )

    rc = detach.cmd_stop(_args(stop=True, detach=False))

    assert rc == 0
    assert "[WARN] could not update the forward keeper" in capsys.readouterr().err
    assert seams.release == [("anchor-repo@repo-1", None)]
    assert seams.deregister == ["sid-42"]


def test_ensure_keeper_os_error_fails_launch_without_starting_session(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        forward_keeper,
        "ensure_running",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("pending delete")),
    )

    rc = detach.cmd_detach(
        _args(), require_live_relay_port=lambda: 61234, relay_healthy=lambda p: True
    )

    assert rc == 1
    assert "pending delete" in capsys.readouterr().err
    assert not any("agent-worktrees embody" in cmd for cmd in seams.run)



def test_a_no_relay_launch_keeps_the_shared_keepers_relay(tmp_path, monkeypatch):
    """A later --no-relay session must not respawn the shared keeper without the
    credential-relay forward an earlier session's hold still relies on."""
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_forward_keeper, "pid_alive", lambda pid: pid == 100)
    forward_keeper._STORE.write("repo-1", {
        "keeper_protocol": 2, "pid": 100, "mux": "wt-anchor-repo", "venue_port": 41234,
        "relay_port": 18080, "host_relay_port": 28080,
        "holds": {"anchor-repo@repo-1": {"mux": "wt-anchor-repo", "updated_at": 1000.0}},
    })
    spawned = []
    out = forward_keeper.ensure_running(
        "repo-1", venue_port=41234, mux="wt-anchor-other", hold_id="other@repo-1",
        relay_port=None, host_relay_port=None, popen=lambda *a, **k: spawned.append(a),
    )
    assert out["started"] is False and spawned == []
    state = forward_keeper.read_state("repo-1")
    assert (state["relay_port"], state["host_relay_port"]) == (18080, 28080)
    assert set(state["holds"]) == {"anchor-repo@repo-1", "other@repo-1"}


def test_forward_keeper_holds_share_one_container_forward(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_forward_keeper, "pid_alive", lambda pid: pid == 100)

    class Proc:
        pid = 200

    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "anchor-repo@repo-1": {
                    "mux": "wt-anchor-repo",
                    "updated_at": 1000.0,
                }
            },
        },
    )
    assert (
        forward_keeper.ensure_running(
            "repo-1",
            venue_port=41234,
            mux="wt-anchor-repo",
            hold_id="anchor-repo@repo-1",
        )["started"]
        is False
    )
    assert (
        forward_keeper.ensure_running(
            "repo-1",
            venue_port=41234,
            mux="wt-anchor-repo",
            hold_id="anchor-repo@repo-1",
        )["hold_added"]
        is False
    )
    out = forward_keeper.ensure_running(
        "repo-1",
        venue_port=41234,
        mux="wt-anchor-other",
        hold_id="other@repo-1",
        popen=lambda *a, **k: Proc(),
    )
    assert out["started"] is False
    assert out["hold_added"] is True
    state = forward_keeper.read_state("repo-1")
    assert state["pid"] == 100
    assert set(state["holds"]) == {"anchor-repo@repo-1", "other@repo-1"}


def test_forward_keeper_restarts_pre_upgrade_keeper_and_preserves_old_mux_hold(
    tmp_path, monkeypatch, synthetic_keeper_processes
):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_forward_keeper, "pid_alive", lambda pid: pid == 100)
    stopped = []
    monkeypatch.setattr(
        forward_keeper._STORE,
        "stop",
        lambda name: stopped.append(name) or forward_keeper._STORE.remove(name) or True,
    )

    class Proc:
        pid = 200

    forward_keeper._STORE.write(
        "repo-1",
        {
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
        },
    )

    out = forward_keeper.ensure_running(
        "repo-1",
        venue_port=41234,
        mux="wt-anchor-other",
        hold_id="other@repo-1",
        popen=lambda *a, **k: Proc(),
    )

    assert out["started"] is True
    assert stopped == ["repo-1"]
    state = forward_keeper.read_state("repo-1")
    assert state["keeper_protocol"] == 2
    assert state["pid"] == 200
    assert set(state["holds"]) == {"wt-anchor-repo", "other@repo-1"}


def test_forward_keeper_restarts_only_when_forward_changes(tmp_path, monkeypatch, synthetic_keeper_processes):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_forward_keeper, "pid_alive", lambda pid: pid == 100)
    stopped = []
    monkeypatch.setattr(
        forward_keeper._STORE,
        "stop",
        lambda name: stopped.append(name) or forward_keeper._STORE.remove(name) or True,
    )

    class Proc:
        pid = 200

    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {"anchor-repo@repo-1": {"mux": "wt-anchor-repo", "updated_at": 1000.0}},
        },
    )
    out = forward_keeper.ensure_running(
        "repo-1",
        venue_port=41235,
        mux="wt-anchor-other",
        hold_id="other@repo-1",
        popen=lambda *a, **k: Proc(),
    )
    assert out["started"] is True and stopped == ["repo-1"]
    state = forward_keeper.read_state("repo-1")
    assert state["pid"] == 200
    assert set(state["holds"]) == {"anchor-repo@repo-1", "other@repo-1"}


def test_forward_keeper_stop_releases_one_hold_then_last_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    stopped = []
    monkeypatch.setattr(
        forward_keeper._STORE,
        "stop",
        lambda name: stopped.append(name) or forward_keeper._STORE.remove(name) or True,
    )
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "anchor-repo@repo-1": {"mux": "wt-anchor-repo", "updated_at": 1000.0},
                "other@repo-1": {"mux": "wt-anchor-other", "updated_at": 1000.0},
            },
        },
    )

    assert (
        forward_keeper.stop_keeper(
            "repo-1",
            hold_id="anchor-repo@repo-1",
            mux_alive=lambda mux: mux == "wt-anchor-other",
        )
        is False
    )
    assert stopped == []
    assert set(forward_keeper.read_state("repo-1")["holds"]) == {"other@repo-1"}
    assert (
        forward_keeper.stop_keeper(
            "repo-1",
            hold_id="other@repo-1",
            mux_alive=lambda mux: False,
        )
        is True
    )
    assert stopped == ["repo-1"]
    assert forward_keeper.read_state("repo-1") is None


def test_forward_keeper_unknown_probe_keeps_old_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: 2000.0)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "old@repo-1": {
                    "mux": "wt-old",
                    "updated_at": 1000.0,
                    "confirmed_at": 1900.0,
                },
            },
        },
    )

    holds = forward_keeper.list_holds("repo-1", mux_alive=lambda mux: None)

    assert set(holds) == {"old@repo-1"}
    assert set(forward_keeper.read_state("repo-1")["holds"]) == {"old@repo-1"}


def test_forward_keeper_gone_probe_drops_recently_confirmed_old_hold(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: 2000.0)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "gone@repo-1": {
                    "mux": "wt-gone",
                    "updated_at": 1000.0,
                    "confirmed_at": 1990.0,
                },
            },
        },
    )

    holds = forward_keeper.list_holds("repo-1", mux_alive=lambda mux: False)

    assert holds == {}
    assert forward_keeper.read_state("repo-1")["holds"] == {}


def test_forward_keeper_unknown_probe_eventually_drops_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: 4000.0)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "old@repo-1": {
                    "mux": "wt-old",
                    "updated_at": 1000.0,
                    "confirmed_at": 1000.0,
                },
            },
        },
    )

    holds = forward_keeper.list_holds("repo-1", mux_alive=lambda mux: None)

    assert holds == {}
    assert forward_keeper.read_state("repo-1")["holds"] == {}


def test_forward_keeper_upgrade_confirms_legacy_mux_before_unknown_probe(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: 2000.0)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "pid": 100,
            "mux": "wt-legacy",
            "started_at": 1000.0,
            "venue_port": 41234,
        },
    )

    holds = forward_keeper.list_holds("repo-1", mux_alive=lambda mux: None)

    assert set(holds) == {"wt-legacy"}
    assert forward_keeper.read_state("repo-1")["holds"]["wt-legacy"][
        "confirmed_at"
    ] == 2000.0


def test_forward_keeper_alive_probe_refreshes_confirmed_at(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: 2000.0)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "live@repo-1": {"mux": "wt-live", "updated_at": 1000.0},
            },
        },
    )

    forward_keeper.list_holds("repo-1", mux_alive=lambda mux: True)

    assert forward_keeper.read_state("repo-1")["holds"]["live@repo-1"][
        "confirmed_at"
    ] == 2000.0


def test_forward_keeper_prune_is_compare_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: 2000.0)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "race@repo-1": {"mux": "wt-race", "updated_at": 1000.0},
            },
        },
    )

    def mux_gone(_mux):
        state = forward_keeper.read_state("repo-1")
        state["holds"]["race@repo-1"]["updated_at"] = 2000.0
        forward_keeper._STORE.write("repo-1", state)
        return False

    holds = forward_keeper.list_holds("repo-1", mux_alive=mux_gone)

    assert set(holds) == {"race@repo-1"}
    assert forward_keeper.read_state("repo-1")["holds"]["race@repo-1"]["updated_at"] == 2000.0


def test_forward_keeper_prunes_stale_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: 2000.0)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "stale@repo-1": {"mux": "wt-stale", "updated_at": 1000.0},
                "live@repo-1": {"mux": "wt-live", "updated_at": 1000.0},
            },
        },
    )

    holds = forward_keeper.list_holds(
        "repo-1",
        mux_alive=lambda mux: mux == "wt-live",
    )

    assert set(holds) == {"live@repo-1"}
    assert set(forward_keeper.read_state("repo-1")["holds"]) == {"live@repo-1"}


def test_forward_keeper_does_not_probe_while_state_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "stale@repo-1": {"mux": "wt-stale", "updated_at": 1000.0},
            },
        },
    )
    real_lock = forward_keeper._keeper_lock
    in_lock = False

    @contextlib.contextmanager
    def wrapped_lock(name):
        nonlocal in_lock
        with real_lock(name):
            in_lock = True
            try:
                yield
            finally:
                in_lock = False

    monkeypatch.setattr(forward_keeper, "_keeper_lock", wrapped_lock)

    def mux_gone(_mux):
        assert in_lock is False
        return False

    forward_keeper.list_holds("repo-1", mux_alive=mux_gone)


def test_forward_keeper_session_alive_fails_open_on_lock_timeout(monkeypatch):
    monkeypatch.setattr(
        forward_keeper,
        "_prune_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lock busy")),
    )

    assert forward_keeper._any_hold_alive("repo-1", object(), startup_grace=0) is True


def test_forward_keeper_session_alive_fails_open_on_state_os_error(monkeypatch):
    monkeypatch.setattr(
        forward_keeper,
        "_prune_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(OSError("sharing violation")),
    )

    assert forward_keeper._any_hold_alive("repo-1", object(), startup_grace=0) is True


def test_forward_keeper_restart_preserves_existing_holds(tmp_path, monkeypatch, synthetic_keeper_processes):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_forward_keeper, "pid_alive", lambda pid: False)

    class Proc:
        pid = 200

    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-anchor-repo",
            "venue_port": 41234,
            "holds": {
                "anchor-repo@repo-1": {
                    "mux": "wt-anchor-repo",
                    "updated_at": 1000.0,
                }
            },
        },
    )

    out = forward_keeper.ensure_running(
        "repo-1",
        venue_port=41234,
        mux="wt-anchor-other",
        hold_id="other@repo-1",
        popen=lambda *a, **k: Proc(),
    )

    assert out["started"] is True
    state = forward_keeper.read_state("repo-1")
    assert state["pid"] == 200
    assert set(state["holds"]) == {"anchor-repo@repo-1", "other@repo-1"}


def test_forward_keeper_self_prune_removes_retiring_state(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: 2000.0)
    monkeypatch.setattr(forward_keeper.os, "getpid", lambda: 100)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-old",
            "venue_port": 41234,
            "holds": {
                "old@repo-1": {"mux": "wt-old", "updated_at": 1000.0},
            },
        },
    )

    _state, holds, _live_muxes = forward_keeper._prune_snapshot(
        "repo-1",
        mux_alive=lambda mux: False,
        startup_grace=0,
    )

    assert holds == {}
    assert forward_keeper.read_state("repo-1") is None


def test_forward_keeper_empty_self_state_is_removed_before_reuse_window(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(forward_keeper.os, "getpid", lambda: 100)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-old",
            "venue_port": 41234,
            "holds": {},
        },
    )

    _state, holds, _live_muxes = forward_keeper._prune_snapshot(
        "repo-1",
        mux_alive=lambda mux: (_ for _ in ()).throw(AssertionError("no probe")),
        startup_grace=0,
    )

    assert holds == {}
    assert forward_keeper.read_state("repo-1") is None


def test_forward_keeper_self_remove_preserves_new_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(forward_keeper.os, "getpid", lambda: 100)
    forward_keeper._STORE.write(
        "repo-1",
        {
            "keeper_protocol": 2,
            "pid": 100,
            "mux": "wt-new",
            "venue_port": 41234,
            "holds": {
                "new@repo-1": {"mux": "wt-new", "updated_at": 2000.0},
            },
        },
    )

    forward_keeper._remove_self_state("repo-1")

    assert set(forward_keeper.read_state("repo-1")["holds"]) == {"new@repo-1"}


def test_forward_keeper_lock_uses_persistent_os_lock_file(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    lock = forward_keeper.state_path("repo-1").with_suffix(".lock")

    with forward_keeper._keeper_lock("repo-1"):
        assert json.loads(lock.read_text(encoding="utf-8"))["pid"]

    assert lock.exists()


def test_forward_keeper_lock_treats_permission_error_as_contention(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(forward_keeper, "_LOCK_POLL", 0.0)
    attempts = 0
    real_acquire = shared_keeper_holds.KeeperHoldStore.acquire_os_lock

    def flaky_acquire(self, handle):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("pending delete")
        return real_acquire(self, handle)

    monkeypatch.setattr(shared_keeper_holds.KeeperHoldStore, "acquire_os_lock", flaky_acquire)

    with forward_keeper._keeper_lock("repo-1"):
        pass

    assert attempts == 2


def test_forward_keeper_live_lock_owner_is_not_stolen(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(forward_keeper, "_LOCK_TIMEOUT", 0.0)
    monkeypatch.setattr(forward_keeper, "_LOCK_POLL", 0.0)
    monkeypatch.setattr(
        shared_keeper_holds.KeeperHoldStore,
        "acquire_os_lock",
        lambda _self, _handle: (_ for _ in ()).throw(PermissionError("lock held")),
    )

    with pytest.raises(RuntimeError, match="Could not acquire"):
        with forward_keeper._keeper_lock("repo-1"):
            pass


def test_forward_keeper_exits_when_mux_is_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target", lambda name: _target()
    )
    monkeypatch.setattr(
        "agent_containers.ssh_transport.prepare_ssh_config", lambda name, user: object()
    )
    monkeypatch.setattr(forward_keeper, "_mux_exists", lambda cfg, mux: False)

    class Fwd:
        started = 0
        stopped = 0

        def __init__(self, *a, **k):
            pass

        async def start(self):
            Fwd.started += 1

        async def stop(self):
            Fwd.stopped += 1

    monkeypatch.setattr(forward_keeper, "SupervisedRelayForward", Fwd)
    rc = forward_keeper.cmd_forward_keeper(
        argparse.Namespace(
            name="repo-1",
            venue_port=41234,
            mux="wt-anchor-repo",
            hold_id="anchor-repo@repo-1",
            relay_port=None,
            host_relay_port=None,
            probe_interval=1,
            startup_grace=0,
        )
    )
    assert rc == 0
    assert Fwd.started == 1 and Fwd.stopped == 1
    assert forward_keeper.read_state("repo-1") is None


def test_dry_run_names_ref_files_and_a_missing_one_fails(seams, tmp_path, capsys):
    har = tmp_path / "trace.har"
    har.write_text("{}")
    assert detach.cmd_detach(_args(dry_run=True, ref_files=[str(har)]), **_RELAY) == 0
    assert json.loads(capsys.readouterr().out)["ref_files"] == ["trace.har"]
    assert (
        detach.cmd_detach(_args(dry_run=True, ref_files=[str(tmp_path / "nope.har")]), **_RELAY)
        == 1
    )
    assert "reference file not found" in capsys.readouterr().err


_RELAY = {"require_live_relay_port": lambda: 61234, "relay_healthy": lambda p: True}


def test_detached_session_mirrors_the_callers_model(monkeypatch):
    seen = []
    monkeypatch.setattr(
        detach,
        "model_copilot_args",
        lambda existing: seen.append(list(existing)) or ["--model=example-model"],
    )
    assert detach._with_caller_model(["--no-ask-user"]) == [
        "--no-ask-user",
        "--model=example-model",
    ]
    assert seen == [["--no-ask-user"]]
