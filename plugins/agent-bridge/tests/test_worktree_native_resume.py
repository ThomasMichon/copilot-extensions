"""Cold ordinary resume preserves owner-asserted native history in either mode."""

from __future__ import annotations

import json
import base64
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from agent_bridge import worktree_native_resume as native_resume
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.app import create_app
from agent_bridge.acquisition_store import AcquisitionStore
from agent_bridge.db import Database
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.routes import worktrees as wt
from agent_bridge.routes import worktree_probe
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget
from agent_bridge.transport import _build_remote_cmd

pytestmark = pytest.mark.contract("agent_bridge.worktree_native_resume")


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    db = Database(tmp_path / "bridge.db")
    manager = SessionManager(db)
    entry = wt._WorktreeEntry(
        id="wt-example", agent_name="example", machine="example",
        path=str(tmp_path / "workspace"), branch="worktree/example", status="active",
    )
    config = SimpleNamespace(project="example-project", host=None)
    target = SpawnTarget(type="local", project=config.project, cwd=entry.path)
    resolver = SimpleNamespace(agents={"example": config}, resolve=Mock(return_value=target))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db=db, session_manager=manager, resolver=resolver,
    )))
    monkeypatch.setattr(wt, "_find_cached_worktree_entry", AsyncMock(return_value=("example", entry)))
    monkeypatch.setattr(native_resume, "_apply_bound_charter", lambda target, *args: target)
    head = {
        "tracked": True, "worktree_id": entry.id, "head_session": "native-original",
        "state": "active", "head_revision": 7, "pending_handoffs": [],
    }
    preflight = {
        "ok": True, "dry_run": True, "worktree_id": entry.id,
        "work_dir": entry.path, "resume_session": "native-original",
        "native_admission_version": 1,
    }
    confirmation = Path(__file__).resolve().parents[1] / (
        "contract/fixtures/http/current/native-worktree-confirmation-generation-39.json"
    )
    launch_receipt = json.loads(confirmation.read_text(encoding="utf-8"))["owner_launch"]
    launch_receipt.update(
        worktree_id=entry.id, work_dir=entry.path, resume_session="native-original",
    )
    calls = []
    state = {"head": head, "preflight": preflight, "launched": False, "record": {
        "id": entry.id, "path": entry.path, "repo": config.project, "status": "active",
        "last_session_id": "native-original",
        "execution_leg_opaque": False, "session_backend_opaque": False,
        "bound_agent": None,
    }, "launch": launch_receipt}

    async def owner(_owner, _config, _resolver, args):
        calls.append(args)
        if args[0] == "head-session":
            return json.dumps(state["head"])
        if args[0] == "list":
            raise AssertionError("native admission must not read an advisory inventory")
        if args[0] == "record":
            return json.dumps({"ok": True, "worktree": state["record"]})
        if args[0] == "embody":
            return json.dumps(state["preflight"])
        if args[0] == "list-sessions":
            return json.dumps({
                "head_session": "native-original",
                "sessions": [{"id": "native-original", "live": state["launched"]}],
            })
        raise AssertionError(args)

    async def launch(_owner, _config, _resolver, args):
        calls.append(args)
        state["launched"] = True
        return json.dumps(state["launch"])

    async def load(session_id, *, drain, allow_recreate, owner_native_resume):
        assert drain is False and allow_recreate is False
        assert owner_native_resume is True
        session = manager.get_session(session_id)
        assert session.acp_session_id == "native-original"
        session.status = SessionStatus.IDLE
        return session

    monkeypatch.setattr(wt, "_run_for_agent", owner)
    monkeypatch.setattr(native_resume, "run_owner_operation", launch)
    manager.resume_session = AsyncMock(side_effect=load)
    manager.start_session = AsyncMock(side_effect=AssertionError("must never create native history"))
    yield SimpleNamespace(
        db=db, manager=manager, request=request, entry=entry, state=state, calls=calls,
    )
    db.close()


@pytest.mark.asyncio
async def test_cold_acp_imports_exact_owner_head_without_bridge_record(boundary):
    result = await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert result.acp_session_id == result.durable_session_id == "native-original"
    assert result.mode == "acp"
    assert result.target_dir == boundary.entry.path
    boundary.manager.start_session.assert_not_called()
    boundary.manager.resume_session.assert_awaited_once()
    assert boundary.db.get_session(result.session_id)["acp_session_id"] == "native-original"
    restarted = SessionManager(boundary.db)
    assert restarted.get_session(result.session_id).acp_session_id == "native-original"
    assert not boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
@pytest.mark.parametrize("worktree_id", [None, 12, "../other", "wt&other", "wt\nother", "wt other", "wt..other", "wt\0other"])
async def test_invalid_record_ids_refuse_before_owner_commands(boundary, worktree_id):
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree(worktree_id, boundary.request, "acp")
    assert refused.value.status_code == 400
    assert not boundary.calls


