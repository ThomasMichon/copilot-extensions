"""Self-contained SDK packaging, without enabling an installation consumer."""
from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys

import pytest

from test_generate import _manifest, _required_context_manifest, generator


@pytest.mark.parametrize("dependency,error", [
    ("publish_python_artifacts", "repository adapter"),
    ("unselected_external_tooling", "undeclared toolkit dependency"),
])
def test_packaging_refuses_unowned_dependency_expansion(tmp_path, dependency, error):
    source = generator.TEMPLATES.parent / "runtime_admission.py"
    spec = importlib.util.spec_from_file_location("_toolkit_closure_control", source)
    assert spec and spec.loader
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    packager.ENTRY_MODULES = ("runtime_profile_policy",)
    (tmp_path / "tools").mkdir()
    (tmp_path / "libs").mkdir()
    (tmp_path / "worktree-manager" / "src").mkdir(parents=True)
    (tmp_path / "tools" / "runtime_profile_policy.py").write_text(
        f"import {dependency}\n", encoding="utf-8",
    )
    with pytest.raises(ValueError, match=error):
        packager.expected_files(tmp_path)


def _enabled(tmp_path):
    manifest = _required_context_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["runtimeAdmissionToolkit"] = True
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest


def test_toolkit_packaging_is_opt_in_and_requires_installation_context(tmp_path):
    manifest = _manifest(tmp_path)
    assert not any("_runtime_admission" in path.parts for path in generator.expected_files(manifest))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["runtimeAdmissionToolkit"] = True
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="requires installation context"):
        generator.expected_files(manifest)
    data["runtimeAdmissionToolkit"] = "true"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a boolean"):
        generator.expected_files(manifest)


def test_generated_toolkit_imports_and_resources_are_payload_local(tmp_path):
    manifest = _enabled(tmp_path)
    generated = generator.expected_files(manifest)
    toolkit = manifest.parent / "scripts" / "_runtime_admission"
    selected = {path: content for path, content in generated.items() if path.is_relative_to(toolkit)}
    assert selected
    assert selected == {
        path: content for path, content in generator.expected_files(manifest).items()
        if path.is_relative_to(toolkit)
    }
    assert generator.process_manifest(manifest, check=False) == []
    assert generator.process_manifest(manifest, check=True) == []
    inventory = json.loads((toolkit / "toolkit.json").read_text(encoding="utf-8"))
    assert inventory["requirements"] == ["packaging>=23"]
    assert not any(
        part in {"packaging", "site-packages", "self_install.py", "publish_python_artifacts.py"}
        for path in selected for part in path.relative_to(toolkit).parts
    )
    script = """
import importlib, importlib.abc, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
blocked = {
    "worktree_manager.self_install", "worktree_manager.__main__",
    "runtime_source_manager", "promotion_python_artifacts",
    "publish_python_artifacts", "promote_release",
}
class RejectRepositoryAdapters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError("unexpected repository adapter: " + fullname)
sys.meta_path.insert(0, RejectRepositoryAdapters())
inventory = json.loads((root / "toolkit.json").read_text())
for name in inventory["entryModules"]:
    importlib.import_module(name)
from native_daemon_artifacts import trusted_setup
assert trusted_setup(root / "libs" / "agent-procutil", {
    "project": {"name": "agent-procutil"},
}) == (root / "libs" / "agent-procutil" / "setup.py").read_bytes()
from worktree_manager import runtime_source_config
assert runtime_source_config.configured_source().channel == "marketplace"
for name in inventory["modules"]:
    module = sys.modules.get(name)
    if module is not None and getattr(module, "__file__", None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root), (name, module.__file__)
assert not blocked.intersection(sys.modules)
print("self-contained-admission-toolkit-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(toolkit)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "self-contained-admission-toolkit-ok"
