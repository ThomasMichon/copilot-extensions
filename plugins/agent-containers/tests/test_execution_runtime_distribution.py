"""Real artifact/installed-process controls; no provider, guest or native launch."""
from __future__ import annotations

from email import message_from_bytes
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from types import ModuleType
import zipfile

import pytest

from . import test_runtime_profile as fixtures

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = Path(__file__).resolve().parents[1]
RESOURCES = {
    "versioned_runtime.py": "_versioned_runtime.py",
    "versioned_runtime_leases.py": "versioned_runtime_leases.py",
    "versioned_runtime_construction.py": "versioned_runtime_construction.py",
    "versioned_runtime_publication.py": "versioned_runtime_publication.py",
    "versioned_runtime_source.py": "versioned_runtime_source.py",
    "runtime_profile.py": "_runtime_profile.py",
}
profiles = fixtures.profiles
payload = fixtures.payload


def _run(
    arguments: list[str], *, cwd: Path, success: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments, cwd=cwd, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project = tmp_path / "release/plugins/agent-containers"
    project.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "setup.py", "MANIFEST.in"):
        shutil.copyfile(PLUGIN / name, project / name)
    shutil.copytree(
        PLUGIN / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__"),
    )
    (project / "scripts").mkdir()
    for name in (*RESOURCES, "standalone_build.py"):
        shutil.copyfile(PLUGIN / "scripts" / name, project / "scripts" / name)
    return project


def _build(project: Path, output: Path, operation: str = "build_wheel") -> Path:
    output.mkdir()
    result = _run([
        sys.executable, "-I", "-B", "-c",
        f"from setuptools.build_meta import {operation}; {operation}({str(output)!r})",
    ], cwd=project)
    suffix = "*.whl" if operation == "build_wheel" else "*.tar.gz"
    artifacts = list(output.glob(suffix))
    assert len(artifacts) == 1, result.stdout + result.stderr
    return artifacts[0]


def _extract(sdist: Path, destination: Path) -> Path:
    destination.mkdir()
    with tarfile.open(sdist) as archive:
        for member in archive.getmembers():
            assert member.isdir() or member.isfile()
            target = destination / member.name
            assert target.resolve().is_relative_to(destination.resolve())
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                content = archive.extractfile(member)
                assert content is not None
                target.write_bytes(content.read())
    project, = destination.iterdir()
    return project