@pytest.mark.asyncio
async def test_resume_resolves_through_the_apps_own_cache_not_the_global_one(boundary, monkeypatch):
    # Regression: a restored serving app lists worktrees from its retained
    # (app-bound) cache, so resume must resolve through that exact cache --
    # never fall back to the process-global singleton, which can be empty
    # or unrelated and would otherwise force an unnecessary live probe.
    monkeypatch.setattr(wt, "_find_cached_worktree_entry", worktree_probe.find_worktree_entry)
    app_cache = wt.WorktreeDiscoveryCache()
    app_cache._cache = {"example": [boundary.entry]}
    boundary.request.app.state.worktree_discovery_cache = app_cache

    global_cache = wt.get_cache()
    monkeypatch.setattr(global_cache, "get_all", Mock(
        side_effect=AssertionError("resume escaped to the process-global cache"),
    ))
    monkeypatch.setattr(global_cache, "probe_live", AsyncMock(
        side_effect=AssertionError("resume escaped to the process-global cache"),
    ))

    result = await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert result.acp_session_id == result.durable_session_id == "native-original"


@pytest.fixture
def singleton_boundary(boundary, monkeypatch):
    from agent_bridge import singleton_resume
    from agent_bridge.singleton_anchor import SingletonRepo

    anchor = "example@anchor"
    monkeypatch.setattr(
        singleton_resume, "find_singleton_repo",
        lambda name: SingletonRepo(name, boundary.entry.path),
    )
    boundary.entry = wt._WorktreeEntry(
        id=anchor, agent_name="example", machine="example",
        path=boundary.entry.path, branch="", status="active",
    )
    monkeypatch.setattr(
        wt, "_find_cached_worktree_entry",
        AsyncMock(return_value=("example", boundary.entry)),
    )
    monkeypatch.setattr(wt, "_resolve_already_live", AsyncMock(return_value=False))
    return boundary


def _singleton_session(boundary, *, session_id="singleton-known", status=SessionStatus.STOPPED):
    target = SpawnTarget(
        type="local", project="example-project", cwd=boundary.entry.path,
        worktree_id="example@anchor", explicit_cwd=True,
    )
    session = Session(session_id, session_id, target, agent_name="example")
    session.acp_session_id = "singleton-native"
    session.status = status
    boundary.manager._sessions[session_id] = session
    boundary.db.create_session(
        session_id=session_id, name=session.name, agent_name="example", caller_id=None,
        target_dir=target.cwd, target_type=target.type, status=status.value,
        now=time.time(), target_json=target.to_json(), acp_session_id=session.acp_session_id,
    )
    return session


@pytest.mark.asyncio
async def test_singleton_resume_passes_the_apps_own_cache(singleton_boundary, monkeypatch):
    # Regression: the singleton-anchor fresh-init cache lookup must also
    # resolve through the app-bound cache (wt.cache_for_app), not the
    # process-global singleton -- the same ownership boundary bug fixed
    # for ordinary worktree resume.
    boundary = singleton_boundary
    mock = AsyncMock(return_value=("example", boundary.entry))
    monkeypatch.setattr(wt, "_find_cached_worktree_entry", mock)

    async def initialize(target, *, agent_name, caller_id, creation_session_id):
        session = Session(creation_session_id, "initialized", target)
        session.acp_session_id = "singleton-created"
        session.status = SessionStatus.IDLE
        return session

    boundary.manager.start_session = AsyncMock(side_effect=initialize)
    await native_resume.resume_native_worktree("example@anchor", boundary.request, "acp")
    mock.assert_awaited_once()
    _args, kwargs = mock.call_args
    assert kwargs.get("cache") is wt.cache_for_app(boundary.request.app)


@pytest.mark.asyncio
async def test_singleton_initialization_preserves_fixed_workspace_contract(singleton_boundary):
    boundary = singleton_boundary

    async def initialize(target, *, agent_name, caller_id, creation_session_id):
        assert target.cwd == boundary.entry.path and target.explicit_cwd is True
        assert target.worktree_id == "example@anchor"
        gate = boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")[0]
        assert gate["session_id"] == creation_session_id
        assert gate["native_session_id"] is None
        assert agent_name == "example" and caller_id.startswith("acquisition:")
        session = Session(creation_session_id, "initialized", target)
        session.acp_session_id = "singleton-created"
        session.status = SessionStatus.IDLE
        return session

    boundary.manager.start_session = AsyncMock(side_effect=initialize)
    result = await native_resume.resume_native_worktree("example@anchor", boundary.request, "acp")
    assert result.acp_session_id == "singleton-created"
    boundary.manager.resume_session.assert_not_called()
    assert not boundary.calls
    assert not boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,strict,reason", [
    ("acp", True, "singleton_head_unavailable"),
    ("cli", False, "singleton_mode_unsupported"),
])
async def test_singleton_refuses_unsupported_initialization(singleton_boundary, mode, strict, reason):
    boundary = singleton_boundary
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("example@anchor", boundary.request, mode, strict=strict)
    assert refused.value.detail["reason"] == reason
    boundary.manager.start_session.assert_not_called()
    boundary.manager.resume_session.assert_not_called()
    assert not boundary.calls


