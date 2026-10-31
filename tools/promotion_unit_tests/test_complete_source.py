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


def _unchecked_link_archive(links):
    identity = case()[1]
    records, inventory = [], []
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, target in sorted(links.items()):
            raw = target.encode()
            records.append(b"120000 " + name.encode() + b"\0" + source._object("blob", raw))
            inventory.append({"path": name, "mode": "120000", "size": len(raw),
                              "sha256": hashlib.sha256(raw).hexdigest()})
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o120000 << 16
            archive.writestr(member, raw)
    identity["tree"] = source._object("tree", b"".join(records)).hex()
    raw = output.getvalue()
    return raw, {
        "schema": source.SCHEMA, "version": 1, **identity,
        "archive_sha256": hashlib.sha256(raw).hexdigest(), "files": inventory,
    }, identity


@pytest.mark.parametrize("links", [
    {"a": ".", "b": "a/.."},
    {"a": ".", "b": "a", "c": "b/.."},
    {"a": ".", "b": "a/../outside"},
    {"a": ".", "b": "A/.."},
    {"a": "b", "b": "a"},
    {"a": "a"},
])
@pytest.mark.parametrize("boundary", ["creation", "readback"])
def test_complete_link_graph_refuses_chained_escape_and_cycles_on_both_boundaries(links, boundary):
    raw, manifest, identity = _unchecked_link_archive(links)
    with pytest.raises(ValueError):
        if boundary == "creation":
            source.source_archive(
                {name: ("120000", target.encode()) for name, target in links.items()}, identity=identity,
            )
        else:
            source.verify_source_archive(raw, manifest, identity=identity)


@pytest.mark.parametrize("target", ["a/bin/run", "a/a/bin/run"])
def test_non_escaping_chains_and_repeated_aliases_preserve_original_link_bytes(target):
    files, identity = case()
    files.update(a=("120000", b"."), b=("120000", target.encode()))
    identity["tree"] = source._inventory(files)[1]
    raw, manifest = source.source_archive(files, identity=identity)
    assert source.verify_source_archive(raw, manifest, identity=identity) == files


@pytest.mark.parametrize("count", [40, 41])
def test_complete_source_link_traversal_bound_is_inclusive(count):
    files, identity = case()
    for number in range(count):
        target = f"l{number + 1}" if number + 1 < count else "bin/run"
        files[f"l{number}"] = ("120000", target.encode())
    if count > source.MAX_LINK_HOPS:
        with pytest.raises(ValueError, match="link traversal"):
            source._inventory(files)
    else:
        identity["tree"] = source._inventory(files)[1]
        raw, manifest = source.source_archive(files, identity=identity)
        assert source.verify_source_archive(raw, manifest, identity=identity) == files


@pytest.mark.parametrize("size", [4096, 4097])
def test_complete_source_link_literal_bound_is_inclusive(size):
    files, identity = case()
    files["link"] = ("120000", b"x" * size)
    if size > source.MAX_LINK_BYTES:
        with pytest.raises(ValueError, match="link.*byte bound"):
            source._inventory(files)
    else:
        identity["tree"] = source._inventory(files)[1]
        raw, manifest = source.source_archive(files, identity=identity)
        assert source.verify_source_archive(raw, manifest, identity=identity) == files
