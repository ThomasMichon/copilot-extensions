"""Trusted venue preparation barrier at the provider process boundary."""

import json
from unittest.mock import AsyncMock

import pytest

from agent_bridge.venue_plugins import prepare_venue_plugins


@pytest.fixture
def controller(monkeypatch, tmp_path):
    from agent_bridge.transport import SpawnTarget

    caller = SpawnTarget(
        caller_worktree=str(tmp_path), caller_owner_ref="local/control/controller",
    )
    owner = AsyncMock(return_value={"worktrees": [{
        "id": "controller", "repo": "control", "machine": "local",
        "path": str(tmp_path), "status": "active",
    }]})
    monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", owner)
    return caller, owner


def _native_provider_workspace_cd():
    """Read the actual producer constant without importing its sibling runtime."""
    import ast
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "agent-codespaces" / "src" / "agent_codespaces" / "config.py"
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_WORKSPACE_CD" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("Native provider workspace command was not found")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["codespace", "container"])
async def test_prepare_uses_selected_transport_and_returns_each_directory(monkeypatch, kind, controller):
    plan = {"plugins": [{"source": "sample@synthetic", "enable": True}]}
    provider = AsyncMock(return_value=(0, json.dumps(plan).encode(), b""))
    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", provider)
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_prepare_script",
        lambda received, workspace: f"PREPARE {workspace} {received['plugins'][0]['source']}",
    )
    transport = AsyncMock()
    transport.run.return_value = (
        0, '__VENUE_PLUGINS__{"sample@synthetic":"/prepared/sample","native@synthetic":"/native"}', "",
    )
    target = {
        "name": "example", "repo": "example/project", "provider_command": ["provider"],
        "plugin_plan": True, "security_profile": "trusted",
    }
    caller, owner = controller
    result = await prepare_venue_plugins(
        target, transport, "/workspaces/selected", prelude="ENV=ready;", caller=caller,
    )
    assert result == ["/prepared/sample", "/native"]
    provider.assert_awaited_once_with(
        ["provider", "plugin-plan", "example", "--repo", "example/project",
         "--plugin-root", caller.caller_worktree], timeout=120,
        cwd=caller.caller_worktree,
    )
    owner.assert_awaited_once_with(
        ["list", "--json", "--cache-only", "--worktree-id", "controller"], "control",
    )
    call = transport.run.await_args
    assert call.args == ("bash -ls",)
    assert b"PREPARE /workspaces/selected sample@synthetic" in call.kwargs["input_bytes"]


@pytest.mark.asyncio
async def test_restricted_venue_refuses_before_any_transport():
    transport = AsyncMock()
    with pytest.raises(RuntimeError, match="Restricted"):
        await prepare_venue_plugins({"security_profile": "restricted"}, transport, "/workspaces/project")
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_preparation_cannot_be_a_ready_launch(controller):
    transport = AsyncMock()
    transport.run.return_value = (1, "", "copy failed")
    with pytest.raises(RuntimeError, match="copy failed"):
        await prepare_venue_plugins(
            {"name": "example"}, transport, "/workspaces/project", caller=controller[0],
        )


@pytest.mark.asyncio
async def test_companion_diagnostics_cross_the_transport_without_expanding_directories(caplog, controller):
    transport = AsyncMock()
    transport.run.return_value = (
        0, '__VENUE_REQUIREMENTS__[{"source":"sample@synthetic",'
        '"dependency":"host-companion@synthetic","status":"outside-selection"}]\n'
        '__VENUE_PLUGINS__{"sample@synthetic":"/prepared/sample"}', "",
    )
    result = await prepare_venue_plugins(
        {"name": "example"}, transport, "/workspaces/project", caller=controller[0],
    )
    assert result == ["/prepared/sample"]
    assert "host-companion@synthetic" in caplog.text
    assert "caller must supply its execution owner" in caplog.text


@pytest.mark.asyncio
async def test_saved_harness_only_sources_cannot_bypass_the_existing_filter(monkeypatch, controller):
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_plan",
        lambda *a, **kw: pytest.fail("harness-only source reached projection"),
    )
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    assert await prepare_venue_plugins(
        {"name": "example", "related_plugins": ["control-harness@synthetic"]},
        transport, "/workspaces/project", caller=controller[0],
    ) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("enable", [False, True])
async def test_related_selection_keeps_enablement_on_fresh_and_retained_launches(
    monkeypatch, retained, enable,
):
    from agent_bridge.transport import PluginRef

    source = "sample@synthetic"
    target = {"name": "example", "repo": "example/project"}
    if retained:
        target["related_plugins"] = json.loads(json.dumps([{"source": source, "enable": enable}]))
    monkeypatch.setattr(
        "agent_bridge.related_plugins.related_plugins_for_repo",
        lambda repo, anchors=None: [] if retained else [PluginRef(source, enable=enable)],
    )
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    plans = []
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_plan",
        lambda specs, roots: {"plugins": specs, "marketplaces": {}},
    )
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_prepare_script",
        lambda plan, workspace: plans.append(plan) or "PREPARE",
    )
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    await prepare_venue_plugins(target, transport, "/selected", controller_cwd="/controller")
    assert plans[0]["plugins"] == [{"source": source, "enable": enable}]