@pytest.mark.parametrize("layout", ["wheel", "sdist", "prepared"])
def test_fresh_installed_process_owns_exact_resources_and_retention(
    project: Path, tmp_path: Path, layout: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if layout == "prepared":
        monkeypatch.syspath_prepend(str(ROOT / "tools"))
        from prepare_runtime_source import _materialize
        project = tmp_path / "prepared"
        _materialize(ROOT, "agent-containers", project)
    expected = {
        "agent_containers/" + destination: (project / "scripts" / source).read_bytes()
        for source, destination in RESOURCES.items()
    }
    if layout == "sdist":
        sdist = _build(project, tmp_path / "sdist", "build_sdist")
        project = _extract(sdist, tmp_path / "independent")
        assert not (project / "scripts").exists()
        assert (project / "_standalone_runtime_build.py").read_bytes() == (
            PLUGIN / "scripts/standalone_build.py"
        ).read_bytes()
    wheel = _build(project, tmp_path / "wheels")
    with zipfile.ZipFile(wheel) as archive:
        record = next(name for name in archive.namelist() if name.endswith(".dist-info/RECORD"))
        for name, data in expected.items():
            assert archive.namelist().count(name) == 1
            assert archive.read(name) == data
            assert name in archive.read(record).decode()
    root = tmp_path / "runtime"
    slot = root / "versions/1.0.0+base"
    _run([sys.executable, "-I", "-m", "venv", "--without-pip", str(slot)], cwd=tmp_path)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv is not None, "the bounded runner must provide uv"
    _run([
        uv, "pip", "install", "--no-index", "--no-deps", "--python", str(python), str(wheel),
    ], cwd=tmp_path)
    project.rename(project.with_name("unavailable-source"))
    probe = """
import json,os,sys
from pathlib import Path
from agent_containers import _runtime_profile as profiles
from agent_containers.execution_runtime import retain_current_execution
root=Path(sys.argv[1])
version="1.0.0+base"
slot=root/"versions"/version
digest="a"*64
assert Path(profiles.__file__).is_relative_to(slot)
assert Path(profiles.runtime.__file__).is_relative_to(slot)
assert Path(profiles.source.__file__).is_relative_to(slot)
(slot/"runtime-profile.json").write_text(json.dumps({
    "packageVersion":"1.0.0","profile":"base","payloadHash":digest,
    "executionContract":profiles._execution_contract(),
}))
profiles.runtime.mark_complete(root,version,payload_hash=digest)
# This mutable selector must not be consulted by the executing slot.
(root/"current-version").write_text("2.0.0+base")
def forbidden(*args,**kwargs):
    raise AssertionError("a selected-runtime context must not resolve or provision")
profiles.runtime.resolve_python=forbidden
profiles.provision=forbidden
with retain_current_execution(root,version=version,payload_hash=digest,timeout=0) as descriptor:
    os.fstat(descriptor)
    with profiles.runtime._cleanup_lease(root,version) as admitted:
        assert not admitted
    assert profiles._read_execution(root,Path(sys.executable)).retained_creation is False
try:
    os.fstat(descriptor)
except OSError:
    pass
else:
    raise AssertionError("context leaked its descriptor")
with profiles.runtime._cleanup_lease(root,version) as admitted:
    assert admitted
for change in ("version","hash","legacy","foreign-package"):
    arguments=dict(version=version,payload_hash=digest,timeout=0)
    if change=="version":
        arguments["version"]="2.0.0+base"
    elif change=="hash":
        arguments["payload_hash"]="b"*64
    elif change=="legacy":
        path=slot/"runtime-profile.json"
        value=json.loads(path.read_text())
        del value["executionContract"]
        path.write_text(json.dumps(value))
    else:
        value["executionContract"]=profiles._execution_contract()
        path.write_text(json.dumps(value))
        import agent_containers.execution_runtime as execution
        execution.__file__=str(root/"foreign"/"execution_runtime.py")
    try:
        with retain_current_execution(root,**arguments):
            raise AssertionError("conflicting execution evidence was admitted")
    except RuntimeError:
        pass
print(json.dumps({"slot_owned":True,"retainedCreation":False}))
"""
    result = _run([str(python), "-I", "-B", "-c", probe, str(root)], cwd=tmp_path)
    assert result.stdout.strip() == '{"slot_owned": true, "retainedCreation": false}'
    site = Path(_run([
        str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))",
    ], cwd=tmp_path).stdout.strip())
    package = site / "agent_containers"
    outside = tmp_path / "mutable-code"
    outside.mkdir()
    for name in ("package", "__init__.py", "execution_runtime.py", *RESOURCES.values()):
        source = package if name == "package" else package / name
        target = outside / name
        source.rename(target)
        try:
            try:
                source.symlink_to(target, target_is_directory=name == "package")
            except OSError as error:
                if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                    pytest.skip("linked-origin controls require native symlink privilege")
                raise
            refusal = """
from pathlib import Path
import sys
from agent_containers.execution_runtime import retain_current_execution
try:
    with retain_current_execution(
        Path(sys.argv[1]),version="1.0.0+base",payload_hash="a"*64,timeout=0,
    ):
        raise AssertionError("mutable linked code escaped the slot retention boundary")
except RuntimeError as error:
    assert "slot-owned resources" in str(error)
else:
    raise AssertionError("linked origin was not refused")
"""
            _run([str(python), "-I", "-B", "-c", refusal, str(root)], cwd=tmp_path)
        finally:
            if source.is_symlink():
                source.unlink()
            target.rename(source)


