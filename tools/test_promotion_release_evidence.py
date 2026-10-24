"""Bounded publisher/readback and unchanged frozen-code reuse contract."""
from __future__ import annotations

import hashlib
import json

import pytest

import promotion_artifact_readiness as readiness
import promotion_release_evidence as evidence
import publish_python_artifacts as publisher
import reuse_promotion_artifacts as reuse
from promotion_unit_tests.test_release_evidence import CANDIDATE, archive_case
from test_reuse_promotion_artifacts import frozen as _source_release_fixture

source_release = _source_release_fixture


@pytest.mark.parametrize("failure", ["none", "upload", "download-digest", "tag", "missing-code"])
def test_existing_release_owner_retains_exact_evidence_and_reuses_only_code(source_release, failure):
    directory, restored, index, server, _attestations, kwargs = source_release
    files, receipt, digest, _ = archive_case()
    dev = kwargs["dev_head"]
    receipt["dev_sha"] = dev
    receipt_raw = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(receipt_raw).hexdigest()
    packet = directory.parent / "unit-packet"
    packet.mkdir()
    for name, raw in files.items():
        (packet / name).write_bytes(raw)
    server.failure = None if failure == "none" else failure
    if failure == "tag":
        server.target = "f" * 40
    elif failure == "missing-code":
        del server.assets[publisher.INDEX_NAME]
    arguments = dict(
        packet_directory=packet, repository=kwargs["repository"],
        tag=f"python-artifacts-{dev}", dev_sha=dev, candidate_sha=CANDIDATE,
        index=index, receipt=receipt, qualification_digest=digest,
    )
    before = list(server.writes)
    if failure != "none":
        with pytest.raises((ValueError, publisher.PromotionArtifactError)):
            evidence.publish_evidence(directory, **arguments)
        return
    result = evidence.publish_evidence(directory, **arguments)
    assert result["filename"] in server.assets
    assert result["qualification_digest"] == digest and result["packet_digest"] == receipt["packet_digest"]
    assert server.writes[:-1] == before
    assert server.writes[-1][:2] == ["release", "upload"]
    assert "--clobber" not in server.writes[-1]
    writes = list(server.writes)
    assert evidence.publish_evidence(directory, **arguments) == result
    assert server.writes == writes
    assert readiness.verify_ready_artifacts(
        directory, repository=kwargs["repository"], candidate_commit=CANDIDATE,
        dev_head=dev, repo=kwargs["repo"],
    ) == f"python-artifacts-{dev}"
    assert reuse.reuse_artifacts(restored, **kwargs) == index
    assert {path.name for path in restored.iterdir()} == {path.name for path in directory.iterdir()}
    downloads = [options for argv, options in server.calls if options.get("binary")]
    assert downloads and all(options.get("verification") for options in downloads)


@pytest.mark.parametrize("count,existing", [(63, False), (64, False), (63, True), (64, True)])
def test_new_evidence_cannot_overfill_release_and_existing_evidence_reuses_at_capacity(
    source_release, count, existing,
):
    directory, restored, index, server, _attestations, kwargs = source_release
    files, receipt, _digest, _raw = archive_case()
    dev = kwargs["dev_head"]
    receipt["dev_sha"] = dev

    def retain_fixture(candidate):
        value = {**receipt, "candidate_sha": candidate}
        receipt_raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        digest = hashlib.sha256(receipt_raw).hexdigest()
        raw = evidence.evidence_archive(
            files, value, dev_sha=dev, candidate_sha=candidate, qualification_digest=digest,
        )
        archive_digest = hashlib.sha256(raw).hexdigest()
        name = evidence.evidence_name(dev, candidate, archive_digest)
        server.assets[name] = {
            "id": max(asset["id"] for asset in server.assets.values()) + 1,
            "name": name, "state": "uploaded", "size": len(raw),
            "digest": "sha256:" + archive_digest, "payload": raw,
        }

    if existing:
        retain_fixture(CANDIDATE)
    for number in range(count - int(existing)):
        retain_fixture(f"{number:040x}")
    packet = directory.parent / "unit-packet"
    packet.mkdir()
    for name, raw in files.items():
        (packet / name).write_bytes(raw)
    receipt_raw = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    before, writes = dict(server.assets), list(server.writes)
    arguments = dict(
        packet_directory=packet, repository=kwargs["repository"],
        tag=f"python-artifacts-{dev}", dev_sha=dev, candidate_sha=CANDIDATE,
        index=index, receipt=receipt, qualification_digest=hashlib.sha256(receipt_raw).hexdigest(),
    )
    if count == 64 and not existing:
        with pytest.raises(publisher.PromotionArtifactError, match="capacity"):
            evidence.publish_evidence(directory, **arguments)
        assert server.assets == before and server.writes == writes
        return
    result = evidence.publish_evidence(directory, **arguments)
    assert result["filename"] in server.assets
    assert len(server.assets) == len(before) + int(not existing)
    assert len(server.writes) == len(writes) + int(not existing)
    assert reuse.reuse_artifacts(restored, **kwargs) == index
    assert {path.name for path in restored.iterdir()} == {path.name for path in directory.iterdir()}
