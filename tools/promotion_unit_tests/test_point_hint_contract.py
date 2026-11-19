"""Pure wire/union controls; HTTP, SQLite and workers stay in the plugin lane."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-index/src/agent_index/indexing"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    package = "_promotion_point_hints"
    parent = ModuleType(package)
    parent.__path__ = []
    monkeypatch.setitem(sys.modules, package, parent)
    result: ModuleType | None = None
    for name in ("change_hints", "point_hints"):
        spec = importlib.util.spec_from_file_location(f"{package}.{name}", SOURCE / f"{name}.py")
        assert spec is not None and spec.loader is not None
        result = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, result)
        spec.loader.exec_module(result)
    assert result is not None
    return result


def point(api: ModuleType, **kwargs: Any) -> Any:
    return api.PointChangeHints(**{
        "source": "ado:sample:points", "canonical_family": "ado:points",
        "account_id": "account-a",
        "binding": api.PointBinding("https://example.test/project", "union-a", "context-a"),
        "targets": (api.WorkItemRevisionTarget("42", 7, ("event-a",)),),
        **kwargs,
    })


def test_v2_wire_union_preserves_binding_target_versions_and_v1_refusal(api: ModuleType) -> None:
    first = point(api)
    second = point(api, targets=(
        api.CommentVersionTarget("42", "9", 2, ("event-b",)),
        api.CommentVersionTarget("42", "9", 3, ("event-c",)),
        api.WorkItemRevisionTarget("42", 7, ("event-d",)),
    ))
    merged = first.union(second)
    assert set(merged.targets) == set(first.targets + second.targets)
    assert api.read_change_hints(merged.to_json()) == merged == second.union(first)
    for binding in (
        replace(first.binding, endpoint="https://other.test/project"),
        replace(first.binding, query_scope="union-b"),
        replace(first.binding, query_context="context-b"),
    ):
        with pytest.raises(ValueError, match="different scopes"):
            first.union(replace(first, binding=binding))
    legacy = api.ChangeHints(source=first.source, canonical_family=first.canonical_family,
                             account_id=first.account_id, item_ids=("42:comment:9:3",))
    assert api.read_change_hints(legacy.to_json()) == legacy
    with pytest.raises(ValueError):
        api.ChangeHints.from_json(merged.to_json())
    with pytest.raises(ValueError, match="different"):
        api.union_change_hints(legacy, merged)


def test_v2_refuses_broad_unknown_or_unbound_targets_before_admission(api: ModuleType) -> None:
    valid = point(api).to_dict()
    for bad in (
        {**valid, "item_ids": ["42"]}, {**valid, "version": True},
        {**valid, "targets": []}, {**valid, "binding": {}},
        {**valid, "targets": [{"kind": "discussion", "item_id": "42"}]},
        {**valid, "targets": [{
            "kind": "comment_version", "comment_id": "9", "version": 1, "event_ids": [],
        }]},
        {**valid, "targets": [{
            "kind": "work_item_revision", "item_id": "42", "revision": True, "event_ids": [],
        }]},
    ):
        with pytest.raises(ValueError):
            api.decode_change_hints(bad)
