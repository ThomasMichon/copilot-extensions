"""Inert file aliases reuse the existing registrar composition authority."""

from __future__ import annotations

import copy
import json
from functools import partial
from pathlib import Path

import pytest
from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin

from agent_dispatch import registrar_discovery as discovery
from agent_dispatch import registrar_recipes as recipes
from agent_dispatch import repository_resources as resources
from agent_dispatch.registrar import RegistrarError

PLUGIN = Path(__file__).resolve().parents[1]
SURFACE = Path(".agent-dispatch") / "registrar"
BASES = ("backlog-triager", "issue-reproducer", "effort-builder",
         "effort-driver", "goal-driven")


@pytest.fixture(autouse=True)
def refuse_process_and_network_effects(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("base resolution or discovery attempted process/network execution")

    monkeypatch.setattr("subprocess.Popen", refuse)
    monkeypatch.setattr("socket.socket.connect", refuse)


@pytest.fixture
def owners(tmp_path, monkeypatch):
    consumer = tmp_path / "consumer"
    execution = tmp_path / "execution"
    consumer.mkdir()
    execution.mkdir()
    roots = {"consumer": consumer, "execution": execution}

    def registered(name, *, timeout):
        if name not in roots:
            raise RegistrarError("unregistered fixture owner")
        return roots[name], "worktree"

    plugin = ActivePlugin(
        source="agent-dispatch@copilot-extensions", name="agent-dispatch",
        marketplace="copilot-extensions", root=PLUGIN, scopes=("global",),
    )
    report = ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions={plugin.source: EntryDecision.active(plugin)},
    )
    monkeypatch.setattr(resources, "_registered_repository", registered)
    monkeypatch.setattr(recipes, "_active_plugins", lambda: report)
    monkeypatch.setattr(
        resources, "run_agent_worktrees_capture",
        lambda *a, **k: pytest.fail("composition attempted a host registry command"),
    )
    return consumer, execution


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _leaf(name, execution):
    document = {
        "name": "bounded-work", "repo": "example/project",
        "execution_repo": "execution", "source": "bounded-source",
        "task_label": "bounded-work", "cadence_seconds": 3600,
        "pool": {"max_active_processes": 1, "body": {"agent": "example-worker"}},
        "filters": {"permit": {"machine": ["example-machine"]}},
    }
    if name == "effort-driver":
        document["state_root"] = str(execution)
        document["effort_slugs"] = ["example-effort"]
    else:
        document.update(
            forge={"provider": "github", "producer_login": "example-bot"},
            reservation={"label": "reserved", "orphan_after_seconds": 600},
        )
    return document


@pytest.mark.parametrize("name", BASES)
def test_file_alias_is_exact_global_recipe_without_duplicate_logic(name, tmp_path):
    path = PLUGIN / SURFACE / "templates" / f"{name}.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"extends": f"global:{name}"}
    assert recipes.resolve_extends(
        {"extends": str(path)}, base_dir=tmp_path,
    ) == recipes.resolve_extends({"extends": f"global:{name}"}, base_dir=tmp_path)


@pytest.mark.parametrize("name", BASES)
def test_leaf_through_consumer_and_plugin_file_preserves_global_contract(name, owners):
    consumer, execution = owners
    relative = SURFACE / "templates" / f"{name}.json"
    _write(consumer / relative, {
        "extends": (
            "plugin:agent-dispatch@copilot-extensions:"
            f".agent-dispatch/registrar/templates/{name}.json"
        ),
        "description": "{purpose}",
    })
    original = copy.deepcopy(recipes.GLOBAL_RECIPES)
    fields = _leaf(name, execution)
    leaf = _write(consumer / SURFACE / "bounded.json", {
        "extends": f"repo:consumer:{relative.as_posix()}",
        "params": {"purpose": "bounded proof"}, **fields,
    })
    resolved = recipes.resolve_extends(
        json.loads(leaf.read_text(encoding="utf-8")), base_dir=consumer,
    )
    expected = recipes.resolve_extends(
        {"extends": f"global:{name}", "description": "bounded proof", **fields},
        base_dir=consumer,
    )
    assert resolved == expected
    assert resolved["pool"]["body"] == {"type": "headless", "agent": "example-worker"}
    declarations = discovery.read_declaration_file_set(leaf, repo_root=consumer)
    assert len(declarations) == 2
    assert [item.name for item in declarations] == ["bounded-work-source", "bounded-work-workers"]
    assert declarations[0].spec["cwd"] == "repo:execution:."
    assert declarations[1].repos == "example/project"
    assert declarations[1].filters.permit["machine"] == frozenset({"example-machine"})
    assert recipes.GLOBAL_RECIPES == original


