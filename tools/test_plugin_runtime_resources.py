"""Registered Containers admission at the trusted producer and wheel boundaries."""
from __future__ import annotations

import ast
import hashlib
import io
from email import message_from_bytes
from email.message import Message
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_python_artifacts as builder
import build_toolchain_lock
import plugin_runtime_resources as resources
import promotion_python_artifacts as artifacts


@pytest.fixture
def candidate(tmp_path):
    source = tmp_path / resources.CONSUMER
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    package = source / "src/agent_containers"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"")
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "agent-containers"\nversion = "1.0"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        encoding="utf-8",
    )
    (source / "setup.py").write_bytes(
        (resources.TRUSTED_ROOT / resources.REGISTERED_HOOK).read_bytes(),
    )
    (source / "MANIFEST.in").write_text("include _standalone_runtime_build.py\n", encoding="utf-8")
    helper = resources.TRUSTED_ROOT / "libs/versioned-runtime/standalone_build.py"
    (scripts / "standalone_build.py").write_bytes(helper.read_bytes())
    tree = ast.parse(helper.read_text(encoding="utf-8"))
    declaration = next(
        node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "RUNTIME_RESOURCES" for target in node.targets)
    )
    for name in ast.literal_eval(declaration):
        (scripts / name).write_bytes(b"RESOURCE = True\n")
    (scripts / "runtime_profile.py").write_bytes(b"PROFILE = True\n")
    return tmp_path, source


def test_registered_hook_retains_the_reviewed_bytes():
    raw = (resources.TRUSTED_ROOT / resources.REGISTERED_HOOK).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "26029b05beca57b1d95aff89f6cc2ee25a7e0496496b7cdc386105605c283796"


def test_admission_identifies_six_resources_without_executing_candidate(candidate, monkeypatch):
    root, source = candidate

    def refuse_execution(*args, **kwargs):
        pytest.fail("candidate code executed during static admission")

    monkeypatch.setattr(runpy, "run_path", refuse_execution)
    data, injected = resources.build_inputs(source, root=root)
    assert data["project"]["name"] == "agent-containers"
    assert set(injected) == {
        "agent_containers/_versioned_runtime.py",
        "agent_containers/versioned_runtime_leases.py",
        "agent_containers/versioned_runtime_construction.py",
        "agent_containers/versioned_runtime_publication.py",
        "agent_containers/versioned_runtime_source.py",
        "agent_containers/_runtime_profile.py",
    }
    assert all(path.parent == source / "scripts" for path in injected.values())


@pytest.mark.parametrize("case", [
    "changed-hook", "missing-hook", "changed-helper", "missing-helper",
    "missing-resource", "missing-profile", "changed-included-resource", "changed-included-helper",
    "missing-canonical-with-included-copy", "missing-helper-with-included-copy",
    "custom-backend", "alternate-config", "shadowed-tooling",
])
def test_invalid_inputs_refuse_before_any_toolchain_resolution(candidate, monkeypatch, case):
    root, source = candidate
    script = source / "scripts/versioned_runtime.py"
    if case == "changed-hook":
        (source / "setup.py").write_bytes(b"raise RuntimeError('unreviewed hook')\n")
    elif case == "missing-hook":
        (source / "setup.py").unlink()
    elif case == "changed-helper":
        (source / "scripts/standalone_build.py").write_bytes(b"raise RuntimeError('unreviewed helper')\n")
    elif case == "missing-helper":
        (source / "scripts/standalone_build.py").unlink()
    elif case == "missing-resource":
        script.unlink()
    elif case == "missing-profile":
        (source / "scripts/runtime_profile.py").unlink()
    elif case == "changed-included-resource":
        (source / "src/agent_containers/_versioned_runtime.py").write_bytes(b"CHANGED = True\n")
    elif case == "changed-included-helper":
        (source / "_standalone_runtime_build.py").write_bytes(b"CHANGED = True\n")
    elif case == "missing-canonical-with-included-copy":
        (source / "src/agent_containers/_versioned_runtime.py").write_bytes(script.read_bytes())
        script.unlink()
    elif case == "missing-helper-with-included-copy":
        helper = source / "scripts/standalone_build.py"
        (source / "_standalone_runtime_build.py").write_bytes(helper.read_bytes())
        helper.unlink()
    elif case == "custom-backend":
        project = source / "pyproject.toml"
        project.write_text(project.read_text().replace("setuptools.build_meta", "candidate_backend"))
    elif case == "alternate-config":
        (source / "setup.cfg").write_text("[metadata]\nname = other\n")
    elif case == "shadowed-tooling":
        (source / "setuptools.py").write_bytes(b"raise RuntimeError('shadowed builder')\n")
    calls = []
    monkeypatch.setattr(artifacts, "candidate_projects", lambda root: {"agent-containers": "1.0"})
    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", lambda *a, **kw: calls.append(a))
    with pytest.raises(artifacts.PromotionArtifactError):
        artifacts.build_candidate_bundles(root, root / "unsigned", source_policy="seasoned-public", min_age_days=7)
    assert not calls


def test_valid_registered_source_reaches_the_real_producer_toolchain_boundary(candidate, monkeypatch):
    root, _source = candidate
    monkeypatch.setattr(artifacts, "candidate_projects", lambda root: {"agent-containers": "1.0"})

    class ReachedToolchain(Exception):
        pass

    def stop_before_resolution(*args, **kwargs):
        raise ReachedToolchain

    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", stop_before_resolution)
    with pytest.raises(ReachedToolchain):
        artifacts.build_candidate_bundles(root, root / "unsigned", source_policy="seasoned-public", min_age_days=7)


