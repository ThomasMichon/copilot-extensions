"""A consolidated core is a real, source-bound promotion artifact."""
from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from pathlib import Path

import build_python_artifacts as builder
import core_python_artifacts as core
import promotion_python_artifacts as artifacts
import pytest
from test_promotion_python_artifacts import _wheel_bytes
from test_standalone_python_artifacts import LOCK, _source


def _fixture(root):
    source = _source(root, "pr-service")
    marketplace = root / ".github" / "plugin" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(json.dumps({"metadata": {"version": "2.0-dev7"}}))
    for relative, name, dependencies in (
        ("plugins/agent-sample", "agent-sample", ["shared-core>=1"]),
        ("libs/shared-core", "shared-core", []),
    ):
        project = root / relative
        (project / "src" / name.replace("-", "_")).mkdir(parents=True)
        (project / "src" / name.replace("-", "_") / "__init__.py").write_text("VALUE = 1\n")
        (project / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
            f'[project]\nname = "{name}"\nversion = "1.0"\n'
            f"dependencies = {json.dumps(dependencies)}\n"
            '[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        )
    return source


@pytest.mark.parametrize("mapping", ["direct", "conditional", "bytes", "project", "dependency", "filename"])
def test_core_preserves_only_the_existing_exact_manager_wheel_exception(tmp_path, mapping):
    _fixture(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    manager = core._read(repository / "worktree-manager" / "pyproject.toml")
    requirement = next(value for value in manager["project"]["dependencies"] if value.startswith("dbus-next"))
    relative = manager["tool"]["uv"]["sources"]["dbus-next"]["path"]
    directory = tmp_path / "plugins" / "agent-sample"
    wheel = directory / relative
    wheel.parent.mkdir()
    wheel.write_bytes((repository / "worktree-manager" / relative).read_bytes())
    if mapping == "bytes":
        wheel.write_bytes(b"changed")
    if mapping == "filename":
        renamed = wheel.with_name("another.whl")
        wheel.rename(renamed)
        relative = "vendor/another.whl"
    name = "other-project" if mapping == "project" else manager["project"]["name"]
    dependency = "other-dependency" if mapping == "dependency" else "dbus-next"
    source = "{path=" + json.dumps(relative) + "}"
    if mapping == "conditional":
        source = "[" + source[:-1] + ',marker="sys_platform == \'linux\'"}]'
    (directory / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=68"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname=' + json.dumps(name) + '\nversion="1.0"\ndependencies='
        + json.dumps([requirement.replace("dbus-next", dependency)])
        + '\n[tool.uv.sources]\n' + dependency + "=" + source + "\n"
    )
    if mapping not in ("direct", "conditional"):
        with pytest.raises(artifacts.PromotionArtifactError, match="canonical"):
            core.source_closure(tmp_path, builder)
        return
    closure = core.source_closure(tmp_path, builder)
    assert dependency not in {entry["name"] for entry in closure.selection["projects"]}
    assert wheel not in closure.resources
    assert any(edge["requirement"] == str(core.Requirement(requirement)) for edge in closure.selection["edges"])


def _build(root, monkeypatch):
    _fixture(root)
    monkeypatch.setattr(builder, "REPO", root)
    monkeypatch.setattr(builder, "PLUGINS_DIR", root / "plugins")
    monkeypatch.setattr(builder, "LIBS_DIR", root / "libs")
    calls = []

    def build(source, out_dir, *, toolchain, reserved_names):
        assert toolchain is LOCK
        package = next((source / "src").iterdir()).name
        filename = f"{package}-1.0-py3-none-any.whl"
        assert filename not in reserved_names
        reserved_names.add(filename)
        calls.append(source)
        wheel = out_dir / filename
        requires = ["shared-core>=1"] if package == "agent_sample" else []
        wheel.write_bytes(_wheel_bytes(
            package, "1.0", payload=(source / "src" / package / "__init__.py").read_bytes(),
            members={f"{package}-1.0.dist-info/METADATA": (
                f"Metadata-Version: 2.4\nName: {package}\nVersion: 1.0\n"
                + "".join(f"Requires-Dist: {value}\n" for value in requires)
            ).encode()},
        ))
        return wheel

    monkeypatch.setattr(builder, "build_wheel", build)
    wheels = root / "wheels"
    manifest = core.build_core_artifacts(out_dir=wheels, toolchain=LOCK, builder=builder)
    return wheels, manifest, calls


def test_core_builds_each_canonical_project_once_and_roundtrips(tmp_path, monkeypatch):
    wheels, manifest, calls = _build(tmp_path, monkeypatch)
    assert len(calls) == len(set(calls)) == 3
    assert manifest["composition"] == "core" and manifest["schema_version"] == 5
    assert "plugin" not in manifest and "component" not in manifest
    assert manifest["version"] == "2.0-dev7"
    assert {wheel["role"] for wheel in manifest["wheels"]} == {"plugin", "standalone", "vendored-lib"}
    destination = tmp_path / "bundles"
    destination.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, destination)
    assert entry["filename"].startswith("composition-core-2.0-dev7-")
    assert artifacts.validate_bundle(
        destination / entry["filename"], entry, candidate_root=tmp_path,
    ) == manifest
    assert artifacts.project_key(entry) == "composition:core"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "role", "selection", "resources", "version"])
