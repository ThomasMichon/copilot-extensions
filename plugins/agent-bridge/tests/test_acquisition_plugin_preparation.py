"""Default local acquisition uses the real writer before ACP task admission."""

import asyncio
import json
import io
import sys
from pathlib import Path

import pytest

from agent_bridge import transport
from agent_bridge import acquisition_runtime
from agent_bridge import acquisition_cli
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.client import BridgeClientError
from agent_bridge.plugin_readiness import (
    PluginReadinessError, PluginReadinessRequest, prepared_readiness,
)
from agent_bridge.session_manager import SessionManager, _REQUEST_OVERRIDES_KEY
from agent_bridge.spawn_target import PluginRef
from plugin_resolve.registration import atomic_json
from plugin_resolve.preparation import prepare
from test_acquisition_runtime import req, rig as acquisition_rig
from test_local_plugin_preparation import checkout, marketplace
from types import SimpleNamespace
from unittest.mock import AsyncMock

rig = acquisition_rig
pytestmark = pytest.mark.contract("agent_bridge.acquisition.runtime")


@pytest.mark.asyncio
async def test_default_acp_prepares_every_payload_before_handshake_and_task(rig, tmp_path, monkeypatch):
    checkout(tmp_path)
    native = marketplace(tmp_path)
    catalog_path = tmp_path / ".ai" / "marketplace.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["name"] = "native"
    atomic_json(catalog_path, catalog)
    atomic_json(tmp_path / ".github" / "copilot" / "settings.json", {
        "extraKnownMarketplaces": {"native": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"sample@native": True},
    })
    controller = tmp_path / "controller"
    related = marketplace(controller)
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    monkeypatch.setattr(
        "agent_bridge.related_plugins.related_plugins_for_repo",
        lambda project, anchors: [PluginRef("sample@local")] if anchors == [controller] else [],
    )
    monkeypatch.setenv("COPILOT_PATH", sys.executable)
    events = []
    submitted = asyncio.Event()

    async def connect(self, target, **kwargs):
        args, cwd, _env = await transport.resolve_local_launch(target)
        assert cwd == str(tmp_path)
        assert target.local_plugin_preparation
        assert target.caller_worktree == str(controller)
        directories = [args[index + 1] for index, value in enumerate(args) if value == "--plugin-dir"]
        assert set(directories) == {str(native), str(related)}
        assert set(target.prepared_plugins["logical_payloads"]) == {"sample@native", "sample@local"}
        assert (tmp_path / ".github" / "copilot" / "settings.local.json").is_file()
        events.append("prepared-before-handshake")
        return rig.client, rig.client.acp_session_id

    async def prompt(*args, **kwargs):
        events.append("task")
        submitted.set()

    monkeypatch.setattr(SessionManager, "_connect_via_session_host", connect)
    monkeypatch.setattr(rig.manager, "_run_prompt", prompt)
    result = await rig.runtime.acquire(req(caller_worktree=str(controller)))
    await asyncio.wait_for(submitted.wait(), 5)
    assert events == ["prepared-before-handshake", "task"]
    row = rig.db.execute_read(
        "SELECT data_json FROM acquisition_effects WHERE delivery_id='delivery-a'",
    )[0]
    context = json.loads(row["data_json"])["prepared_context"]
    assert context["cwd"] == str(tmp_path)
    assert {item["projected_source"] for item in context["plugins"]} == {"sample@native", "sample@local"}
    assert result.native_session_id == rig.head["id"]
    assert await rig.runtime.acquire(req(caller_worktree=str(controller))) == result
    assert events == ["prepared-before-handshake", "task"]
    assert rig.manager.get_session(result.session_id).target.prepared_plugins
    assert "prepared_plugins" not in rig.manager.get_session(result.session_id).target.to_json()
    restored_target = rig.manager.get_session(result.session_id).target
    restored_target.prepared_plugins = None
    assert rig.runtime._prepared_context(
        restored_target, {"path": str(tmp_path)}, result.native_session_id,
    ) == context
    with pytest.raises(AcquisitionRefused, match="no verified prepared selection"):
        rig.runtime._prepared_context(restored_target, {"path": str(tmp_path)}, "different-native")
    assert rig.runtime._prepared_context(
        restored_target, {"path": str(tmp_path)}, "legacy-prepared-native", require_prepared=False,
    ) is None


@pytest.mark.asyncio
async def test_cli_git_checkout_fails_before_owner_launch_without_environment_contract(rig, tmp_path):
    checkout(tmp_path)
    with pytest.raises(AcquisitionRefused) as failure:
        await rig.runtime.acquire(req(mode="cli"))
    assert failure.value.code == "cli_capability_missing"
    assert failure.value.effect == "not_started"
    assert all("--dry-run" in args for args, _project in rig.calls if args[0] == "embody")
    assert not rig.db.execute_read("SELECT id FROM live_messages")