@pytest.mark.asyncio
async def test_retained_plain_source_remains_enabled(monkeypatch):
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    plans = []
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_plan",
        lambda specs, roots: {"plugins": specs, "marketplaces": {}},
    )
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_prepare_script",
        lambda plan, workspace: plans.append(plan) or "PREPARE",
    )
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    await prepare_venue_plugins(
        {"name": "example", "related_plugins": ["sample@synthetic"]},
        transport, "/selected", controller_cwd="/controller",
    )
    assert plans[0]["plugins"] == [{"source": "sample@synthetic", "enable": True}]


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_anchor", [False, True])
async def test_controller_only_related_declaration_precedes_anchor(
    monkeypatch, tmp_path, controller, configured_anchor,
):
    from pathlib import Path

    caller = controller[0]
    controller_root = Path(caller.caller_worktree)
    anchor = tmp_path / "anchor"
    for root, enable in ((controller_root, False), (anchor, True)):
        related = root / ".agent-worktrees"
        related.mkdir(parents=True)
        (related / "related.yaml").write_text(
            "related:\n  example/project:\n    plugins:\n"
            f"      - {{source: sample@synthetic, enable: {str(enable).lower()}}}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "agent_bridge.related_plugins.control_plane_anchors",
        lambda: [anchor] if configured_anchor else [],
    )
    observed = []
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_plan",
        lambda specs, roots: observed.append((specs, roots)) or {"plugins": specs},
    )
    monkeypatch.setattr("agent_bridge.venue_plugins.build_prepare_script", lambda *a: "PREPARE")
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    await prepare_venue_plugins(
        {"name": "example", "repo": "example/project"}, transport, "/selected", caller=caller,
    )
    assert observed == [(
        [{"source": "sample@synthetic", "enable": False}],
        [controller_root, *([anchor] if configured_anchor else [])],
    )]


@pytest.mark.asyncio
@pytest.mark.parametrize("retained", [False, True, "legacy-source"])
async def test_retained_related_rows_outrank_fallback_and_fill_missing(
    monkeypatch, tmp_path, retained,
):
    from agent_bridge.transport import SpawnTarget

    effective = retained is not False
    related = tmp_path / ".agent-worktrees"
    related.mkdir()
    (related / "related.yaml").write_text(
        "related:\n  example/project:\n    plugins:\n"
        f"      - {{source: sample@synthetic, enable: {str(not effective).lower()}}}\n"
        "      - {source: additional@synthetic, enable: true}\n",
        encoding="utf-8",
    )
    row = "sample@synthetic" if retained == "legacy-source" else {
        "source": "sample@synthetic", "enable": retained,
    }
    saved = SpawnTarget(type="command", spawn_command=["provider"], container={
        "name": "example", "repo": "example/project", "related_plugins": [row],
    })
    target = SpawnTarget.from_json(saved.to_json()).container
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [tmp_path])
    observed = []
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_plan",
        lambda specs, roots: observed.extend(specs) or {"plugins": specs},
    )
    monkeypatch.setattr("agent_bridge.venue_plugins.build_prepare_script", lambda *a: "PREPARE")
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    await prepare_venue_plugins(target, transport, "/selected", controller_cwd=str(tmp_path))
    assert observed == [
        {"source": "sample@synthetic", "enable": effective},
        {"source": "additional@synthetic", "enable": True},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_anchor", [False, True])
async def test_controller_related_selection_reflects_worktree_archive(
    monkeypatch, tmp_path, configured_anchor,
):
    import base64
    import io
    import tarfile

    roots = [tmp_path / "controller", tmp_path / "anchor"]
    for root, content, enable in zip(roots, ("controller-v2", "anchor-v1"), (False, True)):
        payload = root / ".ai" / "sample"
        payload.mkdir(parents=True)
        (payload / "plugin.json").write_text(json.dumps({
            "name": "sample", "version": "1.0.0", "author": {"name": "Synthetic Publisher"},
        }), encoding="utf-8")
        (payload / "payload.txt").write_text(content, encoding="utf-8")
        (root / ".ai" / "marketplace.json").write_text(json.dumps({
            "name": "synthetic", "owner": {"name": "Synthetic Publisher"},
            "plugins": [{"name": "sample", "source": "sample", "version": "1.0.0"}],
        }), encoding="utf-8")
        settings = root / ".github" / "copilot"
        settings.mkdir(parents=True)
        (settings / "settings.json").write_text(json.dumps({
            "extraKnownMarketplaces": {
                "synthetic": {"source": {"source": "directory", "path": "./.ai"}},
            },
        }), encoding="utf-8")
        related = root / ".agent-worktrees"
        related.mkdir()
        (related / "related.yaml").write_text(
            "related:\n  example/project:\n    plugins:\n"
            f"      - {{source: sample@synthetic, enable: {str(enable).lower()}}}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "agent_bridge.related_plugins.control_plane_anchors",
        lambda: [roots[1]] if configured_anchor else [],
    )
    observed = []
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_prepare_script",
        lambda plan, workspace: observed.append(plan) or "PREPARE",
    )
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    await prepare_venue_plugins(
        {"name": "example", "repo": "example/project"}, transport, "/selected",
        controller_cwd=str(roots[0]),
    )
    item = observed[0]["plugins"][0]
    assert item["source"] == "sample@synthetic" and item["enable"] is False
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(item["archive"]))) as archive:
        assert archive.extractfile(item["relative"] + "/payload.txt").read() == b"controller-v2"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_enable", [False, True])
