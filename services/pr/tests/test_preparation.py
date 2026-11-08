"""Installed host composition and retained recovery with a finite native provider."""

from __future__ import annotations

import copy
import asyncio
import json
import signal
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import pytest
from single_instance_lease import SingleInstance

from agent_pull_requests.review_workspace import _digest, build_generation
from agent_pull_requests.owner_read import OwnerReadBudgetExceeded, OwnerReadUnavailable
from agent_pull_requests.workspace_operations import WorkspaceOperations, WorkspaceUnavailable
from agent_pull_requests_service.config import HostConfig, load_config
from agent_pull_requests_service import hosting
from agent_pull_requests_service.preparation import NativePreparation
if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration, workspace_config
else:
    from test_host import Configuration, configuration as configuration, workspace_config


@pytest.fixture
def prepared_host(configuration: Configuration, tmp_path: Path) -> tuple[HostConfig, Path, Path]:
    path, value = workspace_config(configuration)
    root, home = tmp_path / "producer", tmp_path / "producer-home"
    (root / ".github").mkdir(parents=True)
    home.mkdir()
    profile = root / ".github/ai-attribution.json"
    profile.write_text(json.dumps({
        "version": 1, "rules": [
            {"header": "[Producer] "},
            {"when": {"activity": "reply"}, "header": "[Reply] "},
        ],
    }))
    source_config = tmp_path / "source.json"
    source_config.write_text("{}")
    control = tmp_path / "native.json"
    control.write_text(json.dumps({"supported": True, "origin": True}))
    script = tmp_path / "native.py"
    script.write_text("""
import json, pathlib, sys, time
control = pathlib.Path(sys.argv[1])
state = json.loads(control.read_text())
args = sys.argv[2:]
with control.with_suffix('.calls').open('a') as stream:
    stream.write(json.dumps(args) + '\\n')
assert args[:2] == ['attribution-prepare', '--comment-origin']
if args[2:] == ['--capabilities']:
    if not state['supported']:
        raise SystemExit(2)
    if 'discovery_delay' in state:
        control.with_suffix('.entered').write_text('entered')
        time.sleep(state['discovery_delay'])
    result = state.get('capabilities', {
        'schema': 'agent-worktrees.comment-origin-capabilities', 'version': 1,
        'origin_protocol': 'agent-worktrees.comment-origin/v1',
        'capabilities': ['explicit_source_root'], 'effects': 'none'})
else:
    assert args[2:] == state['expected']
    if not state['origin']:
        raise SystemExit(1)
    time.sleep(state.get('origin_delay', 0))
    result = state.get('source', {
        'protocol': 'agent-worktrees.comment-origin/v1', 'privacy': 'public',
        'origin': {'codename': 'selected-producer'}, 'effects': 'none'})
print(json.dumps(result))
if 'discovery_delay' in state:
    control.with_suffix('.finished').write_text('finished')
""")
    control.write_text(json.dumps({
        "supported": True, "origin": True,
        "expected": ["--source-root", str(root), "--config", str(source_config),
                     "--visibility", "public"],
    }))
    value["workspaces"]["bindings"][0]["preparation"] = {
        "command": [sys.executable, "-I", str(script), str(control)],
        "source_root": str(root), "source_config": str(source_config),
        "profile_home": str(home), "visibility": "public", "allow_anonymous": False,
    }
    path.write_text(json.dumps(value))
    config = load_config(path)
    assert not control.with_suffix(".calls").exists() and not config.root.exists()
    return config, control, profile


def _request() -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(__file__).resolve().parents[3]
    evidence = json.loads((
        root / "plugins/agent-pull-requests/docs/review-evidence.example.json"
    ).read_text())
    evidence["sections"]["changes"]["data"] = []
    evidence["sections"]["discussions"] = {
        "observed_from": "2026-10-30T00:00:03Z", "observed_to": "2026-10-30T00:00:04Z",
        "data": [], "complete": True, "errors": [],
    }
    generation = build_generation(evidence)["generation"]
    scope = copy.deepcopy(generation["scope"])
    source = {
        "schema": "agent-pull-requests.local-source", "version": 1, "scope": scope,
        "revision": generation["revision"], "complete": True, "errors": [],
        "files": [], "workspace_id": "selected-source",
    }
    source["id"] = _digest(source)
    return generation, {
        "number": 42, "budget_seconds": 60,
        "source_attachment": {
            "state": "ready", "scope": scope, "generation_id": generation["id"], "source": source,
        },
        "intent": {
            "schema": "agent-pull-requests.review-intent", "version": 1,
            "operation_id": "selected-operation", "scope": scope,
            "generation_id": generation["id"], "source_id": source["id"],
            "revision": {key: generation["revision"][key]
                         for key in ("head_sha", "common_sha", "iteration")},
            "actions": [{"id": "finding", "kind": "comment", "body": "Selected note."}],
        },
    }


