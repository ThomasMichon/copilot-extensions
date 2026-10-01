from __future__ import annotations

import argparse
import asyncio
import json
import types
from pathlib import Path

import pytest
from venue_copilot import detached as venue_detached

from agent_ssh import copilot_detach as detach
from agent_ssh.__main__ import main


def _args(**kw):
    base = dict(
        target="devbox",
        workspace="/workspaces/repo",
        seed="do the task",
        seed_file=None,
        copilot_args=["--no-ask-user"],
        driver="orchestrator",
        register_timeout=0.0,
        dry_run=False,
        detach=True,
        stop=False,
        ttl_seconds=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def seams(monkeypatch):
    # Hermetic: never read the developer's own ~/.copilot/settings.json model.
    monkeypatch.setattr(detach, "with_supervisor", lambda venue, ref=None: dict(venue))
    monkeypatch.setattr(detach, "model_copilot_args", lambda existing: [])
    calls = types.SimpleNamespace(
        remote=[],
        reserve=[],
        release=[],
        keeper=[],
        stop_keeper=[],
        deregister=[],
    )
    monkeypatch.setattr(detach, "_ssh_config", lambda target: object())
    monkeypatch.setattr(venue_detached, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(venue_detached, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(
        venue_detached,
        "reserve_with_retry",
        lambda scope, venue, **kw: calls.reserve.append((scope, venue, kw)) or {"reservation_id": "r1"},
    )
    monkeypatch.setattr(venue_detached, "await_claim", lambda scope, rid, timeout: "sid-42")
    monkeypatch.setattr(
        venue_detached,
        "release_cli_mode",
        lambda scope, reservation_id=None: calls.release.append((scope, reservation_id)) or 1,
    )
    monkeypatch.setattr(
        detach,
        "ensure_keeper",
        lambda *a, **k: calls.keeper.append((a, k)) or {"started": True, "state": {"pid": 123}},
    )
    monkeypatch.setattr(detach, "stop_keeper", lambda target: calls.stop_keeper.append(target) or True)
    monkeypatch.setattr(detach, "read_keeper_state", lambda target: None)
    monkeypatch.setattr(
        "venue_copilot.live_session_for",
        lambda handle: {"session_id": "sid-42", "venue": {"target": "devbox"}},
    )
    monkeypatch.setattr(
        "venue_copilot.detached.deregister_live_session",
        lambda sid: calls.deregister.append(sid) or True,
    )
    created = json.dumps({
        "ok": True,
        "created": True,
        "resumed": False,
        "seed_submitted": True,
        "session": "wt-anchor-repo",
    })

    def remote(_cfg, command, *, timeout=60.0):
        calls.remote.append(command)
        if "printf posix" in command:
            return 0, "posix", ""
        if "command -v bash" in command:
            return 0, "", ""
        if "curl -fsS" in command:
            return 0, "", ""
        if "agent-worktrees embody" in command:
            return 0, created, ""
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    return calls


def test_detach_success_reserves_ssh_venue_and_reports_handle(seams, capsys):
    rc = detach.cmd_detach(_args())

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["session_id"] == "sid-42"
    assert out["scope_id"] == "anchor-repo@devbox"
    assert out["venue"] == {
        "kind": "ssh",
        "target": "devbox",
        "mux_session_name": "wt-anchor-repo",
    }
    assert out["commands"]["attach"] == "ssh -t devbox tmux attach -t wt-anchor-repo"
    assert "agent-ssh copilot devbox --stop --workspace /workspaces/repo" == out["commands"]["stop"]
    assert seams.reserve[0][0] == "anchor-repo@devbox"
    assert seams.reserve[0][1]["kind"] == "ssh"
    assert seams.keeper[0][1] == {"venue_port": 41234, "mux": "wt-anchor-repo"}
    launch = next(command for command in seams.remote if "agent-worktrees embody" in command)
    assert "cd /workspaces/repo" in launch
    assert "--bridge-scope-id anchor-repo@devbox" in launch
    assert "--copilot-arg=--no-ask-user" in launch
    assert "--json" in launch
    assert seams.release == [("anchor-repo@devbox", "r1")]


def test_attached_default_uses_venue_copilot_over_ssh(seams, monkeypatch):
    monkeypatch.setattr(
        detach,
        "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None,
            port=None,
            identity_file=None,
            extra_options={},
            ssh_target=target,
        ),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(detach, "resolve_local_auth_token", lambda: "tok")
    seen = {}

    def fake_run_venue(identity, *, connect, **kwargs):
        seen["identity"] = identity
        seen["kwargs"] = kwargs
        return connect("agent-worktrees copilot --anchor")

    class FakeProcess:
        pid = 999

        def wait(self):
            return 0

    def fake_popen(argv, **kwargs):
        seen["ssh_argv"] = argv
        seen["subprocess_kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(detach, "run_venue_copilot", fake_run_venue)
    monkeypatch.setattr(detach.subprocess, "Popen", fake_popen)

    rc = detach.cmd_attached(
        _args(detach=False, workspace="/workspaces/repo", copilot_args=[]),
    )

    assert rc == 0
    assert seen["identity"] == "anchor-repo"
    assert seen["kwargs"] == {
        "anchor": True,
        "ttl_seconds": 300.0,
        "driver": "orchestrator",
        "seed": "do the task",
        "ensure_mux": True,
    }
    argv = seen["ssh_argv"]
    assert "-t" in argv
    assert ["-R", "41234:127.0.0.1:41234"] == argv[argv.index("-R") : argv.index("-R") + 2]
    remote_command = argv[-1]
    assert remote_command.startswith("bash -lc ")
    assert "cd /workspaces/repo" in remote_command
    assert "trustedFolders" in remote_command
    assert "agent-worktrees copilot --anchor" in remote_command
    assert remote_command.index("cd /workspaces/repo") < remote_command.index(
        "agent-worktrees copilot --anchor"
    )
    assert "auth.yaml" not in remote_command
    assert any("auth.yaml" in command for command in seams.remote)


def test_attached_skips_reverse_forward_when_live_keeper_holds_route(seams, monkeypatch):
    monkeypatch.setattr(
        detach,
        "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None,
            port=None,
            identity_file=None,
            extra_options={},
            ssh_target=target,
        ),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(detach, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(detach, "read_keeper_state", lambda target: {"venue_port": 41234})
    monkeypatch.setattr(detach._STORE, "alive", lambda key: True)
    seen = {}

    monkeypatch.setattr(
        detach,
        "run_venue_copilot",
        lambda _identity, *, connect, **_kwargs: connect("agent-worktrees copilot --anchor"),
    )

    class FakeProcess:
        pid = 999

        def wait(self):
            return 0

    def fake_popen(argv, **kwargs):
        seen["ssh_argv"] = argv
        return FakeProcess()

    monkeypatch.setattr(detach.subprocess, "Popen", fake_popen)

    assert detach.cmd_attached(_args(detach=False, workspace="/workspaces/repo", copilot_args=[])) == 0
    assert "-R" not in seen["ssh_argv"]


def test_attached_skips_reverse_forward_when_live_attached_owner_holds_route(
    seams, monkeypatch,
):
    monkeypatch.setattr(
        detach,
        "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None,
            port=None,
            identity_file=None,
            extra_options={},
            ssh_target=target,
        ),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(detach, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(
        detach,
        "read_keeper_state",
        lambda target: {
            "mode": "attached",
            "pid": 111,
            "pid_identity": "id-111",
            "venue_port": 41234,
        },
    )
    monkeypatch.setattr(detach._STORE, "alive", lambda key: True)
    seen = {}

    class FakeProcess:
        pid = 999

        def wait(self):
            return 0

    monkeypatch.setattr(
        detach,
        "run_venue_copilot",
        lambda _identity, *, connect, **_kwargs: connect("agent-worktrees copilot --anchor"),
    )
    def fake_popen(argv, **_kwargs):
        seen["ssh_argv"] = argv
        return FakeProcess()

    monkeypatch.setattr(detach.subprocess, "Popen", fake_popen)

    assert detach.cmd_attached(_args(detach=False, workspace="/workspaces/repo", copilot_args=[])) == 0
    assert "-R" not in seen["ssh_argv"]


def test_attached_adds_reverse_forward_when_attached_owner_state_is_stale(
    seams, monkeypatch,
):
    monkeypatch.setattr(
        detach,
        "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None,
            port=None,
            identity_file=None,
            extra_options={},
            ssh_target=target,
        ),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(detach, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(detach, "read_keeper_state", lambda target: {"mode": "attached", "venue_port": 41234})
    monkeypatch.setattr(detach._STORE, "alive", lambda key: False)
    seen = {}

    class FakeProcess:
        pid = 999

        def wait(self):
            return 0

    monkeypatch.setattr(
        detach,
        "run_venue_copilot",
        lambda _identity, *, connect, **_kwargs: connect("agent-worktrees copilot --anchor"),
    )
    def fake_popen(argv, **_kwargs):
        seen["ssh_argv"] = argv
        return FakeProcess()

    monkeypatch.setattr(detach.subprocess, "Popen", fake_popen)

    assert detach.cmd_attached(_args(detach=False, workspace="/workspaces/repo", copilot_args=[])) == 0
    argv = seen["ssh_argv"]
    assert ["-R", "41234:127.0.0.1:41234"] == argv[argv.index("-R") : argv.index("-R") + 2]


def test_attached_records_route_owner_for_foreground_ssh_lifetime(
    tmp_path: Path, monkeypatch,
):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(detach, "process_identity", lambda pid: f"id-{pid}")
    monkeypatch.setattr(
        detach,
        "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None,
            port=None,
            identity_file=None,
            extra_options={},
            ssh_target=target,
        ),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(detach, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(detach, "_remote", lambda *_args, **_kwargs: (0, "", ""))
    monkeypatch.setattr(
        detach,
        "run_venue_copilot",
        lambda _identity, *, connect, **_kwargs: connect("agent-worktrees copilot --anchor"),
    )

    class FakeProcess:
        pid = 999

        def wait(self):
            state = detach.read_keeper_state("devbox")
            assert state is not None
            assert state["mode"] == "attached"
            assert state["pid"] == 999
            assert state["pid_identity"] == "id-999"
            assert state["venue_port"] == 41234
            return 0

    monkeypatch.setattr(detach.subprocess, "Popen", lambda _argv, **_kwargs: FakeProcess())

    assert detach.cmd_attached(_args(detach=False, workspace="/workspaces/repo", copilot_args=[])) == 0
    assert detach.read_keeper_state("devbox") is None


def test_attached_keeps_reverse_forward_when_only_stray_local_daemon_answers(
    seams, monkeypatch,
):
    monkeypatch.setattr(
        detach,
        "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None,
            port=None,
            identity_file=None,
            extra_options={},
            ssh_target=target,
        ),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(detach, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(detach, "read_keeper_state", lambda target: None)
    seen = {}

    def remote(_cfg, command, *, timeout=60.0):
        seams.remote.append(command)
        if "curl -fsS" in command:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    monkeypatch.setattr(
        detach,
        "run_venue_copilot",
        lambda _identity, *, connect, **_kwargs: connect("agent-worktrees copilot --anchor"),
    )

    class FakeProcess:
        pid = 999

        def wait(self):
            return 0

    def fake_popen(argv, **kwargs):
        seen["ssh_argv"] = argv
        return FakeProcess()

    monkeypatch.setattr(detach.subprocess, "Popen", fake_popen)

    assert detach.cmd_attached(_args(detach=False, workspace="/workspaces/repo", copilot_args=[])) == 0
    argv = seen["ssh_argv"]
    assert ["-R", "41234:127.0.0.1:41234"] == argv[argv.index("-R") : argv.index("-R") + 2]
    assert not any("curl -fsS" in command for command in seams.remote)


def test_workspace_resolution_order_prefers_explicit_then_host_config(tmp_path: Path):
    config = tmp_path / "copilot-hosts.json"
    detach.set_host_workspace("devbox", "/workspaces/configured", config)

    assert detach.resolve_workspace(
        _args(workspace="/workspaces/explicit"),
    ) == "/workspaces/explicit"
    assert (
        detach._workspace_from_host_config("DEVBOX", config)
        == "/workspaces/configured"
    )


def test_copilot_config_set_writes_host_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(detach, "_copilot_config_path", lambda: tmp_path / "copilot-hosts.json")

    assert main(["copilot-config", "set", "devbox", "--workspace", "/workspaces/repo"]) == 0

    assert detach._workspace_from_host_config("devbox", tmp_path / "copilot-hosts.json") == (
        "/workspaces/repo"
    )


def test_copilot_config_set_canonicalizes_host_alias_casing(tmp_path: Path):
    config = tmp_path / "copilot-hosts.json"

    detach.set_host_workspace("DevBox", "/workspaces/old", config)
    detach.set_host_workspace("devbox", "/workspaces/new", config)

    raw = json.loads(config.read_text(encoding="utf-8"))
    assert raw["hosts"] == {"devbox": {"workspace": "/workspaces/new"}}
    assert detach._workspace_from_host_config("DEVBOX", config) == "/workspaces/new"


def test_copilot_config_set_refuses_corrupt_existing_file(tmp_path: Path):
    config = tmp_path / "copilot-hosts.json"
    config.write_text('{"hosts": {', encoding="utf-8")

    with pytest.raises(detach.CopilotConfigError) as exc:
        detach.set_host_workspace("devbox", "/workspaces/new", config)

    text = str(exc.value)
    assert str(config) in text
    assert "not valid JSON" in text
    assert config.read_text(encoding="utf-8") == '{"hosts": {'


def test_resolve_workspace_reports_corrupt_copilot_config(tmp_path: Path, monkeypatch):
    config = tmp_path / "copilot-hosts.json"
    config.write_text('{"hosts": {', encoding="utf-8")
    monkeypatch.setattr(detach, "_copilot_config_path", lambda: config)

    with pytest.raises(detach.CopilotConfigError) as exc:
        detach.resolve_workspace(_args(workspace=None))

    assert str(config) in str(exc.value)
    assert "not valid JSON" in str(exc.value)


def test_copilot_config_cli_refuses_corrupt_existing_file(tmp_path: Path, monkeypatch, capsys):
    config = tmp_path / "copilot-hosts.json"
    config.write_text('{"hosts": {', encoding="utf-8")
    monkeypatch.setattr(detach, "_copilot_config_path", lambda: config)

    rc = main(["copilot-config", "set", "devbox", "--workspace", "/workspaces/new"])

    assert rc == 2
    err = capsys.readouterr().err
    assert str(config) in err
    assert "not valid JSON" in err
    assert config.read_text(encoding="utf-8") == '{"hosts": {'


def test_missing_workspace_error_names_configuration_options(monkeypatch):
    monkeypatch.setattr(detach, "_workspace_from_host_config", lambda target: None)

    with pytest.raises(ValueError) as exc:
        detach.resolve_workspace(_args(workspace=None))

    text = str(exc.value)
    assert "--workspace /path/to/checkout" in text
    assert "agent-ssh copilot-config set" in text


def test_dry_run_without_workspace_fails_before_remote_ssh(seams, monkeypatch):
    calls = []
    monkeypatch.setattr(detach, "_workspace_from_host_config", lambda target: None)
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: calls.append("posix"))

    rc = detach.cmd_detach(_args(workspace=None, dry_run=True))

    assert rc == 1
    assert calls == []
    assert seams.remote == []


def test_non_dry_run_checks_posix_before_missing_workspace(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach, "_workspace_from_host_config", lambda target: None)
    monkeypatch.setattr(
        detach,
        "_ensure_posix",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("Windows SSH targets are not supported yet")),
    )

    rc = detach.cmd_detach(_args(workspace=None))

    assert rc == 1
    assert "Windows SSH targets are not supported yet" in capsys.readouterr().err


def test_ttl_seconds_with_detach_is_usage_error(seams, capsys):
    rc = detach.cmd_detach(_args(ttl_seconds=30.0))

    assert rc == 2
    assert "--ttl-seconds applies only to attached mode" in capsys.readouterr().err
    assert seams.reserve == []


def test_non_posix_target_is_refused(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach, "_remote", lambda *a, **k: (1, "", "not powershell syntax"))
    rc = detach.cmd_detach(_args())
    assert rc == 1
    assert "Windows SSH targets are not supported yet" in capsys.readouterr().err
    assert seams.reserve == []


def test_missing_agent_worktrees_message(seams, monkeypatch, capsys):
    def remote(_cfg, command, *, timeout=60.0):
        if "printf posix" in command or "command -v bash" in command or "curl -fsS" in command:
            return 0, "", ""
        if "agent-worktrees embody" in command:
            return 127, "", "agent-worktrees: command not found"
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    rc = detach.cmd_detach(_args())
    assert rc == 1
    assert "agent-worktrees is not installed on the SSH target" in capsys.readouterr().err
    assert seams.stop_keeper == ["devbox"]


def test_launch_failure_cleanup_kills_mux_and_stops_keeper(seams, monkeypatch):
    unseeded = json.dumps({"ok": True, "created": True, "seed_submitted": False})

    def remote(_cfg, command, *, timeout=60.0):
        seams.remote.append(command)
        if "agent-worktrees embody" in command:
            return 0, unseeded, ""
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    assert detach.cmd_detach(_args()) == 1
    assert any("tmux kill-session" in command for command in seams.remote)
    assert seams.stop_keeper == ["devbox"]


def test_rejoin_reuses_keeper_and_does_not_stop_it(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        detach,
        "ensure_keeper",
        lambda *a, **k: {"started": False, "state": {"pid": 123}},
    )
    resumed = json.dumps({"ok": True, "created": False, "resumed": True})

    def remote(_cfg, command, *, timeout=60.0):
        if "agent-worktrees embody" in command:
            return 0, resumed, ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    assert detach.cmd_detach(_args()) == 0
    assert json.loads(capsys.readouterr().out)["resumed"] is True
    assert seams.stop_keeper == []


def test_stop_kills_mux_stops_keeper_and_deregisters(seams, capsys):
    rc = detach.cmd_stop(_args(stop=True, detach=False))
    assert rc == 0
    assert any("tmux kill-session" in command for command in seams.remote)
    assert seams.stop_keeper == ["devbox"]
    assert seams.release == [("anchor-repo@devbox", None)]
    assert seams.deregister == ["sid-42"]
    assert json.loads(capsys.readouterr().out)["deregistered"] == "sid-42"


def test_dry_run_names_ref_files_and_a_missing_one_fails(tmp_path, capsys):
    har = tmp_path / "trace.har"
    har.write_text("{}")
    assert detach.cmd_detach(_args(dry_run=True, ref_files=[str(har)])) == 0
    assert json.loads(capsys.readouterr().out)["ref_files"] == ["trace.har"]
    assert detach.cmd_detach(_args(dry_run=True, ref_files=[str(tmp_path / "nope.har")])) == 1
    assert "reference file not found" in capsys.readouterr().err


def test_detached_session_mirrors_the_callers_model(monkeypatch):
    seen = []
    monkeypatch.setattr(detach, "model_copilot_args", lambda existing: seen.append(list(existing)) or ["--model=example-model"])
    assert detach._with_caller_model(["--no-ask-user"]) == ["--no-ask-user", "--model=example-model"]
    assert seen == [["--no-ask-user"]]


def test_forward_keeper_rewrites_state_when_relay_pid_changes(tmp_path, monkeypatch):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(detach, "_ssh_config", lambda target: object())
    monkeypatch.setenv(detach._KEEPER_TOKEN_ENV, "tok")

    class Forward:
        def __init__(self, *_args, on_pid_change=None, **_kwargs):
            self._pid = None
            self._on_pid_change = on_pid_change

        @property
        def process_pid(self):
            return self._pid

        @property
        def process_birth_identity(self):
            return None if self._pid is None else f"id-{self._pid}"

        async def start(self):
            self._pid = 111
            assert self._on_pid_change is not None
            self._on_pid_change()

        def restart(self):
            self._pid = 222
            assert self._on_pid_change is not None
            self._on_pid_change()

        async def stop(self):
            return None

    async def fake_loop(forwards, **kwargs):
        kwargs["write_state"]()
        await forwards[0].start()
        kwargs["write_state"]()
        forwards[0].restart()
        return 0

    monkeypatch.setattr(detach, "SupervisedRelayForward", Forward)
    monkeypatch.setattr(detach, "run_supervised_loop", fake_loop)

    args = argparse.Namespace(
        target="devbox",
        venue_port=41234,
        mux="wt-anchor-repo",
        probe_interval=15.0,
        startup_grace=300.0,
    )

    assert asyncio.run(detach._run_forward_keeper(args)) == 0
    assert store.read("devbox")["children"] == [{"pid": 222, "identity": "id-222"}]


def test_keeper_state_token_fences_overlapping_launches(tmp_path, monkeypatch):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)

    first = {
        "pid": 101,
        "pid_identity": "keep-101",
        "target": "devbox",
        "venue_port": 41234,
        "mux": "wt-a",
        "instance_token": "tok-a",
        "children": [],
    }
    second = {
        "pid": 202,
        "pid_identity": "keep-202",
        "target": "devbox",
        "venue_port": 41234,
        "mux": "wt-b",
        "instance_token": "tok-b",
        "children": [],
    }

    detach._write_keeper_state("devbox", first)
    detach._write_keeper_state("devbox", second)

    detach._write_keeper_state(
        "devbox",
        {**first, "children": [{"pid": 111, "identity": "id-111"}]},
    )
    detach._remove_keeper_state("devbox", "tok-a")

    detach._write_keeper_state(
        "devbox",
        {**second, "children": [{"pid": 222, "identity": "id-222"}]},
    )

    assert store.read("devbox") == {
        **second,
        "children": [{"pid": 222, "identity": "id-222"}],
    }


def test_state_lock_path_uses_sanitized_keeper_state_path(tmp_path, monkeypatch):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)

    path = detach._state_lock_path("codespace:repo/branch")

    assert path.parent == tmp_path
    assert path.name == "codespace-repo-branch.lock"
