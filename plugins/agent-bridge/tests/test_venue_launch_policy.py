"""The host launch policy, asked before a Session Host is spawned on a CodeSpace."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge import venue_launch_policy as vlp
from agent_bridge.models import SessionStatus
from agent_bridge.session_host import codespace_transport
from agent_bridge.session_host.spawner import CodeSpaceSpawner
from agent_bridge.session_manager import SessionManager
from agent_bridge.transport import SpawnTarget


@pytest.fixture(autouse=True)
def _no_host_registration(tmp_path, monkeypatch):
    """Point the registration probe at an empty directory (never the real home),
    and the provider registry at an empty one (the PATH stub decides)."""
    monkeypatch.setenv("AGENT_CODESPACES_HOME", str(tmp_path / "agent-codespaces"))
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path / "providers.d"))
    return tmp_path / "agent-codespaces"


def _check(monkeypatch, *, rc=0, stdout="", stderr="", raises=None, binstub="agent-codespaces"):
    calls = []

    def run(argv, **_kw):
        calls.append(argv)
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, rc, stdout, stderr)

    monkeypatch.setattr(vlp.shutil, "which", lambda _n: binstub)
    monkeypatch.setattr(vlp.subprocess, "run", run)
    return calls


def test_allowed(monkeypatch):
    calls = _check(monkeypatch, rc=0, stdout=json.dumps({"codespace": "cs", "refuse": None}))
    assert vlp.codespace_launch_refusal("cs") is None
    assert [c[:4] for c in calls] == [["agent-codespaces", "launch-check", "cs", "--json"]]
    deadline = float(calls[0][calls[0].index("--deadline") + 1])  # the check answers before the outer kill
    assert 0 < deadline - __import__("time").time() <= vlp._CHECK_TIMEOUT - vlp._CHECK_DEADLINE_MARGIN + 0.01


def test_the_active_provider_manifest_command_is_used_before_path(monkeypatch):
    """The daemon's service PATH normally lacks sibling binstubs: providers.d
    carries the active provider's absolute command (in its own install context)."""
    from types import SimpleNamespace as NS

    calls = _check(monkeypatch, rc=0, stdout=json.dumps({"codespace": "cs", "refuse": None}), binstub=None)
    monkeypatch.setattr("agent_bridge.provider_sources.discover_provider_manifests",
                        lambda *a, **k: {"codespace": NS(command=("/opt/cell/bin/agent-codespaces", "--cell"))})
    assert vlp.codespace_launch_refusal("cs") is None
    assert calls[0][:5] == ["/opt/cell/bin/agent-codespaces", "--cell", "launch-check", "cs", "--json"]


def test_refused_carries_the_policy_reason(monkeypatch):
    _check(monkeypatch, rc=79, stdout=json.dumps({"codespace": "cs", "refuse": "the operator paused it"}))
    assert vlp.codespace_launch_refusal("cs") == "the operator paused it"
    with pytest.raises(vlp.LaunchRefusedError) as exc:
        vlp.ensure_codespace_launch_allowed("cs")
    assert exc.value.codespace == "cs" and exc.value.reason == "the operator paused it"


@pytest.mark.parametrize("kw", [
    {"binstub": None},  # agent-codespaces absent
    {"rc": 2, "stderr": "argument command: invalid choice: 'launch-check'"},  # too old to check
])
def test_no_check_possible_allows_only_without_a_registration(monkeypatch, _no_host_registration, kw):
    _check(monkeypatch, **kw)
    assert vlp.codespace_launch_refusal("cs") is None
    _no_host_registration.mkdir(parents=True)
    (_no_host_registration / "launch-policy.json").write_text('{"argv": ["x"]}', encoding="utf-8")
    reason = vlp.codespace_launch_refusal("cs")
    assert reason and "registered but can't be checked" in reason


def test_a_registration_probe_error_is_treated_as_registered(monkeypatch, _no_host_registration):
    """Only a definite FileNotFoundError may allow an unchecked launch."""
    _check(monkeypatch, binstub=None)
    real_lstat = vlp.os.lstat

    def lstat(path, *a, **k):
        if str(path).endswith("launch-policy.json"):
            raise PermissionError("no traverse permission")
        return real_lstat(path, *a, **k)

    monkeypatch.setattr(vlp.os, "lstat", lstat)
    reason = vlp.codespace_launch_refusal("cs")
    assert reason and "registered but can't be checked" in reason


