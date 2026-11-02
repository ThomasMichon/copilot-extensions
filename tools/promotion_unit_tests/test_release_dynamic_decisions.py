"""Decision witnesses for existing self-file-loaded release implementations."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from vendored_lib_inventory import is_artifact_only

ROOT = Path(__file__).resolve().parents[2]


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bumps = _load("qualified_version_bump_guard", "tools/check-version-bump.py")
provenance = _load("qualified_materialized_provenance", "tools/clean-room/lib/materialized_provenance.py")


def test_version_guard_charges_canonical_and_vendored_payloads_without_git(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    plugin = plugins / "example"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"example"}', encoding="utf-8")
    monkeypatch.setattr(bumps, "PLUGINS_DIR", plugins)
    monkeypatch.setattr(bumps, "REPO", tmp_path)
    required = bumps._plugins_needing_bump([
        "plugins/example/src/payload.py", "libs/shared/payload.py",
        "plugins/unknown/src/payload.py",
    ], {"shared": ["example"]})
    assert required == {"example": {"plugins/example/", "libs/shared/ (vendored)"}}
    assert bumps._is_ignored(("src",), "payload.py") is False


@pytest.mark.parametrize("kind,expected", [
    ("cache", True), ("root-build", True), ("nested-build", False),
    ("source", False), ("redirected", False),
])
def test_vendored_source_inventory_does_not_confuse_artifacts_with_source(
    tmp_path, monkeypatch, kind, expected,
):
    root = tmp_path / "library"
    root.mkdir()
    relative = {
        "cache": "__pycache__/stale.cache", "root-build": "build/output.whl",
        "nested-build": "nested/build/output.whl", "source": "payload.py",
        "redirected": "redirected",
    }[kind]
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"synthetic inventory input")
    if kind == "redirected":
        ordinary = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or ordinary(path))
    assert is_artifact_only(root) is expected


@pytest.mark.parametrize("fault", ["none", "changed-content", "wrong-source"])
def test_materialized_source_receipt_binds_actual_paths_and_bytes_without_build(
    tmp_path, fault,
):
    root = tmp_path / "payload"
    (root / "package").mkdir(parents=True)
    content = b"raise AssertionError('data is never executed')\n"
    source = root / "package/payload.py"
    source.write_bytes(content)
    manifest = provenance.content_manifest(root)
    assert manifest["package"] == {"kind": "directory"}
    assert manifest["package/payload.py"] == {
        "kind": "file", "sha256": hashlib.sha256(content).hexdigest(), "executable": False,
    }
    (root / provenance.RECEIPT).write_text(json.dumps({
        "schema": provenance.SCHEMA, "version": 1, "source_commit": "a" * 40,
        "paths": manifest,
    }), encoding="utf-8")
    if fault == "changed-content":
        source.write_bytes(b"changed data\n")
    if fault == "none":
        assert provenance.verify(root, "a" * 40) == "a" * 40
    else:
        with pytest.raises(ValueError, match="does not match"):
            provenance.verify(root, ("b" if fault == "wrong-source" else "a") * 40)