@pytest.mark.parametrize("missing", [
    "standalone_build.py", "versioned_runtime.py", "versioned_runtime_publication.py",
    "runtime_profile.py",
])
@pytest.mark.parametrize("included_copy", [False, True])
def test_missing_owned_resource_refuses_build(
    project: Path, tmp_path: Path, missing: str, included_copy: bool,
) -> None:
    if included_copy:
        included = (
            project / "_standalone_runtime_build.py" if missing == "standalone_build.py"
            else project / "src/agent_containers" / RESOURCES[missing]
        )
        shutil.copyfile(project / "scripts" / missing, included)
    (project / "scripts" / missing).unlink()
    output = tmp_path / "wheels"
    output.mkdir()
    result = _run([
        sys.executable, "-I", "-B", "-c",
        f"from setuptools.build_meta import build_wheel; build_wheel({str(output)!r})",
    ], cwd=project, success=False)
    assert result.returncode != 0
    assert not list(output.glob("*.whl"))
    label = {
        "versioned_runtime.py": "runtime primitive",
        "runtime_profile.py": "_runtime_profile.py",
    }.get(missing, missing)
    if missing == "standalone_build.py":
        assert "FileNotFoundError" in result.stderr and missing in result.stderr
    else:
        assert f"{label} is missing" in result.stderr


@pytest.mark.parametrize("resource", ["_standalone_runtime_build.py", "_runtime_profile.py"])
def test_sdist_missing_resource_refuses_without_source_fallback(
    project: Path, tmp_path: Path, resource: str,
) -> None:
    sdist = _build(project, tmp_path / "sdist", "build_sdist")
    standalone = _extract(sdist, tmp_path / "independent")
    resource_path = (
        standalone / resource if resource.startswith("_standalone")
        else standalone / "src/agent_containers" / resource
    )
    resource_path.unlink()
    output = tmp_path / "wheels"
    output.mkdir()
    result = _run([
        sys.executable, "-I", "-B", "-c",
        f"from setuptools.build_meta import build_wheel; build_wheel({str(output)!r})",
    ], cwd=standalone, success=False)
    assert result.returncode != 0
    assert resource in result.stderr
    assert not list(output.glob("*.whl"))


@pytest.mark.parametrize("name", ["setup.py", "MANIFEST.in", "_standalone_runtime_build.py"])
def test_build_control_bytes_participate_in_profile_fingerprint(
    profiles: ModuleType, payload: Path, name: str,
) -> None:
    path = payload / name
    path.write_bytes(b"# first\n")
    before = profiles._fingerprint(payload, profiles._library_roots(payload))
    info = path.stat()
    path.write_bytes(b"# other\n")
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert profiles._fingerprint(payload, profiles._library_roots(payload)) != before


def test_promotion_maps_real_wheel_resources_without_executing_candidate_hooks(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from plugin_runtime_resources import build_inputs
    from promotion_python_artifacts import validate_wheel_sources
    root = project.parents[1]
    _, resources = build_inputs(project, root=root)
    wheel = _build(project, tmp_path / "wheels")
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        prefix = metadata.removesuffix("METADATA")
        hashes = {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
        }
        validate_wheel_sources(
            archive, hashes, source_dir=project, prefix=prefix,
            metadata=message_from_bytes(archive.read(metadata)), candidate_root=root,
        )
    assert len(resources) == len(RESOURCES)
    for path in (project / "setup.py", project / "scripts/standalone_build.py"):
        original = path.read_bytes()
        path.write_bytes(b"raise AssertionError('candidate code must not execute')\n")
        with pytest.raises(RuntimeError, match="build"):
            build_inputs(project, root=root)
        path.write_bytes(original)


def test_artifact_builder_preserves_its_explicit_input_error_contract(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import build_python_artifacts as builder
    monkeypatch.setattr(builder, "REPO", project.parents[1])
    monkeypatch.setattr(builder, "PLUGINS_DIR", project.parent)
    monkeypatch.setattr(builder, "resolve_vendored_libs", lambda _source: [])
    (project / "scripts/standalone_build.py").write_bytes(
        b"raise AssertionError('candidate build code must not execute')\n",
    )
    with pytest.raises(builder.ArtifactBuildError, match="runtime build inputs"):
        builder.build_plugin_artifacts("agent-containers", out_dir=project.parent / "wheels")