@pytest.mark.asyncio
@pytest.mark.parametrize("first_outcome", ["failed", "cancel"])
async def test_default_cli_compiles_actual_selection_and_holds_task_through_retry(
    rig, tmp_path, monkeypatch, first_outcome,
):
    from test_plugin_readiness import claimed, reply
    from plugin_resolve.reflection import plan_digest

    checkout(tmp_path)
    marketplace(tmp_path)
    settings = tmp_path / ".github" / "copilot" / "settings.json"
    document = json.loads(settings.read_text())
    document["enabledPlugins"] = {"sample@local": True}
    atomic_json(settings, document)
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    base = acquisition_runtime.worktree_command
    launches = []

    async def owner(args, project):
        if args[0] != "embody":
            return await base(args, project)
        if "--dry-run" in args:
            return {**await base(args, project), "plugin_preparation_version": 1}
        path = Path(args[args.index("--bridge-plugin-request") + 1])
        envelope = json.loads(path.read_text())
        context = {}
        prepare(
            {**envelope["plan"], "repo": envelope["cwd"]},
            environment={"COPILOT_HOME": str(tmp_path / "selected-native-profile")},
            prepared_context=context,
        )
        context["producer_plan_digest"] = plan_digest(envelope["plan"])
        atomic_json(path.with_suffix(".prepared.json"), context)
        launches.append(args)
        return {
            **await base(args, project), "worktree_id": "worktree-a",
            "work_dir": str(tmp_path), "prepared_context": context,
        }

    monkeypatch.setattr(acquisition_runtime, "worktree_command", owner)
    request = req(mode="cli")
    task = asyncio.create_task(rig.runtime.acquire(request))
    control = await claimed(rig.db, "cli-native-new")
    expected = json.loads(control["body"])
    assert expected["plugins"] == [{
        "projected_source": "sample@local", "logical_source": "sample@local",
        "version": "1.0.0", "live_marketplace_root": str(tmp_path / ".ai"),
    }]
    assert expected["expected_session_id"] == "cli-native-new" and expected["cwd"] == str(tmp_path)
    assert not rig.db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")
    if first_outcome == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        reply(rig.db, "cli-native-new", control, status="failed")
        with pytest.raises(AcquisitionRefused, match="selected-plugin-disabled"):
            await task
    retried = asyncio.create_task(rig.runtime.acquire(request))
    control = await claimed(rig.db, "cli-native-new")
    reply(rig.db, "cli-native-new", control)
    result = await retried
    assert result.native_session_id == "cli-native-new" and len(launches) == 1
    assert "--seed" not in launches[0] and "--plugin-dir" not in launches[0]
    messages = rig.db.execute_read("SELECT kind FROM live_messages WHERE kind NOT LIKE 'control:%'")
    assert len(messages) == 1 and messages[0]["kind"].startswith("startup:prompt:")


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_preparation_failure_or_cancel_quiesces_and_releases_only_proven_prelaunch_state(
    rig, tmp_path, monkeypatch, cancel,
):
    checkout(tmp_path)
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    base = acquisition_runtime.worktree_command
    entered, stopped = asyncio.Event(), asyncio.Event()
    operations = []

    async def owner(args, project):
        if args[0] != "embody":
            return await base(args, project)
        if "--dry-run" in args:
            return {**await base(args, project), "plugin_preparation_version": 1}
        path = Path(args[args.index("--bridge-plugin-request") + 1])
        operations.append(path.parent)
        entered.set()
        if cancel:
            for _ in range(500):
                if path.with_suffix(".cancel").exists():
                    break
                await asyncio.sleep(0.005)
            else:
                raise AssertionError("owner did not receive cancellation")
        stopped.set()
        raise AcquisitionRefused("plugin_preparation_failed", "owner proved no native launch")

    monkeypatch.setattr(acquisition_runtime, "worktree_command", owner)
    request = req(mode="cli")
    task = asyncio.create_task(rig.runtime.acquire(request))
    await asyncio.wait_for(entered.wait(), 5)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(AcquisitionRefused) as failure:
            await task
        assert failure.value.effect == "not_started"
    assert stopped.is_set() and not any(path.exists() for path in operations)
    assert rig.runtime.store.journal.refused_before_effect(request)
    assert not rig.db.get_cli_mode_reservation("worktree-a")
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert not rig.db.execute_read("SELECT id FROM live_messages")


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_owner_failure_receipt_must_explicitly_prove_native_not_started(monkeypatch, started):
    from agent_bridge import acquisition_authority

    payload = {
        "plugin_preparation_version": 1, "native_launch_started": started,
        "worktree_id": "worktree-a", "error": "preparation failed",
    }
    proc = SimpleNamespace(
        returncode=3, communicate=AsyncMock(return_value=(json.dumps(payload).encode(), b"")),
    )
    monkeypatch.setattr(acquisition_authority, "_agent_worktrees_launch_prefix", lambda: ["owner"])
    monkeypatch.setattr(acquisition_authority, "_agent_worktrees_environment", lambda: {})
    monkeypatch.setattr(acquisition_authority.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    with pytest.raises(AcquisitionRefused) as failure:
        await acquisition_authority.worktree_command([
            "embody", "--worktree-id", "worktree-a", "--bridge-plugin-request", "request",
        ], None)
    assert failure.value.code == ("owner_refused" if started else "plugin_preparation_failed")


@pytest.mark.asyncio
async def test_missing_compiler_metadata_cannot_bypass_gate_on_recovery(rig, tmp_path, monkeypatch):
    checkout(tmp_path)
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    base = acquisition_runtime.worktree_command
    launched = []

    async def owner(args, project):
        result = await base(args, project)
        if args[0] != "embody":
            return result
        if "--dry-run" in args:
            return {**result, "plugin_preparation_version": 1}
        launched.append(args)
        return {**result, "worktree_id": "worktree-a", "work_dir": str(tmp_path)}

    monkeypatch.setattr(acquisition_runtime, "worktree_command", owner)
    for _ in range(2):
        with pytest.raises(AcquisitionRefused, match="compiled selection"):
            await rig.runtime.acquire(req(mode="cli"))
    assert len(launched) == 1
    assert not rig.db.execute_read("SELECT id FROM live_messages")


@pytest.mark.asyncio
async def test_default_target_keeps_opaque_affinity_out_of_controller_path(rig, tmp_path):
    target, _env = await rig.runtime._prepare_target(req(), {"path": str(tmp_path)})
    assert target.local_plugin_preparation
    assert target.static_plugin_args == {"base": [], "automatic": []}
    assert target.caller_worktree is None
    assert target.venue[_REQUEST_OVERRIDES_KEY]["controller_worktree_origin"] == "affinity"


@pytest.mark.parametrize("field", ["caller_worktree", "caller_owner_ref", "caller_session_id"])
def test_optional_controller_context_preserves_legacy_request_digest(field):
    legacy = req().model_dump(mode="json")
    assert field not in legacy
    assert req(**{field: "explicit-value"}).model_dump(mode="json")[field] == "explicit-value"


def test_compiler_uses_actual_projected_selection_not_caller_guesses(tmp_path):
    context = {
        "cwd": str(tmp_path),
        "plugins": [{"projected_source": "helper@owned-alias", "logical_source": "helper@original",
                     "version": "2.0.0", "live_marketplace_root": str(tmp_path / "catalog")}],
    }
    assertion = PluginReadinessRequest(
        owner="bridge", plugins=[{"projected_source": "wrong@guess"}], timeout_seconds=3,
    )
    result = prepared_readiness(context, cwd=str(tmp_path), session_id="actual-native", assertion=assertion)
    assert result.plugins[0].projected_source == "helper@owned-alias"
    assert result.cwd == str(tmp_path) and result.expected_session_id == "actual-native"
    assert result.timeout_seconds == 3


@pytest.mark.parametrize("change", ["cwd", "sid", "malformed"])
def test_compiler_refuses_stale_context_and_malformed_selection(tmp_path, change):
    context = {"cwd": str(tmp_path), "plugins": [{"projected_source": "helper@local"}]}
    assertion = PluginReadinessRequest(owner="bridge", plugins=[])
    if change == "cwd":
        context["cwd"] = str(tmp_path / "other")
    elif change == "sid":
        assertion = assertion.model_copy(update={"expected_session_id": "stale-native"})
    else:
        context["plugins"] = [{"projected_source": "not-qualified"}]
    with pytest.raises(PluginReadinessError):
        prepared_readiness(context, cwd=str(tmp_path), session_id="actual-native", assertion=assertion)


def test_cli_producer_captures_context_once_then_reuses_original_request(monkeypatch):
    from agent_bridge import __main__ as core

    stored = {}
    calls = []

    def inspect(*args, **kwargs):
        if not stored:
            raise BridgeClientError(409, {"code": "acquisition_not_found"})
        return {"caller_context": stored}

    client = SimpleNamespace(daemon_supports=lambda version: True, _request=inspect)
    monkeypatch.setattr(core, "_get_client", lambda: client)
    monkeypatch.setattr(core, "_get_caller_id", lambda: "registered-controller")
    monkeypatch.setattr(core, "_worktrees_get", lambda key: "qualified-owner")
    monkeypatch.setattr(core, "_get_caller_session_id", lambda: "original-caller-session")

    def acquire(client, request):
        calls.append(request)
        stored.update({
            field: getattr(request, field)
            for field in ("caller_worktree", "caller_owner_ref", "caller_session_id")
        })
        return SimpleNamespace(model_dump=lambda **kwargs: {})

    monkeypatch.setattr(acquisition_cli, "acquire", acquire)
    for index in range(2):
        monkeypatch.setattr(acquisition_cli.sys, "stdin", io.StringIO(req().model_dump_json()))
        if index:
            monkeypatch.setattr(core, "_get_caller_id", lambda: "different-controller")
            monkeypatch.setattr(core, "_get_caller_session_id", lambda: "successor-session")
        acquisition_cli.command(SimpleNamespace(request_file="-"))
    assert calls[0].model_dump() == calls[1].model_dump()
    assert calls[0].caller_worktree == "registered-controller"
    assert calls[0].caller_session_id == "original-caller-session"
