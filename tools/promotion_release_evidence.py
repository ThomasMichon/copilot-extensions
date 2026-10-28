"""Retain qualified unit evidence on its existing Dev-owned artifact release."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile

from candidate_qualification_receipt import (
    MAX_ARCHIVE_BYTES, PACKET_FILES, canonical_digest, regular_bytes,
)
from promotion_unit_contract import MAX_PACKET_FILE_BYTES, MAX_UNIT_EVIDENCE_FILE_BYTES

_SHA = re.compile(r"[0-9a-f]{40}")
MAX_EVIDENCE_BYTES = MAX_ARCHIVE_BYTES + MAX_PACKET_FILE_BYTES
MAX_EVIDENCE_ASSETS = 64
EVIDENCE_PREFIXES = ("promotion-unit-evidence-", "promotion-complete-source-", "promotion-source-manifest-")


def evidence_name(dev_sha: str, candidate_sha: str, digest: str) -> str:
    if (
        not all(isinstance(value, str) and _SHA.fullmatch(value) for value in (dev_sha, candidate_sha))
        or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ValueError("invalid release evidence identity")
    return f"promotion-unit-evidence-{dev_sha}-{candidate_sha}-sha256-{digest}.zip"


def supplementary_evidence_asset(asset: dict, dev_sha: str) -> bool:
    """Admit only bounded auxiliary data; this never qualifies executable artifacts."""
    name = asset.get("name")
    match = re.fullmatch(
        rf"promotion-(unit-evidence|complete-source|source-manifest)-{re.escape(dev_sha)}"
        r"-([0-9a-f]{40})-sha256-([0-9a-f]{64})\.(zip|json)",
        name,
    ) if isinstance(name, str) and isinstance(dev_sha, str) and _SHA.fullmatch(dev_sha) else None
    limits = {"unit-evidence": MAX_EVIDENCE_BYTES, "complete-source": 512 << 20,
              "source-manifest": MAX_PACKET_FILE_BYTES}
    limit = limits[match[1]] if match else 0
    return bool(
        match and match[4] == ("json" if match[1] == "source-manifest" else "zip")
        and asset.get("digest") == "sha256:" + match[3]
        and asset.get("state") == "uploaded"
        and type(asset.get("id")) is int and asset["id"] > 0
        and type(asset.get("size")) is int and 0 < asset["size"] <= limit
    )


def evidence_archive(
    files: dict[str, bytes], receipt: dict, *, dev_sha: str, candidate_sha: str,
    qualification_digest: str,
) -> bytes:
    evidence_name(dev_sha, candidate_sha, qualification_digest)
    if (
        not isinstance(files, dict) or set(files) != set(PACKET_FILES)
        or any(not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_UNIT_EVIDENCE_FILE_BYTES
               for raw in files.values())
    ):
        raise ValueError("release evidence must retain exactly the bounded unit packet")
    if not isinstance(receipt, dict):
        raise ValueError("release evidence qualification must be an object")
    receipt_raw = (
        json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    packet_digest = canonical_digest({
        "manifest_file_sha256": hashes["unit-manifest.json"],
        "proof_sha256": hashes["unit-proof.json"], "coverage_sha256": hashes["coverage.json"],
    })
    if (
        not 0 < len(receipt_raw) <= MAX_PACKET_FILE_BYTES
        or hashlib.sha256(receipt_raw).hexdigest() != qualification_digest
        or receipt.get("profile") not in ("source-units-v2", "candidate-units-v3")
        or receipt.get("dev_sha") != dev_sha or receipt.get("candidate_sha") != candidate_sha
        or receipt.get("packet_files") != hashes or receipt.get("packet_digest") != packet_digest
    ):
        raise ValueError("release evidence differs from the verified qualification")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in sorted({**files, "qualification.json": receipt_raw}.items()):
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100600 << 16
            archive.writestr(member, raw, compresslevel=9)
    raw = output.getvalue()
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError("release evidence archive exceeds its byte bound")
    return raw


def publish_evidence(
    directory: Path, *, packet_directory: Path, repository: str, tag: str,
    dev_sha: str, candidate_sha: str, index: dict, receipt: dict, qualification_digest: str,
) -> dict:
    if (
        tag != f"python-artifacts-{dev_sha}" or packet_directory.is_symlink()
        or {path.name for path in packet_directory.iterdir()} != set(PACKET_FILES)
    ):
        raise ValueError("release evidence has a different owner or packet inventory")
    raw = evidence_archive(
        {
            name: regular_bytes(packet_directory, name, max_bytes=MAX_UNIT_EVIDENCE_FILE_BYTES)
            for name in PACKET_FILES
        },
        receipt, dev_sha=dev_sha, candidate_sha=candidate_sha,
        qualification_digest=qualification_digest,
    )
    digest = hashlib.sha256(raw).hexdigest()
    name = evidence_name(dev_sha, candidate_sha, digest)
    assets = _publish_subjects(
        directory, subjects={name: raw}, repository=repository, tag=tag, dev_sha=dev_sha, index=index,
    )
    return {
        "filename": name, "sha256": digest, "asset_id": assets[name]["id"],
        "packet_digest": receipt["packet_digest"], "qualification_digest": qualification_digest,
    }


def _publish_subjects(
    directory: Path, *, subjects: dict[str, bytes], repository: str, tag: str,
    dev_sha: str, index: dict,
) -> dict:
    import promotion_artifact_readiness as readiness
    import publish_python_artifacts as publisher

    if tag != f"python-artifacts-{dev_sha}":
        raise ValueError("supplementary subjects have a different release owner")
    digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in subjects.items()}
    expected = {**readiness._expected(directory, index), **digests}
    release = readiness._read_release(repository, tag)
    if release is None or publisher._tag_commit(repository, tag) != dev_sha:
        raise ValueError("release evidence owner is unavailable or changed")
    assets = readiness._assets(repository, release, expected, pending_evidence=tuple(subjects))
    if set(expected) - set(assets) - subjects.keys():
        raise ValueError("release code-artifact readiness changed before evidence publication")
    missing = [name for name in subjects if name not in assets]
    if missing:
        with tempfile.TemporaryDirectory(prefix="promotion-unit-evidence-") as temporary:
            for name in missing:
                (Path(temporary) / name).write_bytes(subjects[name])
            publisher._check(publisher._gh([
                "release", "upload", tag, "--repo", repository,
                *(str(Path(temporary) / name) for name in missing),
            ], timeout=120))
        assets = readiness._assets(repository, release, expected)
    if assets.keys() != expected.keys():
        raise ValueError("release evidence or code-artifact publication is incomplete")
    publisher.verify_asset_downloads(repository, [
        (f"{tag}/{name}", assets[name]["id"], digest) for name, digest in digests.items()
    ])
    if publisher._tag_commit(repository, tag) != dev_sha:
        raise ValueError("release evidence owner changed during readback")
    return assets


def publish_complete_source(
    directory: Path, *, source_directory: Path, repository: str, tag: str,
    index: dict, receipt: dict, qualification_digest: str,
) -> dict:
    """Use the existing Dev publisher only after fresh exact-subject admission."""
    import publish_python_artifacts as publisher
    from promotion_source_archive import read_source_assets, source_identity

    identity = source_identity(
        (directory / publisher.INDEX_NAME).read_bytes(), receipt,
        qualification_digest=qualification_digest,
    )
    if json.loads((directory / publisher.INDEX_NAME).read_bytes()) != index:
        raise ValueError("complete-source publication index changed after admission")
    subjects = read_source_assets(source_directory, identity=identity)
    with tempfile.TemporaryDirectory(prefix="promotion-source-admission-") as temporary:
        for name, raw in subjects.items():
            subject = Path(temporary) / name
            subject.write_bytes(raw)
            publisher.verify_attestation_subject(
                subject, repository=repository,
                signer_digest=identity["control_sha"], source_digest=identity["control_sha"],
                source_ref="refs/heads/main",
            )
    assets = _publish_subjects(
        directory, subjects=subjects, repository=repository, tag=tag,
        dev_sha=identity["dev_sha"], index=index,
    )
    return {
        **identity,
        "assets": [
            {"filename": name, "sha256": hashlib.sha256(raw).hexdigest(), "asset_id": assets[name]["id"]}
            for name, raw in sorted(subjects.items())
        ],
    }
