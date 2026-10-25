"""Early original-source retention uses only the existing immutable Dev publisher."""
from __future__ import annotations

import hashlib

import pytest

import promote_release as promotion
import promotion_artifact_readiness as readiness
import promotion_build_source as build
import promotion_release_evidence as evidence
import promotion_source_archive as source_io
import publish_python_artifacts as publisher
from promotion_complete_source import canonical_bytes
from promotion_unit_tests import test_complete_source_publication
from promotion_unit_tests.test_build_source import build_case

publication = test_complete_source_publication.publication


@pytest.fixture
def build_publication(publication, tmp_path, monkeypatch):
    directory, arguments, _identity, assets, calls, admissions, downloads = publication
    files, index, index_raw, identity = build_case()
    index["artifacts"] = []
    index_raw = canonical_bytes(index)
    (directory / publisher.INDEX_NAME).write_bytes(index_raw)
    assets[publisher.INDEX_NAME].update(
        raw=index_raw, size=len(index_raw), digest="sha256:" + hashlib.sha256(index_raw).hexdigest(),
    )
    subjects = tmp_path / "build"
    monkeypatch.setattr(source_io, "_git", lambda *args, **kwargs: identity["tree"].encode())
    monkeypatch.setattr(source_io, "read_tree", lambda *args, **kwargs: files)
    monkeypatch.setattr(promotion, "_tree_excluding_state", lambda *args, **kwargs: identity["content_tree"])
    manifest = source_io.prepare_build_source(
        subjects, repo=tmp_path, index_raw=index_raw, candidate_sha=identity["candidate_sha"],
    )
    arguments = {
        "source_directory": subjects, "repository": arguments["repository"], "tag": arguments["tag"],
        "index": index, "candidate_sha": identity["candidate_sha"], "tree": identity["tree"],
        "signer_digest": identity["control_sha"],
    }
    return directory, arguments, manifest, assets, calls, admissions, downloads


def test_early_build_publication_attests_two_subjects_and_raw_reads_them_without_qualification(build_publication):
    directory, arguments, manifest, assets, calls, admissions, downloads = build_publication
    identity = {key: manifest[key] for key in build.IDENTITY_FIELDS}
    assert len(source_io.read_build_assets(arguments["source_directory"], identity=identity)) == 2
    result = evidence.publish_build_source(directory, **arguments)
    assert {key: result[key] for key in identity} == identity
    assert "qualification_digest" not in result and "qualification" not in result
    assert len(result["assets"]) == len(admissions) == len(downloads) == 2
    assert all(values == {
        "repository": arguments["repository"], "signer_digest": identity["control_sha"],
        "source_digest": identity["control_sha"], "source_ref": "refs/heads/main",
    } for _name, _raw, values in admissions)
    before = {key: dict(value) for key, value in assets.items()}
    assert evidence.publish_build_source(directory, **arguments) == result
    assert assets == before and len([argv for argv, _ in calls if argv[0] == "release"]) == 1
    assert readiness._assets(arguments["repository"], {
        "id": 9, "tag_name": arguments["tag"],
    }, readiness._expected(directory, arguments["index"])).keys() == {publisher.INDEX_NAME}


@pytest.mark.parametrize("change", ["archive", "manifest", "index", "signer", "candidate", "tree", "missing", "extra", "attestation"])
def test_invalid_build_subjects_refuse_before_any_supplementary_release_write(build_publication, monkeypatch, change):
    directory, arguments, _manifest, _assets, calls, _admissions, downloads = build_publication
    subjects = arguments["source_directory"]
    if change in ("archive", "manifest"):
        path = next(subjects.glob("*.zip" if change == "archive" else "*.json"))
        path.write_bytes(path.read_bytes() + b"x")
    elif change == "index":
        arguments["index"]["runtime_profiles"].append({"opaque": "changed"})
        (directory / publisher.INDEX_NAME).write_bytes(canonical_bytes(arguments["index"]))
    elif change in ("signer", "candidate", "tree"):
        field = {"signer": "signer_digest", "candidate": "candidate_sha", "tree": "tree"}[change]
        arguments[field] = "1" * 40
    elif change == "missing":
        next(subjects.glob("*.zip")).unlink()
    elif change == "extra":
        (subjects / "other").write_bytes(b"x")
    else:
        def refuse(*args, **kwargs):
            raise ValueError("untrusted original-source signer")
        monkeypatch.setattr(publisher, "verify_attestation_subject", refuse)
    with pytest.raises(ValueError):
        evidence.publish_build_source(directory, **arguments)
    assert not [argv for argv, _ in calls if argv[0] == "release"] and not downloads


