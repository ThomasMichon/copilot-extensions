"""The original membership producer expands a validated cell observation once."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-worktrees/scripts/managed_session.py"


@pytest.mark.parametrize("case", ["fresh", "old-association", "old-declared-paths", "current-association"])
def test_original_membership_producer_preserves_inputs_and_upgrades_only_missing_cell_view(
    monkeypatch, case,
):
    spec = importlib.util.spec_from_file_location("promotion_prepared_cell_membership", SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    class PassivePath(type(Path())):
        def is_absolute(self):
            return True

        def is_dir(self):
            return True

    anchor = PassivePath("/example")
    registry = PassivePath("/cells/selected/plugins/agent-worktrees")
    marker = PassivePath("/sessions/prepared/managed-session.json")
    selected = {
        "namespaceReceipt": str(registry.parent.parent / "namespace.json"),
        "installReceipt": str(registry / "install.json"),
        "payloadRoot": str(PassivePath("/payload")),
        "cellRoot": str(registry.parent.parent),
        "pluginRoot": str(registry),
    }
    files = {path: "a" * 64 for path in (
        selected["namespaceReceipt"], selected["installReceipt"],
        str(PassivePath(selected["payloadRoot"]) / "plugin.json"),
    )}
    selected["membershipFiles"] = files
    roots = {name: {"path": str(registry / name), "resolved": str(registry / name)}
             for name in ("versions", "snapshots", "state", "run", "logs", "cache", "launchers")}
    selected["membershipRoots"] = roots
    selected["membershipSourceKind"] = "github"
    selected["membershipDeclaredPaths"] = {
        name: selected[name] for name in ("payloadRoot", "pluginRoot", "namespaceReceipt")
    }
    monkeypatch.setattr(module, "Path", PassivePath)
    monkeypatch.setattr(module, "os", SimpleNamespace(
        path=SimpleNamespace(isabs=lambda _path: True, expanduser=lambda path: path),
        sep=os.sep, environ={"COPILOT_EXTENSIONS_CONTEXT": selected["installReceipt"]},
    ))
    monkeypatch.setattr(module, "canonical", lambda path: str(PassivePath(path)))
    monkeypatch.setattr(module, "resolve_registry_context", lambda **_kw: selected)
    monkeypatch.setattr(module, "detect_platform", lambda: "windows")
    monkeypatch.setattr(module, "_stamp", lambda path, **_kw: ["file", str(path), "1", "2"])
    monkeypatch.setattr(module, "_session_marker", lambda *_a: marker)
    monkeypatch.setattr(module, "_is_link_or_reparse", lambda *_a: False)
    monkeypatch.setattr(module, "_entries", lambda *_a: {"example": {}})
    monkeypatch.setattr(module, "_anchor_candidates", lambda *_a: [str(anchor)])

    def identity(_cwd, *, observe):
        observe(anchor / ".git")
        return {"root": str(anchor), "git_dir": str(anchor / ".git"),
                "common_dir": str(anchor / ".git")}

    monkeypatch.setattr(module, "repository_identity", identity)
    def missing(_path):
        raise FileNotFoundError()

    monkeypatch.setattr(module, "_read", missing)
    published = []
    monkeypatch.setattr(module, "_publish_association", lambda path, value: published.append((path, value)))
    event = {"sessionId": "prepared", "cwd": str(anchor)}
    first = module.inspect(event)
    assert first["admitted"]
    assert len(published) == 2
    cache, record = published[0]
    assert record["installation"] == {
        "version": 1, "files": files, "roots": roots, "sourceKind": "github",
        "declaredPaths": selected["membershipDeclaredPaths"],
        **{key: value for key, value in selected.items()
           if key not in {
               "membershipFiles", "membershipRoots", "membershipSourceKind", "membershipDeclaredPaths",
           }},
    }
    if case == "fresh":
        return
    original = json.loads(json.dumps(record))
    if case == "old-association":
        original.pop("installation")
    elif case == "old-declared-paths":
        original["installation"].pop("declaredPaths")
    before = json.loads(json.dumps(original))
    monkeypatch.setattr(module, "_read", lambda *_a: json.dumps(original))
    monkeypatch.setattr(module, "repository_identity", lambda *_a, **_kw: pytest.fail("warm Git traversal"))
    published.clear()
    assert module.inspect(event) == first
    assert original == before
    if case in {"old-association", "old-declared-paths"}:
        assert [path for path, _value in published] == [cache, marker]
        assert all(value == record for _path, value in published)
    else:
        assert published == []


@pytest.mark.parametrize("case", [
    "current", "older-sdk", "oversized", "opaque-witness", "opaque-roots",
    "directory-source", "unknown-source",
    "opaque-declarations", "duplicate-declarations", "missing-getter",
    "alias-payload", "alias-plugin", "alias-namespace", "resolver-error",
])
def test_registry_capture_borrows_validated_bytes_without_a_second_resolution(monkeypatch, case):
    source = SOURCE.with_name("registry_root.py")
    spec = importlib.util.spec_from_file_location("promotion_registry_membership_capture", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    class PassivePath(type(Path())):
        def is_absolute(self):
            return True

    root = PassivePath("/durable/marketplaces/selected/plugins/agent-worktrees")
    resolved = {
        "pluginRoot": str(root), "payloadRoot": str(PassivePath("/payload")),
        "namespaceReceipt": str(root.parent.parent / "namespace.json"),
        "installReceipt": str(root / "install.json"),
        "source": {"kind": "directory" if case == "directory-source" else
                   "unknown" if case == "unknown-source" else "github"},
    }
    active = {"files": None}
    calls = []
    root_calls = []
    string_calls = []

    def string_property(value, name, default=""):
        string_calls.append(name)
        return value.get(name, default)

    def resolve_root(plugin_root, relative, name):
        root_calls.append(name)
        return plugin_root / relative

    def scope(function):
        def wrapped():
            active["files"] = {}
            try:
                return function()
            finally:
                active["files"] = None
        return wrapped

    def resolve(**_kw):
        calls.append("resolve")
        if case not in {"opaque-declarations", "missing-getter"}:
            for name, target, alias in (
                ("root", "payloadRoot", "alias-payload"),
                ("pluginRoot", "pluginRoot", "alias-plugin"),
                ("namespaceReceipt", "namespaceReceipt", "alias-namespace"),
            ):
                path = PassivePath(resolved[target])
                text = str(path.parent / "alias" / ".." / path.name) if case == alias else str(path)
                helper._string_property({name: text}, name)
            if case == "duplicate-declarations":
                helper._string_property({"root": resolved["payloadRoot"]}, "root")
        if case == "resolver-error":
            raise ValueError("original refusal")
        if case != "opaque-roots":
            for name in ("versions", "snapshots", "state", "run", "logs", "cache", "launchers"):
                helper._resolve_relative_root(root, name, name)
        if active["files"] is not None:
            for path in (resolved["namespaceReceipt"], resolved["installReceipt"]):
                active["files"][os.path.normcase(os.path.abspath(path))] = SimpleNamespace(
                    digest="b" * 64,
                    metadata=None if case == "opaque-witness" else
                    (0, 1048577 if case == "oversized" else 1, 0, 0),
                )
        return resolved

    helper = SimpleNamespace(
        resolve_context=resolve, _VALIDATED_FILE_SHA256=SimpleNamespace(get=lambda: active["files"]),
        _resolve_relative_root=resolve_root,
    )
    if case != "missing-getter":
        helper._string_property = string_property
    if case != "older-sdk":
        helper._validation_scope = scope

    def payload(_env, _explicit, *, manifest_witness=None):
        if manifest_witness is not None:
            manifest_witness[str(PassivePath("/payload/plugin.json"))] = "a" * 64
        return PassivePath("/payload")

    monkeypatch.setattr(module, "Path", PassivePath)
    monkeypatch.setattr(module, "_payload_root", payload)
    monkeypatch.setattr(module, "_load_context_helper", lambda *_a: helper)
    def invoke():
        return module.resolve_registry_context(
            environment={"COPILOT_EXTENSIONS_CONTEXT": resolved["installReceipt"]},
            capture_membership=True,
        )

    if case == "resolver-error":
        with pytest.raises(module.RegistryRootError, match="original refusal"):
            invoke()
    else:
        result = invoke()
    assert calls == ["resolve"]
    assert active["files"] is None
    assert helper._resolve_relative_root is resolve_root
    if case != "missing-getter":
        assert helper._string_property is string_property
        assert string_calls == (
            [] if case == "opaque-declarations" else
            ["root", "pluginRoot", "namespaceReceipt"] + (
                ["root"] if case == "duplicate-declarations" else []
            )
        )
    if case == "resolver-error":
        assert root_calls == []
        return
    assert root_calls == ([] if case == "opaque-roots" else [
        "versions", "snapshots", "state", "run", "logs", "cache", "launchers",
    ])
    assert "membershipFiles" not in resolved
    if case == "current":
        assert result["membershipSourceKind"] == "github"
        assert result["membershipFiles"] == {
            str(PassivePath("/payload/plugin.json")): "a" * 64,
            resolved["namespaceReceipt"]: "b" * 64, resolved["installReceipt"]: "b" * 64,
        }
        assert result["membershipRoots"] == {
            name: {"path": str(root / name), "resolved": str(root / name)}
            for name in root_calls
        }
        assert result["membershipDeclaredPaths"] == {
            name: resolved[name] for name in ("payloadRoot", "pluginRoot", "namespaceReceipt")
        }
    else:
        assert result == resolved
