"""Standalone release identities reuse the locked builder and source verifier."""
from __future__ import annotations

import io
import json
import hashlib
from pathlib import Path
import subprocess

import pytest

import build_python_artifacts as builder
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
import standalone_python_artifacts as standalone
from standalone_consumers import STANDALONE_CONSUMERS
from test_promotion_python_artifacts import _wheel_bytes

ROOT = Path(__file__).resolve().parent.parent
LOCK = builder.ToolchainLock(Path("."), {
    "setuptools": "80.9.0", "wheel": "0.45.1", "packaging": "25.0",
}, provenance={
    "source_policy": "governed", "python": "/python",
    "validated_index_identity": "sha256:" + "f" * 64,
})


@pytest.mark.parametrize("component", [
    "chat-service", "dispatch-service", "ssh-service", "worktree-manager",
])
def test_existing_core_standalone_roots_have_explicit_artifact_identities(component):
    assert component in standalone.COMPONENTS
    assert standalone.COMPONENTS[component] in STANDALONE_CONSUMERS


def _source(root, component):
    source = root / standalone.COMPONENTS[component]
    source.mkdir(parents=True)
    package = {
        "fleet-manager": "fleet_manager", "pr-service": "agent_pull_requests_service",
        "chat-service": "agent_chat_service", "dispatch-service": "agent_dispatch_service",
        "ssh-service": "agent_ssh_service", "worktree-manager": "worktree_manager",
    }[component]
    (source / "src" / package).mkdir(parents=True)
    (source / "src" / package / "__init__.py").write_bytes(b"VALUE = 'first-party fixture'\n")
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        f'[project]\nname = "{package}"\nversion = "1.0"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n', encoding="utf-8",
    )
    for resource in standalone.standalone_build_resources(
        standalone.COMPONENTS[component], source_root=root,
    ):
        resource.mkdir(parents=True, exist_ok=True)
    helper = root / "libs" / "versioned-runtime" / "standalone_build.py"
    helper.write_bytes((ROOT / "libs" / "versioned-runtime" / "standalone_build.py").read_bytes())
    if component in {"fleet-manager", "chat-service"}:
        (source / "setup.py").write_bytes(
            (ROOT / standalone.COMPONENTS[component] / "setup.py").read_bytes()
        )
        for path in standalone.injected_resources(component, root).values():
            path.write_bytes(b"# controlled canonical runtime resource\n")
    return source


def _build(tmp_path, monkeypatch, component):
    source = _source(tmp_path, component)
    monkeypatch.setattr(builder, "REPO", tmp_path)
    monkeypatch.setattr(builder, "LIBS_DIR", tmp_path / "libs")

    def build(source_dir, out_dir, *, toolchain, reserved_names):
        assert toolchain is LOCK
        package = next((source_dir / "src").iterdir()).name
        members = {
            name: path.read_bytes()
            for name, path in standalone.injected_resources(
                next(name for name, relative in standalone.COMPONENTS.items()
                     if source_dir == tmp_path / relative), tmp_path,
            ).items()
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        wheel = out_dir / f"{package}-1.0-py3-none-any.whl"
        wheel.write_bytes(_wheel_bytes(package, "1.0", members=members))
        assert wheel.name not in reserved_names
        reserved_names.add(wheel.name)
        return wheel

    monkeypatch.setattr(builder, "build_wheel", build)
    directory = tmp_path / "wheels"
    manifest = standalone.build_standalone_artifacts(
        component, out_dir=directory, toolchain=LOCK, builder=builder,
    )
    return source, directory, manifest


@pytest.mark.parametrize("component", list(standalone.COMPONENTS))
def test_standalone_build_bundle_candidate_roundtrip(tmp_path, monkeypatch, component):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, component)
    assert manifest["schema_version"] == 4 and "plugin" not in manifest
    assert manifest["component"] == component
    assert manifest["wheels"][0]["role"] == "standalone"
    directory = tmp_path / "bundles"
    directory.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, directory)
    assert entry["filename"].startswith(f"standalone-{component}-1.0-")
    assert artifacts.validate_bundle(
        directory / entry["filename"], entry, candidate_root=tmp_path,
    ) == manifest
    index = {
        "schema": artifacts.SCHEMA, "schema_version": 2,
        "dev_head": "a" * 40, "content_tree": "b" * 40, "artifacts": [entry],
    }
    (directory / artifacts.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
    assert artifacts.validate_artifacts(
        directory, dev_head="a" * 40, content_tree="b" * 40, main_before="c" * 40,
        candidate_root=tmp_path, projects=artifacts.candidate_projects(tmp_path),
    ) == index
    assert publisher.artifact_label(entry) == f"standalone-{component}"


@pytest.mark.parametrize("mutation", ["unknown", "plugin-alias", "resource-omission", "duplicate-source"])
def test_standalone_identity_and_resource_refusals(tmp_path, monkeypatch, mutation):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, "fleet-manager")
    if mutation == "unknown":
        manifest["component"] = "../fleet-manager"
    elif mutation == "plugin-alias":
        manifest["plugin"] = "fleet-manager"
    elif mutation == "resource-omission":
        manifest["build_resources"].pop()
    else:
        manifest["wheels"].append(dict(manifest["wheels"][0]))
    with pytest.raises((artifacts.PromotionArtifactError, ValueError)):
        artifacts.validate_manifest(manifest)
        artifacts.validate_source_binding(manifest, tmp_path)


