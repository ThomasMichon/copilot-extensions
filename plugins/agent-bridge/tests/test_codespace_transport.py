"""Tests for the codespace-boundary transport (gh cp + ssh_manager exec)."""

from __future__ import annotations

import pytest

from agent_bridge.session_host.codespace_transport import CodeSpaceTransport
from ssh_manager import SSHConfig


class _FakeResult:
    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class _FakeManager:
    def __init__(self, results=None):
        self.connected = []
        self.commands = []
        self._results = results or {}

    async def ensure_connected(self, name, source, forwards):
        self.connected.append(name)

    async def exec_command(self, name, command, timeout=60.0):
        self.commands.append(command)
        for needle, res in self._results.items():
            if needle in command:
                return res
        return _FakeResult(0, "", "")


class _FakeSource:
    def get_ssh_config(self):
        return SSHConfig(host_alias="cs.box", user="vscode",
                         config_file="/tmp/cs.config")


def _transport(**kw):
    return CodeSpaceTransport(
        "cs-foo", "org/repo", manager=_FakeManager(kw.pop("results", None)),
        source=_FakeSource(), **kw,
    )


@pytest.mark.asyncio
async def test_run_maps_result():
    t = _transport(results={"echo hi": _FakeResult(0, "hi", "")})
    rc, out, err = await t.run("echo hi")
    assert (rc, out) == (0, "hi")
    assert t._manager.connected == ["cs-foo"]  # connected once


@pytest.mark.asyncio
async def test_path_exists_true_false():
    t_yes = _transport(results={"test -f": _FakeResult(0, "__EXISTS__\n", "")})
    assert await t_yes.path_exists("/tmp/x") is True
    t_no = _transport(results={"test -f": _FakeResult(0, "", "")})
    assert await t_no.path_exists("/tmp/x") is False


@pytest.mark.asyncio
async def test_push_file_mkdirs_and_cps(monkeypatch):
    t = _transport()
    calls = {}

    class _P:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def fake_spawn(*args, **kw):
        calls["args"] = list(args)
        return _P()

    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    await t.push_file("C:/local/session-host.pyz", "/tmp/agent-bridge/sh.pyz")
    # mkdir -p ran for the parent dir
    assert any("mkdir -p" in c for c in t._manager.commands)
    # gh codespace cp invoked with -e (expand) + remote: prefix
    assert calls["args"][:3] == ["gh", "codespace", "cp"]
    assert "-e" in calls["args"]
    assert calls["args"][-1] == "remote:/tmp/agent-bridge/sh.pyz"


@pytest.mark.asyncio
async def test_push_file_raises_on_gh_failure(monkeypatch):
    t = _transport()

    class _P:
        returncode = 1

        async def communicate(self):
            return (b"", b"no space left")

    async def fake_spawn(*args, **kw):
        return _P()

    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    with pytest.raises(RuntimeError, match="gh codespace cp failed"):
        await t.push_file("x", "/tmp/y")


def test_reverse_forwards_and_extra():
    t = _transport(relay_port=51234)
    assert t.reverse_forwards() == ["51234:127.0.0.1:51234"]
    assert t.endpoint_extra() == {"codespace": "cs-foo", "repo": "org/repo"}
    t2 = _transport()
    assert t2.reverse_forwards() == []
    assert t2.ssh_config().host_alias == "cs.box"


def test_parse_codespace_target_recognizes_shape():
    from agent_bridge.session_host.codespace_transport import parse_codespace_target

    cmd = [
        "python", "-m", "agent_codespaces", "ssh", "--stdio",
        "plugin-propagation-test-v467", "--repo", "example-org/example-web-codespaces",
        "--remote-cmd", "cd /workspaces/example-web && copilot --acp --stdio",
    ]
    parsed = parse_codespace_target(cmd)
    assert parsed["name"] == "plugin-propagation-test-v467"
    assert parsed["repo"] == "example-org/example-web-codespaces"
    assert parsed["acp_command"].startswith("cd /workspaces/example-web")
    # The ACP session cwd is parsed from the leading `cd` so copilot's tools run
    # in the workspace checkout, not /home/<user> (regression: agent ran blind).
    assert parsed["workspace_folder"] == "/workspaces/example-web"