@pytest.mark.parametrize("origin", ["local", "remote"])
async def test_deferred_related_lookup_retains_resolved_provider_item(
    monkeypatch, tmp_path, provider_enable, origin,
):
    from plugin_resolve.reflection import build_plan

    provider_root = tmp_path / "provider"
    controller = tmp_path / "controller"
    controller.mkdir()
    payload = provider_root / ".ai" / "sample"
    payload.mkdir(parents=True)
    (payload / "plugin.json").write_text(json.dumps({
        "name": "sample", "version": "1.0.0",
    }), encoding="utf-8")
    (payload.parent / "marketplace.json").write_text(json.dumps({
        "name": "synthetic", "owner": {"name": "Synthetic Publisher"},
        "plugins": [{"name": "sample", "source": "sample"}],
    }), encoding="utf-8")
    declaration = {"source": "directory", "path": "./.ai"} if origin == "local" else {
        "source": "github", "repo": "example/provider-marketplace",
    }
    settings = provider_root / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {"synthetic": {"source": declaration}},
    }), encoding="utf-8")
    source = "sample@synthetic"
    provider_plan = build_plan([{"source": source, "enable": provider_enable}], [provider_root])
    assert not provider_plan["plugins"][0].get("resolve_on_target")
    assert build_plan([{"source": source, "enable": not provider_enable}], [controller])[
        "plugins"
    ][0]["resolve_on_target"]
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport._run_provider",
        AsyncMock(return_value=(0, json.dumps(provider_plan), "")),
    )
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    plans = []
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_prepare_script",
        lambda plan, workspace: plans.append(plan) or "PREPARE",
    )
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    await prepare_venue_plugins(
        {"name": "example", "provider_command": ["provider"], "plugin_plan": True,
         "related_plugins": [{"source": source, "enable": not provider_enable}]},
        transport, "/selected", controller_cwd=str(controller),
    )
    assert plans[0]["plugins"] == provider_plan["plugins"]
    assert plans[0]["marketplaces"] == provider_plan["marketplaces"]
    if origin == "local":
        import subprocess
        from pathlib import Path
        from agent_procutil import no_window_flags
        from plugin_resolve.preparation import prepare
        from plugin_resolve.settings import read_repo_settings

        checkout = tmp_path / "target"
        subprocess.run(
            ["git", "init", "--quiet", str(checkout)], check=True, timeout=10,
            creationflags=no_window_flags(),
        )
        prepared = prepare({"repo": str(checkout), **plans[0]})
        assert read_repo_settings(checkout).enabled[source] is provider_enable
        assert (source in prepared) is provider_enable
        if provider_enable:
            assert json.loads((Path(prepared[source]) / "plugin.json").read_text())["name"] == "sample"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_enable,related_enable", [(False, True), (True, False)])