@pytest.mark.parametrize("mutation", ["source", "resource", "generator", "digest"])
def test_standalone_changed_bytes_and_toolchain_refused(tmp_path, monkeypatch, mutation):
    source, wheels, manifest = _build(tmp_path, monkeypatch, "fleet-manager")
    if mutation == "source":
        (source / "src" / "fleet_manager" / "__init__.py").write_bytes(b"changed")
    elif mutation == "resource":
        next(iter(standalone.injected_resources("fleet-manager", tmp_path).values())).write_bytes(b"changed")
    elif mutation == "generator":
        manifest["wheels"][0]["generator"] = "unlocked"
    else:
        manifest["wheels"][0]["sha256"] = "sha256:" + "0" * 64
    directory = tmp_path / "bundles"
    directory.mkdir()
    with pytest.raises(artifacts.PromotionArtifactError):
        entry = artifacts.bundle_wheels(manifest, wheels, directory)
        artifacts.validate_bundle(directory / entry["filename"], entry, candidate_root=tmp_path)


@pytest.mark.parametrize("component", ["fleet-manager", "chat-service"])
@pytest.mark.parametrize("mutation", ["setup", "helper", "alternate", "included"])
def test_only_trusted_standalone_build_hook_is_admitted(tmp_path, mutation, component):
    source = _source(tmp_path, component)
    target = {
        "setup": source / "setup.py",
        "helper": tmp_path / "libs" / "versioned-runtime" / "standalone_build.py",
        "alternate": source / "setup.cfg",
        "included": next((source / "src").iterdir()) / "_versioned_runtime.py",
    }[mutation]
    target.write_bytes(b"raise RuntimeError('candidate build code must not run')\n")
    with pytest.raises((builder.ArtifactBuildError, artifacts.PromotionArtifactError)):
        standalone.validate_build(source, component=component, root=tmp_path)


def test_standalone_resource_substitution_refused_even_with_fresh_manifest(tmp_path, monkeypatch):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, "fleet-manager")
    wheel = manifest["wheels"][0]
    archive = wheels / wheel["filename"]
    replacement = _wheel_bytes("fleet_manager", "1.0")
    wheel["sha256"] = "sha256:" + hashlib.sha256(replacement).hexdigest()
    with pytest.raises(artifacts.PromotionArtifactError, match="Python member set"):
        artifacts.validate_wheel(
            io.BytesIO(replacement), wheel, source_dir=tmp_path / "fleet-manager",
            component="fleet-manager", candidate_root=tmp_path,
        )
    assert archive.is_file()