@pytest.mark.asyncio
async def test_singleton_loads_known_native_without_worktree_owner(singleton_boundary):
    boundary = singleton_boundary
    session = _singleton_session(boundary)

    async def load(session_id, *, drain, allow_recreate):
        assert session_id == session.session_id and drain is False and allow_recreate is False
        assert boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")
        session.status = SessionStatus.IDLE
        return session

    boundary.manager.resume_session = AsyncMock(side_effect=load)
    result = await native_resume.resume_native_worktree("example@anchor", boundary.request, "acp", strict=True)
    assert result.session_id == session.session_id and result.acp_session_id == "singleton-native"
    boundary.manager.start_session.assert_not_called()
    assert not boundary.calls
    assert not boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
async def test_singleton_uncertain_load_retains_fence_and_never_creates(singleton_boundary):
    boundary = singleton_boundary
    session = _singleton_session(boundary)
    boundary.manager.resume_session = AsyncMock(side_effect=RuntimeError("load outcome uncertain"))
    for _ in range(2):
        with pytest.raises(HTTPException) as refused:
            await native_resume.resume_native_worktree("example@anchor", boundary.request, "acp")
        assert refused.value.status_code == 502
    gate = boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")[0]
    assert gate["session_id"] == session.session_id and gate["native_session_id"] == "singleton-native"
    boundary.manager.start_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,reason", [
    ("ambiguous", "singleton_head_ambiguous"),
    ("wrong-path", "singleton_binding_unconfirmed"),
    ("missing-native", "singleton_binding_unconfirmed"),
    ("ended", "singleton_head_unavailable"),
    ("foreign-gate", "launch_gate_held"),
])
async def test_singleton_refuses_unconfirmed_history(singleton_boundary, failure, reason):
    boundary = singleton_boundary
    session = _singleton_session(boundary)
    if failure == "ambiguous":
        _singleton_session(boundary, session_id="other")
    elif failure == "wrong-path":
        session.target = SpawnTarget(type="local", cwd="other", worktree_id="example@anchor")
    elif failure == "missing-native":
        session.acp_session_id = None
    elif failure == "ended":
        session.status = SessionStatus.ENDED
    else:
        AcquisitionStore(boundary.db).gate_workspace(
            "example@anchor", "foreign", "acp", session.session_id, "singleton-native", "foreign",
        )
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("example@anchor", boundary.request, "acp")
    assert refused.value.detail["reason"] == reason
    boundary.manager.start_session.assert_not_called()
    boundary.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
async def test_singleton_changed_native_retains_fence(singleton_boundary):
    boundary = singleton_boundary
    session = _singleton_session(boundary)

    async def wrong_native(*args, **kwargs):
        session.status = SessionStatus.IDLE
        session.acp_session_id = "unexpected-native"
        return session

    boundary.manager.resume_session = AsyncMock(side_effect=wrong_native)
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("example@anchor", boundary.request, "acp")
    assert refused.value.detail["reason"] == "singleton_native_unconfirmed"
    assert boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")
    boundary.manager.start_session.assert_not_called()


@pytest.mark.asyncio
async def test_cold_cli_uses_exact_native_owner_admission_and_mux(boundary):
    result = await native_resume.resume_native_worktree("wt-example", boundary.request, "cli")
    assert result.mode == "cli"
    assert result.session_id == result.durable_session_id == "native-original"
    launch = next(args for args in boundary.calls if "--ensure-mux" in args)
    assert launch[launch.index("--bridge-acquisition") + 1] == "native-original"
    assert "--seed" not in launch and "--fresh" not in launch
    boundary.manager.start_session.assert_not_called()
    boundary.manager.resume_session.assert_not_called()
    assert result.status == SessionStatus.STARTING


