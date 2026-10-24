"""Receiver checks cannot be replaced by retained launch plans or readiness."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from repository_policy import AgentHostingError

from agent_bridge import hosting_policy, local_launch_preparation, provider_creation, transport
from agent_bridge.agent_registry import AgentConfig, AgentResolver, CliNamespaceResolver
from agent_bridge.connect import ConnectTracker
from agent_bridge.session_host import agent_runner, launcher
from agent_bridge.session_host_connection import _SessionHostConnectionMixin
from agent_bridge.transport import SpawnTarget
from test_repository_hosting_policy import deny, local_scope as _local_scope, repo as _repo

local_scope = _local_scope
repo = _repo


@pytest.mark.asyncio
async def test_resolved_execution_checkout_checked_before_plugin_preparation(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects: list[str] = []

    async def resolve(*_a, **_k):
        deny(repo)
        return {"launch": {"cmd": ["copilot"], "work_dir": str(repo)}}

    monkeypatch.setattr(transport, "_resolve_worktree", resolve)
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.prepare_local_plugins",
        AsyncMock(side_effect=lambda *_a, **_k: effects.append("plugins")),
    )
    with pytest.raises(AgentHostingError):
        await transport.resolve_local_launch(
            SpawnTarget(cwd=str(repo), project="source", local_plugin_preparation=True),
        )
    assert effects == []


@pytest.mark.asyncio
async def test_prepared_plan_rechecks_policy(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(*_a, **_k):
        return ["copilot"], str(repo), {}

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", resolve)
    target = SpawnTarget(cwd=str(repo))
    plan = await local_launch_preparation.prepare(target, "reserved")
    deny(repo)
    with pytest.raises(AgentHostingError):
        plan.resolve(target, "reserved")


@pytest.mark.asyncio
async def test_host_connection_checks_retained_actual_cwd_not_only_target(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    eligible = tmp_path / "eligible"
    eligible.mkdir()
    target = SpawnTarget(cwd=str(eligible))
    plan = local_launch_preparation.PreparedLocalLaunch(
        "reserved", target.to_json(), ("copilot",), str(repo), {},
    )
    deny(repo)
    effects: list[str] = []
    spawner = SimpleNamespace(
        boundary="local", spawn=AsyncMock(side_effect=lambda *_a, **_k: effects.append("spawn")),
    )
    monkeypatch.setattr(
        "agent_bridge.local_static_preparation.prepare_for_launch",
        AsyncMock(side_effect=lambda *_a, **_k: effects.append("guidance")),
    )
    with pytest.raises(AgentHostingError):
        await _SessionHostConnectionMixin._connect_via_session_host(
            SimpleNamespace(), target, tracker=ConnectTracker(), session_id="reserved",
            on_acp_event=None, permission_callback=None, spawner=spawner,
            prepared_local_launch=plan,
        )
    assert effects == []


@pytest.mark.asyncio
async def test_receiver_overridden_cwd_checked_before_resolution(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    eligible = tmp_path / "eligible"
    eligible.mkdir()
    deny(repo)
    resolver = SimpleNamespace(resolve_async=AsyncMock(return_value=SpawnTarget(cwd=str(eligible))))
    resolve = AsyncMock()
    host = AsyncMock()
    monkeypatch.setattr(transport, "resolve_local_launch", resolve)
    monkeypatch.setattr(launcher, "run_host", host)
    with pytest.raises(AgentHostingError):
        await agent_runner.run_agent_session_host("named", cwd=str(repo), resolver=resolver)
    resolve.assert_not_awaited()
    host.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_host_backstop_precedes_survival_spawn_state_and_ready(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deny(repo)
    effects: list[str] = []
    monkeypatch.setattr(launcher, "apply_host_survival", lambda: effects.append("survival"))
    monkeypatch.setattr(
        launcher, "_spawn_child", AsyncMock(side_effect=lambda *_a, **_k: effects.append("spawn")),
    )
    monkeypatch.setattr(launcher, "_write_host_state", lambda *_a: effects.append("state"))
    ready = MagicMock()
    with pytest.raises(AgentHostingError):
        await launcher.run_host(["copilot"], cwd=str(repo), ready=ready)
    assert effects == []
    ready.set.assert_not_called()


@pytest.mark.asyncio
async def test_direct_spawn_rechecks_after_retained_resolution(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(*_a, **_k):
        deny(repo)
        return ["copilot"], str(repo), {}

    monkeypatch.setattr(transport, "resolve_local_launch", resolve)
    spawn = AsyncMock()
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(AgentHostingError):
        await transport.spawn_local(SpawnTarget(cwd=str(repo)))
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_opaque_provider_refuses_before_journal_or_spawn(
    session_manager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects: list[str] = []
    monkeypatch.setattr(
        session_manager, "start_session", AsyncMock(side_effect=lambda *_a, **_k: effects.append("spawn")),
    )
    with pytest.raises(AgentHostingError, match="receiver cannot enforce"):
        await provider_creation.start(
            session_manager, SpawnTarget(type="command", spawn_command=["provider"]),
            key="reserved", prompt=None,
        )
    assert effects == []
    assert session_manager.db.execute_read("SELECT * FROM provider_embodiments") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["provider:named", "source@provider:named", "named"])
async def test_namespace_refuses_before_readiness_can_cold_start(
    address: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = AgentResolver({}, {})
    resolved = AsyncMock(side_effect=AssertionError("opaque receiver must refuse before resolution"))

    async def resolve(name, *, extra_plugins=(), repo=None, repo_remote=None):
        return await resolved(
            name, extra_plugins=extra_plugins, repo=repo, repo_remote=repo_remote,
        )

    provider = SimpleNamespace(
        prefix="provider", ensure_ready=AsyncMock(), resolve=resolve,
    )
    resolver.register_namespace_resolver(provider)
    monkeypatch.setattr(resolver, "refresh_provider_resolvers", lambda: None)
    monkeypatch.setattr(
        resolver, "_gather_bare_candidates", AsyncMock(return_value=[("provider:named", provider, "named")]),
    )
    monkeypatch.setattr("agent_bridge.agent_registry.resolve_repo_remote", lambda _r: "remote")
    with pytest.raises(AgentHostingError, match="namespace receiver"):
        await resolver.resolve_async(address)
    provider.ensure_ready.assert_not_awaited()
    resolved.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("denied_at", [None, "before-readiness", "after-readiness"])
async def test_worktree_namespace_checks_fresh_owner_path_before_staging(
    repo: Path, monkeypatch: pytest.MonkeyPatch, denied_at: str | None,
) -> None:
    resolver = AgentResolver({}, {})
    provider = CliNamespaceResolver("dispatch", "agent-dispatch")
    resolver.register_namespace_resolver(provider)
    monkeypatch.setattr(resolver, "refresh_provider_resolvers", lambda: None)
    monkeypatch.setattr(provider, "_run", AsyncMock(return_value=(0, json.dumps({
        "type": "worktree", "worktree_id": "workspace", "project": "execution-project",
        "cwd": "/unverified/provider/path",
    }), "")))
    owner = AsyncMock(return_value={"path": str(repo)})
    monkeypatch.setattr("agent_bridge.acquisition_authority.read_worktree_record", owner)
    related = AsyncMock(return_value=[])
    monkeypatch.setattr(resolver, "_related_plugins_for", related)

    async def ready(_name):
        if denied_at == "after-readiness":
            deny(repo)

    provider.ensure_ready = AsyncMock(side_effect=ready)
    if denied_at == "before-readiness":
        deny(repo)
    if denied_at is None:
        target = await resolver.resolve_async("dispatch:task")
        assert target.project == "execution-project" and target.worktree_id == "workspace"
        related.assert_awaited_once()
    else:
        with pytest.raises(AgentHostingError, match="expose_agent: false"):
            await resolver.resolve_async("dispatch:task")
        related.assert_not_awaited()
    if denied_at == "before-readiness":
        provider.ensure_ready.assert_not_awaited()
    else:
        provider.ensure_ready.assert_awaited_once_with("task")
    assert owner.await_args_list
    assert all(call.args == ("workspace", "execution-project") for call in owner.await_args_list)


@pytest.mark.asyncio
async def test_ssh_unenforcing_receiver_never_opens_agent_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = SimpleNamespace(
        exec_command=AsyncMock(return_value=SimpleNamespace(
            timed_out=False, exit_code=2, stdout="", stderr="unknown command: agent-policy",
        )),
        open_stdio_channel=AsyncMock(), ensure_connected=AsyncMock(),
    )
    monkeypatch.setattr("ssh_manager.get_default_manager", lambda: manager)
    monkeypatch.setattr(transport, "get_default_manager", lambda: manager)
    with pytest.raises(AgentHostingError, match="unknown command"):
        await transport.spawn_ssh(SpawnTarget(
            type="ssh", host="receiver", project="source", cwd="/workspace", explicit_cwd=True,
        ))
    manager.open_stdio_channel.assert_not_awaited()
    manager.ensure_connected.assert_not_awaited()
    command = manager.exec_command.call_args.args[1]
    assert "agent-policy check --path /workspace" in command


@pytest.mark.asyncio
@pytest.mark.parametrize("worktree_id", [None, "exact-worktree"])
async def test_project_only_ssh_queries_receiving_owner_metadata(
    monkeypatch: pytest.MonkeyPatch, worktree_id: str | None,
) -> None:
    manager = SimpleNamespace(exec_command=AsyncMock(return_value=SimpleNamespace(
        timed_out=False, exit_code=0, stdout=json.dumps({
            "version": 1, "allowed": True, "repository": "/remote/source",
            "expose_agent": None, "sources": [], "denied_by": [],
        }), stderr="",
    )))
    monkeypatch.setattr("ssh_manager.get_default_manager", lambda: manager)
    await hosting_policy.require_target_hosting(SpawnTarget(
        type="ssh", host="receiver", project="harness", worktree_id=worktree_id,
    ))
    command = manager.exec_command.call_args.args[1]
    assert command.startswith("harness agent-policy check ")
    assert (
        "--worktree-id exact-worktree" if worktree_id else "--project-root"
    ) in command
    assert "resolve" not in command


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_ssh_receiving_shell_rechecks_before_launch(shell: str) -> None:
    target = SpawnTarget(
        type="ssh", host="receiver", project="source", cwd="/workspace",
        explicit_cwd=True, ssh_shell=shell,
    )
    command = transport._build_remote_cmd(target)
    if shell == "powershell":
        command = base64.b64decode(command.split()[-1]).decode("utf-16-le")
        assert "if ($LASTEXITCODE -ne 0)" in command
    assert command.index("agent-policy") < command.rindex("copilot" if shell == "bash" else "powershell")


@pytest.mark.parametrize("payload", [
    {}, {"version": 1}, {"version": True}, {"version": 2},
    {"version": 1, "repository": "/workspace", "expose_agent": False, "sources": [], "denied_by": []},
    {"version": 1, "repository": "/workspace", "expose_agent": "true", "sources": [], "denied_by": []},
])
def test_unsupported_policy_answer_is_never_an_allow(payload: dict) -> None:
    with pytest.raises(AgentHostingError):
        hosting_policy.validate_policy_result(payload)


@pytest.mark.parametrize("changes", [
    {"allowed": False},
    {"allowed": None},
    {"allowed": 1},
    {"allowed": "true"},
    {"version": True},
    {"version": 1.0},
    {"version": "1"},
    {"version": 2},
    {"repository": ""},
    {"repository": " "},
    {"error": "receiver refused"},
    {"code": "repository-agent-exposure-denied"},
    {"expose_agent": False},
    {"expose_agent": "true"},
    {"sources": "config.yaml"},
    {"sources": [None]},
    {"sources": [""]},
    {"denied_by": ["config.yaml"]},
    {"denied_by": None},
])
def test_check_result_requires_explicit_allow_and_well_formed_success(changes: dict) -> None:
    payload = {
        "version": 1, "allowed": True, "repository": "/workspace",
        "expose_agent": None, "sources": [], "denied_by": [],
        **changes,
    }
    with pytest.raises(AgentHostingError):
        hosting_policy.validate_policy_result(payload)


def test_policy_object_without_check_allow_is_not_an_admission() -> None:
    with pytest.raises(AgentHostingError):
        hosting_policy.validate_policy_result({
            "version": 1, "repository": "/workspace", "expose_agent": None,
            "sources": [], "denied_by": [],
        })


def test_valid_negative_eligibility_answer_grants_no_other_authority() -> None:
    for value in (None, True):
        hosting_policy.validate_policy_result({
            "version": 1, "allowed": True, "repository": "/workspace", "expose_agent": value,
            "sources": [], "denied_by": [],
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_supported_container_checks_actual_instance_user_and_guest_before_launch(
    monkeypatch: pytest.MonkeyPatch, denied: bool,
) -> None:
    calls = []
    instance = "a" * 64

    async def run(argv, **_kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return 0, json.dumps([{"Id": instance, "State": {"Running": True}}]).encode(), b""
        assert argv[:7] == ["docker", "exec", "-u", "worker", instance, "bash", "-lc"]
        assert "cd -- /actual" in argv[-1] and "agent-policy check" in argv[-1]
        if denied:
            return 3, b"", b"expose_agent: false"
        return 0, json.dumps({
            "version": 1, "allowed": True, "repository": "/actual",
            "expose_agent": None, "sources": [], "denied_by": [],
        }).encode(), b""

    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", run)
    target = SpawnTarget(
        type="command", user="worker", container={"name": "member", "workspace_folder": "/actual"},
        venue={"instance_id": instance}, spawn_command=["provider", "exec"],
    )
    if denied:
        with pytest.raises(AgentHostingError, match="expose_agent"):
            await hosting_policy.require_target_hosting(target)
    else:
        await hosting_policy.require_target_hosting(target)
    assert len(calls) == 2
    assert not any("session-host-prepare" in argv for argv in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_supported_codespace_checks_guest_and_closes_inspection(
    monkeypatch: pytest.MonkeyPatch, denied: bool,
) -> None:
    calls = []

    async def run(command, **_kwargs):
        calls.append(command)
        if denied:
            return 3, "", "expose_agent: false"
        return 0, json.dumps({
            "version": 1, "allowed": True, "repository": "/actual",
            "expose_agent": None, "sources": [], "denied_by": [],
        }), ""

    guest = SimpleNamespace(is_running=AsyncMock(return_value=True), run=run, close=AsyncMock())
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.CodeSpaceTransport", lambda *_a, **_k: guest,
    )
    target = SpawnTarget(type="command", codespace={"name": "member", "workspace_folder": "/actual"})
    if denied:
        with pytest.raises(AgentHostingError, match="expose_agent"):
            await hosting_policy.require_target_hosting(target)
    else:
        await hosting_policy.require_target_hosting(target)
    assert len(calls) == 1 and "agent-policy check" in calls[0]
    guest.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_project_without_cwd_uses_read_only_owner_and_honors_denial(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deny(repo)
    calls = []

    async def command(argv, project):
        calls.append((argv, project))
        return {
            "version": 1, "allowed": True, "repository": str(repo),
            "expose_agent": None, "sources": [], "denied_by": [],
        }

    monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", command)
    with pytest.raises(AgentHostingError, match="expose_agent"):
        await hosting_policy.require_target_hosting(SpawnTarget(project="registered"))
    assert len(calls) == 1
    argv, project = calls[0]
    assert project == "registered"
    assert argv == [
        "agent-policy", "check", "--project-root", "--operation", "launch", "--json",
    ]


def test_portable_bundle_contains_enforcing_dependency_free_adapter() -> None:
    from agent_bridge.session_host import bundle

    sources = dict(bundle._bundle_sources())
    assert sources["agent_bridge/hosting_policy.py"].name == "receiver_policy.py"


@pytest.mark.parametrize("allowed", [True, False, None, 1])
def test_portable_receiver_requires_explicit_guest_policy_allow(
    monkeypatch: pytest.MonkeyPatch, allowed,
) -> None:
    from agent_bridge.session_host import receiver_policy

    monkeypatch.setattr(receiver_policy.shutil, "which", lambda _name: "/guest/agent-worktrees")
    query = MagicMock(return_value=SimpleNamespace(
        returncode=0, stderr="", stdout=json.dumps({
            "version": 1, "allowed": allowed, "repository": "/actual",
            "expose_agent": None, "sources": [], "denied_by": [],
        }),
    ))
    monkeypatch.setattr(receiver_policy.subprocess, "run", query)
    if allowed is True:
        receiver_policy.require_path_hosting("/actual")
    else:
        with pytest.raises(ValueError, match="explicit version-1 allow"):
            receiver_policy.require_path_hosting("/actual")
    assert query.call_args.args[0][1:5] == ["agent-policy", "check", "--path", "/actual"]


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_supported_namespace_admission_precedes_readiness(
    repo: Path, monkeypatch: pytest.MonkeyPatch, denied: bool,
) -> None:
    from repository_policy import require_agent_hosting

    from agent_bridge.agent_registry_namespace import CliNamespaceResolver

    resolver = AgentResolver({}, {})
    provider = CliNamespaceResolver("codespace", "agent-codespaces")
    provider._run = AsyncMock(return_value=(0, json.dumps({
        "type": "command", "spawn_command": ["provider"],
        "codespace": {"name": "member", "workspace_folder": "/actual"},
    }), ""))
    provider.ensure_ready = AsyncMock()
    resolver.register_namespace_resolver(provider)
    monkeypatch.setattr(resolver, "refresh_provider_resolvers", lambda: None)
    monkeypatch.setattr(resolver, "_related_plugins_for", AsyncMock(return_value=[]))
    calls = []

    async def query(target, *, operation):
        calls.append(operation)
        return {**require_agent_hosting(repo, operation=operation).to_dict(), "allowed": True}

    monkeypatch.setattr("agent_bridge.provider_hosting.query_provider_policy", query)
    if denied:
        deny(repo)
        with pytest.raises(AgentHostingError):
            await resolver.resolve_async("codespace:member")
        provider.ensure_ready.assert_not_awaited()
        assert calls == ["namespace readiness"]
    else:
        target = await resolver.resolve_async("codespace:member")
        assert target.type == "command"
        provider.ensure_ready.assert_awaited_once_with("member")
        assert calls[0] == "namespace readiness" and len(calls) == 3


@pytest.mark.asyncio
async def test_session_reference_namespace_is_inspection_not_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge.agent_registry_namespace import CliNamespaceResolver

    resolver = AgentResolver({}, {})
    provider = CliNamespaceResolver("dispatch", "agent-dispatch")
    provider._run = AsyncMock(return_value=(0, json.dumps({
        "type": "session", "venue": {"task": {"owner_session_id": "existing"}},
    }), ""))
    provider.ensure_ready = AsyncMock()
    resolver.register_namespace_resolver(provider)
    monkeypatch.setattr(resolver, "refresh_provider_resolvers", lambda: None)
    target = await resolver.resolve_async("dispatch:task")
    assert target.type == "session" and target.venue["task"]["owner_session_id"] == "existing"
    provider.ensure_ready.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["bare", "admin"])
async def test_elevated_policy_denial_precedes_subdaemon_effects(
    repo: Path, monkeypatch: pytest.MonkeyPatch, route: str,
) -> None:
    from agent_bridge import elevated
    from agent_bridge.admin_resolver import AdminResolver

    deny(repo)
    resolver = AgentResolver({
        "worker": AgentConfig(name="worker", cwd=str(repo), requires_admin=True),
    }, {})
    monkeypatch.setattr(resolver, "_own_plugin_args", lambda *_a: [])
    monkeypatch.setattr(resolver, "_related_plugin_args", lambda *_a: [])
    monkeypatch.setattr(elevated, "relay_applicable", lambda _required: True)
    start = MagicMock(side_effect=AssertionError("denied repository must not start a subdaemon"))
    monkeypatch.setattr(elevated, "ensure_running", start)
    if route == "admin":
        resolver.register_namespace_resolver(AdminResolver(resolver))
    with pytest.raises(AgentHostingError, match="expose_agent"):
        await resolver.resolve_async("admin:worker" if route == "admin" else "worker")
    start.assert_not_called()


@pytest.mark.asyncio
async def test_read_only_protocol_does_not_admit_an_opaque_receiver(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge.agent_registry_namespace import CliNamespaceResolver

    resolver = AgentResolver({}, {})
    provider = CliNamespaceResolver("custom", "isolated-fixture-provider")
    provider._run = AsyncMock(return_value=(0, json.dumps({
        "type": "command", "spawn_command": ["opaque"],
    }), ""))
    provider.ensure_ready = AsyncMock()
    resolver.register_namespace_resolver(provider)
    monkeypatch.setattr(resolver, "refresh_provider_resolvers", lambda: None)
    with pytest.raises(AgentHostingError, match="receiver cannot enforce"):
        await resolver.resolve_async("custom:worker")
    provider.ensure_ready.assert_not_awaited()
