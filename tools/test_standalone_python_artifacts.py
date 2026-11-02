"""Standalone release identities reuse the locked builder and source verifier."""
from __future__ import annotations

import io
import json
import hashlib
from pathlib import Path
import subprocess
import shutil
import sys
import zipfile
import os
import venv

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
        "index-service": "agent_index_service", "index-engine": "agent_index_engine",
    }[component]
    (source / "src" / package).mkdir(parents=True)
    (source / "src" / package / "__init__.py").write_bytes(b"VALUE = 'first-party fixture'\n")
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        f'[project]\nname = "{package}"\nversion = "1.0"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n', encoding="utf-8",
    )
    resources = standalone.standalone_build_resources(
        standalone.COMPONENTS[component], source_root=root,
    )
    for resource in resources:
        resource.mkdir(parents=True, exist_ok=True)
    helper = root / "libs" / "versioned-runtime" / "standalone_build.py"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_bytes((ROOT / "libs" / "versioned-runtime" / "standalone_build.py").read_bytes())
    if component in {"fleet-manager", "chat-service", "index-service", "worktree-manager"}:
        (source / "setup.py").write_bytes(
            (ROOT / standalone.COMPONENTS[component] / "setup.py").read_bytes()
        )
        for path in standalone.injected_resources(component, root).values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# controlled canonical runtime resource\n")
    if component == "index-service":
        (source / "_build_runtime_resource.py").write_bytes(
            (ROOT / standalone.COMPONENTS[component] / "_build_runtime_resource.py").read_bytes()
        )
    return source


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("modified", [False, True])
def test_trusted_manager_setup_accepts_git_line_endings_but_not_changed_body(
    tmp_path, newline, modified,
):
    from build_toolchain_lock import ArtifactBuildError

    source = _source(tmp_path, "worktree-manager")
    setup = source / "setup.py"
    content = setup.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
    setup.write_bytes(content + (b"\nprint('untrusted build action')\n" if modified else b""))
    if modified:
        with pytest.raises(ArtifactBuildError, match="unsupported executable"):
            standalone.validate_build(source, component="worktree-manager", root=tmp_path)
    else:
        assert standalone.validate_build(
            source, component="worktree-manager", root=tmp_path,
        )["project"]["version"] == "1.0"


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


@pytest.mark.parametrize("component", ["fleet-manager", "chat-service", "index-service", "worktree-manager"])
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


def test_index_build_selector_substitution_is_refused(tmp_path):
    source = _source(tmp_path, "index-service")
    (source / "_build_runtime_resource.py").write_bytes(b"raise RuntimeError('not canonical')\n")
    with pytest.raises(builder.ArtifactBuildError, match="selector"):
        standalone.validate_build(source, component="index-service", root=tmp_path)


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


@pytest.mark.parametrize("relative,package", [
    ("worktree-manager", "worktree_manager"),
    ("plugins/agent-worktrees", "agent_worktrees"),
])
def test_operational_assets_survive_wheel_and_source_distribution(tmp_path, relative, package):
    project = tmp_path / "project"
    shutil.copytree(
        ROOT / relative, project,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.egg-info", ".venv", ".test-runs", "build", "dist",
        ),
    )
    expected = {
        name: path.read_bytes()
        for name, path in __import__("plugin_runtime_resources").application_resources(
            project, package,
        ).items()
    }
    assert expected
    before = {path: path.read_bytes() for path in project.rglob("*.py")}
    archives = tmp_path / "archives"
    archives.mkdir()
    program = (
        "import setuptools.build_meta as backend; out=__import__('sys').argv[1]; "
        "backend.build_wheel(out); backend.build_sdist(out)"
    )
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-c", program, str(archives)],
        cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=90,
        **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next(archives.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert {name: archive.read(name) for name in expected} == expected
        assert not any("/payload/" in name for name in archive.namelist())
    assert all(path.read_bytes() == value for path, value in before.items())
    unpacked = tmp_path / "sdist"
    source_archives = [path for path in archives.iterdir() if path != wheel]
    assert len(source_archives) == 1
    shutil.unpack_archive(source_archives[0], unpacked)
    source = next(unpacked.iterdir())
    assert not (source / "scripts").exists()
    rebuilt = tmp_path / "rebuilt"
    rebuilt.mkdir()
    result = subprocess.run(
        [sys.executable, "-c",
         "import setuptools.build_meta as backend; backend.build_wheel(__import__('sys').argv[1])",
         str(rebuilt)],
        cwd=source, capture_output=True, text=True, encoding="utf-8", timeout=90,
        **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next(rebuilt.glob("*.whl"))) as archive:
        assert {name: archive.read(name) for name in expected} == expected
    slot = tmp_path / "runtime" / "versions" / "1.0"
    environment = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv, "native uv is required for the installed-wheel consumer control"
    result = subprocess.run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", "--no-index", str(wheel)],
        capture_output=True, text=True, encoding="utf-8", timeout=30, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.rmtree(project)
    if package == "worktree_manager":
        probe = (
            "from pathlib import Path; from worktree_manager import self_install as owner; "
            "from types import SimpleNamespace; import sys; "
            "from worktree_manager import relocated_launch; "
            "relocated_launch._core=lambda: SimpleNamespace(_is_windows=lambda: sys.platform=='win32'); "
            "assert not (Path(__import__('sys').prefix).parent/'payload').exists(); "
            "assert '_runtime_assets' in str(owner._runtime_installer().__file__); "
            "assert '_runtime_assets' in str(owner._provider_manifest_template_path()); "
            "assert owner._expected_control_plane_provider_manifest()['provider']=='worktree-manager'; "
            "launcher=relocated_launch._relocated_launch_script(); assert launcher.is_file(); "
            "assert '_runtime_assets' in str(launcher)"
        )
    else:
        bootstrap = slot / ".runtime-bootstrap"
        bootstrap.mkdir()
        (bootstrap / "build-info.json").write_text(json.dumps({
            "version": "1.0", "commit": "wheel-consumer-control", "runtime_generation": "a" * 32,
        }), encoding="utf-8")
        probe = (
            "from pathlib import Path; import agent_worktrees; "
            "from agent_worktrees._build_info import BUILD_INFO; "
            "assert BUILD_INFO['commit']=='wheel-consumer-control'; "
            "assets=Path(agent_worktrees.__file__).parent/'_runtime_assets'/'scripts'; "
            "assert (assets/'runtime_install.py').is_file(); "
            "assert (assets/'installation-context'/'installation_context.py').is_file(); "
            "assert (assets/'installation-context'/'json-query.awk').is_file()"
        )
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", probe], cwd=tmp_path,
        capture_output=True, text=True, encoding="utf-8", timeout=30, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
