"""Candidate-to-wheel payload binding, including self-consistent forged artifacts."""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
from test_promotion_python_artifacts import _complete_manifest, _publish_fixture, _wheel_bytes, _write_index


@pytest.mark.parametrize("case", [
    "match", "changed-code", "injected-code", "missing-code", "missing-source",
    "valid-data", "changed-data", "missing-data", "valid-entry-points",
    "forged-entry-points", "valid-top-level", "forged-top-level", "unsupported-layout",
])
def test_wheel_source_mapping_requires_exact_candidate_bytes(tmp_path, monkeypatch, case):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(directory / entry["filename"]) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    wheel = manifest["wheels"][0]
    source = tmp_path / "plugins" / "demo"
    project = source / "pyproject.toml"
    members = {}
    payload = b"VALUE = 'first-party fixture'\n"
    if case == "changed-code":
        payload = b"INJECTED = True\n"
    elif case == "injected-code":
        members["demo/injected.py"] = b"INJECTED = True\n"
    elif case == "missing-code":
        (source / "src" / "demo" / "required.py").write_text("REQUIRED = True\n")
    elif case == "missing-source":
        (source / "src" / "demo" / "__init__.py").unlink()
    elif case.endswith("data"):
        project.write_text(project.read_text() + '\n[tool.setuptools.package-data]\ndemo = ["*.json"]\n')
        (source / "src" / "demo" / "data.json").write_bytes(b'{"candidate": true}\n')
        if case != "missing-data":
            members["demo/data.json"] = b"forged" if case == "changed-data" else b'{"candidate": true}\n'
    elif case.endswith("entry-points"):
        members["demo-1.0.dist-info/entry_points.txt"] = b"[console_scripts]\ndemo = demo:main\n"
        if case == "valid-entry-points":
            project.write_text(project.read_text() + '\n[project.scripts]\ndemo = "demo:main"\n')
    elif case.endswith("top-level"):
        members["demo-1.0.dist-info/top_level.txt"] = (
            b"demo\n" if case == "valid-top-level" else b"other\n"
        )
    elif case == "unsupported-layout":
        project.write_text(project.read_text() + '\n[tool.setuptools.packages.find]\nwhere = ["generated"]\n')
    archive = io.BytesIO(_wheel_bytes("demo", "1.0", payload=payload, members=members))
    if case in ("match", "valid-data", "valid-entry-points", "valid-top-level"):
        artifacts.validate_wheel(archive, wheel, source_dir=source)
    else:
        with pytest.raises(artifacts.PromotionArtifactError):
            artifacts.validate_wheel(archive, wheel, source_dir=source)


def test_self_consistent_injected_wheel_fails_before_publication(tmp_path, monkeypatch):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    original = directory / entry["filename"]
    with zipfile.ZipFile(original) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    wheel = manifest["wheels"][0]
    wheel_dir = tmp_path / "forged"
    wheel_dir.mkdir()
    path = wheel_dir / wheel["filename"]
    path.write_bytes(_wheel_bytes("demo", "1.0", payload=b"INJECTED = True\n"))
    wheel["sha256"] = "sha256:" + artifacts._digest(path)
    _complete_manifest(manifest)
    original.unlink()
    replacement = artifacts.bundle_wheels(manifest, wheel_dir, directory)
    index = json.loads((directory / artifacts.INDEX_NAME).read_text())
    index["artifacts"] = [replacement]
    _write_index(directory, index)
    calls = []
    monkeypatch.setattr(publisher, "_gh", lambda *a, **kw: calls.append(a))
    with pytest.raises(artifacts.PromotionArtifactError, match="payload differs from candidate source"):
        publisher.publish_artifacts(
            directory, repository="example/plugins", merged_commit="c" * 40,
            dev_head="a" * 40, repo=tmp_path,
        )
    assert not calls


def test_declared_flat_python_modules_bind_to_root_source(tmp_path, monkeypatch):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(directory / entry["filename"]) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    source = tmp_path / "flat"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.0"\n[tool.setuptools]\npy-modules = ["demo"]\n'
    )
    (source / "demo.py").write_bytes(b"VALUE = 'first-party fixture'\n")
    artifacts.validate_wheel(
        io.BytesIO(_wheel_bytes("demo", "1.0", flat=True)), manifest["wheels"][0], source_dir=source,
    )