@pytest.mark.asyncio
async def test_cli_launch_accepts_exact_fresh_runtime_despite_owner_display_flag(boundary, monkeypatch):
    async def launch(*_args):
        assert boundary.db.register_live_session(
            "native-original", machine="example", cwd=boundary.entry.path,
            worktree_id="wt-example", repo="example-project", branch="worktree/example",
            pid=123, role=None, now=time.time(),
        ) == "live"
        return json.dumps(boundary.state["launch"])

    launched = AsyncMock(side_effect=launch)
    monkeypatch.setattr(native_resume, "run_owner_operation", launched)
    for _ in range(2):
        result = await native_resume.resume_native_worktree("wt-example", boundary.request, "cli")
        assert result.session_id == result.durable_session_id == "native-original"
        assert result.read_only is False and result.pid == 123
    launched.assert_awaited_once()
    assert boundary.state["launched"] is False
    assert not boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert boundary.db.get_cli_mode_reservation("wt-example") is None
    assert boundary.db.deregister_live_session("native-original") is True
    result = await native_resume.resume_native_worktree("wt-example", boundary.request, "cli")
    assert result.durable_session_id == "native-original"
    assert launched.await_count == 2
    assert boundary.db.get_cli_mode_reservation("wt-example") is None
    boundary.manager.start_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("registration", ["absent", "stale", "foreign-worktree", "foreign-native"])
async def test_cli_launch_does_not_accept_unproven_runtime(boundary, monkeypatch, registration):
    async def launch(*_args):
        if registration != "absent":
            boundary.db.register_live_session(
                "other-native" if registration == "foreign-native" else "native-original",
                machine="example", cwd=boundary.entry.path,
                worktree_id="other-worktree" if registration == "foreign-worktree" else "wt-example",
                repo="example-project", branch="worktree/example", pid=123, role=None,
                now=time.time() - (1000 if registration == "stale" else 0),
            )
        return json.dumps(boundary.state["launch"])

    ticks = iter([0, 31])
    monkeypatch.setattr(native_resume, "time", SimpleNamespace(
        monotonic=lambda: next(ticks), time=time.time,
    ))
    monkeypatch.setattr(native_resume, "run_owner_operation", AsyncMock(side_effect=launch))
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "cli")
    expected = "launch_gate_held" if registration == "foreign-native" else "cli_launch_unconfirmed"
    assert refused.value.detail["reason"] == expected
    assert boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")
    boundary.manager.start_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"work_dir": "other"}, {"resume_session": "other-native"}, {"mux_verified": False},
])
async def test_cli_launch_requires_exact_owner_receipt(boundary, change):
    boundary.state["launch"].update(change)
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "cli")
    assert refused.value.detail["reason"] == "cli_launch_unconfirmed"
    assert boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")


def test_import_is_atomic_and_not_automatically_recoverable(boundary):
    session = boundary.manager.import_native_session(
        SpawnTarget(type="local", cwd=boundary.entry.path, worktree_id="wt-example"),
        "native-original", agent_name="example",
    )
    stored = boundary.db.get_session(session.session_id)
    assert stored["acp_session_id"] == "native-original"
    assert stored["status"] == "stopped" and stored["background_recovery_enabled"] == 0
    restarted = SessionManager(boundary.db)
    assert restarted.get_session(session.session_id).background_recovery_enabled is False
    assert restarted.get_session(session.session_id).caller_id == "native-owner-resume:wt-example"


def test_runtime_native_admission_preserves_remote_acp_target_serialization():
    target = SpawnTarget(
        type="remote-acp", remote_acp={"name": "example-remote"},
        remote_acp_capabilities={"generation": 1}, native_resume_id="native-original",
    )
    stored = target.to_json()
    restored = SpawnTarget.from_json(stored)
    assert restored.remote_acp == target.remote_acp
    assert restored.remote_acp_capabilities == target.remote_acp_capabilities
    assert restored.native_resume_id is None
    assert "native_resume_id" not in json.loads(stored)
    legacy = json.loads(SpawnTarget().to_json())
    assert "remote_acp" not in legacy and "remote_acp_capabilities" not in legacy


