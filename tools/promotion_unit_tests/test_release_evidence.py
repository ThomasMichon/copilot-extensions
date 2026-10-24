"""Data-only release evidence, immutable metadata and exact file-bound controls."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

import candidate_qualification_receipt as receipt_contract
import promotion_artifact_readiness as readiness
import promotion_release_evidence as evidence
import promotion_unit_packet as packet
import publish_python_artifacts as publisher

DEV, CANDIDATE = "a" * 40, "b" * 40


def archive_case():
    files = {name: b"{}\n" for name in receipt_contract.PACKET_FILES}
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    receipt = {
        "profile": "candidate-units-v3", "dev_sha": DEV, "candidate_sha": CANDIDATE,
        "packet_files": hashes,
        "packet_digest": receipt_contract.canonical_digest({
            "manifest_file_sha256": hashes["unit-manifest.json"],
            "proof_sha256": hashes["unit-proof.json"], "coverage_sha256": hashes["coverage.json"],
        }),
    }
    raw = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return files, receipt, hashlib.sha256(raw).hexdigest(), raw


def metadata(candidate=CANDIDATE):
    digest = "c" * 64
    return {
        "id": 2, "name": evidence.evidence_name(DEV, candidate, digest),
        "digest": "sha256:" + digest, "state": "uploaded", "size": 123,
    }


def test_release_archive_retains_exact_packet_and_qualification_without_source_files():
    files, receipt, digest, receipt_raw = archive_case()
    arguments = dict(dev_sha=DEV, candidate_sha=CANDIDATE, qualification_digest=digest)
    raw = evidence.evidence_archive(files, receipt, **arguments)
    assert evidence.evidence_archive(files, receipt, **arguments) == raw
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert set(archive.namelist()) == {*receipt_contract.PACKET_FILES, "qualification.json"}
        assert {name: archive.read(name) for name in receipt_contract.PACKET_FILES} == files
        assert archive.read("qualification.json") == receipt_raw
        assert all(member.date_time == (1980, 1, 1, 0, 0, 0) for member in archive.infolist())
    name = evidence.evidence_name(DEV, CANDIDATE, hashlib.sha256(raw).hexdigest())
    assert DEV in name and CANDIDATE in name and len(name) <= 255


@pytest.mark.parametrize("change", ["missing-file", "extra-file", "packet-byte", "qualification", "source", "candidate"])
def test_release_archive_refuses_any_departure_from_verified_qualification(change):
    files, receipt, digest, _ = archive_case()
    if change == "missing-file":
        del files["coverage.json"]
    elif change == "extra-file":
        files["source.py"] = b"pass\n"
    elif change == "packet-byte":
        files["coverage.json"] += b" "
    elif change == "qualification":
        receipt["packet_digest"] = "f" * 64
    elif change == "source":
        receipt["dev_sha"] = "f" * 40
    else:
        receipt["candidate_sha"] = "f" * 40
    with pytest.raises(ValueError):
        evidence.evidence_archive(
            files, receipt, dev_sha=DEV, candidate_sha=CANDIDATE, qualification_digest=digest,
        )


@pytest.mark.parametrize("change", ["source", "name", "digest", "size-bool", "size-over", "id-bool", "state"])
def test_only_bounded_hash_named_same_source_auxiliary_metadata_is_admitted(change):
    asset = metadata()
    assert evidence.supplementary_evidence_asset(asset, DEV)
    if change == "source":
        asset["name"] = asset["name"].replace(DEV, "f" * 40)
    elif change == "name":
        asset["name"] = "coverage.json"
    elif change == "digest":
        asset["digest"] = "sha256:" + "f" * 64
    elif change == "size-bool":
        asset["size"] = True
    elif change == "size-over":
        asset["size"] = evidence.MAX_EVIDENCE_BYTES + 1
    elif change == "id-bool":
        asset["id"] = True
    else:
        asset["state"] = "new"
    assert not evidence.supplementary_evidence_asset(asset, DEV)


@pytest.mark.parametrize("change", ["none", "duplicate", "foreign-source", "bad-digest", "unknown", "too-many"])
def test_readiness_keeps_the_exact_code_inventory_with_only_defined_auxiliary_evidence(monkeypatch, change):
    digest = "d" * 64
    index = {"name": "index.json", "id": 1, "digest": "sha256:" + digest, "state": "uploaded"}
    extra = metadata()
    assets = [index, extra]
    if change == "duplicate":
        assets.append(copy.deepcopy(extra))
    elif change == "foreign-source":
        extra["name"] = extra["name"].replace(DEV, "f" * 40)
    elif change == "bad-digest":
        extra["digest"] = "sha256:" + "f" * 64
    elif change == "unknown":
        extra["name"] = "other.zip"
    elif change == "too-many":
        assets = [index, *[metadata(f"{number:040x}") for number in range(65)]]

    def read(argv, **kwargs):
        assert kwargs == {"verification": True, "timeout": 120}
        return SimpleNamespace(returncode=0, stdout=json.dumps([assets]), stderr="")

    monkeypatch.setattr(publisher, "_gh", read)
    arguments = ("example/plugins", {"id": 11, "tag_name": f"python-artifacts-{DEV}"}, {"index.json": digest})
    if change == "none":
        assert readiness._assets(*arguments) == {"index.json": index}
    else:
        with pytest.raises(publisher.PromotionArtifactError):
            readiness._assets(*arguments)


def test_actual_file_boundary_is_shared_by_local_reads_and_hosted_archive_admission(tmp_path):
    limit = 128 * 1024 * 1024
    assert packet.MAX_PACKET_FILE_BYTES == receipt_contract.MAX_PACKET_FILE_BYTES == limit
    prefix, suffix = b'{"padding":"', b'"}'
    raw = prefix + b"x" * (limit - len(prefix) - len(suffix)) + suffix
    path = tmp_path / "coverage.json"
    path.write_bytes(raw)
    observed, value = packet._read_file(tmp_path, "coverage.json")
    assert observed == raw and len(value["padding"]) == limit - len(prefix) - len(suffix)
    del observed, value
    assert receipt_contract.regular_bytes(tmp_path, "coverage.json") == raw
    files = {name: raw if name == "coverage.json" else b"{}" for name in receipt_contract.PACKET_FILES}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as output:
        for name, content in files.items():
            output.writestr(name, content)
    archive_raw = archive.getvalue()
    receipt_contract.verify_archive(
        archive_raw, expected_digest="sha256:" + hashlib.sha256(archive_raw).hexdigest(),
        expected_size=len(archive_raw),
        packet_files={name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    )
    path.write_bytes(raw + b"x")
    with pytest.raises(ValueError, match="bounded"):
        packet._read_file(tmp_path, "coverage.json")
    with pytest.raises(ValueError, match="bound"):
        receipt_contract.regular_bytes(tmp_path, "coverage.json")
    oversized = io.BytesIO()
    with zipfile.ZipFile(oversized, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as output:
        for name, content in files.items():
            output.writestr(name, content + b"x" if name == "coverage.json" else content)
    oversized_raw = oversized.getvalue()
    with pytest.raises(ValueError, match="member inventory"):
        receipt_contract.verify_archive(
            oversized_raw, expected_digest="sha256:" + hashlib.sha256(oversized_raw).hexdigest(),
            expected_size=len(oversized_raw),
            packet_files={name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
        )


@pytest.mark.parametrize("count,existing", [(63, False), (64, False), (63, True), (64, True)])
def test_evidence_capacity_preflight_distinguishes_new_upload_from_existing_readback(
    monkeypatch, count, existing,
):
    digest, name = "d" * 64, metadata()["name"]
    expected = {"index.json": digest, name: "c" * 64}
    index = {"name": "index.json", "id": 1, "digest": "sha256:" + digest, "state": "uploaded"}
    assets = [index, *[metadata(f"{number:040x}") for number in range(count - int(existing))]]
    if existing:
        assets.append(metadata())
    before = copy.deepcopy(assets)
    calls = []

    def read(argv, **kwargs):
        assert argv[0] == "api" and kwargs == {"verification": True, "timeout": 120}
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps([assets]), stderr="")

    monkeypatch.setattr(publisher, "_gh", read)
    arguments = ("example/plugins", {"id": 11, "tag_name": f"python-artifacts-{DEV}"}, expected)
    if count == 64 and not existing:
        with pytest.raises(publisher.PromotionArtifactError, match="capacity"):
            readiness._assets(*arguments, pending_evidence=name)
    else:
        result = readiness._assets(*arguments, pending_evidence=name)
        assert (name in result) is existing
    assert len(calls) == 1 and assets == before