def test_core_source_binding_refuses_incomplete_or_changed_composition(tmp_path, monkeypatch, mutation):
    _wheels, manifest, _calls = _build(tmp_path, monkeypatch)
    changed = copy.deepcopy(manifest)
    if mutation == "missing":
        changed["wheels"].pop()
    elif mutation == "duplicate":
        changed["wheels"].append(dict(changed["wheels"][0]))
    elif mutation == "role":
        changed["wheels"][0]["role"] = "vendored-lib"
    elif mutation == "selection":
        changed["selection"]["roots"].pop()
    elif mutation == "resources":
        changed["build_resources"].pop()
    else:
        changed["version"] = "2.0-dev8"
    with pytest.raises(artifacts.PromotionArtifactError):
        artifacts.validate_source_binding(changed, tmp_path)


def test_core_uses_existing_marketplace_version_and_hashes_selection(tmp_path, monkeypatch):
    _wheels, manifest, _calls = _build(tmp_path, monkeypatch)
    marketplace = tmp_path / ".github" / "plugin" / "marketplace.json"
    data = json.loads(marketplace.read_text())
    data["metadata"]["version"] = "2.0-dev8"
    marketplace.write_text(json.dumps(data))
    with pytest.raises(artifacts.PromotionArtifactError):
        artifacts.validate_source_binding(manifest, tmp_path)
    selection = copy.deepcopy(manifest["selection"])
    selection["roots"].pop()
    assert core.payload_hash(builder, core.source_closure(tmp_path, builder), selection) != manifest["payload_hash"]


def test_core_selection_does_not_depend_on_build_host(tmp_path):
    _fixture(tmp_path)
    plugin = tmp_path / "plugins" / "agent-sample" / "pyproject.toml"
    plugin.write_text(plugin.read_text().replace(
        '"shared-core>=1"', '"shared-core>=1; sys_platform == \'linux\'"',
    ))
    closure = core.source_closure(tmp_path, builder)
    assert any(item["name"] == "shared-core" for item in closure.selection["projects"])
    assert any("sys_platform" in edge["requirement"] for edge in closure.selection["edges"])


def test_core_retains_root_extras_and_optional_environment_boundaries(tmp_path):
    _fixture(tmp_path)
    dispatch = tmp_path / "plugins" / "agent-dispatch"
    dispatch.mkdir()
    (dispatch / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        '[project]\nname = "agent-dispatch"\nversion = "1.0"\n'
        '[project.optional-dependencies]\nmcp = ["shared-core>=1"]\n',
    )
    index = tmp_path / "services" / "index"
    index.mkdir()
    (index / "pyproject.toml").write_text(
        '[project]\nname = "index-service"\nversion = "1.0"\n'
        '[project.optional-dependencies]\nnative = []\n',
    )
    closure = core.source_closure(tmp_path, builder)
    extra = next(root for root in closure.selection["roots"] if root["extra"] == "mcp")
    assert extra["condition"] == 'sys_platform != "win32" or platform_machine != "ARM64"'
    assert "index-service" not in {item["name"] for item in closure.selection["projects"]}
    (dispatch / "pyproject.toml").write_text(
        (dispatch / "pyproject.toml").read_text().replace('mcp = ["shared-core>=1"]', 'mcp = ["index-service>=1"]'),
    )
    with pytest.raises(artifacts.PromotionArtifactError, match="optional environment"):
        core.source_closure(tmp_path, builder)