@pytest.mark.asyncio
async def test_imported_native_origin_survives_restart_without_recreate(boundary, monkeypatch):
    imported = boundary.manager.import_native_session(
        SpawnTarget(type="local", project="example-project", cwd=boundary.entry.path, worktree_id="wt-example"),
        "native-original", agent_name="example",
    )
    manager = SessionManager(boundary.db)
    seen = []

    async def fail_native(session, *, load_existing, **kwargs):
        seen.append((session.target.native_resume_id, load_existing))
        raise RuntimeError("native load unavailable")

    monkeypatch.setattr("agent_bridge.session_resume._MAX_RESUME_ROUNDS", 1)
    monkeypatch.setattr(manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(manager, "_refresh_provider_target", AsyncMock())
    monkeypatch.setattr(manager, "_resume_via_new_remote_host", fail_native)
    with pytest.raises(RuntimeError, match="native load unavailable"):
        await manager.resume_session(imported.session_id, drain=False, allow_recreate=True)
    assert seen == [("native-original", True)]
    assert manager.get_session(imported.session_id).acp_session_id == "native-original"


@pytest.mark.asyncio
async def test_regular_anchor_resume_does_not_gain_a_worktree_owner_dependency(boundary, monkeypatch):
    target = SpawnTarget(
        type="local", project="example-project", worktree_id="example@anchor",
        cwd=boundary.entry.path, explicit_cwd=True,
    )
    session = Session("normal-anchor", "normal", target, caller_id="normal-creator")
    session.acp_session_id = "native-original"
    session.status = SessionStatus.STOPPED
    boundary.manager._sessions[session.session_id] = session
    boundary.db.create_session(
        session_id=session.session_id, name=session.name, agent_name=None,
        caller_id=session.caller_id, target_dir=target.cwd, target_type=target.type,
        status="stopped", now=time.time(), target_json=target.to_json(),
        acp_session_id=session.acp_session_id,
    )
    seen = []

    async def load_anchor(current, **kwargs):
        seen.append(current.target)
        return SimpleNamespace(pid=None, is_running=True), "native-original"

    monkeypatch.setattr(boundary.manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(boundary.manager, "_refresh_provider_target", AsyncMock())
    monkeypatch.setattr(boundary.manager, "_resume_via_new_remote_host", load_anchor)
    result = await SessionManager.resume_session(boundary.manager, session.session_id, drain=False)
    assert result.acp_session_id == "native-original"
    assert seen[0].native_resume_id is None and seen[0].explicit_cwd is True


@pytest.mark.asyncio
async def test_real_manager_loads_imported_native_instead_of_creating(boundary, monkeypatch):
    client = SimpleNamespace(pid=None, is_running=True)
    seen = []

    async def load_native(session, **kwargs):
        seen.append(session.target)
        return client, "native-original"

    load = AsyncMock(side_effect=load_native)
    monkeypatch.setattr(boundary.manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(boundary.manager, "_refresh_provider_target", AsyncMock())
    monkeypatch.setattr(boundary.manager, "_resume_via_new_remote_host", load)
    boundary.manager.resume_session = SessionManager.resume_session.__get__(boundary.manager)
    result = await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert result.durable_session_id == "native-original"
    assert load.call_args.kwargs["load_existing"] is True
    assert seen[0].native_resume_id == "native-original"
    assert seen[0].explicit_cwd is True
    assert load.call_args.args[0].target.native_resume_id is None
    boundary.manager.start_session.assert_not_called()


@pytest.mark.asyncio
async def test_native_provider_cannot_replace_persisted_history(boundary, monkeypatch):
    client = SimpleNamespace(
        pid=None, is_running=True, stderr_tail=lambda: "", shutdown=AsyncMock(),
    )
    monkeypatch.setattr("agent_bridge.session_resume._MAX_RESUME_ROUNDS", 1)
    monkeypatch.setattr(boundary.manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(boundary.manager, "_refresh_provider_target", AsyncMock())
    monkeypatch.setattr(
        boundary.manager, "_resume_via_new_remote_host",
        AsyncMock(return_value=(client, "replacement-conversation")),
    )
    boundary.manager.resume_session = SessionManager.resume_session.__get__(boundary.manager)
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert refused.value.status_code == 502
    assert "different conversation identity" in refused.value.detail["detail"]
    session = boundary.manager.list_sessions()[0]
    assert session.acp_session_id == "native-original"
    assert boundary.db.get_session(session.session_id)["acp_session_id"] == "native-original"
    client.shutdown.assert_awaited_once()
    boundary.manager.start_session.assert_not_called()


@pytest.mark.asyncio
async def test_native_resume_authority_cannot_create_fresh_history(boundary):
    with pytest.raises(ValueError, match="cannot create a fresh"):
        await SessionManager.start_session(
            boundary.manager,
            SpawnTarget(
                type="local", project="example-project", worktree_id="wt-example",
                cwd=boundary.entry.path, native_resume_id="native-original",
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"tracked": False}, {"head_session": None}, {"state": "concluded"},
    {"pending_handoffs": [{"ordinal": 1}]},
])
async def test_missing_or_transitioning_history_never_creates(boundary, change):
    boundary.state["head"].update(change)
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert refused.value.status_code == 409
    boundary.manager.start_session.assert_not_called()
    boundary.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
async def test_owner_preflight_must_match_native_and_workspace(boundary):
    boundary.state["preflight"]["resume_session"] = "unrelated"
    with pytest.raises(HTTPException):
        await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert not boundary.manager.list_sessions()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"last_session_id": "different-native"}, {"execution_leg_opaque": True},
    {"session_backend_opaque": True}, {"status": "finalized"}, {"status": "finalizing"},
])
async def test_exact_record_unknown_or_changed_authority_refuses(boundary, change):
    boundary.state["record"].update(change)
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert refused.value.detail["reason"] == "workspace_identity_changed"
    assert not boundary.manager.list_sessions()


