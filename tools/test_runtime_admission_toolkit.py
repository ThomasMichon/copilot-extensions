"""Fresh payload-only traversal of the actual source/build/constructor owners."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from test_runtime_source_build import (
    build_only_feed, options, packaging_feed, source_with_process_owner,
)
from test_runtime_source_build import configured_source as configured_source  # noqa: F401
from test_runtime_source_build import feeds as feeds  # noqa: F401
from test_runtime_source_build import adopter as adopter  # noqa: F401
from test_runtime_source_build import target as target  # noqa: F401
from test_runtime_source_build import tmp_path as tmp_path  # noqa: F401
from test_runtime_feed_configuration import environment
from test_runtime_source_lock import plan
from test_installer_wheel_acquisition import CERTIFICATE
from test_installer_packaging_seed import engine as engine  # noqa: F401
from test_installer_packaging_seed import seed, selected_seed
from promotion_unit_tests.test_runtime_profile_wheels import _record


def _toolkit(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "payload_toolkit_packager", repo / "libs" / "payload-invocation" / "runtime_admission.py",
    )
    assert spec and spec.loader
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    toolkit = tmp_path / "payload" / "_runtime_admission"
    for relative, content in packager.expected_files(repo).items():
        path = toolkit / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        if path.suffix == ".sh":
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return toolkit


def test_admitted_seed_runs_payload_local_semantic_validation(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
):
    toolkit = _toolkit(tmp_path)
    record = tmp_path / "profile.json"
    record.write_text(json.dumps(_record()), encoding="utf-8")
    with selected_seed(
        adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
    ) as (_state, arguments, _checks, _rejected):
        with seed.retain_packaging_seed(**arguments) as tooling:
            python = tooling.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            script = """
import importlib.metadata, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root.parent))
import _runtime_admission
_runtime_admission.activate()
assert {d.metadata["Name"] for d in importlib.metadata.distributions()} == {"packaging"}
from runtime_profile import ProfileError, validate_profile
record = json.loads(pathlib.Path(sys.argv[2]).read_text())
profile = validate_profile(record)
assert profile.record() == record
record["wheels"].pop()
try:
    validate_profile(record)
except ProfileError:
    pass
else:
    raise AssertionError("admitted interpreter accepted an incomplete closure")
inventory = json.loads((root / "toolkit.json").read_text())
for name in inventory["entryModules"]:
    __import__(name)
for name in inventory["modules"]:
    module = sys.modules.get(name)
    if module is not None and getattr(module, "__file__", None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root), (name, module.__file__)
print("admitted-tooling-semantic-validation-ok")
"""
            result = subprocess.run(
                [str(python), "-I", "-c", script, str(toolkit), str(record)],
                executable=seed.dependencies._native_retained_path(python),
                cwd=tmp_path, capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert result.stdout.strip() == "admitted-tooling-semantic-validation-ok"


def test_self_contained_toolkit_builds_and_constructs_without_checkout_imports(
    adopter, configured_source, target, feeds, tmp_path,
):
    source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    build_only_feed(state)
    toolkit = _toolkit(tmp_path)
    env = environment(adopter, state, tmp_path)
    arguments = options(adopter, target, env)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "policy": plan(adopter).policy(),
        "options": {
            **arguments,
            "acquisition_limits": vars(arguments["acquisition_limits"]),
            "wheel_limits": vars(arguments["wheel_limits"]),
        },
    }, default=str), encoding="utf-8")
    script = """
import hashlib, importlib.abc, json, os, pathlib, subprocess, sys
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
from installer_wheel_acquisition import AcquisitionLimits
from installer_wheel_inputs import WheelLimits
from runtime_profile_policy import InstallationProfilePlan
from runtime_source_build import engine, retain_source_runtime_inputs
import runtime_feed_configuration as configuration
# Match the existing credential-free source fixture; keyed identities have
# separate native controls and must not create a machine credential key here.
configuration._opaque_index_identity = lambda value: hashlib.sha256(value.encode()).hexdigest()
request = json.loads(pathlib.Path(sys.argv[2]).read_text())
options = request["options"]
options["python"] = pathlib.Path(options["python"])
options["acquisition_limits"] = AcquisitionLimits(**options["acquisition_limits"])
options["wheel_limits"] = WheelLimits(**options["wheel_limits"])
plan = InstallationProfilePlan(json.dumps(request["policy"]).encode(), None, "release-index-unavailable")
with retain_source_runtime_inputs(plan, **options) as source:
    source.verify()
    assert source.local_packages == ("agent-example", "agent-procutil")
    assert {wheel["name"] for wheel in source.profile.record()["wheels"]} == {
        "agent-example", "agent-procutil",
    }
    runtime = pathlib.Path(plan.policy()["owner"]["runtimeRoot"])
    construction = dict(
        plugin=source.payload, uv=pathlib.Path(options["uv"]), base_python=options["python"],
        health_module="agent_example", wheel_inputs=source.wheels,
        local_packages=source.local_packages,
        expected_source_identity=engine.runtime_source.manifest_fields(source.identity),
    )
    with engine.numbered_candidate(runtime, source.runtime_version, **construction) as candidate:
        assert not candidate.reused and candidate.slot != source.build_slot
        python = engine.runtime.slot_python(runtime, candidate.slot.name)
        result = subprocess.run(
            [str(python), "-I", "-c",
             "import agent_example, importlib.metadata as m; "
             "assert agent_example.VALUE == 'from-authenticated-source'; "
             "assert {d.metadata['Name'] for d in m.distributions()} == {'agent-example', 'agent-procutil'}"],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert result.returncode == 0, result.stderr
        receipt = (candidate.slot / ".runtime-input-origins.json").read_bytes()
    with engine.numbered_candidate(runtime, source.runtime_version, **construction) as reused:
        assert reused.reused
        assert (reused.slot / ".runtime-input-origins.json").read_bytes() == receipt
    source.verify()
    assert engine.runtime.current_version(runtime) is None
for name in json.loads((root / "toolkit.json").read_text())["modules"]:
    module = sys.modules.get(name)
    if module is not None and getattr(module, "__file__", None):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root), (name, module.__file__)
assert not blocked.intersection(sys.modules)
print("payload-source-build-and-constructor-ok")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(toolkit), str(request)],
        cwd=tmp_path, env={
            **os.environ, **env, "WORKTREE_MANAGER_ROOT": str(configured_source[0]),
        },
        capture_output=True, text=True, timeout=150,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "payload-source-build-and-constructor-ok"
    assert any(path.startswith("/files/") for path, _ in state["requests"])
