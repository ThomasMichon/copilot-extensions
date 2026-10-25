"""Original full build source is retained data, not qualified landing authority."""
from __future__ import annotations

import copy
import hashlib

import pytest

import promotion_build_source as build
import promotion_complete_source as qualified
from promotion_unit_tests.test_complete_source import case


def build_case():
    files, identity = case()
    index = {
        "schema_version": 2, "dev_head": identity["dev_sha"],
        "main_before": identity["main_before"], "content_tree": identity["content_tree"],
        "admission": {
            "signer_digest": identity["control_sha"], "source_digest": identity["control_sha"],
            "source_ref": "refs/heads/main",
        },
        "runtime_profiles": [{"opaque": "not interpreted or approved"}],
    }
    raw = qualified.canonical_bytes(index)
    identity = build.build_identity(raw, candidate_sha=identity["candidate_sha"], tree=identity["tree"])
    return files, index, raw, identity


def test_original_build_source_is_deterministic_complete_and_cannot_be_qualified_source():
    files, _index, index_raw, identity = build_case()
    raw, manifest = build.build_archive(files, identity=identity)
    assert build.build_archive(dict(reversed(list(files.items()))), identity=identity) == (raw, manifest)
    assert build.verify_build_archive(raw, manifest, identity=identity) == files
    assert manifest["source_index_sha256"] == hashlib.sha256(index_raw).hexdigest()
    assert manifest["schema"] != qualified.SCHEMA
    assert "qualification_digest" not in manifest
    landing = {**identity, "qualification_digest": "0" * 64}
    with pytest.raises(ValueError):
        qualified.verify_source_archive(raw, manifest, identity=landing)
    with pytest.raises(ValueError):
        build.verify_build_archive(raw, {**manifest, "qualification_digest": "0" * 64}, identity=identity)
    for kind in ("build-source", "build-manifest"):
        name = build.asset_name(kind, identity, hashlib.sha256(raw).hexdigest())
        assert len(name) <= 255 and name.startswith(f"promotion-{kind}-{identity['dev_sha']}-")


@pytest.mark.parametrize("field", sorted(build.IDENTITY_FIELDS))
def test_original_build_source_cannot_relabel_any_signed_generation_input(field):
    files, _index, _index_raw, identity = build_case()
    raw, manifest = build.build_archive(files, identity=identity)
    changed = {**identity, field: "1" * len(identity[field])}
    with pytest.raises(ValueError):
        build.verify_build_archive(raw, manifest, identity=changed)


@pytest.mark.parametrize("change", [
    "legacy", "boolean", "missing-admission", "source-ref", "source-digest",
    "missing-source", "invalid-main", "invalid-tree", "extra-identity",
])
def test_original_build_source_refuses_untrusted_or_malformed_source_identity(change):
    _files, index, _index_raw, identity = build_case()
    if change == "legacy":
        index["schema_version"] = 1
    elif change == "boolean":
        index["schema_version"] = True
    elif change == "missing-admission":
        del index["admission"]
    elif change == "source-ref":
        index["admission"]["source_ref"] = "refs/heads/dev"
    elif change == "source-digest":
        index["admission"]["source_digest"] = "1" * 40
    elif change == "missing-source":
        del index["dev_head"]
    elif change == "invalid-main":
        index["main_before"] = "not-a-sha"
    elif change == "invalid-tree":
        identity["tree"] = "not-a-tree"
    else:
        with pytest.raises(ValueError):
            build.validate_identity({**identity, "qualification_digest": "0" * 64})
        return
    with pytest.raises(ValueError):
        build.build_identity(
            qualified.canonical_bytes(index), candidate_sha=identity["candidate_sha"], tree=identity["tree"],
        )


@pytest.mark.parametrize("change", ["archive", "bytes", "mode", "missing", "extra", "manifest", "version", "digest"])
def test_original_build_archive_refuses_incomplete_or_corrupted_source(change):
    files, _index, _index_raw, identity = build_case()
    raw, manifest = build.build_archive(files, identity=identity)
    manifest = copy.deepcopy(manifest)
    if change == "archive":
        raw += b"x"
    elif change in ("manifest", "version", "digest"):
        if change == "manifest":
            manifest["files"].pop()
        elif change == "version":
            manifest["version"] = True
        else:
            manifest["archive_sha256"] = "1" * 64
    else:
        if change == "bytes":
            files["hidden"] = ("100644", b"changed")
        elif change == "mode":
            files["bin/run"] = ("100644", files["bin/run"][1])
        elif change == "missing":
            del files["hidden"]
        else:
            files["extra"] = ("100644", b"x")
        with pytest.raises(ValueError):
            build.build_archive(files, identity=identity)
        return
    with pytest.raises(ValueError):
        build.verify_build_archive(raw, manifest, identity=identity)