@pytest.mark.asyncio
async def test_owner_configured_member_repository_is_not_the_project_label(boundary):
    boundary.state["record"]["repo"] = "member-repository"
    result = await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert result.durable_session_id == "native-original"


@pytest.mark.asyncio
async def test_exact_owner_charter_supplies_the_native_mcp_profile(boundary, monkeypatch):
    from agent_bridge.worktree_charter import _apply_bound_charter

    resolver = boundary.request.app.state.resolver
    profile = SimpleNamespace(
        managed=False, copilot_path=None, copilot_args=["--agent", "fixture-charter"],
        mcp_servers=[{"name": "fixture", "command": "fixture-only"}], env={},
    )
    resolver.agents["fixture-charter"] = profile
    resolver.canonical_agent_name = lambda value: value
    boundary.state["record"]["bound_agent"] = "fixture-charter"
    monkeypatch.setattr(native_resume, "_apply_bound_charter", _apply_bound_charter)
    result = await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    session = boundary.manager.get_session(result.session_id)
    assert session.mcp_servers == profile.mcp_servers
    assert session.target.copilot_args == profile.copilot_args
    assert session.target.project == "example-project" and session.target.cwd == boundary.entry.path


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, True, 0, "1", 2])
async def test_owner_native_admission_capability_must_be_exact(boundary, version):
    boundary.state["preflight"]["native_admission_version"] = version
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert refused.value.status_code == 503
    assert refused.value.detail["reason"] == "owner_capability_missing"
    assert not boundary.manager.list_sessions()


@pytest.mark.asyncio
async def test_head_revision_change_prevents_native_import(boundary, monkeypatch):
    original = wt._run_for_agent
    reads = 0

    async def changed(*args):
        nonlocal reads
        output = await original(*args)
        if args[-1][0] == "head-session":
            reads += 1
            if reads == 2:
                value = json.loads(output)
                value["head_revision"] += 1
                return json.dumps(value)
        return output

    monkeypatch.setattr(wt, "_run_for_agent", changed)
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert refused.value.detail["reason"] == "head_changed"
    assert not boundary.manager.list_sessions()


@pytest.mark.asyncio
async def test_uncertain_load_retry_retains_bridge_and_native_identity(boundary):
    loader = boundary.manager.resume_session.side_effect
    boundary.manager.resume_session.side_effect = RuntimeError("native load unavailable")
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert refused.value.status_code == 502
    original = boundary.manager.list_sessions()[0]
    assert boundary.db.execute_read("SELECT * FROM acquisition_launch_gates")
    boundary.manager.resume_session.side_effect = loader
    result = await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert result.session_id == original.session_id
    assert result.durable_session_id == original.acp_session_id
    assert len(boundary.manager.list_sessions()) == 1


@pytest.mark.asyncio
async def test_represented_foreign_holder_refuses_without_takeover(boundary, monkeypatch):
    monkeypatch.setattr(boundary.db, "list_fresh_live_sessions", lambda *a, **k: [
        {"session_id": "other-conversation"},
    ])
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert refused.value.detail["reason"] == "live_holder"
    assert not boundary.manager.list_sessions()


@pytest.mark.asyncio
@pytest.mark.parametrize("reclaim", [False, True])
async def test_foreign_native_launch_gate_returns_explicit_conflict(boundary, reclaim):
    store = AcquisitionStore(boundary.db)
    store.gate_workspace(
        "wt-example", "foreign-delivery", "cli", None, "foreign-native", "foreign-driver",
    )
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree(
            "wt-example", boundary.request, "acp", reclaim=reclaim,
        )
    assert refused.value.status_code == 409
    assert refused.value.detail["reason"] == "launch_gate_held"
    assert not boundary.manager.list_sessions()
    boundary.manager.start_session.assert_not_called()


@pytest.mark.asyncio
async def test_owner_live_cli_replay_succeeds_without_local_registration(boundary, monkeypatch):
    boundary.state["launched"] = True
    launch = AsyncMock(side_effect=AssertionError("must reconcile, not relaunch"))
    monkeypatch.setattr(native_resume, "run_owner_operation", launch)
    for _ in range(2):
        result = await native_resume.resume_native_worktree("wt-example", boundary.request, "cli")
        assert result.durable_session_id == "native-original"
    assert not any(args[0] == "embody" for args in boundary.calls)
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_owner_live_cli_preserves_its_actual_local_representation(boundary):
    boundary.state["launched"] = True
    AcquisitionStore(boundary.db)
    assert boundary.db.register_live_session(
        "native-original", machine="example", cwd=boundary.entry.path,
        worktree_id="wt-example", repo="example-project", branch="worktree/example",
        pid=123, role=None, now=time.time(),
    ) == "live"
    boundary.db.update_live_turn_state(
        "native-original", turn_state="idle", last_activity_at=time.time(),
    )
    for _ in range(2):
        result = await native_resume.resume_native_worktree("wt-example", boundary.request, "cli")
        assert result.status == SessionStatus.IDLE and result.pid == 123
        assert result.read_only is False