def test_nested_base_assets_alone_are_not_active_registrations():
    before = {
        path: path.read_bytes() for path in (PLUGIN / SURFACE).rglob("*") if path.is_file()
    }
    assert discovery.read_location(PLUGIN / SURFACE) == []
    assert {path: path.read_bytes() for path in before} == before


def test_consumer_nested_bases_alone_are_inactive(owners):
    consumer, _ = owners
    _write(consumer / SURFACE / "templates" / "base.json", {
        "extends": "plugin:agent-dispatch@copilot-extensions:"
                   ".agent-dispatch/registrar/templates/goal-driven.json",
    })
    assert discovery.read_location(consumer / SURFACE, repo_root=consumer) == []


@pytest.mark.parametrize("reference,reason", [
    ("repo:consumer:missing.json", "unavailable"),
    ("repo:consumer:../outside.json", "within its registered root"),
    ("plugin:agent-dispatch@copilot-extensions:../outside.json", "outside"),
    ("plugin:missing@copilot-extensions:base.json", "not an active plugin"),
])
def test_invalid_owner_references_refuse_without_fallback(reference, reason, owners):
    consumer, _ = owners
    with pytest.raises(RegistrarError, match=reason):
        recipes.resolve_extends({"extends": reference}, base_dir=consumer)


def test_repo_file_cycle_refuses(owners):
    consumer, _ = owners
    path = _write(consumer / SURFACE / "templates" / "cycle.json", {
        "extends": "repo:consumer:.agent-dispatch/registrar/templates/cycle.json",
    })
    with pytest.raises(RegistrarError, match="cyclic reference chain"):
        recipes.resolve_extends({"extends": str(path)}, base_dir=consumer)


def test_disabled_flag_is_not_invented_by_base_aliases(owners):
    consumer, execution = owners
    path = _write(consumer / SURFACE / "bounded.json", {
        "extends": "plugin:agent-dispatch@copilot-extensions:"
                   ".agent-dispatch/registrar/templates/goal-driven.json",
        **_leaf("goal-driven", execution), "enabled": False,
    })
    with pytest.raises(RegistrarError, match="unknown key"):
        discovery.read_declaration_file_set(path, repo_root=consumer)


@pytest.mark.parametrize("field", ["forge", "reservation"])
def test_effort_driver_alias_rejects_repository_issue_only_fields(field, owners):
    consumer, execution = owners
    document = {
        "extends": "plugin:agent-dispatch@copilot-extensions:"
                   ".agent-dispatch/registrar/templates/effort-driver.json",
        **_leaf("effort-driver", execution),
        field: {},
    }
    path = _write(consumer / SURFACE / "bounded.json", document)
    with pytest.raises(RegistrarError, match=f"unknown key.*{field}"):
        discovery.read_declaration_file_set(path, repo_root=consumer)


def test_file_chain_resolves_plugin_through_real_local_manifest_mapping(tmp_path, monkeypatch):
    from plugin_activation import resolve_active_plugins

    consumer = tmp_path / "consumer"
    execution = tmp_path / "execution"
    execution.mkdir()
    roots = {"consumer": consumer, "execution": execution}

    def registered(name, *, timeout):
        return roots[name], "worktree"

    _write(consumer / ".github" / "copilot" / "settings.json", {
        "extraKnownMarketplaces": {"local-alias": {"source": {
            "source": "directory", "path": str(PLUGIN.parents[1]),
            "manifestName": "copilot-extensions", "stableId": "fixture-installation",
        }}},
        "enabledPlugins": {"agent-dispatch@local-alias": True},
    })
    relative = SURFACE / "templates" / "goal-driven.json"
    _write(consumer / relative, {
        "extends": "plugin:agent-dispatch@local-alias:"
                   ".agent-dispatch/registrar/templates/goal-driven.json",
    })
    leaf = _write(consumer / SURFACE / "bounded.json", {
        "extends": f"repo:consumer:{relative.as_posix()}",
        **_leaf("goal-driven", execution),
    })
    monkeypatch.setattr(resources, "_registered_repository", registered)
    monkeypatch.setattr(recipes, "resolve_active_plugins", partial(
        resolve_active_plugins, home=tmp_path / "home", project_root=consumer,
        include_projects=False, plugin_names=("agent-dispatch",),
    ))
    recipes._reset_active_plugins_cache()
    try:
        declarations = discovery.read_declaration_file_set(leaf, repo_root=consumer)
        assert recipes._active_plugins().active["agent-dispatch@local-alias"].root == PLUGIN
        assert [item.name for item in declarations] == ["bounded-work-source", "bounded-work-workers"]
        assert declarations[1].filters.permit["machine"] == frozenset({"example-machine"})
    finally:
        recipes._reset_active_plugins_cache()