async def test_related_payload_refresh_keeps_provider_enablement(
    monkeypatch, provider_enable, related_enable,
):
    source = "sample@synthetic"
    provider_plan = {"plugins": [{
        "source": source, "enable": provider_enable, "digest": "old-payload",
    }]}
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport._run_provider",
        AsyncMock(return_value=(0, json.dumps(provider_plan), "")),
    )
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_plan",
        lambda specs, roots: {
            "plugins": [{**specs[0], "digest": "controller-payload"}], "marketplaces": {},
        },
    )
    plans = []
    monkeypatch.setattr(
        "agent_bridge.venue_plugins.build_prepare_script",
        lambda plan, workspace: plans.append(plan) or "PREPARE",
    )
    transport = AsyncMock()
    transport.run.return_value = (0, "__VENUE_PLUGINS__{}", "")
    await prepare_venue_plugins(
        {"name": "example", "provider_command": ["provider"], "plugin_plan": True,
         "related_plugins": [{"source": source, "enable": related_enable}]},
        transport, "/selected", controller_cwd="/controller",
    )
    assert plans[0]["plugins"] == [{
        "source": source, "enable": provider_enable, "digest": "controller-payload",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "empty-owner", "malformed-owner", "relative", "absent", "untracked",
    "duplicate", "wrong-project", "wrong-machine", "wrong-path", "terminal", "unavailable",
    "invalid-response",
])
async def test_controller_failure_never_spawns_plugin_provider(monkeypatch, controller, fault):
    caller, owner = controller
    row = owner.return_value["worktrees"][0]
    if fault == "empty-owner":
        caller.caller_owner_ref = ""
    elif fault == "malformed-owner":
        caller.caller_owner_ref = "not-an-owner"
    elif fault == "relative":
        caller.caller_worktree = "."
    elif fault == "absent":
        caller.caller_worktree += "\\absent"
    elif fault == "untracked":
        owner.return_value["worktrees"] = []
    elif fault == "duplicate":
        owner.return_value["worktrees"].append(dict(row))
    elif fault == "invalid-response":
        owner.return_value["worktrees"] = None
    elif fault.startswith("wrong-"):
        row[{"wrong-project": "repo", "wrong-machine": "machine", "wrong-path": "path"}[fault]] = "other"
    elif fault == "terminal":
        row["status"] = "finalized"
    else:
        owner.side_effect = RuntimeError("owner unavailable")
    provider = AsyncMock()
    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", provider)
    transport = AsyncMock()
    with pytest.raises(RuntimeError, match="[Cc]ontroller"):
        await prepare_venue_plugins(
            {"name": "example", "plugin_plan": True, "provider_command": ["provider"]},
            transport, "/workspaces/project", caller=caller,
        )
    provider.assert_not_awaited()
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [True, False])
async def test_session_manager_callback_preserves_controller_context(
    monkeypatch, session_manager, controller, owned,
):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from agent_bridge.transport import SpawnTarget

    caller, owner = controller
    provider = AsyncMock(return_value=(0, b'{"plugins":[]}', b""))
    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", provider)
    claim = MagicMock(return_value=("ok", ""))
    monkeypatch.setattr("agent_bridge.session_manager._claim_codespace", claim)
    monkeypatch.setattr("agent_bridge.session_manager._release_codespace_claim", lambda *a, **kw: None)
    monkeypatch.setattr(session_manager, "_acquire_codespace_lock", lambda *a: None)
    monkeypatch.setattr("agent_bridge.session_manager._resolve_relay_launch_env", lambda *a: ("", None))
    transport = SimpleNamespace(
        boundary="codespace",
        run=AsyncMock(return_value=(0, '__VENUE_PLUGINS__{"sample@synthetic":"/prepared/sample"}', "")),
    )
    spawner = SimpleNamespace(transport=transport)
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        lambda *a, **kw: spawner,
    )

    async def connect(target, **kwargs):
        assert target.cwd == "/workspaces/project"
        argv = await kwargs["spawner"].prepare_child(kwargs["remote_child_argv"])
        assert "--plugin-dir=/prepared/sample" in argv[-1]
        return MagicMock(is_running=True, pid=12345), "acp-synthetic"

    monkeypatch.setattr(session_manager, "_connect_via_session_host", connect)
    target = SpawnTarget(type="command", codespace={
        "name": "synthetic", "repo": "example/project", "provider_command": ["provider"],
        "workspace_folder": "/workspaces/project",
        "acp_command": "cd /workspaces/project && copilot --acp --stdio",
    })
    session = await session_manager.start_session(target, caller_id=caller.caller_worktree,
                                                caller_owner_ref=caller.caller_owner_ref if owned else None)
    assert session.target.caller_worktree == caller.caller_worktree
    assert provider.await_args.kwargs["cwd"] == caller.caller_worktree
    assert owner.await_count == int(owned)


@pytest.mark.asyncio
async def test_local_session_does_not_require_or_resolve_controller(monkeypatch, session_manager):
    from unittest.mock import MagicMock
    from agent_bridge.transport import SpawnTarget

    controller = AsyncMock(side_effect=AssertionError("nonvenue controller lookup"))
    monkeypatch.setattr("agent_bridge.venue_plugins.resolve_controller_cwd", controller)
    monkeypatch.setattr(session_manager, "_connect_via_session_host", AsyncMock(return_value=(
        MagicMock(is_running=True, pid=12345), "acp-synthetic",
    )))
    await session_manager.start_session(SpawnTarget(type="local", cwd="."))
    controller.assert_not_awaited()