@pytest.mark.asyncio
async def test_unrepresented_owner_live_head_refuses_acp(boundary):
    boundary.state["launched"] = True
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert refused.value.detail["reason"] == "live_holder"
    boundary.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
async def test_retained_bridge_can_reattach_its_same_live_native_head(boundary):
    session = boundary.manager.import_native_session(
        SpawnTarget(
            type="local", project="example-project", cwd=boundary.entry.path,
            worktree_id="wt-example", explicit_cwd=True,
        ),
        "native-original", agent_name="example",
    )
    boundary.state["launched"] = True
    result = await native_resume.resume_native_worktree("wt-example", boundary.request, "acp")
    assert result.session_id == session.session_id
    boundary.manager.resume_session.assert_awaited_once()
    assert not any(args[0] == "embody" for args in boundary.calls)


def test_windows_ssh_native_resume_keeps_encoded_owner_transport():
    target = SpawnTarget(
        type="ssh", host="example-host", ssh_shell="pwsh",
        project="example-project", worktree_id="wt-example",
        cwd=r"C:\Example\Worktree", explicit_cwd=True, native_resume_id="native-original",
    )
    command = _build_remote_cmd(target)
    assert command.startswith("pwsh -NoProfile")
    decoded = base64.b64decode(command.split("-EncodedCommand ", 1)[1]).decode("utf-16-le")
    assert "--native-resume-session" in decoded and "native-original" in decoded
    assert "exec copilot" not in decoded
    assert "native_resume_id" not in target.to_json()

@pytest.mark.asyncio
async def test_modern_route_refuses_implicit_live_takeover(boundary):
    boundary.state["launched"] = True
    with pytest.raises(HTTPException) as refused:
        await native_resume.resume_native_worktree(
            "wt-example", boundary.request, "acp", reclaim=True,
        )
    assert refused.value.detail["reason"] == "live_holder"


@pytest.mark.asyncio
async def test_reclaim_input_does_not_block_a_proven_dormant_native_head(boundary):
    result = await native_resume.resume_native_worktree(
        "wt-example", boundary.request, "acp", reclaim=True,
    )
    assert result.durable_session_id == "native-original"


def _dead_local_host(boundary, session, monkeypatch, connected):
    from agent_bridge.routes.worktree_probe import resolve_already_live

    client = None if connected is None else SimpleNamespace(
        is_running=connected, pid=123, mark_host_child_exited=Mock(),
    )
    session.client = client
    record = SimpleNamespace(boundary="local")
    boundary.manager._host_index = SimpleNamespace(get=lambda _sid: record)
    monkeypatch.setattr(boundary.manager, "_rec_host_alive", lambda _rec: True)
    monkeypatch.setattr(boundary.manager, "_rec_child_alive", lambda _rec: False)
    monkeypatch.setattr(boundary.manager, "_reap_host_record", Mock())
    monkeypatch.setattr(wt, "_resolve_already_live", resolve_already_live)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [True, False, None])
@pytest.mark.parametrize("status", [SessionStatus.IDLE, SessionStatus.RUNNING])
async def test_singleton_reconciles_dead_host_before_client_readiness(
    singleton_boundary, monkeypatch, connected, status,
):
    boundary = singleton_boundary
    session = _singleton_session(boundary, status=status)
    client = _dead_local_host(boundary, session, monkeypatch, connected)

    async def load(session_id, **kwargs):
        assert session_id == session.session_id and session.status == SessionStatus.STOPPED
        assert kwargs == {"drain": False, "allow_recreate": False}
        session.status = SessionStatus.IDLE
        return session

    boundary.manager.resume_session = AsyncMock(side_effect=load)
    result = await native_resume.resume_native_worktree("example@anchor", boundary.request, "acp")
    assert result.acp_session_id == "singleton-native"
    boundary.manager.resume_session.assert_awaited_once()
    if client is not None:
        client.mark_host_child_exited.assert_called_once_with(-1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["acp", "cli"])
@pytest.mark.parametrize("connected", [True, False, None])
async def test_worktree_reconciles_dead_retained_host_in_both_modes(
    boundary, monkeypatch, mode, connected,
):
    session = boundary.manager.import_native_session(
        SpawnTarget(type="local", project="example-project", cwd=boundary.entry.path,
                    worktree_id="wt-example"), "native-original", agent_name="example",
    )
    session.status = SessionStatus.RUNNING
    _dead_local_host(boundary, session, monkeypatch, connected)
    result = await native_resume.resume_native_worktree("wt-example", boundary.request, mode)
    assert result.durable_session_id == "native-original"
    if mode == "acp":
        boundary.manager.resume_session.assert_awaited_once()
    else:
        assert session.status == SessionStatus.STOPPED
        boundary.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
