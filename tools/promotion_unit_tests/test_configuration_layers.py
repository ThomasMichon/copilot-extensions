"""Pure composition coverage; native Node/filesystem qualification stays separate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs/configuration-layers/src/configuration_layers/__init__.py"
SPEC = importlib.util.spec_from_file_location("promotion_configuration_layers", SOURCE)
assert SPEC and SPEC.loader
sdk = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sdk
SPEC.loader.exec_module(sdk)


def _context(**fields):
    return {
        "repository_id": "repo-alpha",
        "execution_machine": "worker-one",
        "installation_cell": "cell-one",
        "binding": "unbound",
        **fields,
    }


def _layer(name, tier, origin, values, **scope):
    return {"id": name, "tier": tier, "origin": origin, "values": values, **scope}


def test_knowledge_grafts_below_machine_and_exact_repository_machine():
    context = _context(binding="bound", knowledge_root=str(ROOT))
    layers = [
        _layer("exact", "repository_machine", "repository_machine",
               {"target": "exact"}, repository_id="repo-alpha",
               execution_machine="worker-one", installation_cell="cell-one"),
        _layer("knowledge", "repository", "knowledge",
               {"target": "knowledge", "portable": "knowledge"},
               repository_id="repo-alpha"),
        _layer("defaults", "defaults", "defaults", {"target": "default", "keep": True}),
        _layer("machine", "machine", "local_machine",
               {"target": "machine", "host": "machine"}, execution_machine="worker-one"),
        _layer("base", "repository", "base",
               {"target": "base", "portable": "base"}, repository_id="repo-alpha"),
    ]
    result = sdk.compose(context, layers)
    assert result["values"] == {
        "target": "exact", "portable": "knowledge", "keep": True, "host": "machine",
    }
    assert result["layers"] == ["defaults", "base", "knowledge", "machine", "exact"]
    assert result["provenance"]["/portable"]["id"] == "knowledge"
    assert result["provenance"]["/target"]["id"] == "exact"


def test_exact_scope_excludes_foreign_repository_machine_and_cell():
    layers = [
        _layer("defaults", "defaults", "defaults", {"target": "default"}),
        _layer("same-name-other-repo", "repository", "base",
               {"foreign_repo": True}, repository_id="repo-beta"),
        _layer("other-machine", "machine", "local_machine",
               {"foreign_machine": True}, execution_machine="worker-two"),
        _layer("other-cell", "repository_machine", "repository_machine",
               {"foreign_cell": True}, repository_id="repo-alpha",
               execution_machine="worker-one", installation_cell="cell-two"),
    ]
    result = sdk.compose(_context(), layers)
    assert result["values"] == {"target": "default"}
    assert result["layers"] == ["defaults"]


def test_unresolved_binding_and_atomic_replacement_do_not_fall_back():
    with pytest.raises(sdk.ConfigurationError, match="unresolved"):
        sdk.compose(_context(binding="unresolved"), [])
    layers = [
        _layer("defaults", "defaults", "defaults",
               {"items": ["base"], "map": {"old": True}, "keep": {"nested": True}}),
        _layer("base", "repository", "base",
               {"items": [], "map": None, "keep": {}}, repository_id="repo-alpha"),
        _layer("unbound-knowledge", "repository", "knowledge",
               {"items": ["private"]}, repository_id="repo-alpha"),
    ]
    result = sdk.compose(_context(), layers)
    assert result["values"] == {"items": [], "map": None, "keep": {"nested": True}}
    assert result["provenance"]["/items"]["id"] == "base"
    assert result["provenance"]["/map"]["id"] == "base"
    assert "/map/old" not in result["provenance"]
    assert result["provenance"]["/keep/nested"]["id"] == "defaults"
