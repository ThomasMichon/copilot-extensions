"""Full-source reconstruction and generation binding without Git or child actors."""
from __future__ import annotations

import copy
import hashlib
import io
import zipfile

import pytest

import promotion_complete_source as source


def case():
    files = {
        ".gitattributes": ("100644", b"hidden export-ignore\nmessage export-subst\n"),
        ".github/workflows/check.yml": ("100644", b"on: push\n"),
        "hidden": ("100644", b"retained despite export-ignore\n"),
        "message": ("100644", b"$Format:%H$\n"),
        "bin/run": ("100755", b"#!/bin/sh\nexit 0\n"),
        "bin/alias": ("120000", b"run"),
        "empty": ("100644", b""),
        "vendor/lib/source.py": ("100644", b"VALUE = 1\n"),
    }
    identity = {
        "dev_sha": "a" * 40, "candidate_sha": "b" * 40, "main_before": "c" * 40,
        "control_sha": "d" * 40, "content_tree": "e" * 40,
        "tree": source._inventory(files)[1], "source_index_sha256": "f" * 64,
        "qualification_digest": "0" * 64,
    }
    return files, identity


def test_complete_source_is_deterministic_self_contained_and_reconstructs_exact_modes_and_bytes():
    files, identity = case()
    raw, manifest = source.source_archive(files, identity=identity)
    assert source.source_archive(dict(reversed(list(files.items()))), identity=identity) == (raw, manifest)
    assert source.verify_source_archive(raw, manifest, identity=identity) == files
    assert manifest["tree"] == source._inventory(files)[1]
    assert manifest["archive_sha256"] == hashlib.sha256(raw).hexdigest()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.namelist() == sorted(files)
        assert archive.read("hidden") == files["hidden"][1]
        assert archive.read("message") == b"$Format:%H$\n"
        assert not any(name.endswith("manifest.json") or name.startswith(".git/") for name in archive.namelist())
    for kind in ("complete-source", "source-manifest"):
        name = source.source_asset_name(kind, identity, hashlib.sha256(raw).hexdigest())
        assert len(name) <= 255 and identity["dev_sha"] in name and identity["candidate_sha"] in name


@pytest.mark.parametrize("field", sorted(source.IDENTITY_FIELDS))
def test_source_generation_cannot_relabel_old_archive_or_admission(field):
    files, identity = case()
    raw, manifest = source.source_archive(files, identity=identity)
    changed = {**identity, field: "1" * len(identity[field])}
    with pytest.raises(ValueError, match="generation"):
        source.verify_source_archive(raw, manifest, identity=changed)


@pytest.mark.parametrize("change", ["missing", "extra", "bytes", "mode", "symlink", "vendor"])
def test_any_missing_or_changed_full_source_refuses_even_with_unchanged_other_payload(change):
    files, identity = case()
    if change == "missing":
        del files["hidden"]
    elif change == "extra":
        files["unexpected"] = ("100644", b"x")
    elif change == "bytes":
        files["message"] = ("100644", b"substituted\n")
    elif change == "mode":
        files["bin/run"] = ("100644", files["bin/run"][1])
    elif change == "symlink":
        files["bin/alias"] = ("100644", b"run")
    else:
        files["vendor/lib/source.py"] = ("100644", b"VALUE = 2\n")
    with pytest.raises(ValueError, match="reconstruct"):
        source.source_archive(files, identity=identity)


@pytest.mark.parametrize("path", [
    "../outside", "/absolute", "a//b", "a/./b", "a\\b", ".git/config",
    ".GIT/config", "a:stream", "CON.txt", "a/trailing.", "a/space ", "a/\x00b",
])
def test_complete_source_refuses_unsafe_or_nonportable_paths(path):
    files, identity = case()
    files[path] = ("100644", b"x")
    with pytest.raises(ValueError):
        source.source_archive(files, identity=identity)


@pytest.mark.parametrize("change", ["case", "ancestor", "directory", "submodule", "link", "file-limit", "tree-limit"])
def test_complete_source_refuses_ambiguous_types_and_existing_byte_bounds(monkeypatch, change):
    files, identity = case()
    if change == "case":
        files["BIN/other"] = ("100644", b"x")
    elif change == "ancestor":
        files["hidden/child"] = ("100644", b"x")
    elif change == "directory":
        files["bin"] = ("100644", b"x")
    elif change == "submodule":
        files["module"] = ("160000", b"")
    elif change == "link":
        files["bin/alias"] = ("120000", b"../../outside")
    elif change == "file-limit":
        monkeypatch.setattr(source, "MAX_PACKET_FILE_BYTES", 1)
    else:
        monkeypatch.setattr(source, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(ValueError):
        source.source_archive(files, identity=identity)


@pytest.mark.parametrize("change", ["archive", "inventory", "size", "digest", "tree", "boolean", "schema", "extra"])
def test_independent_archive_readback_refuses_tamper_and_incomplete_inventory(change):
    files, identity = case()
    raw, manifest = source.source_archive(files, identity=identity)
    manifest = copy.deepcopy(manifest)
    if change == "archive":
        raw += b"changed"
    elif change == "inventory":
        manifest["files"].pop()
    elif change == "size":
        manifest["files"][0]["size"] += 1
    elif change == "digest":
        manifest["files"][0]["sha256"] = "f" * 64
    elif change == "tree":
        manifest["tree"] = "f" * 40
    elif change == "boolean":
        manifest["files"][0]["size"] = True
    elif change == "schema":
        manifest["version"] = True
    else:
        manifest["unadmitted"] = "value"
    with pytest.raises(ValueError):
        source.verify_source_archive(raw, manifest, identity=identity)
