"""The existing Dev publisher retains full-source subjects without granting code approval."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import promotion_artifact_readiness as readiness
import promotion_release_evidence as evidence
import promotion_source_archive as source_io
import publish_python_artifacts as publisher
from promotion_complete_source import canonical_bytes
from promotion_unit_tests.test_complete_source import case


@pytest.fixture
def publication(tmp_path, monkeypatch):
    files, identity = case()
    receipt = {
        **{key: identity[key] for key in ("dev_sha", "candidate_sha", "main_before", "control_sha", "tree")},
        "profile": "candidate-units-v3",
    }
    index = {
        "schema_version": 2, "dev_head": identity["dev_sha"], "content_tree": identity["content_tree"],
        "artifacts": [], "runtime_profiles": [{"opaque": "retained, not interpreted"}],
    }
    directory = tmp_path / "packages"
    directory.mkdir()
    index_raw = canonical_bytes(index)
    (directory / publisher.INDEX_NAME).write_bytes(index_raw)
    qualification_digest = hashlib.sha256(canonical_bytes(receipt)).hexdigest()
    identity = source_io.source_identity(index_raw, receipt, qualification_digest=qualification_digest)
    monkeypatch.setattr(source_io, "read_tree", lambda *args, **kwargs: files)
    subjects = tmp_path / "complete"
    source_io.prepare_source(subjects, repo=tmp_path, index_raw=index_raw, receipt=receipt)
    release = {"id": 9, "tag_name": f"python-artifacts-{identity['dev_sha']}"}
    assets = {
        publisher.INDEX_NAME: {
            "name": publisher.INDEX_NAME, "id": 1, "digest": "sha256:" + hashlib.sha256(index_raw).hexdigest(),
            "state": "uploaded", "size": len(index_raw), "raw": index_raw,
        },
    }
    calls, admissions, downloads = [], [], []

    def gh(argv, **options):
        calls.append((argv, options))
        if argv[0] == "api":
            assert options == {"verification": True, "timeout": 120}
            return SimpleNamespace(returncode=0, stdout=json.dumps([[
                {key: value for key, value in asset.items() if key != "raw"} for asset in assets.values()
            ]]), stderr="")
        assert argv[:5] == ["release", "upload", release["tag_name"], "--repo", "example/plugins"]
        assert "--clobber" not in argv and options == {"timeout": 120}
        for name in argv[5:]:
            path = Path(name)
            raw = path.read_bytes()
            assert path.name not in assets
            assets[path.name] = {
                "name": path.name, "id": len(assets) + 1, "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "state": "uploaded", "size": len(raw), "raw": raw,
            }
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def attest(path, **identity):
        admissions.append((path.name, path.read_bytes(), identity))

    def readback(repository, expected):
        assert repository == "example/plugins"
        for name, identifier, digest in expected:
            assert name.split("/")[0] == release["tag_name"]
            asset = assets[name.split("/")[1]]
            assert asset["id"] == identifier and hashlib.sha256(asset["raw"]).hexdigest() == digest
            downloads.append(asset["name"])

    monkeypatch.setattr(readiness, "_read_release", lambda *args: release)
    monkeypatch.setattr(publisher, "_tag_commit", lambda *args: identity["dev_sha"])
    monkeypatch.setattr(publisher, "_gh", gh)
    monkeypatch.setattr(publisher, "verify_attestation_subject", attest)
    monkeypatch.setattr(publisher, "verify_asset_downloads", readback)
    arguments = {
        "source_directory": subjects, "repository": "example/plugins", "tag": release["tag_name"],
        "index": index, "receipt": receipt, "qualification_digest": qualification_digest,
    }
    return directory, arguments, identity, assets, calls, admissions, downloads


def test_complete_source_uses_same_publisher_exact_subject_admission_and_independent_readback(publication):
    directory, arguments, identity, assets, calls, admissions, downloads = publication
    result = evidence.publish_complete_source(directory, **arguments)
    assert {key: result[key] for key in identity} == identity
    assert len(result["assets"]) == len(admissions) == len(downloads) == 2
    assert len([argv for argv, _ in calls if argv[0] == "release"]) == 1
    assert all(values == {
        "repository": "example/plugins", "signer_digest": identity["control_sha"],
        "source_digest": identity["control_sha"], "source_ref": "refs/heads/main",
    } for _name, _raw, values in admissions)
    before = {key: dict(value) for key, value in assets.items()}
    assert evidence.publish_complete_source(directory, **arguments) == result
    assert assets == before and len([argv for argv, _ in calls if argv[0] == "release"]) == 1
    assert readiness._assets(arguments["repository"], {
        "id": 9, "tag_name": arguments["tag"],
    }, readiness._expected(directory, arguments["index"])).keys() == {publisher.INDEX_NAME}


@pytest.mark.parametrize("change", ["archive", "manifest", "qualification", "index", "missing", "extra", "signer"])
def test_invalid_complete_source_refuses_before_any_release_write(publication, monkeypatch, change):
    directory, arguments, _identity, _assets, calls, admissions, downloads = publication
    subjects = arguments["source_directory"]
    if change == "archive":
        archive = next(subjects.glob("*.zip"))
        archive.write_bytes(archive.read_bytes() + b"x")
    elif change == "manifest":
        manifest = next(subjects.glob("*.json"))
        manifest.write_bytes(manifest.read_bytes() + b" ")
    elif change == "qualification":
        arguments["receipt"]["control_sha"] = "1" * 40
    elif change == "index":
        arguments["index"]["runtime_profiles"].append({"opaque": "changed"})
        (directory / publisher.INDEX_NAME).write_bytes(canonical_bytes(arguments["index"]))
    elif change == "missing":
        next(subjects.glob("*.zip")).unlink()
    elif change == "extra":
        (subjects / "other").write_bytes(b"x")
    else:
        def refuse(*args, **kwargs):
            raise ValueError("untrusted source subject signer")
        monkeypatch.setattr(publisher, "verify_attestation_subject", refuse)
    with pytest.raises(ValueError):
        evidence.publish_complete_source(directory, **arguments)
    assert not [argv for argv, _ in calls if argv[0] == "release"] and not downloads


@pytest.mark.parametrize("change", ["tag", "missing-code", "conflict", "readback", "full"])
def test_publication_refuses_wrong_owner_incomplete_or_conflicting_server_state(publication, monkeypatch, change):
    directory, arguments, identity, assets, calls, _admissions, _downloads = publication
    if change == "tag":
        monkeypatch.setattr(publisher, "_tag_commit", lambda *args: "1" * 40)
    elif change == "missing-code":
        assets.clear()
    elif change == "conflict":
        for path in arguments["source_directory"].iterdir():
            assets[path.name] = {
                "name": path.name, "id": len(assets) + 1, "digest": "sha256:" + "1" * 64,
                "state": "uploaded", "size": path.stat().st_size,
            }
    elif change == "full":
        for number in range(63):
            name = evidence.evidence_name(identity["dev_sha"], f"{number:040x}", "1" * 64)
            assets[name] = {"name": name, "id": number + 2, "digest": "sha256:" + "1" * 64,
                            "state": "uploaded", "size": 1}
    else:
        def refuse(*args):
            raise ValueError("independent raw readback failed")
        monkeypatch.setattr(publisher, "verify_asset_downloads", refuse)
    with pytest.raises((ValueError, publisher.PromotionArtifactError)):
        evidence.publish_complete_source(directory, **arguments)
    assert len([argv for argv, _ in calls if argv[0] == "release"]) == int(change == "readback")


@pytest.mark.parametrize("kind", ["complete-source", "source-manifest"])
@pytest.mark.parametrize("change", ["none", "digest", "source", "extension", "size", "boolean"])
def test_supplementary_source_metadata_is_bounded_data_not_executable_admission(kind, change):
    _files, identity = case()
    digest = "1" * 64
    from promotion_complete_source import source_asset_name

    asset = {
        "name": source_asset_name(kind, identity, digest), "id": 1, "state": "uploaded",
        "digest": "sha256:" + digest, "size": 1,
    }
    if change == "digest":
        asset["digest"] = "sha256:" + "2" * 64
    elif change == "source":
        asset["name"] = asset["name"].replace(identity["dev_sha"], "2" * 40)
    elif change == "extension":
        asset["name"] += ".exe"
    elif change == "size":
        asset["size"] = (512 << 20) + 1 if kind == "complete-source" else (128 << 20) + 1
    elif change == "boolean":
        asset["id"] = True
    assert evidence.supplementary_evidence_asset(asset, identity["dev_sha"]) is (change == "none")