@pytest.mark.parametrize("root_context", ["missing", "different"])
def test_registration_cannot_admit_a_hook_outside_its_exact_consumer(candidate, root_context):
    root, source = candidate
    with pytest.raises(artifacts.PromotionArtifactError, match="unsupported executable"):
        resources.build_inputs(source, root=None if root_context == "missing" else root / "other")


def test_linked_resource_is_refused(candidate, native_file_symlink):
    root, source = candidate
    script = source / "scripts/versioned_runtime.py"
    target = root / "outside.py"
    script.rename(target)
    native_file_symlink(script, target)
    with pytest.raises(artifacts.PromotionArtifactError, match="must not be linked"):
        resources.build_inputs(source, root=root)


@pytest.mark.parametrize("case", ["match", "missing", "changed"])
def test_wheel_source_mapping_binds_every_injected_resource(candidate, case):
    root, source = candidate
    _data, injected = resources.build_inputs(source, root=root)
    paths = {"agent_containers/__init__.py": source / "src/agent_containers/__init__.py", **injected}
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    name = "agent_containers/_runtime_profile.py"
    if case == "missing":
        del hashes[name]
    elif case == "changed":
        hashes[name] = hashlib.sha256(b"forged profile").hexdigest()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w"):
        pass
    with zipfile.ZipFile(stream) as bundle:
        arguments = {
            "source_dir": source, "prefix": "agent_containers-1.0.dist-info/",
            "metadata": Message(), "candidate_root": root,
        }
        if case == "match":
            artifacts.validate_wheel_sources(bundle, hashes, **arguments)
        else:
            with pytest.raises(artifacts.PromotionArtifactError, match="member set|payload differs"):
                artifacts.validate_wheel_sources(bundle, hashes, **arguments)


def test_direct_plugin_builder_uses_the_same_registered_admission(candidate, monkeypatch):
    root, source = candidate
    monkeypatch.setattr(builder, "REPO", root)
    monkeypatch.setattr(builder, "PLUGINS_DIR", root / "plugins")
    calls = []
    monkeypatch.setattr(builder, "build_source_artifacts", lambda **kw: calls.append(kw) or {"admitted": True})
    assert builder.build_plugin_artifacts("agent-containers", out_dir=root / "wheels") == {"admitted": True}
    assert len(calls) == 1
    (source / "setup.py").write_bytes(b"raise RuntimeError('unreviewed hook')\n")
    with pytest.raises(builder.ArtifactBuildError, match="invalid Containers runtime build inputs"):
        builder.build_plugin_artifacts("agent-containers", out_dir=root / "other-wheels")
    assert len(calls) == 1


def _build(project, output, operation="build_wheel", *, success=True):
    output.mkdir()
    environment = builder.sanitize_subprocess_env()
    builder.strip_package_source_env_vars(environment, strip_credentials=True)
    result = subprocess.run(
        [
            sys.executable, "-I", "-B", "-c",
            f"from setuptools.build_meta import {operation}; {operation}({str(output)!r})",
        ],
        cwd=project, capture_output=True, text=True, env=environment, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if not success:
        assert result.returncode != 0, result.stdout + result.stderr
        return result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    suffix = "*.whl" if operation == "build_wheel" else "*.tar.gz"
    artifact, = output.glob(suffix)
    return artifact


def _independent_sdist(project, root):
    archive = _build(project, root / "sdist", "build_sdist")
    destination = root / "independent"
    destination.mkdir()
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            target = destination / member.name
            assert target.resolve().is_relative_to(destination.resolve())
            assert member.isdir() or member.isfile()
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                assert stream is not None
                target.write_bytes(stream.read())
    independent, = destination.iterdir()
    assert not (independent / "scripts").exists()
    return independent


@pytest.mark.parametrize("layout", ["source", "independent-sdist"])
def test_real_distribution_preserves_six_members_and_record(candidate, layout):
    root, source = candidate
    _data, injected = resources.build_inputs(source, root=root)
    expected = {name: path.read_bytes() for name, path in injected.items()}
    project = source if layout == "source" else _independent_sdist(source, root)
    wheel = _build(project, root / "wheels")
    with zipfile.ZipFile(wheel) as bundle:
        record = next(name for name in bundle.namelist() if name.endswith(".dist-info/RECORD"))
        for name, raw in expected.items():
            assert bundle.namelist().count(name) == 1
            assert bundle.read(name) == raw
            assert name in bundle.read(record).decode()
        metadata = message_from_bytes(
            bundle.read(record.removesuffix("RECORD") + "METADATA"),
        )
        hashes = {name: hashlib.sha256(bundle.read(name)).hexdigest() for name in bundle.namelist()}
        artifacts.validate_wheel_sources(
            bundle, hashes, source_dir=source, prefix=record.removesuffix("RECORD"),
            metadata=metadata, candidate_root=root,
        )


@pytest.mark.parametrize("layout", ["source", "independent-sdist"])
@pytest.mark.parametrize("missing", ["helper", "profile"])
def test_real_distribution_has_no_missing_input_fallback(candidate, layout, missing):
    root, source = candidate
    if layout == "independent-sdist":
        project = _independent_sdist(source, root)
        path = project / (
            "_standalone_runtime_build.py" if missing == "helper"
            else "src/agent_containers/_runtime_profile.py"
        )
    else:
        project = source
        path = source / "scripts" / ("standalone_build.py" if missing == "helper" else "runtime_profile.py")
        included = source / (
            "_standalone_runtime_build.py" if missing == "helper"
            else "src/agent_containers/_runtime_profile.py"
        )
        included.write_bytes(path.read_bytes())
    path.unlink()
    error = _build(project, root / "refused-wheels", success=False)
    assert path.name in error
    assert ("FileNotFoundError" if missing == "helper" else "is missing") in error
