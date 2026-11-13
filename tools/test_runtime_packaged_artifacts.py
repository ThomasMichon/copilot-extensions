"""Canonical build resources survive the shipped validators' isolation boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from test_local_install_source import embedded_profile_source as embedded_profile_source  # noqa: F401
from test_runtime_admission_toolkit import _toolkit

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["worktrees", "core"])
def test_copied_toolkit_validates_resource_bearing_artifacts(tmp_path, monkeypatch, kind):
    import build_python_artifacts as builder
    import promotion_python_artifacts as artifacts

    toolkit = _toolkit(tmp_path)
    candidate = tmp_path / "candidate"
    if kind == "core":
        from test_core_python_artifacts import _build

        wheels, manifest, _calls = _build(candidate, monkeypatch)
    else:
        from plugin_runtime_resources import build_inputs
        from test_promotion_python_artifacts import _STATIC_BUILD, _wheel_bytes
        from test_standalone_python_artifacts import LOCK

        source = candidate / "plugins/agent-worktrees"
        (source / "src/agent_worktrees").mkdir(parents=True)
        (source / "src/agent_worktrees/__init__.py").write_bytes(b"VALUE = 'first-party fixture'\n")
        (source / "pyproject.toml").write_text(
            _STATIC_BUILD + '[project]\nname="agent-worktrees"\nversion="1.0"\n'
            '[tool.setuptools.packages.find]\nwhere=["src"]\n', encoding="utf-8",
        )
        (source / "setup.py").write_bytes((ROOT / "plugins/agent-worktrees/setup.py").read_bytes())
        helper = candidate / "libs/versioned-runtime/standalone_build.py"
        helper.parent.mkdir(parents=True)
        helper.write_bytes((ROOT / "libs/versioned-runtime/standalone_build.py").read_bytes())
        mapping = runpy.run_path(str(helper))["RUNTIME_RESOURCES"]
        for name in mapping:
            path = helper.parent / name
            path.write_bytes(b"# controlled runtime resource\n")
        monkeypatch.setattr(builder, "REPO", candidate)
        monkeypatch.setattr(builder, "PLUGINS_DIR", candidate / "plugins")
        monkeypatch.setattr(builder, "LIBS_DIR", candidate / "libs")

        def build(source_dir, out_dir, **_options):
            _metadata, resources = build_inputs(source_dir, root=candidate)
            out_dir.mkdir(parents=True, exist_ok=True)
            wheel = out_dir / "agent_worktrees-1.0-py3-none-any.whl"
            wheel.write_bytes(_wheel_bytes(
                "agent_worktrees", "1.0",
                members={name: path.read_bytes() for name, path in resources.items()},
            ))
            return wheel

        monkeypatch.setattr(builder, "build_wheel", build)
        wheels = tmp_path / "wheels"
        manifest = builder.build_plugin_artifacts("agent-worktrees", out_dir=wheels, toolchain=LOCK)
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, bundles)
    script = """
import importlib.abc, json, pathlib, sys
root, candidate, bundle = map(pathlib.Path, sys.argv[1:4])
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
class NoPublisher(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"promotion_python_artifacts", "publish_python_artifacts", "promote_release"}:
            raise AssertionError("validator imported publisher: " + fullname)
sys.meta_path.insert(0, NoPublisher())
import artifact_bundle_validation as validation
observed = validation.validate_bundle(bundle, json.loads(sys.argv[4]), candidate_root=candidate)
assert observed == json.loads(sys.argv[5])
for name in json.loads((root / "toolkit.json").read_text())["modules"]:
    module = sys.modules.get(name)
    if module is not None and getattr(module, "__file__", None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root), name
print("isolated-resource-bundle-ok")
"""
    observed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(toolkit), str(candidate),
         str(bundles / entry["filename"]), json.dumps(entry), json.dumps(manifest)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    assert observed.stdout.strip() == "isolated-resource-bundle-ok"


def test_copied_manager_provider_prepares_embedded_first_party_inputs(
    tmp_path, embedded_profile_source,
):
    from materialize_launch_wrapper_assets import materialize_into

    package = tmp_path / "package"
    package.mkdir()
    report = materialize_into(
        source_consumer_dir=ROOT / "worktree-manager",
        dest_consumer_dir=package / "_runtime_assets", canonical_root=ROOT, dest_root=package,
    )
    assert report and all(row.startswith("OK ") for row in report), report
    provider = package / "_runtime_assets/scripts/runtime-input-provider"
    script = """
import pathlib, sys, time
root, snapshot, output = map(pathlib.Path, sys.argv[1:4])
sys.path[:0] = [str(root / name) for name in (
    "tools", "libs/installer-engine", "libs/versioned-runtime",
    "libs/agent-procutil/src", "worktree-manager/scripts",
)]
import local_install_profile as native
from packaging.markers import default_environment
deadline = time.monotonic() + 25
selected, wheels = native.first_party(snapshot, deadline=deadline)
assert set(wheels) == {"copilot-extensions-worktree-manager"}
archives = {row["filename"]: snapshot / row["wheel_directory"] / row["filename"]
            for row in wheels.values()}
with native.retain_wheel_inputs(
    archives, {row["filename"]: row["sha256"] for row in wheels.values()},
    max_wheels=2, max_archive_bytes=1 << 20, max_total_bytes=2 << 20,
    max_snapshot_bytes=2 << 20, deadline=deadline,
) as retained:
    profile = native.complete_profile(
        selected, {"device": "cpu", "environment": default_environment(), "tags": ["py3-none-any"]},
        ["copilot-extensions-worktree-manager==1.0.0"], retained,
        policy_id="fixture-policy", acquisition_id="fixture-acquisition", deadline=deadline,
    )
    prepared = native._write_snapshot(selected, output, profile, retained, deadline=deadline)
with native.source_inputs.manager_inputs(prepared, deadline=deadline) as (observed, inputs):
    assert observed.identity == selected.identity
    inputs.verify()
for module in (native, sys.modules["standalone_python_artifacts"],
               sys.modules["artifact_bundle_validation"]):
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
print("isolated-manager-first-party-preparation-ok")
"""
    observed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(provider),
         str(embedded_profile_source), str(tmp_path / "prepared")],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    assert observed.stdout.strip() == "isolated-manager-first-party-preparation-ok"