@pytest.mark.asyncio
async def test_container_context_refusal_precedes_provider_setup(monkeypatch, session_manager, controller):
    from unittest.mock import MagicMock
    from agent_bridge.models import SessionStatus
    from agent_bridge.transport import SpawnTarget

    provider = AsyncMock()
    claim = MagicMock()
    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", provider)
    monkeypatch.setattr("agent_bridge.session_manager._claim_container", claim)
    target = SpawnTarget(type="command", caller_worktree=controller[0].caller_worktree + "\\absent",
                        container={"name": "synthetic", "security_profile": "trusted"})
    failed = await session_manager.start_session(target, caller_id=controller[0].caller_worktree)
    assert failed.status == SessionStatus.FAILED
    provider.assert_not_awaited()
    claim.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("qualified", [False, True])
async def test_declared_owner_resolves_without_controller_path(controller, qualified):
    from agent_bridge.venue_plugins import resolve_controller_cwd

    caller, owner = controller
    expected = caller.caller_worktree
    caller.caller_worktree = None
    if qualified:
        caller.caller_owner_ref += "#native-caller"
    assert await resolve_controller_cwd(caller, caller_session_id="native-caller") == expected
    owner.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [None, "other", "bad/session"])
async def test_owner_session_mismatch_refuses_before_owner_query(controller, session_id):
    from agent_bridge.venue_plugins import resolve_controller_cwd

    caller, owner = controller
    caller.caller_owner_ref += "#native-caller"
    with pytest.raises(RuntimeError, match="controller context"):
        await resolve_controller_cwd(caller, caller_session_id=session_id)
    owner.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["explicit", "affinity", None])
@pytest.mark.parametrize("usable", [False, True])
async def test_persisted_controller_provenance_and_legacy_affinity(controller, origin, usable, caplog):
    from pathlib import Path
    from agent_bridge.transport import SpawnTarget
    from agent_bridge.venue_plugins import resolve_controller_cwd, with_controller_origin

    caller, owner = controller
    caller.caller_owner_ref = None
    path = Path(caller.caller_worktree) / "controller-context"
    path.mkdir()
    caller.caller_worktree = str(path)
    if origin is not None:
        caller = with_controller_origin(caller, origin)
    caller = SpawnTarget.from_json(caller.to_json())
    if not usable:
        path.rmdir()
    if origin == "explicit" and not usable:
        with pytest.raises(RuntimeError, match="controller context"):
            await resolve_controller_cwd(caller, affinity_id=str(path))
    else:
        assert await resolve_controller_cwd(caller, affinity_id=str(path)) == (str(path) if usable else None)
    assert ("Legacy controller directory unavailable" in caplog.text) == (origin is None and not usable)
    assert str(path) not in caplog.text
    owner.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["explicit", "affinity", None])
async def test_provider_refresh_preserves_controller_provenance(
    session_manager, controller, origin,
):
    from types import SimpleNamespace
    from agent_bridge.session_manager import Session, _REQUEST_OVERRIDES_KEY
    from agent_bridge.transport import SpawnTarget
    from agent_bridge.venue_plugins import resolve_controller_cwd, with_controller_origin

    caller, _ = controller
    raw = caller.caller_worktree + "\\missing-controller"
    target = SpawnTarget(
        type="command", caller_worktree=raw, codespace={"name": "synthetic"},
        venue={_REQUEST_OVERRIDES_KEY: {"env": {}, "copilot_args": []}},
    )
    if origin is not None:
        target = with_controller_origin(target, origin)
    session = Session("refresh-context", "synthetic", target, "codespace:synthetic")
    session_manager.set_resolver(SimpleNamespace(resolve_async=AsyncMock(return_value=SpawnTarget(
        type="command", codespace={"name": "synthetic"},
    ))))
    await session_manager._refresh_provider_target(session)
    overrides = session.target.venue[_REQUEST_OVERRIDES_KEY]
    assert overrides.get("controller_worktree_origin") == origin
    if origin == "explicit":
        with pytest.raises(RuntimeError, match="controller context"):
            await resolve_controller_cwd(session.target, affinity_id=raw)
    else:
        assert await resolve_controller_cwd(session.target, affinity_id=raw) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["codespace", "container"])
