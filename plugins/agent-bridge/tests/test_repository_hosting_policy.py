"""Repository denial is fresh negative eligibility, not machine exposure or custody."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from repository_policy import AgentHostingError

from agent_bridge import hosting_policy, local_launch_preparation
from agent_bridge.agent_registry import AgentConfig, AgentResolver, discover_local_agents
from agent_bridge.models import SessionStatus, StartSessionRequest
from agent_bridge.routes import sessions
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget


def deny(repo: Path, text: str = "expose_agent: false\n") -> None:
    policy = repo / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(text, encoding="utf-8")


@pytest.fixture(autouse=True)
def local_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_INSTALL_DIR", raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "source"
    path.mkdir()
    (path / ".git").mkdir()
    return path


def test_repository_false_beats_stale_project_true(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = tmp_path / "projects.yaml"
    projects.write_text(
        f"projects:\n  source:\n    anchor: {repo}\n    expose_agent: true\n", encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(projects))
    assert "source" in discover_local_agents()
    deny(repo)
    assert "source" not in discover_local_agents()


def test_historical_machine_false_is_only_discovery(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = tmp_path / "projects.yaml"
    projects.write_text(
        f"projects:\n  source:\n    anchor: {repo}\n    expose_agent: false\n", encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(projects))
    assert discover_local_agents() == {}
    hosting_policy.require_path_hosting(repo)
    deny(repo, "expose_agent: true\n")
    hosting_policy.require_path_hosting(repo)


@pytest.mark.asyncio
async def test_direct_repo_absent_policy_needs_no_catalog_or_project_adoption(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "not-adopted.yaml"))
    owner = AsyncMock(side_effect=AssertionError("direct checkout admission must not adopt or resolve a project"))
    monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", owner)
    await hosting_policy.require_target_hosting(SpawnTarget(cwd=str(repo)))
    owner.assert_not_awaited()
    assert discover_local_agents() == {}
    assert not (tmp_path / "not-adopted.yaml").exists()


@pytest.mark.asyncio
async def test_project_only_target_reads_owner_source_without_launch_resolution(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = AsyncMock(return_value={
        "version": 1, "allowed": True, "repository": str(repo),
        "expose_agent": None, "sources": [], "denied_by": [],
    })
    monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", owner)
    await hosting_policy.require_target_hosting(SpawnTarget(project="harness"))
    owner.assert_awaited_once_with(
        ["agent-policy", "check", "--project-root", "--operation", "launch", "--json"],
        "harness",
    )
    deny(repo)
    with pytest.raises(AgentHostingError, match="expose_agent: false"):
        await hosting_policy.require_target_hosting(SpawnTarget(project="harness"))


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["expose_agent: false\n", "expose_agent: 'false'\n"])
async def test_named_target_refuses_before_session_or_preparation(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch, policy: str,
) -> None:
    deny(repo, policy)
    effects: list[str] = []
    monkeypatch.setattr(
        local_launch_preparation, "resolve_local_launch",
        AsyncMock(side_effect=lambda *_a, **_k: effects.append("prepare")),
    )
    with pytest.raises(AgentHostingError, match="expose_agent"):
        await session_manager.start_session(
            SpawnTarget(cwd=str(repo)), agent_name="code-review", copilot_args=["--agent", "code-review"],
        )
    assert effects == []
    assert session_manager.list_sessions() == []
    assert session_manager.db.execute_read("SELECT * FROM sessions") == []
    assert session_manager.db.execute_read("SELECT * FROM turns") == []


@pytest.mark.asyncio
async def test_actual_request_cwd_override_wins_before_manager_effects(
    repo: Path, tmp_path: Path, session_manager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = tmp_path / "eligible"
    allowed.mkdir()
    deny(repo)
    target = SpawnTarget(cwd=str(allowed), project="registered")
    resolver = SimpleNamespace(resolve_async=AsyncMock(return_value=target))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        session_manager=session_manager, resolver=resolver, ready=True,
    )))
    monkeypatch.setattr(sessions, "apply_request_preferences", lambda *_a: ({}, "harness-default"))
    with pytest.raises(AgentHostingError, match="expose_agent: false"):
        await sessions.start_session(
            StartSessionRequest(agent="explicit", target_dir=str(repo)), request,
        )
    assert target.cwd == str(repo) and target.explicit_cwd
    assert session_manager.db.execute_read("SELECT * FROM sessions") == []


@pytest.mark.asyncio
async def test_policy_added_during_plan_resolution_precedes_registration(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects: list[str] = []

    async def resolve(*_args, **_kwargs):
        deny(repo)
        return ["copilot", "--acp"], str(repo), {}

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", resolve)
    monkeypatch.setattr(
        session_manager, "_connect_via_session_host",
        AsyncMock(side_effect=lambda *_a, **_k: effects.append("spawn")),
    )
    with pytest.raises(AgentHostingError):
        await session_manager.start_session(SpawnTarget(cwd=str(repo)))
    assert effects == []
    assert session_manager.list_sessions() == []
    assert session_manager.db.execute_read("SELECT * FROM sessions") == []


@pytest.mark.asyncio
async def test_policy_added_in_creation_callback_precedes_registration(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(*_args, **_kwargs):
        return ["copilot", "--acp"], str(repo), {}

    async def before_launch() -> None:
        deny(repo)

    monkeypatch.setattr(local_launch_preparation, "resolve_local_launch", resolve)
    with pytest.raises(AgentHostingError):
        await session_manager.start_session(
            SpawnTarget(cwd=str(repo), explicit_cwd=True), creation_session_id="reserved",
            creation_before_launch=before_launch,
        )
    assert session_manager.db.execute_read("SELECT * FROM sessions") == []
    assert session_manager.db.execute_read("SELECT * FROM turns") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, "explicit", "affinity"])
async def test_nonprojecting_local_preparation_preserves_existing_provenance(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch, origin: str | None,
) -> None:
    overrides = {"env": {"REQUEST": "kept"}, "copilot_args": ["--request-flag"]}
    if origin is not None:
        overrides["controller_worktree_origin"] = origin
    target = SpawnTarget(
        cwd=str(repo), venue={"kind": "provider", "_agent_bridge_request_overrides": overrides},
    )

    class PreparedBoundary(Exception):
        pass

    async def prepare(current, _session_id):
        assert current.venue["_agent_bridge_request_overrides"] == overrides
        raise PreparedBoundary

    monkeypatch.setattr(local_launch_preparation, "prepare", prepare)
    with pytest.raises(PreparedBoundary):
        await session_manager.start_session(target)
    assert target.venue["_agent_bridge_request_overrides"] == overrides
    assert session_manager.db.execute_read("SELECT * FROM sessions") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resume", "resync", "handoff", "implicit"])
async def test_late_policy_preserves_custody_and_refuses_reembodiment(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    target = SpawnTarget(cwd=str(repo), worktree_id="workspace")
    session = Session("existing", "existing", target, "source")
    session.status = SessionStatus.STOPPED if operation == "resume" else SessionStatus.IDLE
    session.acp_session_id = "native"
    session.event_log = MagicMock()
    session.client = MagicMock(is_running=False)
    session_manager._sessions["existing"] = session
    old_status = session.status
    effects: list[str] = []
    for method in ("_try_reattach_live_host", "_refresh_provider_target", "_run_turn_sync", "stop_session"):
        monkeypatch.setattr(
            session_manager, method, AsyncMock(side_effect=lambda *_a, **_k: effects.append("effect")),
        )
    deny(repo)
    with pytest.raises(AgentHostingError):
        if operation == "resume":
            await session_manager.resume_session("existing", allow_recreate=True)
        elif operation == "resync":
            await session_manager.resync_session("existing")
        elif operation == "handoff":
            await session_manager.handoff_session("existing", seed_text="retained seed")
        else:
            await session_manager.submit_prompt("existing", "cold-start seed")
    assert effects == []
    assert session_manager.get_session("existing") is session
    assert session.status is old_status and session.acp_session_id == "native"
    session.client.shutdown.assert_not_called()
    session.event_log.append.assert_not_called()
    assert session_manager.db.execute_read("SELECT * FROM turns") == []


def test_read_only_session_inspection_does_not_check_new_policy(
    repo: Path, session_manager,
) -> None:
    session = Session("existing", "existing", SpawnTarget(cwd=str(repo)), "source")
    session_manager._sessions["existing"] = session
    deny(repo)
    assert session_manager.get_session("existing") is session
    assert session in session_manager.list_sessions()


def test_selected_marketplace_cannot_erase_base_false(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deny(repo)
    overlay = repo / ".copilot-extensions/agent-worktrees/marketplaces/selected/config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("expose_agent: true\n", encoding="utf-8")
    root = tmp_path / "marketplaces/selected/plugins/agent-bridge"
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_bridge_owner_root", lambda: root)
    validated = Mock(return_value={"marketplaceId": "selected"})
    monkeypatch.setattr("agent_bridge._peer_launch.validate_owner", validated)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    with pytest.raises(AgentHostingError, match="expose_agent: false"):
        hosting_policy.require_path_hosting(repo)
    validated.assert_called_once_with("agent-bridge", root, str(root / "install.json"))


def test_invalid_installation_scope_does_not_silently_drop_overlay(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{invalid")
    with pytest.raises(AgentHostingError, match="installation context"):
        hosting_policy.require_path_hosting(repo)


def test_overlay_only_false_uses_validated_receiving_marketplace(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay = repo / ".copilot-extensions/agent-worktrees/marketplaces/selected/config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("expose_agent: false\n", encoding="utf-8")
    root = tmp_path / "marketplaces/selected/plugins/agent-bridge"
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_bridge_owner_root", lambda: root)
    validated = Mock(return_value={"marketplaceId": "selected"})
    monkeypatch.setattr("agent_bridge._peer_launch.validate_owner", validated)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    with pytest.raises(AgentHostingError, match="expose_agent: false"):
        hosting_policy.require_path_hosting(repo)
    assert validated.call_count == 1


@pytest.mark.parametrize("raw", [
    '{"marketplaceId": "unvalidated"}',
    "/missing/installation-receipt.json",
])
def test_invalid_context_refuses_even_without_repository_policy(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    root = tmp_path / "marketplaces/selected/plugins/agent-bridge"
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_bridge_owner_root", lambda: root)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", raw)
    with pytest.raises(AgentHostingError, match="invalid installation context"):
        hosting_policy.require_path_hosting(repo)


def test_namespaced_receiver_uses_implicit_canonical_receipt(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "marketplaces/selected/plugins/agent-bridge"
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_bridge_owner_root", lambda: root)
    validated = Mock(return_value={"marketplaceId": "selected"})
    monkeypatch.setattr("agent_bridge._peer_launch.validate_owner", validated)
    hosting_policy.require_path_hosting(repo)
    validated.assert_called_once_with("agent-bridge", root, str(root / "install.json"))


def test_missing_implicit_receipt_never_becomes_unscoped_policy(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "marketplaces/selected/plugins/agent-bridge"
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_bridge_owner_root", lambda: root)
    with pytest.raises(AgentHostingError, match="invalid installation context"):
        hosting_policy.require_path_hosting(repo)


def test_nonlegacy_root_without_context_refuses(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_bridge.session_lifecycle_cli._agent_bridge_owner_root", lambda: tmp_path / "custom",
    )
    with pytest.raises(AgentHostingError, match="non-legacy installation context is unavailable"):
        hosting_policy.require_path_hosting(repo)


def test_receiving_context_is_revalidated_not_cached(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlay = repo / ".copilot-extensions/agent-worktrees/marketplaces/selected/config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("expose_agent: true\n", encoding="utf-8")
    root = tmp_path / "marketplaces/selected/plugins/agent-bridge"
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_bridge_owner_root", lambda: root)
    validated = Mock(side_effect=[
        {"marketplaceId": "selected"}, ValueError("namespace deactivated"),
    ])
    monkeypatch.setattr("agent_bridge._peer_launch.validate_owner", validated)
    hosting_policy.require_path_hosting(repo)
    with pytest.raises(AgentHostingError, match="namespace deactivated"):
        hosting_policy.require_path_hosting(repo)
    assert validated.call_count == 2


@pytest.mark.asyncio
async def test_static_explicit_target_still_requires_launch_admission(
    repo: Path, session_manager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = AgentResolver({"named": AgentConfig(name="named", cwd=str(repo))}, {})
    monkeypatch.setattr(resolver, "_own_plugin_args", lambda *_a: [])
    monkeypatch.setattr(resolver, "_related_plugin_args", lambda *_a: [])
    target = resolver.resolve("named")
    deny(repo)
    with pytest.raises(AgentHostingError):
        await session_manager.start_session(target, agent_name="named")
    assert session_manager.db.execute_read("SELECT * FROM sessions") == []


@pytest.mark.asyncio
async def test_exact_owner_checkout_wins_over_configured_anchor(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    deny(repo)
    monkeypatch.setattr(
        "agent_bridge.acquisition_authority.read_worktree_record",
        AsyncMock(return_value={"path": str(repo)}),
    )
    with pytest.raises(AgentHostingError):
        await hosting_policy.require_target_hosting(SpawnTarget(
            project="source", worktree_id="exact", cwd=str(anchor),
        ))
