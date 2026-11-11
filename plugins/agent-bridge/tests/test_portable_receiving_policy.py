"""Real isolated policy consumers do not select an installed guest suite."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent_procutil import no_window_flags
from repository_policy import AgentHostingError

from agent_bridge import hosting_policy, portable_policy
from agent_bridge.agent_registry import AgentResolver, CliNamespaceResolver
from agent_bridge.session_host import bundle, receiver_policy
from agent_bridge.session_host.client import SessionHostClient


@pytest.fixture(autouse=True)
def scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosting_policy, "marketplace_id", lambda: "selected-source")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "actual checkout"
    root.mkdir()
    (root / ".git").mkdir()
    return root


def _policy(repo: Path, text: str, *, overlay: bool = False) -> Path:
    path = repo / ".copilot-extensions" / "agent-worktrees"
    if overlay:
        path /= "marketplaces/selected-source"
    path /= "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _read(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    command, payload = portable_policy.guest_policy_check("/actual checkout", "resume")
    argv = shlex.split(command.split(" && ", 1)[1])
    assert argv[:4] == ["python3", "-I", "-S", "-c"]
    argv[0] = sys.executable
    argv[-4] = str(repo)
    return subprocess.run(
        argv, input=payload, capture_output=True, timeout=30, env=env,
        creationflags=no_window_flags(),
    )


@pytest.mark.parametrize("guest_suite", ["absent", "old", "current"])
@pytest.mark.parametrize("policy", ["allow", "base-deny", "overlay-deny", "invalid", "unreadable"])
def test_isolated_preflight_uses_selected_canonical_policy(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    guest_suite: str, policy: str,
) -> None:
    guest = tmp_path / "guest-bin"
    guest.mkdir()
    if guest_suite != "absent":
        (guest / "agent-worktrees").write_text(
            "#!/bin/sh\nprintf 'must not select guest suite' >&2\nexit 91\n", encoding="utf-8",
        )
    env = {
        **os.environ, "PATH": str(guest),
        "COPILOT_EXTENSIONS_CONTEXT": '{"marketplaceId":"unrelated-guest"}',
    }
    if policy == "base-deny":
        _policy(repo, "expose_agent: false\n")
    elif policy == "overlay-deny":
        _policy(repo, "expose_agent: true\n")
        _policy(repo, "expose_agent: false\n", overlay=True)
    elif policy == "invalid":
        _policy(repo, "expose_agent: 'true'\n")
    elif policy == "unreadable":
        path = _policy(repo, "expose_agent: true\n")
        path.unlink()
        path.mkdir()
    result = _read(repo, env)
    answer = json.loads(result.stdout)
    assert result.returncode == (0 if policy == "allow" else 3), result.stderr.decode()
    if policy == "allow":
        assert answer["allowed"] is True and answer["repository"] == str(repo)
    else:
        assert answer["code"].startswith("repository-agent-exposure-")
        assert "allowed" not in answer


@pytest.mark.parametrize("legacy", [False, True])
def test_policy_change_after_preflight_refuses_the_actual_bundle(
    repo: Path, tmp_path: Path, legacy: bool,
) -> None:
    from agent_remote_driver.session_host import bundle as core_bundle

    assert _read(repo, dict(os.environ)).returncode == 0
    archive, _hash = (
        bundle.build_session_host_bundle(tmp_path / "bundles") if legacy
        else core_bundle.build_session_host_bundle(tmp_path / "bundles")
    )
    _policy(repo, "expose_agent: false\n", overlay=True)
    marker = tmp_path / "child-started"
    result = subprocess.run(
        [
            sys.executable, "-I", "-S", str(archive),
            "--cwd", str(repo), "--repository-policy-context", portable_policy.selected_context(),
            "--", sys.executable, "-c", f"open({str(marker)!r}, 'w').close()",
        ],
        capture_output=True, timeout=30, creationflags=no_window_flags(),
    )
    assert result.returncode != 0
    assert b"expose_agent: false" in result.stderr
    assert not marker.exists()


def test_portable_receiver_rechecks_selected_overlay_without_guest_owner(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(receiver_policy.shutil, "which", lambda _name: None)
    context = portable_policy.selected_context()
    receiver_policy.require_portable_path_hosting(repo, context)
    _policy(repo, "expose_agent: false\n", overlay=True)
    for operation in ("launch", "resume", "successor"):
        with pytest.raises(AgentHostingError, match="expose_agent: false"):
            receiver_policy.require_portable_path_hosting(repo, context, operation=operation)
    with pytest.raises(ValueError, match="receiver is unavailable"):
        receiver_policy.require_path_hosting(repo)


@pytest.mark.parametrize("context", [
    "", "{}", "[]",
    '{"schema":"agent-bridge.receiving-policy","version":true,"marketplaceId":""}',
    '{"schema":"agent-bridge.receiving-policy","version":2,"marketplaceId":""}',
    '{"schema":"agent-bridge.receiving-policy","version":1,"marketplaceId":"../other"}',
])
def test_invalid_portable_context_never_downgrades_to_legacy(repo: Path, context: str) -> None:
    with pytest.raises(AgentHostingError):
        receiver_policy.require_portable_path_hosting(repo, context)


def test_context_comes_from_validated_host_not_request_environment(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", '{"marketplaceId":"other"}')
    _policy(repo, "expose_agent: false\n", overlay=True)
    with pytest.raises(AgentHostingError, match="selected-source"):
        receiver_policy.require_portable_path_hosting(repo, portable_policy.selected_context())


@pytest.mark.asyncio
async def test_codespace_selects_declared_raw_command_without_stripping_legacy_guard() -> None:
    from agent_bridge.session_host.codespace_transport import resolve_execution_workspace

    guarded = "repository_policy_launch() { legacy-owner-check; }; repository_policy_launch"
    prior = {"acp_command": guarded, "workspace_folder": "/actual"}
    assert await resolve_execution_workspace(prior, None) == "/actual"
    assert prior["acp_command"] == guarded
    current = {
        **prior, "portable_receiving_policy": {
            "version": 1, "acp_command": "cd /actual && copilot --acp --stdio",
        },
    }
    assert await resolve_execution_workspace(current, None) == "/actual"
    assert current["acp_command"] == "cd /actual && copilot --acp --stdio"
    current["portable_receiving_policy"]["version"] = True
    with pytest.raises(AgentHostingError, match="Unsupported CodeSpace"):
        await resolve_execution_workspace(current, None)


@pytest.mark.asyncio
async def test_prior_provider_cannot_pass_portable_preflight_then_fail_its_legacy_guard(monkeypatch) -> None:
    from agent_bridge import provider_hosting
    from agent_bridge.transport import SpawnTarget

    guest = SimpleNamespace(
        is_running=AsyncMock(return_value=True), close=AsyncMock(),
        run=AsyncMock(return_value=(2, "", "unknown command: agent-policy")),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.CodeSpaceTransport", lambda *_a, **_k: guest,
    )
    monkeypatch.setattr(
        portable_policy, "guest_policy_check",
        lambda *_a: pytest.fail("unadvertised portable support or fallback after owner failure"),
    )
    with pytest.raises(AgentHostingError, match="no portable receiving handoff"):
        await provider_hosting.query_provider_policy(SpawnTarget(type="command", codespace={
            "name": "selected", "workspace_folder": "/actual", "acp_command": "legacy-guard",
        }), operation="launch")
    guest.run.assert_awaited_once()
    assert "agent-policy check" in guest.run.await_args.args[0]
    assert "input_bytes" not in guest.run.await_args.kwargs
    guest.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("declaration", [None, {"version": 1}, {"version": True}])
async def test_container_preparation_negotiates_exact_selected_provider_before_new_syntax(
    monkeypatch: pytest.MonkeyPatch, declaration,
) -> None:
    from agent_bridge.session_host import container_transport

    calls = []

    async def run(command, *, timeout):
        calls.append(command)
        return 0, json.dumps({
            "name": "selected", "execution_instance": "instance",
            "ssh": {"host_alias": "selected"}, "remote_command": "copilot --acp",
            **({"portable_receiving_policy": {"version": 1}} if declaration else {}),
        }).encode(), b""

    monkeypatch.setattr(container_transport, "_run_provider", run)
    target = {
        "name": "selected", "instance_id": "instance", "provider_command": ["provider"],
        **({"portable_receiving_policy": declaration} if declaration is not None else {}),
    }
    if declaration == {"version": True} and type(declaration["version"]) is bool:
        with pytest.raises(RuntimeError, match="Unsupported container"):
            await container_transport.prepare_container_session_host(target, None)
        assert calls == []
        return
    await container_transport.prepare_container_session_host(target, None)
    assert len(calls) == 1
    assert ("--repository-policy-context" in calls[0]) is (declaration is not None)


@pytest.mark.asyncio
@pytest.mark.parametrize("activate", [False, True])
@pytest.mark.parametrize("denied", [False, True])
async def test_only_launch_entry_activates_then_checks_before_staging(
    repo: Path, monkeypatch: pytest.MonkeyPatch, activate: bool, denied: bool,
) -> None:
    resolver = AgentResolver({}, {})
    provider = CliNamespaceResolver("codespace", "agent-codespaces")
    specification = {
        "type": "command", "spawn_command": ["provider"],
        "codespace": {
            "name": "selected", "workspace_folder": "/actual",
            "portable_receiving_policy": {"version": 1, "activation": "start", "acp_command": "copilot"},
        },
    }
    resolver.register_namespace_resolver(provider)
    monkeypatch.setattr(resolver, "refresh_provider_resolvers", lambda: None)
    events: list[str] = []
    running = False

    async def provider_call(arguments, **kwargs):
        nonlocal running
        if arguments == ["start", "selected"]:
            assert kwargs == {"timeout": 240}
            events.append("activate")
            running = True
            if denied:
                _policy(repo, "expose_agent: false\n", overlay=True)
        return 0, json.dumps(specification), ""

    provider._run = AsyncMock(side_effect=provider_call)

    async def ready(name: str) -> None:
        assert name == "selected"
        events.append("ready")
        # The real provider admits a Shutdown target here without waking it.

    async def gate(target) -> None:
        assert target.codespace["name"] == "selected"
        events.append("launch-policy")

    async def run(command, *, timeout, input_bytes) -> tuple[int, str, str]:
        events.append("policy")
        from repository_policy import require_agent_hosting

        answer = require_agent_hosting(repo, marketplace_id="selected-source")
        return 0, json.dumps({**answer.to_dict(), "allowed": True}), ""

    guest = SimpleNamespace(
        is_running=AsyncMock(side_effect=lambda: running), run=run, close=AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.CodeSpaceTransport", lambda *_a, **_k: guest,
    )
    monkeypatch.setattr("agent_bridge.venue_launch_policy.gate_raw_codespace_spawn", gate)
    staging = AsyncMock(side_effect=lambda *_a: events.append("staging") or [])
    monkeypatch.setattr(resolver, "_related_plugins_for", staging)
    provider.ensure_ready = AsyncMock(side_effect=ready)
    if not activate:
        with pytest.raises(AgentHostingError, match="without waking"):
            await resolver.resolve_async("codespace:selected")
        assert events == []
        provider.ensure_ready.assert_not_awaited()
    elif denied:
        with pytest.raises(AgentHostingError, match="expose_agent: false"):
            await resolver.resolve_async("codespace:selected", activate=True)
        assert events == ["launch-policy", "activate", "ready", "policy"]
        staging.assert_not_awaited()
    else:
        target = await resolver.resolve_async("codespace:selected", activate=True)
        assert target.codespace["name"] == "selected"
        assert events == ["launch-policy", "activate", "ready", "policy", "staging", "policy"]
        provider.ensure_ready.assert_awaited_once_with("selected")


@pytest.mark.asyncio
async def test_available_policy_denial_is_not_a_stopped_venue_activation(monkeypatch) -> None:
    resolver = AgentResolver({}, {})
    provider = CliNamespaceResolver("codespace", "agent-codespaces")
    provider._run = AsyncMock(return_value=(0, json.dumps({
        "type": "command", "spawn_command": ["provider"],
        "codespace": {
            "name": "selected", "workspace_folder": "/actual",
            "portable_receiving_policy": {"version": 1, "activation": "start", "acp_command": "copilot"},
        },
    }), ""))
    provider.ensure_ready = AsyncMock()
    resolver.register_namespace_resolver(provider)
    monkeypatch.setattr(resolver, "refresh_provider_resolvers", lambda: None)
    monkeypatch.setattr("agent_bridge.venue_launch_policy.gate_raw_codespace_spawn", AsyncMock())
    guest = SimpleNamespace(
        is_running=AsyncMock(return_value=True), close=AsyncMock(),
        run=AsyncMock(return_value=(3, "", "expose_agent: false")),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.CodeSpaceTransport", lambda *_a, **_k: guest,
    )
    with pytest.raises(AgentHostingError, match="expose_agent: false"):
        await resolver.resolve_async("codespace:selected", activate=True)
    provider.ensure_ready.assert_not_awaited()
    assert not any(call.args[0][0] == "start" for call in provider._run.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("running", [False, True])
async def test_http_container_refusal_precedes_activating_readiness(
    session_manager, monkeypatch: pytest.MonkeyPatch, running: bool,
) -> None:
    import httpx
    from fastapi import FastAPI

    from agent_bridge.routes import sessions
    from agent_bridge.session_host import container_transport

    owner = AgentResolver({}, {})
    provider = CliNamespaceResolver("container", "fixture-provider")
    provider._run = AsyncMock(return_value=(0, json.dumps({
        "type": "command", "spawn_command": ["fixture-provider"], "user": "fixture-user",
        "container": {
            "name": "selected", "instance_id": "fixture-instance", "workspace_folder": "/actual",
            "portable_receiving_policy": {"version": 1},
        },
    }), ""))
    provider.ensure_ready = AsyncMock()
    owner.register_namespace_resolver(provider)
    monkeypatch.setattr(owner, "refresh_provider_resolvers", lambda: None)
    staging = AsyncMock(return_value=[])
    monkeypatch.setattr(owner, "_related_plugins_for", staging)
    calls = []

    async def runtime(argv, **kwargs):
        calls.append(argv)
        if argv == ["docker", "inspect", "fixture-instance"]:
            return 0, json.dumps([{
                "Id": "fixture-instance", "State": {"Running": running},
            }]).encode(), b""
        assert argv[:6] == ["docker", "exec", "-i", "-u", "fixture-user", "fixture-instance"]
        assert kwargs["input_bytes"]
        return 3, b"", b"expose_agent: false"

    monkeypatch.setattr(container_transport, "_run_provider", runtime)
    app = FastAPI()
    app.state.session_manager = session_manager
    app.state.resolver = owner
    app.state.ready = True
    app.include_router(sessions.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture",
    ) as client:
        response = await client.post("/api/v1/sessions", json={"agent": "container:selected"})
    assert response.status_code == 400, response.text
    assert ("expose_agent: false" if running else "actual running container") in response.json()["detail"]
    provider.ensure_ready.assert_not_awaited()
    staging.assert_not_awaited()
    assert len(calls) == (2 if running else 1)
    assert session_manager.list_sessions() == []
    assert session_manager.db.execute_read("SELECT * FROM sessions") == []
    assert session_manager._active_acquisitions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [
    "allow", "deny", "stop-before", "stop-during", "retarget", "legacy", "upgrade",
    "available-upgrade", "available-upgrade-stop", "available-legacy", "available-portable-owner-absent",
])
async def test_stopped_codespace_resume_activates_under_original_stop_fence(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    from agent_bridge.cold_outages import ColdRecoveryStoppedError
    from agent_bridge.models import SessionStatus
    from agent_bridge.session_manager import Session
    from agent_bridge.spawn_target import SpawnTarget

    metadata = {
        "name": "selected", "workspace_folder": "/actual",
        "portable_receiving_policy": {"version": 1, "activation": "start", "acp_command": "copilot"},
    }
    retained_metadata = dict(metadata)
    if "upgrade" in outcome or outcome == "available-legacy":
        retained_metadata.pop("portable_receiving_policy")
    session = Session(
        "existing", "existing", SpawnTarget(
            type="command", codespace=retained_metadata, cwd="/actual", explicit_cwd=True,
        ),
        "codespace:selected",
    )
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "retained-conversation"
    session_manager._sessions[session.session_id] = session
    session_manager.db.create_session(
        "existing", "existing", "codespace:selected", "/actual", "command", "stopped", 0,
    )
    owner = AgentResolver({}, {})
    provider = CliNamespaceResolver("codespace", "agent-codespaces")
    owner.register_namespace_resolver(provider)
    session_manager._resolver = None if outcome == "available-portable-owner-absent" else owner
    events = []
    running = outcome.startswith("available-")

    async def provider_call(arguments, **kwargs):
        nonlocal running
        if arguments == ["start", "selected"]:
            assert kwargs == {"timeout": 240}
            events.append("activate")
            running = True
            if outcome == "deny":
                _policy(repo, "expose_agent: false\n", overlay=True)
            if outcome == "stop-during":
                session._stop_notices += 1
            return 0, "", ""
        assert arguments[:2] == ["namespace-resolve", "selected"]
        if outcome == "available-upgrade-stop":
            session._stop_notices += 1
        current = {
            **metadata, "name": "another" if outcome == "retarget" else "selected",
            "workspace_folder": "/provider-default",
        }
        if outcome in ("legacy", "available-legacy"):
            current.pop("portable_receiving_policy")
        return 0, json.dumps({
            "type": "command", "spawn_command": ["provider"], "codespace": current,
        }), ""

    async def gate(_target):
        events.append("launch-policy")
        if outcome == "stop-before":
            session._stop_notices += 1

    async def read(command, **kwargs):
        from repository_policy import require_agent_hosting

        if "input_bytes" not in kwargs:
            events.append("legacy-policy")
            return 127, "", "guest owner unsupported"
        assert "/actual" in command and "/provider-default" not in command
        assert kwargs["input_bytes"]
        events.append("policy")
        answer = require_agent_hosting(repo, marketplace_id="selected-source")
        return 0, json.dumps({**answer.to_dict(), "allowed": True}), ""

    guest = SimpleNamespace(
        is_running=AsyncMock(side_effect=lambda: running), run=read, close=AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.CodeSpaceTransport", lambda *_a, **_k: guest,
    )
    monkeypatch.setattr("agent_bridge.venue_launch_policy.gate_raw_codespace_spawn", gate)
    provider._run = AsyncMock(side_effect=provider_call)
    admitted = AsyncMock(return_value=session)
    monkeypatch.setattr(session_manager, "_resume_session_admitted", admitted)
    if outcome in ("allow", "upgrade", "available-upgrade", "available-portable-owner-absent"):
        assert await session_manager.resume_session("existing") is session
        assert events == (["policy"] if running and outcome.startswith("available-") else [
            "launch-policy", "activate", "policy",
        ])
        fence = admitted.await_args.kwargs["hosting_fence"]
        assert fence.session is session and fence.stop_notices == 0
        assert session.target.cwd == "/actual" and session.target.explicit_cwd
        assert session.target.codespace == metadata
    else:
        expected = ColdRecoveryStoppedError if (
            outcome.startswith("stop-") or outcome == "available-upgrade-stop"
        ) else AgentHostingError
        with pytest.raises(expected):
            await session_manager.resume_session("existing")
        admitted.assert_not_awaited()
        assert ("activate" in events) is (outcome in ("deny", "stop-during"))
    assert session.status is SessionStatus.STOPPED
    assert session.acp_session_id == "retained-conversation"


@pytest.mark.asyncio
async def test_real_resume_refresh_cannot_reactivate_after_new_durable_stop(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.models import SessionStatus
    from agent_bridge.session_manager import ProviderTargetRefreshError, Session
    from agent_bridge.spawn_target import SpawnTarget

    metadata = {
        "name": "selected", "workspace_folder": "/actual",
        "portable_receiving_policy": {
            "version": 1, "activation": "start", "acp_command": "cd /actual && copilot",
        },
    }
    session = Session("existing", "existing", SpawnTarget(
        type="command", codespace=metadata, cwd="/actual", explicit_cwd=True,
        venue={"_agent_bridge_request_overrides": {"env": {}, "copilot_args": []}},
    ), "codespace:selected")
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "retained-conversation"
    session_manager._sessions["existing"] = session
    session_manager.db.create_session(
        "existing", "existing", session.agent_name, "/actual", "command", "stopped", 0,
        target_json=session.target.to_json(), acp_session_id=session.acp_session_id,
    )
    owner = AgentResolver({}, {})
    provider = CliNamespaceResolver("codespace", "fixture")
    owner.register_namespace_resolver(provider)
    session_manager._resolver = owner
    monkeypatch.setattr(owner, "refresh_provider_resolvers", lambda: None)
    monkeypatch.setattr(owner, "_related_plugins_for", AsyncMock(return_value=[]))
    provider.ensure_ready = AsyncMock()
    entered, finish = asyncio.Event(), asyncio.Event()
    running = True
    starts = []

    async def provider_call(arguments, **_kwargs):
        nonlocal running
        if arguments == ["start", "selected"]:
            starts.append(arguments)
            running = True
            return 0, "", ""
        assert arguments[:2] == ["namespace-resolve", "selected"]
        entered.set()
        await finish.wait()
        return 0, json.dumps({
            "type": "command", "spawn_command": ["fixture"], "codespace": metadata,
        }), ""

    async def read(_command, **kwargs):
        from repository_policy import require_agent_hosting

        assert kwargs["input_bytes"]
        answer = require_agent_hosting(repo, marketplace_id="selected-source")
        return 0, json.dumps({**answer.to_dict(), "allowed": True}), ""

    guest = SimpleNamespace(
        is_running=AsyncMock(side_effect=lambda: running), run=read, close=AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.CodeSpaceTransport", lambda *_a, **_k: guest,
    )
    monkeypatch.setattr("agent_bridge.venue_launch_policy.gate_raw_codespace_spawn", AsyncMock())
    provider._run = AsyncMock(side_effect=provider_call)
    monkeypatch.setattr(session_manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    new_host = AsyncMock()
    monkeypatch.setattr(session_manager, "_resume_via_new_remote_host", new_host)
    resume = asyncio.create_task(session_manager.resume_session("existing"))
    stop = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        running = False
        stop = asyncio.create_task(session_manager.stop_session("existing", force=True))
        token = None
        deadline = asyncio.get_running_loop().time() + 10
        while token is None and asyncio.get_running_loop().time() < deadline:
            token = session_manager.db.get_session("existing")["cold_recovery_stop_token"]
            await asyncio.sleep(.01)
        assert token and not stop.done()
        finish.set()
        with pytest.raises(ProviderTargetRefreshError):
            await asyncio.wait_for(resume, timeout=10)
        await asyncio.wait_for(stop, timeout=10)
        assert starts == []
        provider.ensure_ready.assert_not_awaited()
        new_host.assert_not_awaited()
        assert session_manager.db.get_session("existing")["cold_recovery_stop_token"] == token
        assert session.status is SessionStatus.STOPPED and not session.background_recovery_enabled
        assert session.target.cwd == "/actual" and session.acp_session_id == "retained-conversation"
    finally:
        finish.set()
        pending = [task for task in (resume, stop) if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
def delayed_activation_provider(tmp_path: Path):
    started, release, completed = (tmp_path / name for name in ("started", "release", "completed"))
    program = (
        "import pathlib,time\n"
        f"started,release,completed=map(pathlib.Path,{[str(started), str(release), str(completed)]!r})\n"
        "started.write_text('started')\n"
        "deadline=time.monotonic()+15\n"
        "while not release.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "assert release.exists(), 'fixture release timed out'\n"
        "completed.write_text('activated')\n"
    )
    provider = CliNamespaceResolver("codespace", "fixture", command=[sys.executable, "-c", program])
    return provider, started, release, completed


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["pre-drain", "drain", "cancel"])
async def test_resume_activation_remains_owned_through_cancellation_and_drain(
    delayed_activation_provider, session_manager, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    from agent_bridge.models import SessionStatus
    from agent_bridge.provider_hosting import CodeSpaceNotRunning
    from agent_bridge.session_manager import DaemonDrainingError, Session
    from agent_bridge.spawn_target import SpawnTarget

    provider, started, release, completed = delayed_activation_provider
    target = SpawnTarget(type="command", cwd="/actual", explicit_cwd=True, codespace={
        "name": "selected", "workspace_folder": "/actual",
        "portable_receiving_policy": {"version": 1, "activation": "start", "acp_command": "copilot"},
    })
    session = Session("existing", "existing", target, "codespace:selected")
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "retained-conversation"
    session_manager._sessions[session.session_id] = session
    session_manager.db.create_session(
        "existing", "existing", "codespace:selected", "/actual", "command", "stopped", 0,
    )
    owner = AgentResolver({}, {})
    owner.register_namespace_resolver(provider)
    owner._call_resolver = AsyncMock(return_value=target)
    session_manager._resolver = owner

    async def hosting(*_args, **_kwargs):
        if not completed.exists():
            raise CodeSpaceNotRunning("stopped")

    monkeypatch.setattr(hosting_policy, "require_target_hosting", hosting)
    guest = SimpleNamespace(
        is_running=AsyncMock(side_effect=completed.exists),
        run=AsyncMock(side_effect=AssertionError("unexpected workspace probe")), close=AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.CodeSpaceTransport", lambda *_a, **_k: guest,
    )
    monkeypatch.setattr(
        "agent_bridge.venue_launch_policy.gate_raw_codespace_spawn", AsyncMock(),
    )
    admitted = AsyncMock(return_value=session)
    monkeypatch.setattr(session_manager, "_resume_session_admitted", admitted)
    session_manager.set_draining(outcome == "pre-drain")
    if outcome == "pre-drain":
        with pytest.raises(DaemonDrainingError):
            await session_manager.resume_session("existing")
        owner._call_resolver.assert_not_awaited()
        assert not started.exists()
        return
    operation = asyncio.create_task(session_manager.resume_session("existing"))
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while not started.exists() and asyncio.get_running_loop().time() < deadline:
            if operation.done():
                await operation
            await asyncio.sleep(.01)
        assert started.exists()
        assert session_manager._active_acquisitions == 1
        assert (await session_manager.drain(timeout=0))["drained"] is False
        if outcome == "cancel":
            for _ in range(2):
                operation.cancel()
                await asyncio.sleep(.02)
                assert not operation.done()
                assert session_manager._active_acquisitions == 1
        release.write_text("settle original activation")
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, timeout=10)
            admitted.assert_not_awaited()
        else:
            assert await asyncio.wait_for(operation, timeout=10) is session
            admitted.assert_awaited_once()
        assert completed.read_text() == "activated"
        assert session_manager._active_acquisitions == 0
        assert (await session_manager.drain(timeout=0))["drained"] is True
        owner._call_resolver.assert_awaited_once()
        assert session.target == target and session.acp_session_id == "retained-conversation"
    finally:
        release.touch()
        if not operation.done():
            operation.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await operation


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["pre-drain", "drain", "cancel"])
async def test_http_activation_remains_owned_until_original_provider_settles(
    delayed_activation_provider, session_manager, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    import httpx
    from fastapi import FastAPI

    from agent_bridge.provider_hosting import CodeSpaceNotRunning
    from agent_bridge.routes import sessions
    from agent_bridge.spawn_target import SpawnTarget

    provider, started, release, completed = delayed_activation_provider
    target = SpawnTarget(type="command", cwd="/actual", explicit_cwd=True, codespace={
        "name": "selected", "workspace_folder": "/actual",
        "portable_receiving_policy": {"version": 1, "activation": "start", "acp_command": "copilot"},
    })
    owner = AgentResolver({}, {})
    owner.register_namespace_resolver(provider)
    owner._call_resolver = AsyncMock(return_value=target)
    monkeypatch.setattr(owner, "refresh_provider_resolvers", lambda: None)
    monkeypatch.setattr(owner, "_related_plugins_for", AsyncMock(return_value=[]))
    provider.ensure_ready = AsyncMock()

    async def hosting(*_args, **_kwargs):
        if not completed.exists():
            raise CodeSpaceNotRunning("stopped")

    monkeypatch.setattr(hosting_policy, "require_target_hosting", hosting)
    monkeypatch.setattr("agent_bridge.venue_launch_policy.gate_raw_codespace_spawn", AsyncMock())
    monkeypatch.setattr(sessions, "apply_request_preferences", lambda *_args: ({}, "caller-settings"))
    app = FastAPI()
    app.state.session_manager = session_manager
    app.state.resolver = owner
    app.state.ready = True
    app.include_router(sessions.router)
    session_manager.set_draining(outcome == "pre-drain")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture",
    ) as client:
        if outcome == "pre-drain":
            response = await client.post("/api/v1/sessions", json={"agent": "codespace:selected"})
            assert response.status_code == 503
            owner._call_resolver.assert_not_awaited()
            assert not started.exists()
            return
        operation = asyncio.create_task(client.post(
            "/api/v1/sessions", json={"agent": "codespace:selected"},
        ))
        try:
            deadline = asyncio.get_running_loop().time() + 10
            while not started.exists() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(.01)
            assert started.exists() and session_manager._active_acquisitions == 1
            assert (await session_manager.drain(timeout=0))["drained"] is False
            if outcome == "cancel":
                for _ in range(2):
                    operation.cancel()
                    await asyncio.sleep(.02)
                    assert not operation.done() and session_manager._active_acquisitions == 1
            release.write_text("settle original activation")
            if outcome == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, timeout=10)
            else:
                assert (await asyncio.wait_for(operation, timeout=10)).status_code == 503
            assert completed.read_text() == "activated"
            assert session_manager._active_acquisitions == 0
            assert (await session_manager.drain(timeout=0))["drained"] is True
            assert session_manager.list_sessions() == []
            assert session_manager.db.execute_read("SELECT * FROM sessions") == []
        finally:
            release.touch()
            if not operation.done():
                operation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await operation


@pytest.mark.asyncio
@pytest.mark.parametrize("moved,became_available", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("explicit_cwd", [False, True])
async def test_stopped_cross_repo_resume_negotiates_retained_repo_before_policy(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch,
    moved: bool, became_available: bool, explicit_cwd: bool,
) -> None:
    from agent_bridge.models import SessionStatus
    from agent_bridge.session_manager import Session
    from agent_bridge.spawn_target import SpawnTarget

    metadata = {
        "name": "selected", "repo": "other", "requested_repo": "other",
        "repo_remote": "https://example.com/owner/other.git",
        "workspace_folder": "/workspaces/other",
        "acp_command": "cd /workspaces/other && legacy-guard && copilot",
    }
    session = Session("existing", "existing", SpawnTarget(
        type="command", codespace=metadata, cwd="/workspaces/other", explicit_cwd=explicit_cwd,
    ), "other@codespace:selected")
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "retained-conversation"
    session_manager._sessions["existing"] = session
    session_manager.db.create_session(
        "existing", "existing", session.agent_name, session.target.cwd, "command", "stopped", 0,
    )
    owner = AgentResolver({}, {})
    provider = CliNamespaceResolver("codespace", "fixture")
    owner.register_namespace_resolver(provider)
    session_manager._resolver = owner
    running = False
    status_reads = 0
    policy_reads = []

    async def provider_call(arguments, **_kwargs):
        nonlocal running
        if arguments == ["start", "selected"]:
            running = True
            return 0, "", ""
        assert arguments == [
            "namespace-resolve", "selected", "--repo", "other",
            "--repo-remote", "https://example.com/owner/other.git",
        ]
        workspace = "/workspaces/home" if moved else "/workspaces/other"
        return 0, json.dumps({
            "type": "command", "spawn_command": ["fixture"], "codespace": {
                **metadata, "workspace_folder": workspace,
                "portable_receiving_policy": {
                    "version": 1, "activation": "start",
                    "acp_command": f"cd {workspace} && copilot --acp --stdio",
                },
            },
        }), ""

    async def read(command, **kwargs):
        from repository_policy import require_agent_hosting

        if "input_bytes" not in kwargs:
            assert "/workspaces/home" in command
            return 0, "/workspaces/home\n", ""
        policy_reads.append(command)
        assert "/workspaces/other" in command and "/workspaces/home" not in command
        assert kwargs["input_bytes"]
        answer = require_agent_hosting(repo, marketplace_id="selected-source")
        return 0, json.dumps({**answer.to_dict(), "allowed": True}), ""

    async def is_running():
        nonlocal running, status_reads
        status_reads += 1
        if became_available and status_reads >= 2:
            running = True
        return running

    guest = SimpleNamespace(is_running=is_running, run=read, close=AsyncMock())
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.CodeSpaceTransport", lambda *_a, **_k: guest,
    )
    monkeypatch.setattr("agent_bridge.venue_launch_policy.gate_raw_codespace_spawn", AsyncMock())
    provider._run = AsyncMock(side_effect=provider_call)
    admitted = AsyncMock(return_value=session)
    monkeypatch.setattr(session_manager, "_resume_session_admitted", admitted)
    if moved:
        with pytest.raises(AgentHostingError, match="changed the retained execution checkout"):
            await session_manager.resume_session("existing")
        admitted.assert_not_awaited()
        assert policy_reads == [] and session.target.codespace is metadata
    else:
        assert await session_manager.resume_session("existing") is session
        assert len(policy_reads) == 1
        assert session.target.cwd == "/workspaces/other" and session.target.explicit_cwd is explicit_cwd
        assert session.target.codespace["requested_repo"] == "other"
    assert session.acp_session_id == "retained-conversation"


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["explicit", "provider", "managed"])
@pytest.mark.parametrize("moved", [False, True])
async def test_real_remote_host_preparation_preserves_retained_checkout(
    session_manager, monkeypatch: pytest.MonkeyPatch, binding: str, moved: bool,
) -> None:
    from agent_bridge.models import SessionStatus
    from agent_bridge.session_manager import Session
    from agent_bridge.spawn_target import SpawnTarget

    workspace = "/provider-default" if moved else "/actual"
    metadata = {
        "name": "selected", "repo": "example/project", "workspace_folder": workspace,
        "acp_command": f"cd {workspace} && copilot --acp --stdio",
        "portable_receiving_policy": {
            "version": 1, "activation": "start",
            "acp_command": f"cd {workspace} && copilot --acp --stdio",
        },
    }
    session = Session("existing", "existing", SpawnTarget(
        type="command", cwd="/actual", codespace=metadata,
        explicit_cwd=binding == "explicit", worktree_id="retained-worktree" if binding == "managed" else None,
    ), "codespace:selected")
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "retained-conversation"
    session.event_log = SimpleNamespace(append=lambda *_args: None)
    session_manager._sessions["existing"] = session
    session_manager.db.create_session(
        "existing", "existing", session.agent_name, "/actual", "command", "stopped", 0,
        target_json=session.target.to_json(), acp_session_id=session.acp_session_id,
    )
    retained_row = session_manager.db.get_session("existing")
    guest = SimpleNamespace(run=AsyncMock(side_effect=AssertionError("unexpected workspace probe")))
    spawner = SimpleNamespace(transport=guest)
    connect = AsyncMock(return_value=("fixture-client", "retained-conversation"))
    monkeypatch.setattr(session_manager, "_connect_via_session_host", connect)
    monkeypatch.setattr(session_manager, "_acquire_codespace_lock", lambda *_args: None)
    monkeypatch.setattr(session_manager, "_release_codespace_lock", lambda *_args: None)
    monkeypatch.setattr("agent_bridge.venue_plugins.resolve_controller_cwd", AsyncMock(return_value=None))
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    monkeypatch.setattr("agent_bridge.session_manager._resolve_relay_launch_env", lambda *_args: ("", None))
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner", lambda *_a, **_k: spawner,
    )
    if moved:
        with pytest.raises(AgentHostingError, match="changed the retained execution checkout"):
            await session_manager._resume_via_new_remote_host(
                session, on_acp_event=None, permission_callback=None,
            )
        connect.assert_not_awaited()
        assert session_manager.db.get_session("existing") == retained_row
        assert session.target.codespace is metadata
        assert not hasattr(spawner, "prepare_child")
    else:
        assert await session_manager._resume_via_new_remote_host(
            session, on_acp_event=None, permission_callback=None,
        ) == ("fixture-client", "retained-conversation")
        assert connect.await_args.kwargs["remote_cwd"] == "/actual"
        assert connect.await_args.kwargs["load_session_id"] == "retained-conversation"
        assert callable(spawner.prepare_child)
    assert session.target.cwd == "/actual" and session.acp_session_id == "retained-conversation"
    assert session.target.worktree_id == ("retained-worktree" if binding == "managed" else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_shipped_receiver_launches_observes_sends_and_reattaches_without_guest_suite(
    repo: Path, tmp_path: Path, legacy: bool,
) -> None:
    from agent_remote_driver.session_host import bundle as core_bundle

    archive, _hash = (
        bundle.build_session_host_bundle(tmp_path / "bundles") if legacy
        else core_bundle.build_session_host_bundle(tmp_path / "bundles")
    )
    state = tmp_path / "authority" / "host.json"
    transcript = tmp_path / "peer.jsonl"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", str(archive),
        "--cwd", str(repo), "--state-file", str(state), "--session-id", "portable-fixture",
        "--mode", "headless", "--backend", "native-acp",
        "--lifetime-policy", "legacy" if legacy else "acp",
        "--unexpected-reap-seconds", "0.05",
        "--repository-policy-context", portable_policy.selected_context(),
        "--", sys.executable, str(Path(__file__).with_name("mock_acp_peer.py")),
        "--acp", "--stdio", "--no-auto-update",
        env={
            **os.environ, "PATH": str(tmp_path / "no-guest-suite"),
            "MOCK_ACP_TRANSCRIPT": str(transcript),
            "AGENT_BRIDGE_SESSION_HOST_NONCE": "fixture-nonce",
        },
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, creationflags=no_window_flags(),
    )
    client = None
    try:
        async def wait_ready() -> None:
            while not state.exists():
                if process.returncode is not None:
                    _out, error = await process.communicate()
                    pytest.fail(error.decode(errors="replace"))
                await asyncio.sleep(0.02)
        await asyncio.wait_for(wait_ready(), 15)
        authority = json.loads(state.read_text())
        assert authority["lifetime_policy"] == ("legacy" if legacy else "acp")
        client = await SessionHostClient.connect(port=authority["port"])
        hello = await client.attach(0, nonce=b"fixture-nonce")
        frames = client.frames()
        cursor = 0

        async def request(number: int, method: str, params: dict) -> dict:
            nonlocal cursor
            await client.write(json.dumps({
                "jsonrpc": "2.0", "id": number, "method": method, "params": params,
            }).encode() + b"\n")
            async def response() -> dict:
                nonlocal cursor
                while True:
                    cursor, raw = await anext(frames)
                    await client.ack(cursor)
                    message = json.loads(raw)
                    if message.get("id") == number:
                        return message["result"]
            return await asyncio.wait_for(response(), 10)

        assert (await request(1, "initialize", {"protocolVersion": 1}))["protocolVersion"] == 1
        conversation = (await request(2, "session/new", {"cwd": str(repo), "mcpServers": []}))["sessionId"]
        result = await request(3, "session/prompt", {
            "sessionId": conversation, "prompt": [{"type": "text", "text": "fixture turn"}],
        })
        assert result["stopReason"] == "end_turn"
        await client.detach(False)
        await client.close()
        client = await SessionHostClient.connect(port=authority["port"])
        resumed = await client.attach(cursor, nonce=b"fixture-nonce")
        assert resumed.child_pid == hello.child_pid == authority["child_pid"]
        records = [json.loads(line) for line in transcript.read_text().splitlines()]
        boots = [row for row in records if row["kind"] == "boot"]
        prompts = [
            row["message"] for row in records
            if row["kind"] == "inbound" and row["message"].get("method") == "session/prompt"
        ]
        assert len(boots) == len(prompts) == 1
        assert boots[0]["session_id"] == prompts[0]["params"]["sessionId"] == conversation
        if not legacy:
            await client.send_status(True)
            await client.detach(True)
            await client.close()
            await asyncio.sleep(0.2)
            assert process.returncode is None
            client = await SessionHostClient.connect(port=authority["port"])
            retained = await client.attach(cursor, nonce=b"fixture-nonce")
            assert retained.child_pid == hello.child_pid
    finally:
        if client is not None:
            with contextlib.suppress(ConnectionError, OSError):
                await client.terminate()
            await client.close()
        try:
            await asyncio.wait_for(process.communicate(), timeout=10)
        except asyncio.TimeoutError:
            from agent_bridge.transport import AgentProcess, SpawnTarget

            await AgentProcess(process, SpawnTarget(type="command")).kill()
        assert process.returncode is not None


@pytest.mark.asyncio
async def test_container_provider_transport_delivers_original_stdin_bytes() -> None:
    from agent_bridge.session_host.container_transport import _run_provider

    payload = b"portable policy archive\x00\n"
    code = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    rc, output, error = await _run_provider(
        [sys.executable, "-I", "-S", "-c", code], timeout=10, input_bytes=payload,
    )
    assert rc == 0 and output == payload and error == b""
