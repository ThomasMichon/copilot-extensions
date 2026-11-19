"""Pure component ordering carriers; native recovery remains in the plugin lane."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "plugins/agent-index/src/agent_index/indexing/component_manifest.py"
)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_promotion_component_manifest", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_component_versions_preserve_exact_mutation_coverage_and_semantics(api: ModuleType) -> None:
    body = api.ComponentVersion("sample:components", "items/1/body.md", 7, "body/v1")
    deleted = api.ComponentVersion("sample:components", "items/1/comments/2.md", 4, "comment/v1")
    manifest = SimpleNamespace(
        upserts=[SimpleNamespace(source=body.source, path=body.path)],
        deletions=[SimpleNamespace(source=deleted.source, path=deleted.path)],
        component_versions=[body, deleted], poll_refresh=None,
    )
    api.validate_components(manifest)
    assert api.ComponentVersion.from_dict(asdict(deleted)) == deleted
    for changed in (
        [], (body, deleted), [object()], [body], [body, deleted, body],
        [body, replace(deleted, source="another:account")],
        [body, replace(deleted, path="items/1/comments/3.md")],
    ):
        manifest.component_versions = changed
        with pytest.raises(ValueError):
            api.validate_components(manifest)
    manifest.component_versions = [body, deleted]
    manifest.poll_refresh = object()
    with pytest.raises(ValueError, match="polling refresh"):
        api.validate_components(manifest)
    for version in (True, False, 0, -1, 1.5, "7", None):
        with pytest.raises(ValueError, match="positive integer"):
            api.ComponentVersion.from_dict(asdict(body) | {"version": version})
    for name in ("source", "path", "semantics"):
        for value in ("", " ", None, 1):
            with pytest.raises(ValueError, match="nonempty string"):
                api.ComponentVersion.from_dict(asdict(body) | {name: value})
    for raw in ({"version": 1}, asdict(body) | {"unknown": "ignored"}, None):
        with pytest.raises(ValueError, match="Invalid pending component"):
            api.ComponentVersion.from_dict(raw)


def test_unqualified_component_application_refuses_without_reinterpreting_legacy(api: ModuleType) -> None:
    records: tuple[object, ...] = ([], {}, [object()])
    for versions in records:
        manifest = SimpleNamespace(component_versions=versions)
        with pytest.raises(ValueError, match="Ordered component application"):
            api.require_unordered_application(manifest)
        assert manifest.component_versions is versions
    legacy = SimpleNamespace(component_versions=None)
    api.require_unordered_application(legacy)
    api.validate_components(legacy)


def test_component_ordering_retains_deletions_and_refuses_equal_version_conflicts(api: ModuleType) -> None:
    version = api.ComponentVersion("sample:components", "items/1/comment.md", 7, "comment/v1")
    current = api.ComponentContent(version, "a" * 64, False)
    assert current.covered_by(current)
    assert not replace(current, component=replace(version, version=8)).covered_by(current)
    tombstone = replace(current, component=replace(version, version=9), deleted=True)
    assert current.covered_by(tombstone)
    assert not replace(current, component=replace(version, version=10)).covered_by(tombstone)
    for conflicting in (
        replace(current, fingerprint="b" * 64), replace(current, deleted=True),
        replace(current, component=replace(version, semantics="comment/v2")),
    ):
        with pytest.raises(ValueError, match="Equal component versions"):
            conflicting.covered_by(current)
    for field in ("source", "path"):
        with pytest.raises(ValueError, match="identity"):
            replace(current, component=replace(version, **{field: "different"})).covered_by(current)