@pytest.mark.parametrize("kw,needle", [
    ({"rc": 1, "stderr": "Traceback ..."}, "exit 1"),
    ({"rc": 78, "stderr": "[BLOCKED] context refused"}, "exit 78"),
    ({"raises": subprocess.TimeoutExpired(["x"], 60)}, "timed out"),
    ({"raises": OSError("permission denied")}, "could not run"),
    ({"rc": 0, "stdout": ""}, "without an explicit allow"),
    ({"rc": 0, "stdout": '{"codespace": "cs", "ref'}, "without an explicit allow"),
    ({"rc": 0, "stdout": '{"codespace": "cs"}'}, "without an explicit allow"),
    ({"rc": 0, "stdout": '{"codespace": "cs", "refuse": "paused"}'}, "without an explicit allow"),
])
def test_a_check_that_cannot_answer_fails_closed(monkeypatch, kw, needle):
    _check(monkeypatch, **kw)
    reason = vlp.codespace_launch_refusal("cs")
    assert reason and needle in reason


@pytest.mark.asyncio
async def test_the_spawner_asks_before_launching_and_never_launches_when_refused(monkeypatch):
    launched = []

    async def spawn(self, child_argv, **kwargs):
        launched.append(child_argv)
        return "host"

    monkeypatch.setattr(CodeSpaceSpawner, "spawn", spawn)
    spawner = codespace_transport.build_codespace_spawner("cs")
    assert isinstance(spawner, CodeSpaceSpawner)

    _check(monkeypatch, rc=79, stdout=json.dumps({"refuse": "paused"}))
    with pytest.raises(vlp.LaunchRefusedError):
        await spawner.spawn(["copilot"], session_id="s1")
    assert launched == []

    _check(monkeypatch, rc=0, stdout=json.dumps({"refuse": None}))
    assert await spawner.spawn(["copilot"], session_id="s1") == "host"
    assert launched == [["copilot"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_recreate", [False, True])  # True: the implicit resume a send triggers
async def test_a_refused_resume_is_terminal_never_retried_or_recreated(session_manager, monkeypatch,
                                                                        allow_recreate):
    from unittest.mock import AsyncMock, MagicMock

    from agent_bridge.session_manager import Session

    target = SpawnTarget(
        type="command", spawn_command=["agent-codespaces", "ssh", "cs-one", "--stdio"],
        codespace={"name": "cs-one", "repo": "org/repo",
                   "acp_command": "cd /workspaces/repo && copilot --acp --stdio",
                   "workspace_folder": "/workspaces/repo"},
    )
    session = Session("s1", "one", target, "codespace:cs-one")
    session.acp_session_id = "acp-1"
    session.status = SessionStatus.STOPPED
    session.event_log = MagicMock()
    session_manager._sessions["s1"] = session
    monkeypatch.setattr(session_manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    host_resume = AsyncMock(side_effect=vlp.LaunchRefusedError("cs-one", "the operator paused it"))
    monkeypatch.setattr(session_manager, "_resume_via_new_remote_host", host_resume)
    monkeypatch.setattr("agent_bridge.session_manager.spawn",
                        AsyncMock(side_effect=AssertionError("a refusal must not fall back to a fresh spawn")))

    with pytest.raises(vlp.LaunchRefusedError):
        await session_manager.resume_session("s1", allow_recreate=allow_recreate)

    host_resume.assert_awaited_once()  # asked once, not once per ladder round
    assert session.status is SessionStatus.STOPPED
    events = [c.args[0] for c in session.event_log.append.call_args_list]
    assert "launch_refused" in events and "acp_resume_retry" not in events


@pytest.mark.asyncio
async def test_a_refusal_at_the_final_recreate_still_records_the_typed_event(session_manager, monkeypatch):
    """The policy can change to refused after the ladder's ordinary failures: the
    recreate step's refusal gets the same launch_refused event, not just an error."""
    from unittest.mock import AsyncMock, MagicMock

    from agent_bridge.session_manager import Session

    target = SpawnTarget(
        type="command", spawn_command=["agent-codespaces", "ssh", "cs-one", "--stdio"],
        codespace={"name": "cs-one", "repo": "org/repo",
                   "acp_command": "cd /workspaces/repo && copilot --acp --stdio",
                   "workspace_folder": "/workspaces/repo"},
    )
    session = Session("s1", "one", target, "codespace:cs-one")
    session.acp_session_id = "acp-1"
    session.status = SessionStatus.STOPPED
    session.event_log = MagicMock()
    session_manager._sessions["s1"] = session
    monkeypatch.setattr(session_manager, "_try_reattach_live_host", AsyncMock(return_value=False))

    async def host(_session, *, load_existing=True, **_kw):
        if load_existing:
            raise RuntimeError("wedged")  # an ordinary ladder failure, retried
        raise vlp.LaunchRefusedError("cs-one", "paused meanwhile")

    monkeypatch.setattr(session_manager, "_resume_via_new_remote_host", host)
    monkeypatch.setattr("agent_bridge.session_resume.asyncio.sleep", AsyncMock())
    with pytest.raises(vlp.LaunchRefusedError):
        await session_manager.resume_session("s1", allow_recreate=True)
    events = [c.args[0] for c in session.event_log.append.call_args_list]
    assert "acp_resume_retry" in events and "launch_refused" in events
    assert session.status is SessionStatus.STOPPED


@pytest.mark.asyncio
async def test_a_refused_start_fails_the_session_with_a_typed_event(tmp_db, monkeypatch):
    monkeypatch.setattr("agent_bridge.session_host.codespace_transport.build_codespace_spawner",
                        lambda *a, **k: SimpleNamespace(transport=object()))
    monkeypatch.setattr("agent_bridge.session_manager._resolve_remote_ai_plugin_dirs", AsyncMock(return_value=[]))
    monkeypatch.setattr("agent_bridge.session_manager._claim_codespace", lambda *a, **k: ("ok", ""))

    async def refused_connect(self, target, **kwargs):
        raise vlp.LaunchRefusedError("example-codespace", "the operator paused it")

    monkeypatch.setattr(SessionManager, "_connect_via_session_host", refused_connect)
    target = SpawnTarget(
        type="command", cwd="/workspaces/example", caller_worktree="/wt/a",
        codespace={"name": "example-codespace", "repo": "example/repo",
                   "acp_command": "cd /workspaces/example && copilot --acp --stdio",
                   "workspace_folder": "/workspaces/example"},
    )
    session = await SessionManager(tmp_db).start_session(target, agent_name="codespace:example")
    assert session.status == SessionStatus.FAILED
    events = {e.event: e.data for e in session.event_log.get_events()}
    assert events["launch_refused"] == {"codespace": "example-codespace", "reason": "the operator paused it"}


def test_the_check_output_is_decoded_leniently(monkeypatch):
    """Malformed bytes must reach the answer validation (and refuse), never raise
    UnicodeDecodeError out of the gate."""
    seen = {}

    def run(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "\ufffd{not json", "")

    monkeypatch.setattr(vlp.shutil, "which", lambda _n: "agent-codespaces")
    monkeypatch.setattr(vlp.subprocess, "run", run)
    assert "without an explicit allow" in vlp.codespace_launch_refusal("cs")
    assert seen.get("encoding") == "utf-8" and seen.get("errors") == "replace"


@pytest.mark.asyncio
async def test_a_raw_codespace_spawn_asks_the_policy_too(monkeypatch):
    """A resync (or a resume without a Session Host) spawns through the raw
    transport: it must not launch a worker the host policy refuses."""
    from agent_bridge import transport

    monkeypatch.setattr(vlp, "codespace_launch_refusal", lambda name: f"{name} is paused")
    monkeypatch.setattr(transport, "spawn_raw", AsyncMock(side_effect=AssertionError("must not launch")))
    target = SpawnTarget(type="command", spawn_command=["agent-codespaces", "ssh", "cs-one", "--stdio"],
                         codespace={"name": "cs-one", "repo": "org/repo"})
    with pytest.raises(vlp.LaunchRefusedError) as exc:
        await transport.spawn(target)
    assert exc.value.codespace == "cs-one"