def _operations(config: HostConfig) -> Any:
    assert config.workspaces is not None
    return config.workspaces.operations(config.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ]


def _execute(operations: Any, operation: str, payload: dict[str, Any]) -> Any:
    result = operations.execute(
        operation, payload, expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )
    return result if operation == "capabilities" else result["result"]


def test_normal_host_recovers_original_preview_after_restart_and_provider_rollback(
    prepared_host: tuple[HostConfig, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, control, profile = prepared_host
    config.provision()
    operations = _operations(config)
    generation, request = _request()
    operations.cache.publish(generation, expected_current=None)
    capabilities = _execute(operations, "capabilities", {})
    assert {"allocate", "receipt"} <= set(capabilities["operations"])
    assert "submit" not in capabilities["operations"] and not operations.write_available

    original = NativePreparation.__call__

    def resolve(self: NativePreparation, *args: Any, **kwargs: Any) -> Any:
        # A same-owner read here refuses if origin/profile IO ran under the replay lock.
        config.store().review_intent_versions(expires_at=int(time.time()) + 60)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(NativePreparation, "__call__", resolve)
    preview = _execute(operations, "allocate", request)
    chosen = preview["intent"]
    assert chosen["actions"][0]["body"].startswith("[Producer] Selected note.")
    assert chosen["actions"][0]["preparation"]["identity"]["source"]["origin"] == {
        "codename": "selected-producer",
    }
    before = config.store().path.read_bytes()
    profile.write_text("invalid-profile")
    state = json.loads(control.read_text())
    control.write_text(json.dumps({**state, "origin": False}))
    restarted = _operations(config)
    assert _execute(restarted, "allocate", request) == preview
    with pytest.raises(WorkspaceUnavailable, match="write context unavailable"):
        _execute(restarted, "submit", {**request, "intent": chosen})
    changed = copy.deepcopy(request)
    changed["intent"]["operation_id"] = "new-operation"
    with pytest.raises(ValueError, match="origin unavailable"):
        _execute(restarted, "allocate", changed)
    control.write_text(json.dumps({**state, "supported": False}))
    rollback = _operations(config)
    assert "allocate" not in _execute(rollback, "capabilities", {})["operations"]
    inspected = _execute(rollback, "inspect", {
        "number": 42, "operation_id": request["intent"]["operation_id"],
    })
    assert inspected["intent"] == chosen and inspected["selection"]["stage"] == "prepared"
    with pytest.raises(WorkspaceUnavailable):
        _execute(rollback, "allocate", request)
    with pytest.raises(ValueError, match="not execution-admitted"):
        config.store().inspect_review(
            chosen["operation_id"], chosen["scope"], expires_at=int(time.time()) + 60,
        )
    with pytest.raises(ValueError, match="outside selected scope"):
        _execute(rollback, "inspect", {"number": 43, "operation_id": chosen["operation_id"]})
    assert config.store().path.read_bytes() == before
    calls = [json.loads(line) for line in control.with_suffix(".calls").read_text().splitlines()]
    assert all(call[:2] == ["attribution-prepare", "--comment-origin"] for call in calls)
    assert len([call for call in calls if "--source-root" in call]) == 2
    assert "agent_worktrees" not in sys.modules


@pytest.mark.parametrize("failure", ["profile", "privacy", "scope"])
def test_new_preparation_refuses_unavailable_or_differently_bound_inputs_without_commit(
    prepared_host: tuple[HostConfig, Path, Path], failure: str,
) -> None:
    config, control, profile = prepared_host
    config.provision()
    operations = _operations(config)
    generation, request = _request()
    operations.cache.publish(generation, expected_current=None)
    if failure == "profile":
        profile.write_text("invalid-profile")
    elif failure == "privacy":
        state = json.loads(control.read_text())
        state["source"] = {
            "protocol": "agent-worktrees.comment-origin/v1", "privacy": "internal",
            "origin": {"codename": "selected-producer", "worktree": "private-stamp"},
            "effects": "none",
        }
        control.write_text(json.dumps(state))
    else:
        assert operations.selection_preparer is not None
        generation["scope"]["principal_id"] = "different-principal"
        with pytest.raises(ValueError, match="destination binding"):
            operations.selection_preparer(generation, checkpoint=lambda: None)
        return
    before = config.store().path.read_bytes()
    with pytest.raises(ValueError):
        _execute(operations, "allocate", request)
    assert config.store().path.read_bytes() == before


def test_resolved_reply_composer_does_no_io_and_preserves_allocated_identity(
    prepared_host: tuple[HostConfig, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests_service import preparation

    config, _, profile = prepared_host
    config.provision()
    operations = _operations(config)
    generation, _ = _request()
    compose = operations.selection_preparer(generation, checkpoint=lambda: None)
    profile.write_text("invalid-profile")
    monkeypatch.setattr(preparation, "_run_native", lambda *args: pytest.fail("native IO under lock"))
    monkeypatch.setattr(preparation, "load_profile", lambda *args, **kw: pytest.fail("profile IO under lock"))
    identity = {
        "comment_id": "11111111-1111-4111-8111-111111111111",
        "write_id": "22222222-2222-4222-8222-222222222222",
    }
    prepared = compose("reply", "Selected reply.", scope=generation["scope"], **identity)
    assert prepared["body"].startswith("[Reply] Selected reply.")
    assert all(prepared["identity"][name] == value for name, value in identity.items())
    with pytest.raises(ValueError, match="action binding"):
        compose("edit_owned_comment", "Changed note.", scope=generation["scope"], **identity)


def test_slow_native_origin_uses_remaining_allocation_budget_not_host_limit(
    prepared_host: tuple[HostConfig, Path, Path],
) -> None:
    config, control, _ = prepared_host
    config.provision()
    operations = _operations(config)
    generation, request = _request()
    operations.cache.publish(generation, expected_current=None)
    state = json.loads(control.read_text())
    control.write_text(json.dumps({**state, "origin_delay": 5}))
    request["budget_seconds"] = 0.25
    before = config.store().path.read_bytes()
    started = time.monotonic()
    with pytest.raises((OwnerReadUnavailable, OwnerReadBudgetExceeded)):
        _execute(operations, "allocate", request)
    assert time.monotonic() - started < 3
    assert config.store().path.read_bytes() == before


@pytest.mark.parametrize("ending", ["stop", "cancel", "repeated-cancel"])
def test_slow_startup_discovery_keeps_loop_responsive_and_owns_cleanup(
    prepared_host: tuple[HostConfig, Path, Path], monkeypatch: pytest.MonkeyPatch, ending: str,
) -> None:
    config, control, _ = prepared_host
    state = json.loads(control.read_text())
    control.write_text(json.dumps({**state, "discovery_delay": 0.5}))
    prior_handler = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(
        hosting, "load_service_credentials", lambda **kw: pytest.fail("withdrawn startup read credentials"),
    )
    monkeypatch.setattr(hosting, "WatchService", lambda *a, **kw: pytest.fail("withdrawn startup served"))

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(hosting.run(config, stop=stop))
        try:
            async def entered() -> None:
                while not control.with_suffix(".entered").exists():
                    await asyncio.sleep(0.005)

            await asyncio.wait_for(entered(), 3)
            assert signal.getsignal(signal.SIGTERM) is not prior_handler
            assert not control.with_suffix(".finished").exists()
            if ending == "stop":
                stop.set()
            else:
                task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            if ending == "repeated-cancel":
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0.01)
                    assert not task.done()
            result = (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3))[0]
            assert isinstance(result, asyncio.CancelledError) if ending != "stop" else result is None
            assert control.with_suffix(".finished").exists()
            assert signal.getsignal(signal.SIGTERM) is prior_handler
            assert not config.root.exists()
        finally:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_fresh_preparation_provision_never_adopts_an_existing_or_competing_volume(
    prepared_host: tuple[HostConfig, Path, Path],
) -> None:
    config, _, _ = prepared_host
    with SingleInstance(config.root, service="agent-pull-requests-watch"):
        with pytest.raises(ValueError, match="fresh dedicated root"):
            config.provision()
    assert not config.store().path.exists()
    with pytest.raises(ValueError, match="fresh dedicated root"):
        config.provision()


@pytest.mark.parametrize("change", [
    {"version": True}, {"origin_protocol": "unknown"}, {"capabilities": []}, {"effects": "write"},
])
def test_executing_discovery_refuses_unproven_contract_without_resolving_source(
    prepared_host: tuple[HostConfig, Path, Path], change: dict[str, Any],
) -> None:
    config, control, _ = prepared_host
    assert config.workspaces is not None
    selected = config.workspaces.preparations[0][2]
    missing = replace(
        selected, source_root=selected.source_root / "absent",
        profile_home=selected.profile_home / "absent",
        source_config=selected.source_config.with_name("absent.json"),
    )
    generation, _ = _request()
    scope = {key: value for key, value in generation["scope"].items() if key != "number"}
    assert missing.select(scope, lambda: None, 60) is not None
    state = json.loads(control.read_text())
    state["capabilities"] = {
        "schema": "agent-worktrees.comment-origin-capabilities", "version": 1,
        "origin_protocol": "agent-worktrees.comment-origin/v1",
        "capabilities": ["explicit_source_root"], "effects": "none", **change,
    }
    control.write_text(json.dumps(state))
    assert missing.select(scope, lambda: None, 60) is None
    assert not config.root.exists()
    calls = [json.loads(line) for line in control.with_suffix(".calls").read_text().splitlines()]
    assert all(call == ["attribution-prepare", "--comment-origin", "--capabilities"] for call in calls)


@pytest.mark.parametrize("change", [
    lambda entry: entry.update(command=["relative"]),
    lambda entry: entry.update(source_root="relative"),
    lambda entry: entry.update(profile_home="relative"),
    lambda entry: entry.update(visibility="unknown"),
    lambda entry: entry.update(allow_anonymous=1),
    lambda entry: entry.update(write_context="not-an-owner"),
])
def test_preparation_config_is_strict_without_executing_discovery(
    prepared_host: tuple[HostConfig, Path, Path], configuration: Configuration,
    change: Callable[[dict[str, Any]], object],
) -> None:
    config, control, _ = prepared_host
    path, _ = configuration
    value = json.loads(path.read_text())
    change(value["workspaces"]["bindings"][0]["preparation"])
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_config(path)
    assert not control.with_suffix(".calls").exists() and not config.root.exists()


@pytest.mark.parametrize("selection", [None, False, True])
def test_host_mapping_selection_is_fresh_only_and_independent_of_preparer_availability(
    prepared_host: tuple[HostConfig, Path, Path], configuration: Configuration,
    selection: bool | None,
) -> None:
    from agent_pull_requests.review_comment_mapping import CommentMappingStore

    _, control, _ = prepared_host
    path, _ = configuration
    value = json.loads(path.read_text())
    if selection is not None:
        value["workspaces"]["bindings"][0]["comment_mappings"] = selection
    path.write_text(json.dumps(value))
    config = load_config(path)
    assert not config.root.exists() and not control.with_suffix(".calls").exists()
    config.provision()
    store = config.store()
    original = store.path.read_bytes()
    supported = CommentMappingStore(store).supported(expires_at=int(time.time()) + 60)
    assert supported is (selection is True)
    state = json.loads(control.read_text())
    control.write_text(json.dumps({**state, "supported": False}))
    operations = _operations(config)
    capabilities = _execute(operations, "capabilities", {})
    assert ("retained-comment" in capabilities["operations"]) is (selection is True)
    assert "allocate" not in capabilities["operations"] and "submit" not in capabilities["operations"]
    assert not operations.write_available
    assert store.path.read_bytes() == original
    with pytest.raises(ValueError, match="fresh dedicated root"):
        config.provision()
    assert store.path.read_bytes() == original


def test_host_mapping_selection_does_not_adopt_existing_prepared_storage(
    prepared_host: tuple[HostConfig, Path, Path], configuration: Configuration,
) -> None:
    config, _, _ = prepared_host
    config.provision()
    original = config.store().path.read_bytes()
    path, _ = configuration
    value = json.loads(path.read_text())
    value["workspaces"]["bindings"][0]["comment_mappings"] = True
    path.write_text(json.dumps(value))
    configured = load_config(path)
    operations = _operations(configured)
    operations.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert "retained-comment" not in _execute(operations, "capabilities", {})["operations"]
    assert "allocate" in _execute(operations, "capabilities", {})["operations"]
    assert configured.store().path.read_bytes() == original


@pytest.mark.parametrize("selection", [None, 0, 1, "true", {}, []])
def test_mapping_config_rejects_nonboolean_selection_without_io(
    prepared_host: tuple[HostConfig, Path, Path], configuration: Configuration, selection: object,
) -> None:
    config, control, _ = prepared_host
    path, _ = configuration
    value = json.loads(path.read_text())
    value["workspaces"]["bindings"][0]["comment_mappings"] = selection
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="JSON boolean"):
        load_config(path)
    assert not config.root.exists() and not control.with_suffix(".calls").exists()


def test_mapping_config_requires_existing_prepared_host(configuration: Configuration) -> None:
    path, value = workspace_config(configuration)
    value["workspaces"]["bindings"][0]["comment_mappings"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="explicit preparation"):
        load_config(path)


def test_host_mapping_selection_does_not_spread_to_other_admitted_routes(
    prepared_host: tuple[HostConfig, Path, Path], configuration: Configuration,
) -> None:
    path, _ = configuration
    value = json.loads(path.read_text())
    value["bindings"].append({
        **value["bindings"][0], "principal": "caller-b", "target": "repository-b",
        "consumer": "dispatch-b",
    })
    first = value["workspaces"]["bindings"][0]
    value["workspaces"]["bindings"].append({
        **first, "principal": "caller-b", "target": "repository-b",
    })
    first["comment_mappings"] = True
    path.write_text(json.dumps(value))
    config = load_config(path)
    config.provision()
    assert config.workspaces is not None
    operations = config.workspaces.operations(config.store(), lambda: None, 60)
    assert "retained-comment" in _execute(
        operations[("caller-a", "repository-a")], "capabilities", {},
    )["operations"]
    assert "retained-comment" not in _execute(
        operations[("caller-b", "repository-b")], "capabilities", {},
    )["operations"]


@pytest.mark.parametrize("support", [
    "missing-store", "missing-receiver", "incompatible", "nested-import", "contract-error",
])
def test_optional_mapping_core_skew_preserves_baseline_and_refuses_fresh_adoption(
    prepared_host: tuple[HostConfig, Path, Path], monkeypatch: pytest.MonkeyPatch, support: str,
) -> None:
    from agent_pull_requests import workspace_comment_mapping
    from agent_pull_requests_service import config as configuration_module

    config, _, _ = prepared_host
    assert config.workspaces is not None
    selected = replace(config, workspaces=replace(
        config.workspaces, comment_mappings=(("caller-a", "repository-a"),),
    ))
    if support == "incompatible":
        contract = workspace_comment_mapping.contract()
        monkeypatch.setattr(workspace_comment_mapping, "contract", lambda: {**contract, "effects": "write"})
    elif support == "nested-import":
        import builtins
        original_import = builtins.__import__

        def missing_dependency(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "agent_pull_requests.review_comment_mapping":
                raise ImportError("synthetic nested optional dependency unavailable")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", missing_dependency)
    elif support == "contract-error":
        def invalid_contract() -> dict[str, str]:
            raise ValueError("synthetic optional declaration invalid")

        monkeypatch.setattr(workspace_comment_mapping, "contract", invalid_contract)
    else:
        module = "review_comment_mapping" if support == "missing-store" else "workspace_comment_mapping"
        monkeypatch.setitem(sys.modules, "agent_pull_requests." + module, None)
    with pytest.raises(ValueError, match="core unsupported"):
        selected.provision()
    assert not config.root.exists()
    config.provision()
    before = config.store().path.read_bytes()
    def prior_constructor(*args: Any, **kwargs: Any) -> Any:
        assert "comment_mappings" not in kwargs
        return WorkspaceOperations(*args, **kwargs)

    monkeypatch.setattr(configuration_module, "WorkspaceOperations", prior_constructor)
    for hosted in (config, selected):
        operations = _operations(hosted)
        capabilities = _execute(operations, "capabilities", {})
        assert "allocate" in capabilities["operations"] and "load" in capabilities["operations"]
        assert "retained-comment" not in capabilities["operations"]
    assert config.store().path.read_bytes() == before