@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("context", [
    "managed", "unmanaged", "legacy", "affinity", "qualified", "owner-only", "bad-session",
    "invalid-explicit", "empty-explicit", "absolute-affinity", "legacy-absolute-affinity",
    "explicit-equals-affinity",
])
async def test_native_venue_controller_context_start_and_recovery(
    monkeypatch, session_manager, controller, kind, recovery, context,
):
    import os
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from agent_bridge.models import SessionStatus
    from agent_bridge.transport import SpawnTarget

    caller, owner = controller
    before = os.getcwd()
    provider = AsyncMock(return_value=(0, b'{"plugins":[]}', b""))
    monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", provider)
    monkeypatch.setattr("agent_bridge.session_manager._resolve_relay_launch_env", lambda *a: ("", None))
    claim = MagicMock(return_value=("ok", ""))
    for name in ("codespace", "container"):
        monkeypatch.setattr(f"agent_bridge.session_manager._claim_{name}", claim)
        monkeypatch.setattr(f"agent_bridge.session_manager._release_{name}_claim", lambda *a, **kw: None)
        monkeypatch.setattr(session_manager, f"_acquire_{name}_lock", lambda *a: None)
    transport = SimpleNamespace(
        boundary=kind,
        run=AsyncMock(return_value=(0, '__VENUE_PLUGINS__{"sample@synthetic":"/prepared/sample"}', "")),
    )
    spawner = SimpleNamespace(transport=transport)
    monkeypatch.setattr("agent_bridge.session_host.codespace_transport.build_codespace_spawner",
                        lambda *a, **kw: spawner)
    container_module = "agent_bridge.session_host.container_transport."
    monkeypatch.setattr(container_module + "ensure_container_ready", AsyncMock())
    monkeypatch.setattr(container_module + "prepare_container_session_host", AsyncMock(return_value={
        "ssh": {}, "state_command": ["provider", "state"], "workspace_folder": "/workspaces/project",
    }))
    monkeypatch.setattr(container_module + "cleanup_container_session_host", AsyncMock())
    monkeypatch.setattr(container_module + "build_container_spawner", lambda *a, **kw: spawner)
    roots = []

    def related_plan(sources, supplied_roots):
        assert sources == [{"source": "sample@synthetic", "enable": False}]
        roots.extend(supplied_roots)
        return {"plugins": []}

    monkeypatch.setattr("agent_bridge.venue_plugins.build_plan", related_plan)
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    child_started = []

    async def connect(target, **kwargs):
        argv = await kwargs["spawner"].prepare_child(kwargs["remote_child_argv"])
        assert "--plugin-dir=/prepared/sample" in argv[-1]
        child_started.append(True)
        return MagicMock(is_running=True, pid=12345), "acp-synthetic"

    monkeypatch.setattr(session_manager, "_connect_via_session_host", connect)
    metadata = {
        "name": "synthetic", "repo": "example/project", "provider_command": ["provider"],
        "workspace_folder": "/workspaces/project",
        "related_plugins": [{"source": "sample@synthetic", "enable": False}],
        "acp_command": "cd /workspaces/project && copilot --acp --stdio",
    }
    target = SpawnTarget(type="command", cwd="/workspaces/project", **{kind: metadata})
    managed = context in {"managed", "qualified", "owner-only", "bad-session"}
    owner_ref = caller.caller_owner_ref if managed else None
    if context in {"qualified", "bad-session"}:
        owner_ref += "#native-caller"
    caller_id = caller.caller_worktree if context in {
        "managed", "unmanaged", "qualified", "bad-session", "empty-explicit",
    } else None
    if context == "affinity":
        caller_id = "sdk/opaque-affinity"
    if context in {"absolute-affinity", "legacy-absolute-affinity"}:
        caller_id = caller.caller_worktree + "\\nonexistent-opaque-affinity"
    if context == "invalid-explicit":
        target.caller_worktree = "relative-controller"
    if context == "empty-explicit":
        target.caller_worktree = ""
    if context == "explicit-equals-affinity":
        caller_id = caller.caller_worktree + "\\missing-explicit-controller"
        target.caller_worktree = caller_id
    session_id = "other" if context == "bad-session" else "native-caller"
    refused = context in {"bad-session", "invalid-explicit", "empty-explicit", "explicit-equals-affinity"}
    if recovery:
        target = SpawnTarget.from_json(target.to_json())
        if target.caller_worktree is None:
            target.caller_worktree = caller_id
        target.caller_owner_ref = owner_ref
        if context in {"absolute-affinity", "explicit-equals-affinity"}:
            from agent_bridge.venue_plugins import with_controller_origin
            target = SpawnTarget.from_json(with_controller_origin(
                target, "explicit" if context == "explicit-equals-affinity" else "affinity",
            ).to_json())
        session = SimpleNamespace(
            session_id="recovery", target=target, caller_id=caller_id, caller_session_id=session_id,
            event_log=SimpleNamespace(append=lambda *a: None), mcp_servers=[],
            acp_session_id=None, model_override=None, effort_override=None,
        )
        if refused:
            with pytest.raises(RuntimeError, match="controller context"):
                await session_manager._resume_via_new_remote_host(
                    session, on_acp_event=lambda *a: None, permission_callback=None,
                )
        else:
            assert await session_manager._resume_via_new_remote_host(
                session, on_acp_event=lambda *a: None, permission_callback=None,
            )
    else:
        session = await session_manager.start_session(
            target, caller_id=caller_id, caller_owner_ref=owner_ref, caller_session_id=session_id,
        )
        assert session.status == (SessionStatus.FAILED if refused else SessionStatus.IDLE)
    if refused:
        assert not child_started
        provider.assert_not_awaited()
        claim.assert_not_called()
        owner.assert_not_awaited()
        transport.run.assert_not_awaited()
    else:
        expected = None if context in {
            "legacy", "affinity", "absolute-affinity", "legacy-absolute-affinity",
        } else caller.caller_worktree
        assert [str(root) for root in roots] == ([expected] if expected else [])
        if kind == "codespace":
            assert provider.await_args.args[0] == [
                "provider", "plugin-plan", "synthetic", "--repo", "example/project",
                *(["--plugin-root", expected] if expected is not None else []),
            ]
            assert provider.await_args.kwargs.get("cwd") == expected
            assert ("cwd" in provider.await_args.kwargs) == (expected is not None)
        assert owner.await_count == int(managed)
        assert child_started == [True]
    assert os.getcwd() == before


