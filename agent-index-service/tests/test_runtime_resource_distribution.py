"""The immutable slot helper is copied into artifacts, never hand-forked."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from agent_procutil import no_window_kwargs


def _project(tmp_path):
    source = Path(__file__).resolve().parents[1]
    project = tmp_path / "release" / "agent-index-service"
    project.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "setup.py", "_build_runtime_resource.py", "MANIFEST.in"):
        shutil.copy2(source / name, project / name)
    shutil.copytree(
        source / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__"),
    )
    primitive = project.parent / "libs" / "versioned-runtime" / "versioned_runtime.py"
    primitive.parent.mkdir(parents=True)
    shutil.copy2(source.parent / "libs" / "versioned-runtime" / "versioned_runtime.py", primitive)
    return project, primitive


def _build(project, output, operation):
    output.mkdir(exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-c",
         f"from setuptools.build_meta import {operation}; {operation}({str(output)!r})"],
        cwd=project, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    return result


def test_sdist_to_wheel_preserves_canonical_primitive_outside_repository(tmp_path):
    project, primitive = _project(tmp_path)
    expected = primitive.read_bytes()
    artifacts = tmp_path / "artifacts"
    result = _build(project, artifacts, "build_sdist")
    assert result.returncode == 0, result.stdout + result.stderr
    sdist = next(artifacts.glob("*.tar.gz"))
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(sdist) as archive:
        names = [
            name for name in archive.getnames()
            if name.endswith("/src/agent_index_service/_versioned_runtime.py")
        ]
        assert len(names) == 1
        stream = archive.extractfile(names[0])
        assert stream is not None
        assert stream.read() == expected
        archive.extractall(extracted, filter="data")
    standalone = next(path for path in extracted.iterdir() if path.is_dir())
    assert not (standalone.parent / "libs").exists()
    result = _build(standalone, artifacts, "build_wheel")
    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(next(artifacts.glob("*.whl"))) as wheel:
        member = "agent_index_service/_versioned_runtime.py"
        assert wheel.namelist().count(member) == 1
        assert wheel.read(member) == expected
        record = next(name for name in wheel.namelist() if name.endswith(".dist-info/RECORD"))
        assert member in wheel.read(record).decode()


def test_missing_canonical_and_sdist_resource_fails_build(tmp_path):
    project, primitive = _project(tmp_path)
    primitive.unlink()
    result = _build(project, tmp_path / "artifacts", "build_wheel")
    assert result.returncode != 0
    assert "runtime primitive is missing" in result.stderr


def test_conflicting_included_resource_fails_instead_of_preferring_stale_copy(tmp_path):
    project, _ = _project(tmp_path)
    included = project / "src" / "agent_index_service" / "_versioned_runtime.py"
    included.write_text("stale\n", encoding="utf-8")
    result = _build(project, tmp_path / "artifacts", "build_wheel")
    assert result.returncode != 0
    assert "differs from the canonical source" in result.stderr