def test_standalone_signature_failure_remains_blocking(tmp_path, monkeypatch):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, "pr-service")
    directory = tmp_path / "bundles"
    directory.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, directory)
    (directory / artifacts.INDEX_NAME).write_text(json.dumps({
        "schema": artifacts.SCHEMA, "schema_version": 2,
        "dev_head": "a" * 40, "content_tree": "b" * 40, "artifacts": [entry],
        "admission": {
            "signer_digest": "c" * 40, "source_digest": "c" * 40, "source_ref": "refs/heads/main",
        },
    }), encoding="utf-8")
    calls = []
    def gh(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if str(argv[2]).endswith(".zip") else 0, "", "bad signature")
    monkeypatch.setattr(publisher, "_gh", gh)
    with pytest.raises(artifacts.PromotionArtifactError, match="bad signature"):
        publisher.verify_attestations(
            directory, repository="example/extensions", signer_digest="c" * 40,
            source_digest="c" * 40, source_ref="refs/heads/main",
        )
    assert any(str(argv[2]).endswith(".zip") for argv in calls)


def test_candidate_builder_uses_one_lock_for_registered_standalones(tmp_path, monkeypatch):
    import build_toolchain_lock
    import promote_release

    _source_dir, _wheels, _manifest = _build(tmp_path, monkeypatch, "fleet-manager")
    _source(tmp_path, "pr-service")
    monkeypatch.setattr(builder, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(promote_release, "_load_module", lambda *a: builder)
    locks = []
    def resolve(*args, **kwargs):
        locks.append(kwargs)
        return LOCK
    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", resolve)
    directory = tmp_path / "candidate"
    artifacts.build_candidate_bundles(
        tmp_path, directory, source_policy="governed", min_age_days=None,
    )
    entries = json.loads((directory / artifacts._BUNDLES_NAME).read_text(encoding="utf-8"))
    assert len(locks) == 1
    assert {standalone.project_key(entry) for entry in entries} == {
        "standalone:fleet-manager", "standalone:pr-service",
    }
    for entry in entries:
        manifest = artifacts.validate_bundle(
            directory / entry["filename"], entry, candidate_root=tmp_path,
        )
        assert manifest["build_toolchain"] == LOCK.record


def test_candidate_omission_and_same_name_plugin_substitution_refuse(tmp_path, monkeypatch):
    _source_dir, wheels, manifest = _build(tmp_path, monkeypatch, "pr-service")
    directory = tmp_path / "bundles"
    directory.mkdir()
    entry = artifacts.bundle_wheels(manifest, wheels, directory)
    index = {
        "schema": artifacts.SCHEMA, "schema_version": 2,
        "dev_head": "a" * 40, "content_tree": "b" * 40, "artifacts": [entry],
    }
    (directory / artifacts.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
    for projects in ({}, {"pr-service": "1.0"}):
        with pytest.raises(artifacts.PromotionArtifactError, match="project set"):
            artifacts.validate_artifacts(
                directory, dev_head="a" * 40, content_tree="b" * 40, main_before="c" * 40,
                candidate_root=tmp_path, projects=projects,
            )


def test_standalone_cli_selects_explicit_builder_without_changing_plugin_default(tmp_path, monkeypatch, capsys):
    import artifact_builder_cli

    calls = []
    def plugin(name, **kwargs):
        calls.append(("plugin", name))
        return {}
    def component(name, **kwargs):
        calls.append(("standalone", name))
        return {}
    for standalone_flag in ([], ["--standalone"]):
        monkeypatch.setattr(
            "sys.argv", ["build_python_artifacts.py", "pr-service", "--out-dir", str(tmp_path), *standalone_flag],
        )
        assert artifact_builder_cli.main(plugin, "", standalone_builder=component) == 0
    assert calls == [("plugin", "pr-service"), ("standalone", "pr-service")]
    assert capsys.readouterr().out.count("{}") == 2


@pytest.mark.parametrize("location", ["source", "resource"])
def test_standalone_linked_source_or_resource_is_not_followed(tmp_path, monkeypatch, location):
    source = _source(tmp_path, "fleet-manager")
    path = source if location == "source" else tmp_path / "libs" / "installer-engine"
    seen = []
    monkeypatch.setattr(builder.uer, "_find_symlinked_ancestor", lambda target, root: (
        seen.append(target) or (target if target == path else None)
    ))
    with pytest.raises(builder.ArtifactBuildError, match="missing, linked or outside"):
        standalone.source_closure("fleet-manager", tmp_path, builder)
    assert path in seen