async def test_cli_confirmed_exit_can_resume_immediately_without_old_reservation(boundary):
    for _ in range(2):
        await native_resume.resume_native_worktree("wt-example", boundary.request, "cli")
        assert boundary.db.get_cli_mode_reservation("wt-example") is None
        boundary.state["launched"] = False
    assert sum("--ensure-mux" in call for call in boundary.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("represented", [False, True])
async def test_cli_replay_releases_only_its_claimed_reservation(boundary, represented):
    boundary.state["launched"] = not represented
    reservation = boundary.db.create_cli_mode_reservation("wt-example", now=time.time())
    assert reservation
    assert boundary.db.claim_cli_mode_reservation("wt-example", "native-original", now=time.time())
    if represented:
        boundary.db.register_live_session(
            "native-original", machine="example", cwd=boundary.entry.path,
            worktree_id="wt-example", repo="example-project", branch="worktree/example",
            pid=123, role=None, now=time.time(),
        )
    await native_resume.resume_native_worktree("wt-example", boundary.request, "cli")
    assert boundary.db.get_cli_mode_reservation("wt-example") is None
    assert not any("--ensure-mux" in call for call in boundary.calls)


@pytest.mark.parametrize("foreign", ["claimed", "newer"])
def test_cli_reservation_cleanup_preserves_other_owners(boundary, foreign):
    owned = boundary.db.create_cli_mode_reservation("wt-example", now=time.time())
    if foreign == "claimed":
        boundary.db.claim_cli_mode_reservation("wt-example", "other-native", now=time.time())
    else:
        boundary.db.release_cli_mode_reservation("wt-example", reservation_id=owned)
        boundary.db.create_cli_mode_reservation("wt-example", now=time.time())
    expected = boundary.db.get_cli_mode_reservation("wt-example")
    native_resume._release_cli_reservation(
        boundary.db, "wt-example", "native-original", owned_id=owned,
    )
    assert boundary.db.get_cli_mode_reservation("wt-example") == expected


def test_cli_replay_does_not_treat_unknown_reservation_id_as_ownership(boundary, monkeypatch):
    monkeypatch.setattr(boundary.db, "get_cli_mode_reservation", lambda _wid: {
        "reservation_id": None, "claimed_by_session_id": None,
    })
    release = Mock()
    monkeypatch.setattr(boundary.db, "release_cli_mode_reservation", release)
    native_resume._release_cli_reservation(boundary.db, "wt-example", "native-original")
    release.assert_not_called()


@pytest.mark.parametrize("mode", ["acp", "cli"])
def test_client_gates_and_sends_explicit_native_resume_mode(mode):
    client = object.__new__(BridgeClient)
    client.daemon_supports = Mock(return_value=True)
    client._request = Mock(return_value={"durable_session_id": "native-original"})
    assert client.resume_worktree("wt-example", mode=mode)["durable_session_id"] == "native-original"
    assert client._request.call_args.args[1] == f"/api/v1/worktrees/wt-example/resume/{mode}"
    assert client._request.call_args.kwargs["params"] is None
    client.daemon_supports.assert_called_once_with(40)
    client.daemon_supports.return_value = False
    with pytest.raises(BridgeClientError) as refused:
        client.resume_worktree("wt-example", mode=mode)
    assert refused.value.status == 426
    assert client._request.call_count == 1


@pytest.mark.parametrize("mode", ["acp", "cli"])
@pytest.mark.parametrize("generation", [38, 39])
def test_remote_acp_or_receipt_generation_does_not_advertise_native_resume(mode, generation):
    client = object.__new__(BridgeClient)
    client.daemon_supports = Mock(side_effect=lambda version: version <= generation)
    client._request = Mock()
    with pytest.raises(BridgeClientError) as refused:
        client.resume_worktree("wt-example", mode=mode)
    assert refused.value.status == 426
    client._request.assert_not_called()


def test_qualified_resume_route_is_registered_without_replacing_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "absent.yaml"))
    app = create_app(
        config=ServiceConfig(db_path=str(tmp_path / "api.db")), token="test-token",
    )
    paths = app.openapi()["paths"]
    assert "/api/v1/worktrees/{worktree_id}/resume/{mode}" in paths
    assert "/api/v1/worktrees/{worktree_id}/resume" in paths


def test_daemon_downgrade_cannot_select_legacy_fresh_fallback():
    client = object.__new__(BridgeClient)
    client.daemon_supports = Mock(return_value=True)
    client._request = Mock(side_effect=BridgeClientError(404, "unsupported route"))
    with pytest.raises(BridgeClientError):
        client.resume_worktree("wt-example")
    assert client._request.call_count == 1
    assert client._request.call_args.args[1].endswith("/resume/acp")