def test_workspace_folder_from_acp_command():
    from agent_bridge.session_host.codespace_transport import (
        workspace_folder_from_acp_command,
    )

    assert (
        workspace_folder_from_acp_command("cd /workspaces/example-web && copilot --acp")
        == "/workspaces/example-web"
    )
    # Env-expanded fallback (no static path) -> None, so the caller keeps its
    # default cwd rather than passing an unresolved shell expression to ACP.
    assert workspace_folder_from_acp_command(
        'cd "${CODESPACE_VSCODE_FOLDER:-.}" && copilot --acp'
    ) is None
    # No cd prefix at all.
    assert workspace_folder_from_acp_command("copilot --acp --stdio") is None
    assert workspace_folder_from_acp_command("") is None


def test_parse_codespace_target_rejects_non_codespace():
    from agent_bridge.session_host.codespace_transport import parse_codespace_target

    assert parse_codespace_target([]) is None
    assert parse_codespace_target(["copilot", "--acp", "--stdio"]) is None
    # agent_codespaces but not an stdio launch (e.g. a diagnostic remote-cmd)
    assert parse_codespace_target(
        ["python", "-m", "agent_codespaces", "ssh", "cs-x", "--remote-cmd", "ls"]
    ) is None


def test_spawn_target_codespace_metadata_roundtrips():
    """SpawnTarget carries the structured codespace block through DB JSON (#177)."""
    from agent_bridge.transport import SpawnTarget

    meta = {"name": "cs-foo", "repo": "org/repo",
            "acp_command": "cd /workspaces/x && copilot --acp --stdio",
            "workspace_folder": "/workspaces/x"}
    t = SpawnTarget(type="command", spawn_command=["x"], codespace=meta)
    restored = SpawnTarget.from_json(t.to_json())
    assert restored.codespace == meta
    # legacy JSON without the field deserializes to None (back-compat)
    legacy = SpawnTarget.from_json('{"type": "command", "spawn_command": ["x"]}')
    assert legacy.codespace is None


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["session_start.py", "session_host_recovery.py"])
@pytest.mark.parametrize("clone_fails", [False, True])
async def test_both_child_callbacks_provision_empty_checkout_before_preparation(
    filename, clone_fails, tmp_path,
):
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    from agent_bridge.session_host.codespace_transport import codespace_child_preparer

    checkout = tmp_path / "initially-absent"
    events = ["relay-ready"]

    async def run(command, **kwargs):
        if "git -C" in command:
            assert (checkout / ".git").is_dir()
            events.append("validate")
            return 0, "", ""
        assert not checkout.exists()
        assert "SYNTHETIC_RELAY;" in command and "PROVIDER_CLONE" in command
        events.append("clone")
        if clone_fails:
            return 1, "", "failed"
        (checkout / ".git").mkdir(parents=True)
        return 0, "", ""

    async def prepare(*a, **kw):
        assert (checkout / ".git").is_dir()
        events.append("install-copy-settings")
        return ["/prepared/sample"]

    source = Path(__file__).resolve().parents[1] / "src" / "agent_bridge" / filename
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "codespace_child_preparer"
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
    )
    callback = codespace_child_preparer(
        {"workspace_folder": str(checkout), "acp_command": "copilot --acp",
         "checkout_provision_command": "PROVIDER_CLONE"},
        SimpleNamespace(run=run), prelude="SYNTHETIC_RELAY;", controller_cwd="/controller",
        prepare_plugins=prepare,
        append_plugin_dirs=lambda command, dirs: command + " --plugin-dir=" + dirs[0],
    )
    if clone_fails:
        with pytest.raises(RuntimeError, match="checkout provisioning failed"):
            await callback([])
        assert events == ["relay-ready", "clone"]
    else:
        argv = await callback([])
        events.append("child")
        assert "--plugin-dir=/prepared/sample" in argv[-1]
        assert events == ["relay-ready", "clone", "validate", "install-copy-settings", "child"]


