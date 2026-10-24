"""Immutable-bundle refusal contracts, not native artifact qualification."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools.coverage_guided_selection import observer_artifact as artifact


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    source, entry = tmp_path / "observer.c", tmp_path / "entry.py"
    source.write_bytes(b"canonical C input")
    entry.write_bytes(b"canonical Python entry")
    monkeypatch.setattr(artifact.native, "C_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    identity = {"platform": "linux-x86_64", "python_cache_tag": "cpython-312", "python_sha256": "a" * 64}
    monkeypatch.setattr(artifact, "_identity", lambda: identity)
    root = tmp_path / "bundle"
    root.mkdir()
    content = b"synthetic-library-not-executed"
    (root / "observer.so").write_bytes(content)
    manifest = {
        "schema": artifact.SCHEMA, **identity,
        "c_source_sha256": artifact.native.C_SHA256,
        "entry_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
        "library_sha256": hashlib.sha256(content).hexdigest(), "library_bytes": len(content),
        "compiler_wheel_sha256": artifact.native.WHEEL_SHA256,
        "build_variant": "original", "constructor_entries": 1, "admission": "unqualified",
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="ascii")
    pin = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    calls = []

    def verify(path, entry_sha):
        calls.append((path, entry_sha))
        assert path.read_bytes() == content
        return {"constructor_entries": 1, "entry_sha256": entry_sha, "static_shape_only": True}

    monkeypatch.setattr(artifact.native, "verify_artifact", verify)
    return root, source, entry, pin, calls


def test_stage_verifies_fresh_owned_copy_and_never_builds_or_loads(bundle, tmp_path, monkeypatch):
    root, source, entry, pin, calls = bundle
    monkeypatch.setattr(artifact.subprocess, "run", lambda *_a, **_k: pytest.fail("compiler ran during setup"))
    monkeypatch.setattr(artifact.native, "extract_toolchain", lambda *_a: pytest.fail("toolchain extracted during setup"))
    destination = tmp_path / "active.so"
    result = artifact.stage_bundle(root, destination, expected_manifest_sha256=pin, source=source, entry=entry)
    assert destination.read_bytes() == (root / "observer.so").read_bytes()
    assert calls == [(destination, hashlib.sha256(entry.read_bytes()).hexdigest())]
    assert result["manifest_sha256"] == pin and result["admission"] == "unqualified"
    assert result["static_shape_only"] is True
    with pytest.raises(FileExistsError):
        artifact.stage_bundle(root, destination, expected_manifest_sha256=pin, source=source, entry=entry)
    assert destination.is_file()


@pytest.mark.parametrize("changed", ["manifest", "library", "source", "entry", "empty-pin"])
def test_changed_input_refuses_without_staging(bundle, tmp_path, changed):
    root, source, entry, pin, calls = bundle
    path = {"manifest": root / "manifest.json", "library": root / "observer.so",
            "source": source, "entry": entry}.get(changed)
    if path:
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        pin = ""
    destination = tmp_path / "active.so"
    with pytest.raises((ValueError, json.JSONDecodeError)):
        artifact.stage_bundle(root, destination, expected_manifest_sha256=pin, source=source, entry=entry)
    assert not destination.exists() and not calls


@pytest.mark.parametrize("field", [
    "platform", "python_cache_tag", "python_sha256", "c_source_sha256", "entry_sha256",
    "compiler_wheel_sha256", "build_variant", "admission", "constructor_entries",
])
def test_even_pinned_manifest_must_match_current_inputs(bundle, tmp_path, field):
    root, source, entry, _pin, _calls = bundle
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = 2 if field == "constructor_entries" else "foreign"
    path.write_text(json.dumps(manifest), encoding="ascii")
    pin = hashlib.sha256(path.read_bytes()).hexdigest()
    destination = tmp_path / "active.so"
    with pytest.raises(ValueError, match="identity differs"):
        artifact.stage_bundle(root, destination, expected_manifest_sha256=pin, source=source, entry=entry)
    assert not destination.exists()


def test_shape_refusal_removes_only_new_owned_copy(bundle, tmp_path, monkeypatch):
    root, source, entry, pin, _calls = bundle
    destination = tmp_path / "active.so"

    def refuse(*_args):
        raise ValueError("bad constructor")

    monkeypatch.setattr(artifact.native, "verify_artifact", refuse)
    with pytest.raises(ValueError, match="bad constructor"):
        artifact.stage_bundle(root, destination, expected_manifest_sha256=pin, source=source, entry=entry)
    assert not destination.exists()
    assert (root / "observer.so").is_file()


def test_oversized_input_refuses_before_reading_unbounded_content(tmp_path):
    path = tmp_path / "large"
    path.write_bytes(b"12345")
    with pytest.raises(ValueError, match="exceeds bound"):
        artifact._read(path, 4)


def test_hardlinked_input_is_not_an_independent_immutable_artifact(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"artifact")
    alias = tmp_path / "alias"
    os.link(source, alias)
    with pytest.raises(ValueError, match="single ordinary file"):
        artifact._read(alias, 64)


def test_input_replaced_between_stat_and_open_is_refused(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"modified")
    original_open = artifact.os.open

    def replaced(path, flags):
        replacement.replace(source)
        return original_open(path, flags)

    monkeypatch.setattr(artifact.os, "open", replaced)
    with pytest.raises(ValueError, match="changed during open"):
        artifact._read(source, 64)


def test_build_failure_preserves_existing_destination(tmp_path, monkeypatch):
    root = tmp_path / "existing"
    root.mkdir()
    kept = root / "keep"
    kept.write_text("must survive")
    source, entry = tmp_path / "observer.c", tmp_path / "entry.py"
    source.write_bytes(b"C")
    entry.write_bytes(b"entry")
    monkeypatch.setattr(artifact, "_identity", lambda: {})
    monkeypatch.setattr(artifact.native, "C_SHA256", hashlib.sha256(b"C").hexdigest())
    with pytest.raises(FileExistsError):
        artifact.build_bundle(Path("unused.whl"), source, entry, root)
    assert kept.read_text() == "must survive"