@pytest.mark.asyncio
async def test_provider_subprocess_runs_in_only_the_supplied_cwd(tmp_path):
    import os
    import sys
    from agent_bridge.session_host.container_transport import _run_provider

    before = os.getcwd()
    rc, out, error = await _run_provider(
        [sys.executable, "-c", "import os;print(os.getcwd())"], timeout=10, cwd=str(tmp_path),
    )
    assert rc == 0, error.decode()
    assert out.decode().strip() == str(tmp_path)
    assert os.getcwd() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "install", "relay"])
async def test_cold_remote_spawn_authenticates_before_preparation_and_child(monkeypatch, failure):
    from pathlib import Path
    from types import SimpleNamespace
    from ssh_manager import SSHConfig
    from agent_bridge.session_host import endpoints, spawner as module

    events = []

    class Relay:
        async def start(self):
            events.append("relay-start")

        async def stop(self):
            events.append("relay-stop")

    class Forward:
        def __init__(self, *args):
            pass

        async def establish(self):
            events.append("host-forward")
            return 22002

        async def refresh(self):
            pass

        async def cancel(self):
            pass

    async def run(command, **kwargs):
        return 0, "OK", ""

    async def provision(command, **kwargs):
        events.append("helpers")
        return 0, "", ""

    async def ready(probe, **kwargs):
        assert kwargs["timeout"] == 37.0
        if failure == "relay":
            raise endpoints.CredentialRelayReadinessError("readiness unavailable")
        assert await probe()
        events.append("relay-ready")

    relay = Relay()
    transport = SimpleNamespace(
        boundary="codespace", codespace_name="synthetic",
        path_exists=AsyncMock(return_value=True), run=run, run_script=provision,
        ssh_config=lambda: SSHConfig(host_alias="synthetic"),
        reverse_forwards=lambda: ["12001:127.0.0.1:12002"],
    )
    monkeypatch.setattr("agent_bridge.session_host.bundle.build_session_host_bundle",
                        lambda: (Path("bundle.pyz"), "digest"))
    monkeypatch.setattr(module, "_resolve_provision_command", lambda *a: "PROVISION")
    monkeypatch.setattr("ssh_manager.LocalForward", Forward)
    monkeypatch.setattr(endpoints, "relay_forwards_from_ssh_config", lambda *a, **kw: [relay])
    monkeypatch.setattr(endpoints, "wait_for_relay_serving", ready)

    def launch(*args, **kwargs):
        events.append("child-launch")
        assert "--plugin-dir=/prepared/sample" in args[3]
        return "LAUNCH"

    monkeypatch.setattr(module, "build_remote_launch", launch)
    owner = module.CodeSpaceSpawner(
        transport, remote_dir="/state", ready_timeout=37.0, relay_ready_timeout=0.01,
    )
    monkeypatch.setattr(owner, "_authority_path", AsyncMock(return_value="/state/host.json"))
    monkeypatch.setattr(owner, "_poll_state", AsyncMock(return_value={
        "port": 22001, "pid": 101, "child_pid": 102,
    }))

    async def prepare(argv):
        events.append("install-copy-register")
        if failure == "install":
            raise RuntimeError("private installation failed")
        return argv + ["--plugin-dir=/prepared/sample"]

    owner.prepare_child = prepare
    argv = ["copilot", "--acp"]
    if failure == "relay":
        with pytest.raises(endpoints.CredentialRelayReadinessError, match="readiness unavailable"):
            await owner.spawn(argv, cwd="/workspaces/project", session_id="synthetic")
        assert "install-copy-register" not in events
        assert "child-launch" not in events
        assert events[-1] == "relay-stop"
    elif failure == "install":
        with pytest.raises(RuntimeError, match="private installation failed"):
            await owner.spawn(argv, cwd="/workspaces/project", session_id="synthetic")
        assert "child-launch" not in events
        assert events[-1] == "relay-stop"
    else:
        spawned = await owner.spawn(argv, cwd="/workspaces/project", session_id="synthetic")
        assert events.index("helpers") < events.index("relay-ready")
        assert events.index("relay-ready") < events.index("install-copy-register") < events.index("child-launch")
        assert events.count("relay-start") == 1
        assert "relay-stop" not in events
        assert spawned.relay == [relay]
        assert argv[-1] == "--plugin-dir=/prepared/sample"