@pytest.mark.asyncio
async def test_existing_checkout_needs_no_provider_clone():
    from unittest.mock import AsyncMock
    from agent_bridge.session_host.codespace_transport import provision_execution_checkout
    transport = AsyncMock()
    await provision_execution_checkout({"workspace_folder": "/already-existing"}, transport)
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_clone_prefix_requires_provider_data_not_shell_execution():
    from unittest.mock import AsyncMock
    from agent_bridge.session_host.codespace_transport import resolve_execution_workspace
    transport = AsyncMock()
    target = {"acp_command": "[ -d /target/.git ] || BAD_COMMAND; cd /target && copilot"}
    with pytest.raises(RuntimeError, match="fresh provider metadata"):
        await resolve_execution_workspace(target, transport)
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_checkout_metadata_refresh_uses_only_provider_argv(monkeypatch):
    import json
    from unittest.mock import AsyncMock
    from agent_bridge.session_host.codespace_transport import resolve_execution_workspace

    command = "[ -d /legacy/.git ] || DO_NOT_EXECUTE; cd /legacy && copilot"
    target = {
        "name": "synthetic", "repo": "example/requested", "repo_remote": "https://example.test/repo",
        "provider_command": ["provider"], "acp_command": command,
    }
    metadata = {
        "name": "synthetic", "workspace_folder": "/resolved",
        "acp_command": "cd /resolved && copilot",
        "checkout_provision_command": "PROVIDER_CLONE",
    }

    class Process:
        returncode = 0

        async def communicate(self):
            return json.dumps({"codespace": metadata}).encode(), b""

    spawn = AsyncMock(return_value=Process())
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.asyncio.create_subprocess_exec", spawn,
    )
    transport = AsyncMock()
    assert await resolve_execution_workspace(target, transport) == "/resolved"
    assert spawn.await_args.args == (
        "provider", "namespace-resolve", "synthetic", "--repo", "example/requested",
        "--repo-remote", "https://example.test/repo",
    )
    assert target["checkout_provision_command"] == "PROVIDER_CLONE"
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_retained_workspace_resolution_does_not_refresh_a_live_rejoin(monkeypatch):
    from unittest.mock import AsyncMock
    from agent_bridge.session_host.codespace_transport import resolve_execution_workspace
    refresh = AsyncMock(side_effect=AssertionError("must wait for prepare_child"))
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport._refresh_checkout_metadata", refresh,
    )
    target = {
        "workspace_folder": "/retained",
        "acp_command": "[ -e /retained/.git ] || PROVIDER_CLONE; cd /retained && copilot",
    }
    assert await resolve_execution_workspace(target, AsyncMock()) == "/retained"
    refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_exists", [True, False])
async def test_dynamic_workspace_overrides_populated_conventional_metadata(tmp_path, runtime_exists):
    import os
    import shlex
    import subprocess
    import sys
    from types import SimpleNamespace
    from agent_procutil import no_window_flags
    from agent_bridge.session_host.codespace_transport import (
        codespace_child_preparer, resolve_execution_workspace,
    )

    runtime = tmp_path / "runtime"
    if runtime_exists:
        subprocess.run(
            ["git", "init", "--quiet", str(runtime)], check=True,
            timeout=10, creationflags=no_window_flags(),
        )
    conventional = tmp_path / "conventional"
    conventional.mkdir()
    events = []
    target = {
        "workspace_folder": "/workspaces/conventional",
        "acp_command": 'cd "${CODESPACE_VSCODE_FOLDER:-${WORKING_DIRECTORY:-.}}" && copilot --acp',
    }

    async def run(command, **kwargs):
        events.append("resolve")
        outer = shlex.split(command)
        assert outer[:2] == ["bash", "-lc"]
        if "git -C" in outer[2]:
            events[-1] = "validate"
            result = subprocess.run(
                ["git", "-C", str(runtime), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=10, creationflags=no_window_flags(),
            )
            return result.returncode, result.stdout, result.stderr
        _exports, separator, reader = outer[2].partition(" && ")
        assert separator and "copilot" not in reader
        argv = shlex.split(reader)
        assert argv[0] == "python3"
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"CODESPACE_VSCODE_FOLDER", "WORKING_DIRECTORY"}}
        environment["CODESPACE_VSCODE_FOLDER"] = str(runtime)
        result = subprocess.run(
            [sys.executable, *argv[1:]], env=environment,
            capture_output=True, text=True, timeout=10, creationflags=no_window_flags(),
        )
        return result.returncode, "/runtime/actual\n" if not result.returncode else "", result.stderr

    async def prepare(selected, transport, workspace, **kwargs):
        events.append("prepare")
        assert runtime.is_dir()
        assert workspace == selected["workspace_folder"] == "/runtime/actual"
        return ["/prepared/sample"]

    transport = SimpleNamespace(run=run)
    assert await resolve_execution_workspace(target, transport) == "/runtime/actual"
    operation = codespace_child_preparer(
        target, transport, prelude="", controller_cwd=None, prepare_plugins=prepare,
        append_plugin_dirs=lambda command, dirs: command + " --plugin-dir=" + dirs[0],
    )([])
    if not runtime_exists:
        with pytest.raises(RuntimeError, match="checkout"):
            await operation
        assert events == ["resolve", "validate"]
        return
    child = await operation
    assert child[-1] == "cd /runtime/actual && copilot --acp --plugin-dir=/prepared/sample"
    assert "$" not in child[-1]
    assert events == ["resolve", "validate", "prepare"]