def test_core_rejects_duplicate_canonical_distribution(tmp_path):
    _fixture(tmp_path)
    duplicate = tmp_path / "libs" / "duplicate"
    duplicate.mkdir()
    (duplicate / "pyproject.toml").write_text('[project]\nname = "shared_core"\nversion = "1.0"\n')
    with pytest.raises((builder.ArtifactBuildError, artifacts.PromotionArtifactError), match="ambiguous"):
        core.source_closure(tmp_path, builder)


def test_core_release_profile_cannot_omit_any_first_party_wheel(tmp_path, monkeypatch):
    import runtime_profile_release as release
    from runtime_profile import ProfileError
    from runtime_profile_release import validate_release_bindings

    _wheels, manifest, _calls = _build(tmp_path, monkeypatch)
    digest = "sha256:" + "a" * 64
    record = {
        "artifact": {"artifact_id": manifest["artifact_id"], "manifest_sha256": digest},
        "release": {"identity": f"composition-core:{manifest['version']}", "sha256": manifest["payload_hash"]},
        "component": "composition-core", "wheels": manifest["wheels"][:-1],
    }

    class Profile:
        def record(self):
            return record

    monkeypatch.setattr(release, "release_profiles", lambda index: (Profile(),))
    with pytest.raises(ProfileError, match="omits"):
        validate_release_bindings({}, {manifest["artifact_id"]: (manifest, digest)})


def test_promotion_builds_and_requires_the_core_with_the_shared_lock(tmp_path, monkeypatch):
    import build_toolchain_lock
    import promote_release

    _wheels, _manifest, _calls = _build(tmp_path, monkeypatch)
    selection = tmp_path / core.SELECTION_PATH
    selection.parent.mkdir()
    selection.write_text('{"schema":"copilot-extensions.core-composition","version":1}')
    monkeypatch.setattr(promote_release, "_load_module", lambda *args: builder)
    locks = []
    def resolve(*args, **kwargs):
        locks.append(kwargs)
        return LOCK
    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", resolve)
    destination = tmp_path / "promotion"
    artifacts.build_candidate_bundles(
        tmp_path, destination, source_policy="governed", min_age_days=None,
    )
    entries = json.loads((destination / artifacts._BUNDLES_NAME).read_text())
    assert len(locks) == 1
    assert {artifacts.project_key(entry) for entry in entries} == {
        "agent-sample", "standalone:pr-service", "composition:core",
    }
    for entry in entries:
        assert artifacts.validate_bundle(
            destination / entry["filename"], entry, candidate_root=tmp_path,
        )["build_toolchain"] == LOCK.record
    assert artifacts.candidate_projects(tmp_path)["composition:core"] == "2.0-dev7"


@pytest.mark.parametrize("value", ['[]', '{"version":true}', '{"schema":"copilot-extensions.core-composition","version":true}', 'invalid'])
def test_invalid_core_activation_is_not_silently_ignored(tmp_path, value):
    path = tmp_path / core.SELECTION_PATH
    path.parent.mkdir()
    path.write_text(value)
    with pytest.raises(artifacts.PromotionArtifactError, match="selection"):
        artifacts.candidate_projects(tmp_path)


