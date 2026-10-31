"""Existing promotion authentication, complete bindings and native byte custody."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
import runtime_profile_release as authority
from runtime_profile import ProfileError, validate_profile
from runtime_profile_cases import _record, _wheel
from test_promotion_python_artifacts import DEV, MAIN, _index, _validate, _write_index


def _extension(record):
    return {"schema": authority.SCHEMA, "version": 1, "profiles": [record]}


@pytest.fixture
def release(tmp_path):
    directory = tmp_path / "release"
    index = _index(directory)
    index["schema_version"] = 2
    del index["main_before"]
    index["admission"] = dict(
        signer_digest=MAIN, source_digest=MAIN, source_ref="refs/heads/main",
    )
    entry = index["artifacts"][0]
    with zipfile.ZipFile(directory / entry["filename"]) as bundle:
        raw = bundle.read("manifest.json")
        manifest = json.loads(raw)
    first = manifest["wheels"][0]
    record = _record()
    record.update(
        release={"identity": "demo:1.0", "sha256": manifest["payload_hash"]},
        component="demo", roots=["demo==1.0"], mode="artifact",
        artifact={
            "artifact_id": manifest["artifact_id"],
            "manifest_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        },
        approval_source=authority.APPROVAL_SOURCE,
        wheels=[_wheel("demo") | {"sha256": first["sha256"]}],
    )
    index["runtime_profiles"] = _extension(record)
    _write_index(directory, index)
    return directory, index, record


def _select(fixture, **overrides):
    directory, _, record = fixture
    arguments = dict(
        repository="example/plugins", signer_digest=MAIN, source_digest=MAIN,
        source_ref="refs/heads/main", release=record["release"], artifact=record["artifact"],
        target=record["target"], component=record["component"], roots=record["roots"],
        max_subject_bytes=1 << 20,
    )
    return authority.select_release_profile(
        directory / artifacts.INDEX_NAME, **(arguments | overrides),
    )


def _verification(monkeypatch, release, *, failure=None, on_verify=None):
    original = release[0] / artifacts.INDEX_NAME
    data = original.read_bytes()
    calls = []

    def verify(argv, **options):
        subject = Path(argv[2])
        calls.append(subject)
        assert subject != original and subject.name == artifacts.INDEX_NAME
        assert subject.read_bytes() == data
        assert argv == [
            "attestation", "verify", str(subject), "--repo", "example/plugins",
            "--signer-workflow", "example/plugins/.github/workflows/validate-and-promote.yml",
            "--signer-digest", MAIN, "--source-digest", MAIN,
            "--source-ref", "refs/heads/main", "--deny-self-hosted-runners",
        ]
        assert options == {"verification": True}
        if on_verify:
            on_verify(subject)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 120)
        if failure == "cancel":
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(argv, bool(failure), "", "wrong producer" if failure else "")

    monkeypatch.setattr(publisher, "_gh", verify)
    return calls


def test_pre_signing_validation_binds_complete_record_to_real_bundle(release):
    directory, index, _ = release
    assert _validate(directory) == index


def test_unselected_optional_first_party_library_need_not_enter_profile(release):
    directory, index, record = release
    with zipfile.ZipFile(directory / index["artifacts"][0]["filename"]) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    manifest["wheels"].append({
        "name": "optional-library", "filename": "optional_library-1.0-py3-none-any.whl",
        "sha256": "sha256:" + "7" * 64, "role": "vendored-lib",
    })
    bindings = {manifest["artifact_id"]: (manifest, record["artifact"]["manifest_sha256"])}
    authority.validate_release_bindings(index, bindings)
    manifest["wheels"][-1]["name"] = "demo"
    with pytest.raises(ProfileError, match="changes a first-party wheel"):
        authority.validate_release_bindings(index, bindings)


@pytest.mark.parametrize("mutate", [
    lambda r: r["release"].update(identity="other:1.0"),
    lambda r: r["release"].update(sha256="sha256:" + "3" * 64),
    lambda r: r.update(component="other"),
    lambda r: r["artifact"].update(manifest_sha256="sha256:" + "3" * 64),
    lambda r: r["artifact"].update(artifact_id="sha256:" + "3" * 64),
    lambda r: r["wheels"][0].update(sha256="sha256:" + "3" * 64),
    lambda r: r.update(wheels=[_wheel("other")], roots=["other==1.0"]),
    lambda r: r.update(approval_source="self-asserted"),
    lambda r: r.update(mode="source", artifact=None),
])
def test_pre_signing_rejects_wrong_source_manifest_component_or_wheel(release, mutate):
    directory, index, record = release
    mutate(record)
    _write_index(directory, index)
    with pytest.raises(artifacts.PromotionArtifactError, match="release runtime profiles"):
        _validate(directory)


@pytest.mark.parametrize("extension", [
    None, {}, {"schema": authority.SCHEMA, "version": True, "profiles": []},
    {"schema": authority.SCHEMA, "version": 2, "profiles": []},
    {"schema": authority.SCHEMA, "version": 1, "profiles": []},
])
def test_invalid_extension_refuses_before_signing(release, extension):
    directory, index, _ = release
    index["runtime_profiles"] = extension
    _write_index(directory, index)
    with pytest.raises(artifacts.PromotionArtifactError, match="release runtime profiles"):
        _validate(directory)


def test_legacy_index_without_profiles_is_unchanged(release):
    directory, index, _ = release
    del index["runtime_profiles"]
    _write_index(directory, index)
    assert _validate(directory) == index
    assert authority.release_profiles(index) == ()


def test_actual_verifier_pins_producer_and_returns_immutable_whole_profile(release, monkeypatch):
    calls = _verification(monkeypatch, release)
    selected = _select(release)
    assert selected.selected.profile == validate_profile(release[2])
    assert selected.selected.approval_source == authority.APPROVAL_SOURCE
    assert selected.repository == "example/plugins"
    assert selected.signer_digest == selected.source_digest == MAIN
    assert selected.source_ref == "refs/heads/main"
    assert selected.subject_sha256 == (
        "sha256:" + hashlib.sha256((release[0] / artifacts.INDEX_NAME).read_bytes()).hexdigest()
    )
    release[2]["wheels"][0]["sha256"] = "sha256:" + "9" * 64
    assert selected.selected.profile.record()["wheels"][0] != release[2]["wheels"][0]
    assert len(calls) == 1 and not calls[0].parent.parent.exists()


@pytest.mark.parametrize("failure", ["signature", "timeout", "cancel"])
def test_authentication_failure_or_cancellation_never_selects_and_cleans(release, monkeypatch, failure):
    calls = _verification(monkeypatch, release, failure=failure)
    error = {
        "signature": artifacts.PromotionArtifactError,
        "timeout": subprocess.TimeoutExpired, "cancel": KeyboardInterrupt,
    }[failure]
    with pytest.raises(error):
        _select(release)
    assert len(calls) == 1 and not calls[0].parent.parent.exists()


def test_replacing_original_during_verification_cannot_replace_approved_bytes(release, monkeypatch):
    original = release[0] / artifacts.INDEX_NAME
    calls = _verification(
        monkeypatch, release, on_verify=lambda _subject: original.write_bytes(b"untrusted replacement"),
    )
    assert _select(release).selected.profile == validate_profile(release[2])
    assert not calls[0].exists()


def test_private_subject_cannot_change_between_authentication_and_selection(release, monkeypatch):
    def mutate(subject):
        subject.chmod(0o600)
        if os.name == "nt":
            with pytest.raises(OSError):
                subject.write_bytes(b"substitution")
        else:
            subject.write_bytes(b"substitution")

    calls = _verification(monkeypatch, release, on_verify=mutate)
    if os.name == "nt":
        assert _select(release).selected.profile == validate_profile(release[2])
    else:
        with pytest.raises(ProfileError, match="retained or decoded|subject changed"):
            _select(release)
    assert not calls[0].exists()


@pytest.mark.parametrize("field,value", [
    ("source_ref", "refs/heads/dev"), ("source_digest", DEV),
])
def test_candidate_or_ambient_source_cannot_choose_approval_producer(release, monkeypatch, field, value):
    calls = _verification(monkeypatch, release)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    with pytest.raises(ProfileError, match="pinned trusted Main"):
        _select(release, **{field: value})
    assert not calls


@pytest.mark.parametrize("field,value", [
    ("schema_version", 1), ("admission", {}),
    ("admission", {"signer_digest": DEV, "source_digest": DEV, "source_ref": "refs/heads/main"}),
])
def test_attested_index_must_have_exact_independent_source_admission(release, monkeypatch, field, value):
    release[1][field] = value
    _write_index(release[0], release[1])
    _verification(monkeypatch, release)
    with pytest.raises(ProfileError, match="another source admission"):
        _select(release)


@pytest.mark.parametrize("field", ["release", "artifact", "target", "component", "roots"])
def test_whole_signed_record_must_match_independent_installation_selection(release, monkeypatch, field):
    _verification(monkeypatch, release)
    value = deepcopy(release[2][field])
    if field == "release":
        value["sha256"] = "sha256:" + "9" * 64
    elif field == "artifact":
        value["manifest_sha256"] = "sha256:" + "9" * 64
    elif field == "target":
        value["environment"]["platform_version"] = "different"
    elif field == "component":
        value = "other"
    else:
        value = ["other==1.0"]
    with pytest.raises(ProfileError, match="unavailable"):
        _select(release, **{field: value})


def test_duplicate_records_and_unprofiled_migration_refuse(release, monkeypatch):
    _verification(monkeypatch, release)
    with pytest.raises(ProfileError, match="migration"):
        _select(release, existing_installation=True)
    assert _select(release, existing_installation=True, backend="cpu")
    release[1]["runtime_profiles"]["profiles"].append(deepcopy(release[2]))
    _write_index(release[0], release[1])
    _verification(monkeypatch, release)
    with pytest.raises(ProfileError, match="duplicate release runtime profile"):
        _select(release)


@pytest.mark.parametrize("identity", [None, {}, [], "not-a-digest"])
def test_malformed_artifact_id_is_a_structured_refusal(release, identity):
    release[1]["artifacts"][0]["artifact_id"] = identity
    with pytest.raises(ProfileError, match="artifact identity"):
        authority.release_profiles(release[1])


@pytest.mark.parametrize("limit", [True, 0, -1, 1])
def test_subject_budget_refuses_before_authentication(release, monkeypatch, limit):
    calls = _verification(monkeypatch, release)
    with pytest.raises(ProfileError):
        _select(release, max_subject_bytes=limit)
    assert not calls


def test_subject_budget_is_inclusive_and_duplicate_json_is_refused(release, monkeypatch):
    original = release[0] / artifacts.INDEX_NAME
    _verification(monkeypatch, release)
    assert _select(release, max_subject_bytes=len(original.read_bytes()))
    original.write_text('{"schema":1,"schema":2}', encoding="utf-8")
    _verification(monkeypatch, release)
    with pytest.raises(ProfileError, match="duplicate JSON property"):
        _select(release)


def test_linked_subject_is_refused_before_verification(release, monkeypatch, tmp_path):
    original = release[0] / artifacts.INDEX_NAME
    original.replace(tmp_path / "original.json")
    try:
        original.symlink_to(tmp_path / "original.json")
    except OSError:
        pytest.skip("symbolic links unavailable")
    calls = _verification(monkeypatch, release)
    with pytest.raises(ProfileError, match="retained or decoded"):
        _select(release)
    assert not calls