@pytest.mark.asyncio
async def test_legacy_dynamic_workspace_recovery_resolves_persists_then_prepares(monkeypatch, controller):
    from types import SimpleNamespace
    from agent_bridge.transport import SpawnTarget
    from agent_bridge.session_host_recovery import _SessionHostRecoveryMixin

    command = _native_provider_workspace_cd() + " && copilot --acp --stdio"
    target = SpawnTarget(type="command", caller_worktree=controller[0].caller_worktree,
                         caller_owner_ref=controller[0].caller_owner_ref, spawn_command=[
        "agent-codespaces", "ssh", "synthetic", "--stdio", "--repo", "example/project",
        "--remote-cmd", command,
    ])
    transport = SimpleNamespace(run=AsyncMock(return_value=(0, "/workspaces/resolved\n", "")))
    spawner = SimpleNamespace(transport=transport)
    persisted = []
    prepare = AsyncMock(return_value=["/prepared/sample"])
    monkeypatch.setattr("agent_bridge.venue_plugins.prepare_venue_plugins", prepare)
    monkeypatch.setattr("agent_bridge.session_host.codespace_transport.build_codespace_spawner",
                        lambda *a, **kw: spawner)
    monkeypatch.setattr("agent_bridge.session_manager._resolve_relay_launch_env",
                        lambda *a: ("SYNTHETIC_PRELUDE;", 12001))
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: 12002)

    async def connect(selected, **kwargs):
        assert selected.cwd == "/workspaces/resolved"
        assert kwargs["remote_cwd"] == "/workspaces/resolved"
        assert persisted
        actual = await kwargs["spawner"].prepare_child(kwargs["remote_child_argv"])
        assert "--plugin-dir=/prepared/sample" in actual[-1]
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
        mcp_servers=[], acp_session_id=None, model_override=None, effort_override=None,
    )
    result = await _SessionHostRecoveryMixin._resume_via_new_remote_host(
        manager, session, on_acp_event=lambda *a: None, permission_callback=None,
    )
    assert result == ("client", "acp-session")
    assert "copilot" not in transport.run.await_args.args[0]
    assert target.codespace["workspace_folder"] == "/workspaces/resolved"
    assert target.codespace["acp_command"].startswith("cd /workspaces/resolved &&")
    assert prepare.await_args.args[2] == "/workspaces/resolved"
    assert prepare.await_args.kwargs["controller_cwd"] == controller[0].caller_worktree
    assert persisted[-1][2] == "/workspaces/resolved"


@pytest.mark.parametrize("chosen", ["CODESPACE_VSCODE_FOLDER", "WORKING_DIRECTORY", "VM_REPO_PATH", None])
def test_native_nested_workspace_fallback_reads_environment_without_shell_eval(tmp_path, chosen):
    import os
    import shlex
    import subprocess
    import sys
    from pathlib import Path
    from agent_procutil import no_window_flags
    from agent_bridge.session_host.codespace_transport import _workspace_environment_probe

    argument = shlex.split(_native_provider_workspace_cd())[1]
    variables = ["CODESPACE_VSCODE_FOLDER", "WORKING_DIRECTORY", "VM_REPO_PATH"]
    environment = dict(os.environ)
    expected = tmp_path
    for index, variable in enumerate(variables):
        environment.pop(variable, None)
        if chosen is not None and index >= variables.index(chosen):
            folder = tmp_path / variable.lower()
            folder.mkdir()
            environment[variable] = str(folder)
            if variable == chosen:
                expected = folder
        elif chosen is not None:
            environment[variable] = ""
    exports, separator, reader = _workspace_environment_probe(argument).partition(" && ")
    assert separator and exports == "export " + " ".join(variables)
    command = shlex.split(reader)
    assert command[0] == "python3"
    result = subprocess.run(
        [sys.executable, *command[1:]], cwd=tmp_path, env=environment,
        capture_output=True, text=True, check=True, timeout=10,
        creationflags=no_window_flags(),
    )
    assert Path(result.stdout.strip()).resolve() == expected.resolve()


@pytest.mark.asyncio
@pytest.mark.parametrize("expression", [
    "${CODESPACE_VSCODE_FOLDER:-$(side_effect)}",
    "${CODESPACE_VSCODE_FOLDER:-${WORKING_DIRECTORY:-/workspaces/$(side_effect)}}",
    "${CODESPACE_VSCODE_FOLDER:=/workspaces/project}",
])
async def test_workspace_resolution_refuses_non_fallback_shell_grammar(expression):
    from agent_bridge.session_host.codespace_transport import resolve_execution_workspace
    transport = AsyncMock()
    with pytest.raises(RuntimeError, match="Unsupported"):
        await resolve_execution_workspace(
            {"acp_command": f'cd "{expression}" && copilot --acp'}, transport,
        )
    transport.run.assert_not_awaited()