def test_core_cli_reuses_existing_policy_and_toolchain_arguments(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    import artifact_builder_cli

    calls = []
    lock = SimpleNamespace(
        venv_python=Path(sys.prefix) / "bin" / "python",
        marker_environment={"python_version": "3.11"},
    )
    monkeypatch.setattr(artifact_builder_cli, "resolve_toolchain_lock", lambda *args, **kwargs: lock)
    monkeypatch.setattr(sys, "argv", ["builder", "core", "--core", "--out-dir", str(tmp_path)])
    def build(**kwargs):
        calls.append(kwargs)
        return {"composition": "core"}
    assert artifact_builder_cli.main(None, "", core_builder=build) == 0
    assert calls == [{"out_dir": tmp_path, "python": None, "toolchain": lock}]


def test_legacy_builder_help_needs_no_ambient_packaging():
    import os
    import subprocess
    import sys

    script = Path(builder.__file__).resolve()
    result = subprocess.run(
        [sys.executable, "-I", "-c", """
import builtins, pathlib, runpy, sys
script = pathlib.Path(sys.argv[1])
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'packaging' or name.startswith('packaging.'):
        raise ModuleNotFoundError('ambient packaging is unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
sys.path.insert(0, str(script.parent))
sys.argv = [str(script), '--help']
runpy.run_path(str(script), run_name='__main__')
""", str(script)],
        capture_output=True, text=True, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert "--core" in result.stdout and "--standalone" in result.stdout


@pytest.mark.parametrize("mutation", ["shape", "missing-source", "nonobject-project", "aliased-identity"])
def test_core_malformed_manifests_are_structured_refusals(tmp_path, monkeypatch, mutation):
    _wheels, manifest, _calls = _build(tmp_path, monkeypatch)
    if mutation == "shape":
        manifest["selection"] = []
    elif mutation == "missing-source":
        del manifest["selection"]["projects"][0]["source"]
    elif mutation == "nonobject-project":
        manifest["selection"]["projects"][0] = []
    else:
        manifest["component"] = "pr-service"
    with pytest.raises(artifacts.PromotionArtifactError):
        artifacts.validate_manifest(manifest)


def test_real_core_bundle_binds_a_complete_profile_through_existing_release_adapter(tmp_path, monkeypatch):
    from runtime_profile_cases import _record, _wheel
    from runtime_profile_release import APPROVAL_SOURCE, SCHEMA

    wheels, manifest, _calls = _build(tmp_path, monkeypatch)
    destination = tmp_path / "release"
    destination.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, destination)
    with zipfile.ZipFile(destination / entry["filename"]) as bundle:
        raw = bundle.read("manifest.json")
    record = _record()
    record.update(
        release={"identity": f"composition-core:{manifest['version']}", "sha256": manifest["payload_hash"]},
        component="composition-core", mode="artifact", approval_source=APPROVAL_SOURCE,
        roots=[f"{item['name']}==1.0" for item in manifest["selection"]["roots"]],
        artifact={
            "artifact_id": manifest["artifact_id"],
            "manifest_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        },
        wheels=[
            _wheel(
                wheel["name"], dependencies=["shared-core>=1"] if wheel["name"] == "agent_sample" else [],
            ) | {"filename": wheel["filename"], "sha256": wheel["sha256"]}
            for wheel in manifest["wheels"]
        ],
    )
    index = {
        "schema": artifacts.SCHEMA, "schema_version": 2,
        "dev_head": "a" * 40, "content_tree": "b" * 40, "artifacts": [entry],
        "runtime_profiles": {"schema": SCHEMA, "version": 1, "profiles": [record]},
    }
    (destination / artifacts.INDEX_NAME).write_text(json.dumps(index))
    assert artifacts.validate_artifacts(
        destination, dev_head="a" * 40, content_tree="b" * 40, main_before="c" * 40,
        projects={"composition:core": manifest["version"]}, candidate_root=tmp_path,
    ) == index
    record["artifact"]["manifest_sha256"] = "sha256:" + "0" * 64
    (destination / artifacts.INDEX_NAME).write_text(json.dumps(index))
    with pytest.raises(artifacts.PromotionArtifactError, match="exact first-party manifest"):
        artifacts.validate_artifacts(
            destination, dev_head="a" * 40, content_tree="b" * 40, main_before="c" * 40,
            candidate_root=tmp_path,
        )