@pytest.mark.parametrize("change", ["tag", "readback", "full"])
def test_build_publication_preserves_owner_readback_and_shared_capacity(build_publication, monkeypatch, change):
    directory, arguments, manifest, assets, calls, _admissions, _downloads = build_publication
    if change == "tag":
        monkeypatch.setattr(publisher, "_tag_commit", lambda *args: "1" * 40)
    elif change == "full":
        for number in range(63):
            name = evidence.evidence_name(manifest["dev_sha"], f"{number:040x}", "1" * 64)
            assets[name] = {"name": name, "id": number + 2, "digest": "sha256:" + "1" * 64,
                            "state": "uploaded", "size": 1}
    else:
        def refuse(*args):
            raise ValueError("independent raw readback failed")
        monkeypatch.setattr(publisher, "verify_asset_downloads", refuse)
    with pytest.raises((ValueError, publisher.PromotionArtifactError)):
        evidence.publish_build_source(directory, **arguments)
    assert len([argv for argv, _ in calls if argv[0] == "release"]) == int(change == "readback")


@pytest.mark.parametrize("kind", ["build-source", "build-manifest"])
@pytest.mark.parametrize("change", ["none", "digest", "source", "extension", "size", "boolean"])
def test_build_auxiliary_metadata_retains_existing_bounds(kind, change):
    _files, _index, _raw, identity = build_case()
    asset = {
        "name": build.asset_name(kind, identity, "1" * 64), "id": 1, "state": "uploaded",
        "digest": "sha256:" + "1" * 64, "size": 1,
    }
    if change == "digest":
        asset["digest"] = "sha256:" + "2" * 64
    elif change == "source":
        asset["name"] = asset["name"].replace(identity["dev_sha"], "2" * 40)
    elif change == "extension":
        asset["name"] += ".exe"
    elif change == "size":
        asset["size"] = (512 << 20) + 1 if kind == "build-source" else (128 << 20) + 1
    elif change == "boolean":
        asset["id"] = True
    assert evidence.supplementary_evidence_asset(asset, identity["dev_sha"]) is (change == "none")


def test_build_preparation_refuses_index_tree_drift_before_creating_subject_directory(tmp_path, monkeypatch):
    _files, _index, index_raw, identity = build_case()
    monkeypatch.setattr(source_io, "_git", lambda *args, **kwargs: identity["tree"].encode())
    monkeypatch.setattr(promotion, "_tree_excluding_state", lambda *args, **kwargs: "1" * 40)
    directory = tmp_path / "subjects"
    with pytest.raises(ValueError, match="signed source index"):
        source_io.prepare_build_source(
            directory, repo=tmp_path, index_raw=index_raw, candidate_sha=identity["candidate_sha"],
        )
    assert not directory.exists()


def test_prepare_cli_retains_original_source_after_code_readiness_without_issuing_landing_receipt(
    build_publication, monkeypatch, tmp_path, capsys,
):
    directory, arguments, manifest, _assets, _calls, admissions, downloads = build_publication
    events = []
    monkeypatch.setenv("ARTIFACT_VERIFY_TOKEN", "synthetic-consumer-authority")
    def attest(*args, **kwargs):
        events.append("index-admitted")
        return arguments["index"]
    def prepare(*args, **kwargs):
        events.append("code-ready")
        assert kwargs["candidate_commit"] == manifest["candidate_sha"]
        return arguments["tag"]
    monkeypatch.setattr(publisher, "verify_attestations", attest)
    monkeypatch.setattr(readiness, "prepare_artifacts", prepare)
    monkeypatch.setattr(promotion, "_git", lambda *args, **kwargs: manifest["tree"])
    output = tmp_path / "github-output"
    assert publisher.main([
        "prepare", "--directory", str(directory), "--repository", arguments["repository"],
        "--signer-digest", manifest["control_sha"], "--source-digest", manifest["control_sha"],
        "--source-ref", "refs/heads/main", "--candidate-commit", manifest["candidate_sha"],
        "--dev-head", manifest["dev_sha"], "--build-source-dir", str(arguments["source_directory"]),
        "--github-output", str(output),
    ]) == 0
    assert events == ["index-admitted", "code-ready"]
    assert len(admissions) == len(downloads) == 2 and not output.exists()
    stdout = capsys.readouterr().out
    assert "build-source-retained:" in stdout and "artifact-published:" not in stdout


@pytest.mark.parametrize("change", ["no-consumer", "untrusted-ref", "different-source"])
def test_build_prepare_cli_refuses_untrusted_authority_before_publisher_effects(tmp_path, monkeypatch, change):
    _files, _index, _raw, identity = build_case()
    monkeypatch.setenv("ARTIFACT_VERIFY_TOKEN", "synthetic-consumer-authority")
    if change == "no-consumer":
        monkeypatch.delenv("ARTIFACT_VERIFY_TOKEN")
    def refuse(*args, **kwargs):
        raise AssertionError("untrusted invocation reached the publisher")
    monkeypatch.setattr(publisher, "verify_attestations", refuse)
    assert publisher.main([
        "prepare", "--directory", str(tmp_path), "--repository", "example/plugins",
        "--signer-digest", identity["control_sha"],
        "--source-digest", "1" * 40 if change == "different-source" else identity["control_sha"],
        "--source-ref", "refs/heads/dev" if change == "untrusted-ref" else "refs/heads/main",
        "--candidate-commit", identity["candidate_sha"], "--dev-head", identity["dev_sha"],
        "--build-source-dir", str(tmp_path / "subjects"),
    ]) == 1
