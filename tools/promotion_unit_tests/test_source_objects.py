"""Pinned raw-object batch protocol; no actual Git storage or subprocess execution."""
from __future__ import annotations

import hashlib

import pytest

import promotion_source_archive as source_io
from promotion_unit_tests.test_complete_source import case


@pytest.fixture
def objects(monkeypatch):
    files, identity = case()
    blobs, records = {}, []
    for name, (mode, raw) in sorted(files.items()):
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        blobs[blob] = raw
        records.append(f"{mode} blob {blob}\t{name}".encode() + b"\0")
    identifiers = sorted(blobs)
    checks = [f"{blob} blob {len(blobs[blob])}\n".encode() for blob in identifiers]
    outputs = {
        "commit": f"tree {identity['tree']}\nparent {identity['main_before']}\n\ncandidate\n".encode(),
        "tree": b"".join(records), "metadata": b"".join(checks),
        "blobs": b"".join(header + blobs[blob] + b"\n" for header, blob in zip(checks, identifiers, strict=True)),
    }
    calls = []

    def git(repo, arguments, *, data=None):
        calls.append((repo, arguments, data))
        if arguments[0] == "ls-tree":
            assert arguments == ["ls-tree", "-rz", identity["tree"]] and data is None
            return outputs["tree"]
        if arguments[1] == "commit":
            assert arguments == ["cat-file", "commit", identity["candidate_sha"]] and data is None
            return outputs["commit"]
        assert data == ("\n".join(identifiers) + "\n").encode()
        return outputs["metadata" if arguments == ["cat-file", "--batch-check"] else "blobs"]

    monkeypatch.setattr(source_io, "_git", git)
    return files, identity, outputs, calls


def test_source_reader_uses_one_bounded_metadata_and_blob_batch_without_export_attributes(objects, tmp_path):
    files, identity, _outputs, calls = objects
    assert source_io.read_tree(
        tmp_path, **{key: identity[key] for key in ("candidate_sha", "main_before", "tree")},
    ) == files
    assert [arguments[:2] for _repo, arguments, _data in calls] == [
        ["cat-file", "commit"], ["ls-tree", "-rz"], ["cat-file", "--batch-check"], ["cat-file", "--batch"],
    ]
    assert not any("archive" in arguments or "--filters" in arguments for _repo, arguments, _data in calls)


@pytest.mark.parametrize("change", [
    "parent", "tree", "submodule", "mode", "duplicate", "metadata-count", "metadata-kind",
    "size", "blob-header", "blob-truncated", "blob-extra", "identity",
])
def test_source_reader_refuses_wrong_generation_unsupported_types_and_batch_corruption(
    objects, tmp_path, change,
):
    _files, identity, outputs, calls = objects
    if change == "parent":
        outputs["commit"] = outputs["commit"].replace(identity["main_before"].encode(), b"1" * 40)
    elif change == "tree":
        outputs["commit"] = outputs["commit"].replace(identity["tree"].encode(), b"1" * 40)
    elif change == "submodule":
        outputs["tree"] = outputs["tree"].replace(b"100644 blob", b"160000 commit", 1)
    elif change == "mode":
        outputs["tree"] = outputs["tree"].replace(b"100644", b"100600", 1)
    elif change == "duplicate":
        outputs["tree"] += outputs["tree"].split(b"\0")[0] + b"\0"
    elif change == "metadata-count":
        outputs["metadata"] = outputs["metadata"].splitlines(keepends=True)[0]
    elif change == "metadata-kind":
        outputs["metadata"] = outputs["metadata"].replace(b" blob ", b" tree ", 1)
    elif change == "size":
        first = outputs["metadata"].splitlines()[0]
        outputs["metadata"] = outputs["metadata"].replace(first, first.rsplit(b" ", 1)[0] + b" 999999999", 1)
    elif change == "blob-header":
        outputs["blobs"] = b"x" + outputs["blobs"]
    elif change == "blob-truncated":
        outputs["blobs"] = outputs["blobs"][:-1]
    elif change == "blob-extra":
        outputs["blobs"] += b"x"
    else:
        identity["candidate_sha"] = "not-a-sha"
    with pytest.raises(ValueError):
        source_io.read_tree(
            tmp_path, **{key: identity[key] for key in ("candidate_sha", "main_before", "tree")},
        )
    if change in ("size", "metadata-kind", "metadata-count"):
        assert len(calls) == 3
    if change == "identity":
        assert not calls