@pytest.mark.asyncio
async def test_replacement_resolves_dynamic_metadata_and_persists_concrete_child_cwd(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agent_bridge.transport import SpawnTarget
    from agent_bridge.session_host_recovery import _SessionHostRecoveryMixin

    target = SpawnTarget(type="command", codespace={
        "name": "synthetic", "repo": "example/project", "workspace_folder": "/workspaces/conventional",
        "acp_command": 'cd "${CODESPACE_VSCODE_FOLDER:-.}" && copilot --acp',
    })
    transport = SimpleNamespace(run=AsyncMock(return_value=(0, "/runtime/actual\n", "")))
    spawner = SimpleNamespace(transport=transport)
    persisted = []
    prepare = AsyncMock(return_value=[])
    monkeypatch.setattr("agent_bridge.venue_plugins.resolve_controller_cwd", AsyncMock(return_value=None))
    monkeypatch.setattr("agent_bridge.venue_plugins.prepare_venue_plugins", prepare)
    monkeypatch.setattr("agent_bridge.session_host.codespace_transport.build_codespace_spawner",
                        lambda *a, **kw: spawner)
    monkeypatch.setattr("agent_bridge.session_manager._resolve_relay_launch_env",
                        lambda *a: ("SYNTHETIC_PRELUDE;", 12001))

    async def connect(selected, **kwargs):
        assert selected.cwd == kwargs["remote_cwd"] == "/runtime/actual"
        assert persisted[-1][2] == "/runtime/actual"
        argv = await spawner.prepare_child(kwargs["remote_child_argv"])
        assert argv[-1] == "SYNTHETIC_PRELUDE;cd /runtime/actual && copilot --acp"
        return "client", "acp-session"

    manager = SimpleNamespace(
        _codespace_lock_sessions=set(), _acquire_codespace_lock=lambda *a: None,
        _release_codespace_lock=lambda *a: None,
        _session_host_unexpected_reap_seconds=60, _session_host_active_reap_seconds=0,
        _db=SimpleNamespace(update_session_target=lambda *a: persisted.append(a)),
        _connect_via_session_host=connect,
    )
    session = SimpleNamespace(
        session_id="synthetic", target=target, event_log=SimpleNamespace(append=lambda *a: None),
        mcp_servers=[], acp_session_id="old-acp", model_override=None, effort_override=None,
    )
    assert await _SessionHostRecoveryMixin._resume_via_new_remote_host(
        manager, session, on_acp_event=lambda *a: None, permission_callback=None,
    ) == ("client", "acp-session")
    assert prepare.await_args.args[2] == "/runtime/actual"
    assert "$" not in target.codespace["acp_command"]


@pytest.mark.asyncio
@pytest.mark.parametrize("authority_v2", [False, True])
async def test_genuinely_live_recovery_keeps_persisted_workspace_without_dynamic_probe(
    monkeypatch, authority_v2,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agent_bridge.transport import SpawnTarget
    from agent_bridge.session_host_recovery import _SessionHostRecoveryMixin
    from agent_bridge.session_host.version_mux import HostDisposition

    target = SpawnTarget(type="command", cwd="/persisted/live", codespace={
        "workspace_folder": "/persisted/live",
        "acp_command": 'cd "${CODESPACE_VSCODE_FOLDER:-.}" && copilot --acp',
    })
    rec = SimpleNamespace(boundary="codespace", extra={"remote_authority_v2": authority_v2},
                          protocol_version=1, created_at=None)
    probe = AsyncMock(side_effect=AssertionError("must not redirect a live Host"))
    monkeypatch.setattr("agent_bridge.session_host.codespace_transport.resolve_execution_workspace", probe)
    monkeypatch.setattr("agent_bridge.session_host.version_mux.plan_host",
                        lambda **kw: SimpleNamespace(disposition=HostDisposition.REATTACH))
    manager = SimpleNamespace(
        _host_index=SimpleNamespace(get=lambda session_id: rec),
        _rec_host_alive=lambda record: True, _rec_child_alive=lambda record: True,
        _session_host_stale_reap_seconds=60, _reattach_one=AsyncMock(return_value=True),
        _recover_remote_host_records=AsyncMock(), _remote_recovery_inconclusive=set(),
    )
    session = SimpleNamespace(session_id="live", acp_session_id="running", target=target)
    assert await _SessionHostRecoveryMixin._try_reattach_live_host(manager, session)
    assert target.cwd == target.codespace["workspace_folder"] == "/persisted/live"
    assert target.codespace["acp_command"].startswith('cd "${')
    probe.assert_not_awaited()
    assert manager._reattach_one.await_args.args[0] is rec


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["probe", "unsupported"])
@pytest.mark.parametrize("release_confirmed", [True, False])
async def test_failed_workspace_preflight_releases_real_ownership_before_next_launch(
    session_manager, monkeypatch, tmp_path, failure, release_confirmed,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from ssh_manager import TargetLock
    from agent_bridge.models import SessionStatus
    from agent_bridge.transport import SpawnTarget

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    claims = {}
    released = []

    def claim(name, owner, **kwargs):
        if name in claims and claims[name] != owner:
            return "conflict", "incumbent"
        claims[name] = owner
        return "ok", ""

    def release(name, owner):
        assert claims.get(name) == owner
        released.append((name, owner))
        if not release_confirmed:
            return False
        claims.pop(name)
        return True

    transport = SimpleNamespace(run=AsyncMock(return_value=(1, "", "probe failed")))
    monkeypatch.setattr("agent_bridge.session_manager._claim_codespace", claim)
    monkeypatch.setattr("agent_bridge.session_manager._release_codespace_claim", release)
    monkeypatch.setattr("agent_bridge.session_manager._resolve_relay_launch_env", lambda *a: ("", None))
    monkeypatch.setattr("agent_bridge.venue_plugins.resolve_controller_cwd", AsyncMock(return_value=None))
    monkeypatch.setattr("agent_bridge.session_host.codespace_transport.build_codespace_spawner",
                        lambda *a, **kw: SimpleNamespace(transport=transport))
    connected = AsyncMock(return_value=(SimpleNamespace(is_running=False), "acp"))
    monkeypatch.setattr(session_manager, "_connect_via_session_host", connected)
    expression = "${CODESPACE_VSCODE_FOLDER:-.}" if failure == "probe" else "${WORKSPACE:=/unsupported}"

    def target(command):
        return SpawnTarget(type="command", caller_worktree="/task/controller", codespace={
            "name": "synthetic", "repo": "example/project",
            "workspace_folder": "/workspaces/conventional", "acp_command": command,
        })

    try:
        failed = await session_manager.start_session(target(f'cd "{expression}" && copilot --acp'))
        assert failed.status == SessionStatus.FAILED
        if not release_confirmed:
            assert claims == {"synthetic": "/task/controller"}
            assert session_manager._codespace_lock_sessions == {failed.session_id: "synthetic"}
            assert failed.client is None
            assert session_manager._host_index is None or session_manager._host_index.get(failed.session_id) is None
            blocked = await session_manager.start_session(target("cd /workspaces/ready && copilot --acp"))
            assert blocked.status == SessionStatus.FAILED
            assert session_manager._codespace_lock_sessions == {failed.session_id: "synthetic"}
            release_confirmed = True
            assert release("synthetic", "/task/controller")
            session_manager._release_codespace_lock(failed.session_id)
        assert not claims, "preflight failure retained the acquired claim"
        assert not session_manager._codespace_lock_sessions
        assert TargetLock("synthetic", directory=locks).read_holder() is None
        connected.assert_not_awaited()
        assert released and all(row == ("synthetic", "/task/controller") for row in released)
        transport.run.return_value = (0, "/workspaces/ready\n", "")
        succeeded = await session_manager.start_session(target("cd /workspaces/ready && copilot --acp"))
        assert succeeded.status == SessionStatus.IDLE
        assert session_manager._codespace_lock_sessions[succeeded.session_id] == "synthetic"
        assert TargetLock("synthetic", directory=locks).read_holder() is not None
    finally:
        for session_id in list(session_manager._codespace_lock_sessions):
            session_manager._release_codespace_lock(session_id)
        claims.clear()


def test_ownership_scope_never_releases_a_live_incumbent(session_manager, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ssh_manager import TargetLock
    from agent_bridge.session_host.codespace_transport import codespace_launch_ownership

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    session_manager._acquire_codespace_lock("incumbent", "synthetic")
    core = SimpleNamespace(_claim_codespace=Mock(), _release_codespace_claim=Mock())
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            with codespace_launch_ownership(
                session_manager, "new", {"name": "synthetic"}, "/new", core,
            ):
                pytest.fail("must not pass incumbent ownership")
        assert session_manager._codespace_lock_sessions == {"incumbent": "synthetic"}
        assert TargetLock("synthetic", directory=locks).read_holder() is not None
        core._claim_codespace.assert_not_called()
        core._release_codespace_claim.assert_not_called()
    finally:
        session_manager._release_codespace_lock("incumbent")


@pytest.mark.parametrize("status", ["conflict", "coordination-rejected"])
@pytest.mark.parametrize("retain_on_error", [False, True])
def test_rejected_claim_releases_only_new_local_lock(
    session_manager, monkeypatch, tmp_path, status, retain_on_error,
):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ssh_manager import TargetLock
    from agent_bridge.session_manager import CodespaceClaimConflictError, CodespaceCoordinationRejectedError
    from agent_bridge.session_host.codespace_transport import codespace_launch_ownership

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    core = SimpleNamespace(_claim_codespace=Mock(return_value=(status, "external owner")),
                           _release_codespace_claim=Mock())
    error = CodespaceClaimConflictError if status == "conflict" else CodespaceCoordinationRejectedError
    with pytest.raises(error):
        with codespace_launch_ownership(
            session_manager, "new", {"name": "synthetic"}, "/new", core,
            retain_on_error=retain_on_error,
        ):
            pytest.fail("rejected claim cannot proceed")
    assert not session_manager._codespace_lock_sessions
    assert TargetLock("synthetic", directory=locks).read_holder() is None
    core._release_codespace_claim.assert_not_called()


@pytest.mark.parametrize("failure", ["preflight", "cancel", "pending"])
def test_ownership_scope_restores_transfer_or_preserves_pending_authority(
    session_manager, monkeypatch, tmp_path, failure,
):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock
    from agent_bridge.session_host.codespace_transport import codespace_launch_ownership
    from agent_bridge.session_host.spawner import RemoteSpawnCleanupPendingError

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    session_manager._acquire_codespace_lock("old", "synthetic")
    core = SimpleNamespace(_claim_codespace=Mock(return_value=("ok", "")),
                           _release_codespace_claim=Mock())
    error = {
        "preflight": RuntimeError("probe failed"),
        "cancel": asyncio.CancelledError(),
        "pending": RemoteSpawnCleanupPendingError("remote authority remains"),
    }[failure]
    try:
        with pytest.raises(type(error)):
            with codespace_launch_ownership(
                session_manager, "new", {"name": "synthetic"}, "/same-owner", core,
                replace_session_id="old",
            ):
                raise error
        expected = "new" if failure == "pending" else "old"
        assert session_manager._codespace_lock_sessions == {expected: "synthetic"}
        core._release_codespace_claim.assert_not_called()
    finally:
        for session_id in list(session_manager._codespace_lock_sessions):
            session_manager._release_codespace_lock(session_id)


@pytest.mark.parametrize("release_result", [False, RuntimeError("release command failed")])
@pytest.mark.parametrize("retain_on_error", [False, True])
def test_unconfirmed_claim_release_retains_ownership_and_original_preflight_cause(
    session_manager, monkeypatch, tmp_path, release_result, retain_on_error,
):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ssh_manager import TargetLock
    from agent_bridge.session_host.codespace_transport import codespace_launch_ownership
    from agent_bridge.session_host.spawner import RemoteSpawnCleanupPendingError

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    release = Mock(side_effect=release_result) if isinstance(release_result, Exception) else Mock(
        return_value=release_result,
    )
    core = SimpleNamespace(_claim_codespace=Mock(return_value=("ok", "")),
                           _release_codespace_claim=release)
    original = RuntimeError("workspace probe failed")
    try:
        with pytest.raises(RuntimeError, match="claim cleanup is unconfirmed") as captured:
            with codespace_launch_ownership(
                session_manager, "failed-preflight", {"name": "synthetic"}, "/task/owner", core,
                retain_on_error=retain_on_error,
            ):
                raise original
        assert captured.value.__cause__ is original
        assert not isinstance(captured.value, RemoteSpawnCleanupPendingError)
        assert session_manager._codespace_lock_sessions == {"failed-preflight": "synthetic"}
        assert TargetLock("synthetic", directory=locks).read_holder() is not None
        release.assert_called_once_with("synthetic", "/task/owner")
        with pytest.raises(RuntimeError, match="already owned"):
            with codespace_launch_ownership(
                session_manager, "another", {"name": "synthetic"}, "/different", core,
            ):
                pytest.fail("unsettled ownership must not be replaced")
        release.assert_called_once()
    finally:
        session_manager._release_codespace_lock("failed-preflight")


@pytest.mark.parametrize("stage", ["preflight", "connect"])
def test_parity_retention_only_applies_after_entering_connect(
    session_manager, monkeypatch, tmp_path, stage,
):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ssh_manager import TargetLock
    from agent_bridge.session_host.codespace_transport import codespace_launch_ownership

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    core = SimpleNamespace(_claim_codespace=Mock(return_value=("ok", "")),
                           _release_codespace_claim=Mock(return_value=True))
    original = RuntimeError(f"{stage} failed")
    try:
        with pytest.raises(RuntimeError) as captured:
            with codespace_launch_ownership(
                session_manager, "parity", {"name": "synthetic"}, "/task/owner", core,
                retain_on_error=True,
            ) as enter_connect:
                if stage == "connect":
                    enter_connect()
                raise original
        assert captured.value is original
        if stage == "preflight":
            core._release_codespace_claim.assert_called_once_with("synthetic", "/task/owner")
            assert not session_manager._codespace_lock_sessions
            assert TargetLock("synthetic", directory=locks).read_holder() is None
        else:
            core._release_codespace_claim.assert_not_called()
            assert session_manager._codespace_lock_sessions == {"parity": "synthetic"}
            assert TargetLock("synthetic", directory=locks).read_holder() is not None
    finally:
        session_manager._release_codespace_lock("parity")


@pytest.mark.parametrize("admission", ["no-owner", "disabled", "missing-binstub"])
def test_skipped_claim_admission_releases_new_lock_despite_cleanup_path_changes(
    session_manager, monkeypatch, tmp_path, admission,
):
    from unittest.mock import Mock
    from ssh_manager import TargetLock
    from agent_bridge import session_manager as core
    from agent_bridge.session_host.codespace_transport import codespace_launch_ownership

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    release = Mock(return_value=False)
    monkeypatch.setattr(core, "_release_codespace_claim", release)
    owner = "" if admission == "no-owner" else "/task/owner"
    original = RuntimeError("workspace preflight failed")
    real_which = core.shutil.which

    def configure_admission():
        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        if admission == "disabled":
            monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
        monkeypatch.setattr(core.shutil, "which", lambda name, *a, **kw: (
            None if name == "agent-codespaces" else real_which(name, *a, **kw)
        ))

    configure_admission()
    try:
        with pytest.raises(RuntimeError) as captured:
            with codespace_launch_ownership(
                session_manager, "skipped", {"name": "synthetic"}, owner, core,
            ):
                monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
                monkeypatch.setattr(core.shutil, "which", lambda name, *a, **kw: (
                    "/provider/appeared" if name == "agent-codespaces" else real_which(name, *a, **kw)
                ))
                raise original
        assert captured.value is original
        release.assert_not_called()
        assert not session_manager._codespace_lock_sessions
        assert TargetLock("synthetic", directory=locks).read_holder() is None
        configure_admission()
        with codespace_launch_ownership(
            session_manager, "next", {"name": "synthetic"}, owner, core,
        ):
            assert session_manager._codespace_lock_sessions == {"next": "synthetic"}
        release.assert_not_called()
    finally:
        for session_id in list(session_manager._codespace_lock_sessions):
            session_manager._release_codespace_lock(session_id)


@pytest.mark.parametrize("release_result", [False, RuntimeError("release failed")])
def test_actual_acquired_claim_keeps_required_cleanup_when_provider_disappears(
    session_manager, monkeypatch, tmp_path, release_result,
):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ssh_manager import TargetLock
    from agent_bridge import session_manager as core
    from agent_bridge.session_host.codespace_transport import codespace_launch_ownership

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    real_which = core.shutil.which
    monkeypatch.setattr(core.shutil, "which", lambda name, *a, **kw: (
        "/provider/present" if name == "agent-codespaces" else real_which(name, *a, **kw)
    ))
    acquire = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(core.subprocess, "run", acquire)
    release = Mock(side_effect=release_result) if isinstance(release_result, Exception) else Mock(
        return_value=release_result,
    )
    monkeypatch.setattr(core, "_release_codespace_claim", release)
    original = RuntimeError("workspace preflight failed")
    try:
        with pytest.raises(RuntimeError, match="claim cleanup is unconfirmed") as captured:
            with codespace_launch_ownership(
                session_manager, "acquired", {"name": "synthetic"}, "/task/owner", core,
            ):
                monkeypatch.setattr(core.shutil, "which", lambda name, *a, **kw: (
                    None if name == "agent-codespaces" else real_which(name, *a, **kw)
                ))
                raise original
        assert acquire.call_args.args[0] == ["/provider/present", "claim", "synthetic", "--owner", "/task/owner"]
        assert captured.value.__cause__ is original
        assert session_manager._codespace_lock_sessions == {"acquired": "synthetic"}
        assert TargetLock("synthetic", directory=locks).read_holder() is not None
    finally:
        session_manager._release_codespace_lock("acquired")


@pytest.mark.parametrize("outcome", [
    "confirmed", "release-false", "release-error", "remote-pending",
    "transferred", "already-held", "transferred-skipped", "already-held-skipped",
])
def test_claim_receipt_clears_only_after_confirmed_new_claim_rollback(
    session_manager, monkeypatch, tmp_path, outcome,
):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from ssh_manager import TargetLock
    from agent_bridge.session_host.codespace_transport import codespace_launch_ownership
    from agent_bridge.session_host.spawner import RemoteSpawnCleanupPendingError

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    transferred = outcome.startswith("transferred")
    already_held = outcome.startswith("already-held")
    if transferred:
        session_manager._acquire_codespace_lock("old", "synthetic")
    elif already_held:
        session_manager._acquire_codespace_lock("current", "synthetic")
    release = Mock(return_value=outcome != "release-false")
    if outcome == "release-error":
        release.side_effect = RuntimeError("release failed")
    core = SimpleNamespace(_claim_codespace=Mock(return_value=(
        "skipped" if outcome.endswith("-skipped") else "ok", "",
    )),
                           _release_codespace_claim=release)
    persisted = []
    identity = {"name": "synthetic", "owner": "/task/owner"}
    metadata = {"codespace_claim": dict(identity)} if transferred or already_held else {}

    def persist():
        receipt = metadata["codespace_claim"]
        persisted.append(dict(receipt) if receipt is not None else None)
        if receipt is None:
            if len(persisted) == 1:
                assert not session_manager._codespace_lock_sessions
                core._claim_codespace.assert_not_called()
            else:
                assert session_manager._codespace_lock_sessions == {"current": "synthetic"}
                assert TargetLock("synthetic", directory=locks).read_holder() is not None

    error = (
        RemoteSpawnCleanupPendingError("remote authority remains")
        if outcome == "remote-pending" else RuntimeError("preflight failed")
    )
    try:
        with pytest.raises(RuntimeError):
            with codespace_launch_ownership(
                session_manager, "current", {"name": "synthetic"}, "/task/owner", core,
                replace_session_id="old" if transferred else None,
                claim_metadata=metadata, persist_claim_metadata=persist,
            ):
                raise error
        initial = [] if transferred or already_held else [None]
        if outcome == "confirmed":
            assert persisted == [*initial, identity, None]
            assert "codespace_claim" in metadata and metadata["codespace_claim"] is None
            assert not session_manager._codespace_lock_sessions
        else:
            assert metadata["codespace_claim"] == identity
            assert persisted == [*initial, identity]
            assert session_manager._codespace_lock_sessions
        if outcome == "remote-pending" or transferred or already_held:
            release.assert_not_called()
    finally:
        for session_id in list(session_manager._codespace_lock_sessions):
            session_manager._release_codespace_lock(session_id)
