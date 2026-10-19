"""Data-only bindings for reuse of one exact source-unit execution."""
from __future__ import annotations

from datetime import datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from promotion_unit_contract import admit_manifest, decode_json, manifest_digest

SCHEMA = "copilot-extensions.candidate-qualification"
UNIT_PROFILE = "source-units-v2"
LEGACY_PROFILE = "legacy-ci-v1"
UNIT_JOB = "Eligible source units with coverage"
UNIT_UPLOAD_STEP = "Upload promotion unit evidence"
UNIT_ARTIFACT = "promotion-unit-evidence"
WORKFLOW = ".github/workflows/validate-and-promote.yml"
PACKET_FILES = ("unit-manifest.json", "unit-proof.json", "coverage.json")
MAX_PACKET_FILE_BYTES = 16 * 1024 * 1024
MAX_PACKET_BYTES = len(PACKET_FILES) * MAX_PACKET_FILE_BYTES
MAX_ARCHIVE_BYTES = MAX_PACKET_BYTES + 64 * 1024
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class UnitQualificationError(ValueError):
    """Unit evidence cannot authorize this candidate."""


def _positive(value: object) -> bool:
    return type(value) is int and 0 < value < 10**20


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def owner_identity(
    repository: str, candidate_ref: str, candidate_sha: str, dev_sha: str,
    main_before: str, control_sha: str, promotion_run: str,
) -> tuple[int, int]:
    owner = (
        re.fullmatch(r"([1-9][0-9]{0,19})-([1-9][0-9]{0,9})", promotion_run)
        if isinstance(promotion_run, str) else None
    )
    if (
        not isinstance(repository, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+", repository)
        or repository.rsplit("/", 1)[-1] in (".", "..") or owner is None
        or any(not isinstance(value, str) or not _SHA.fullmatch(value)
               for value in (candidate_sha, dev_sha, main_before, control_sha))
        or candidate_ref != f"release/promote-{owner[1]}"
    ):
        raise UnitQualificationError("invalid source-unit qualification identity")
    return int(owner[1]), int(owner[2])


def regular_bytes(root: Path, name: str) -> bytes:
    if (
        not isinstance(name, str) or not name or "\\" in name or ":" in name
        or PurePosixPath(name).is_absolute()
        or any(part in ("", ".", "..", ".git") for part in name.split("/"))
        or any(ord(char) < 32 for char in name)
        or root.is_symlink() or not root.is_dir()
    ):
        raise UnitQualificationError("unit input path is not canonical")
    path = root
    for part in name.split("/"):
        path /= part
        if path.is_symlink():
            raise UnitQualificationError(f"indirect unit input refused: {name}")
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise UnitQualificationError(f"required unit input is unavailable: {name}")
    if path.stat().st_size > MAX_PACKET_FILE_BYTES:
        raise UnitQualificationError(f"unit input exceeds its byte bound: {name}")
    return path.read_bytes()


def load_manifest(source_root: Path, name: str) -> tuple[dict, str]:
    if not isinstance(name, str) or not name.endswith(".json"):
        raise UnitQualificationError("the admitted unit manifest must name a JSON file")
    raw = regular_bytes(source_root, name)
    return admit_manifest(decode_json(raw)), hashlib.sha256(raw).hexdigest()


def input_equivalence(source_root: Path, candidate_root: Path, manifest: dict) -> str:
    sources = manifest["sources"]
    if len({name.casefold() for name in sources}) != len(sources):
        raise UnitQualificationError("unit input paths have case-equivalent aliases")
    for name, expected in sources.items():
        source = regular_bytes(source_root, name)
        candidate = regular_bytes(candidate_root, name)
        if source != candidate or hashlib.sha256(source).hexdigest() != expected:
            raise UnitQualificationError(f"candidate unit source differs from admitted source bytes: {name}")
    return canonical_digest(sources)


def packet_binding(directory: Path, *, source_sha: str, manifest: dict, source_root: Path) -> dict:
    try:
        from promotion_unit_packet import verify_packet
    except ModuleNotFoundError as exc:
        raise UnitQualificationError("the source-unit packet verifier is unavailable") from exc
    before = {name: regular_bytes(directory, name) for name in PACKET_FILES}
    verified = verify_packet(
        directory, expected_source_sha=source_sha, expected_manifest=manifest, source_root=source_root,
    )
    fields = {
        "profile", "source_sha", "manifest_sha256", "manifest_file_sha256",
        "proof_sha256", "coverage_sha256", "packet_sha256", "manifest", "proof",
    }
    if not isinstance(verified, dict) or set(verified) != fields:
        raise UnitQualificationError("the source-unit packet verifier returned no validated binding")
    after = {name: regular_bytes(directory, name) for name in PACKET_FILES}
    if before != after:
        raise UnitQualificationError("unit packet changed during verification")
    digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in after.items()}
    raw_digests = {
        "manifest_file_sha256": digests["unit-manifest.json"],
        "proof_sha256": digests["unit-proof.json"], "coverage_sha256": digests["coverage.json"],
    }
    if (
        verified["profile"] != manifest["profile"] or verified["source_sha"] != source_sha
        or verified["manifest"] != manifest or verified["manifest_sha256"] != manifest_digest(manifest)
        or any(verified[key] != value for key, value in raw_digests.items())
        or verified["packet_sha256"] != canonical_digest(raw_digests)
    ):
        raise UnitQualificationError("validated unit packet bindings are inconsistent")
    return {
        "unit_profile": verified["profile"], "unit_manifest_digest": verified["manifest_sha256"],
        "packet_files": digests, "packet_digest": verified["packet_sha256"],
    }


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise UnitQualificationError("unit producer timestamp is unavailable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UnitQualificationError("unit producer timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise UnitQualificationError("unit producer timestamp has no timezone")
    return parsed


def artifact_identity(artifact_id: object, artifact_digest: object) -> None:
    if not _positive(artifact_id) or not isinstance(artifact_digest, str) or not _DIGEST.fullmatch(artifact_digest):
        raise UnitQualificationError("unit artifact requires an exact provider ID and SHA256 digest")


def producer_binding(
    *, repository: str, run_id: int, attempt: int, control_sha: str,
    artifact_id: int, artifact_digest: str,
    workflow: object, run: object, jobs: list[dict], artifact: object,
) -> dict:
    artifact_identity(artifact_id, artifact_digest)
    if (
        not isinstance(workflow, dict) or not _positive(workflow.get("id"))
        or workflow.get("path") != WORKFLOW or workflow.get("state") != "active"
        or not isinstance(run, dict) or not _positive(run.get("id")) or run["id"] != run_id
        or not _positive(run.get("workflow_id")) or run["workflow_id"] != workflow["id"]
        or run.get("path") != WORKFLOW
        or run.get("event") not in ("repository_dispatch", "workflow_dispatch")
        or run.get("head_branch") != "main" or run.get("head_sha") != control_sha
        or not _positive(run.get("run_attempt")) or run["run_attempt"] < attempt
        or not isinstance(run.get("repository"), dict)
        or run["repository"].get("full_name") != repository or not _positive(run["repository"].get("id"))
        or not isinstance(run.get("head_repository"), dict)
        or run["head_repository"].get("full_name") != repository
        or not _positive(run["head_repository"].get("id"))
        or run["head_repository"].get("id") != run["repository"]["id"]
    ):
        raise UnitQualificationError("unit producer is not the exact trusted Main VP generation")
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise UnitQualificationError("unit producer job inventory is invalid")
    matches = [job for job in jobs if job.get("name") == UNIT_JOB]
    if len(matches) != 1:
        raise UnitQualificationError("unit producer job is missing or ambiguous")
    job = matches[0]
    if (
        not _positive(job.get("id")) or not _positive(job.get("run_id")) or job["run_id"] != run_id
        or not _positive(job.get("run_attempt")) or job["run_attempt"] != attempt
        or job.get("head_sha") != control_sha
        or job.get("status") != "completed" or job.get("conclusion") != "success"
        or not isinstance(job.get("steps"), list)
        or any(not isinstance(step, dict) for step in job["steps"])
    ):
        raise UnitQualificationError("the exact source-unit job did not complete successfully")
    uploads = [step for step in job["steps"] if step.get("name") == UNIT_UPLOAD_STEP]
    if (
        len(uploads) != 1 or not _positive(uploads[0].get("number"))
        or uploads[0].get("status") != "completed" or uploads[0].get("conclusion") != "success"
    ):
        raise UnitQualificationError("unit artifact has no unique successful producer upload")
    if (
        not isinstance(artifact, dict) or not _positive(artifact.get("id")) or artifact["id"] != artifact_id
        or artifact.get("name") != UNIT_ARTIFACT or artifact.get("expired") is not False
        or artifact.get("digest") != artifact_digest
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE_BYTES
        or not isinstance(artifact.get("workflow_run"), dict)
    ):
        raise UnitQualificationError("unit artifact provider metadata is unavailable or conflicting")
    origin = artifact["workflow_run"]
    if (
        not _positive(origin.get("id")) or origin["id"] != run_id or origin.get("head_branch") != "main"
        or origin.get("head_sha") != control_sha
        or not _positive(origin.get("repository_id")) or not _positive(origin.get("head_repository_id"))
        or origin.get("repository_id") != run["repository"]["id"]
        or origin.get("head_repository_id") != run["repository"]["id"]
        or not _time(job.get("started_at")) <= _time(uploads[0].get("started_at"))
        <= _time(artifact.get("created_at")) <= _time(uploads[0].get("completed_at"))
        <= _time(job.get("completed_at"))
    ):
        raise UnitQualificationError("unit artifact does not belong to the recorded job attempt/upload")
    return {
        "workflow_id": workflow["id"], "run_id": run_id, "run_attempt": attempt,
        "unit_job_id": job["id"], "unit_upload_step": uploads[0]["number"],
        "unit_artifact_id": artifact_id, "unit_artifact_digest": artifact_digest,
        "unit_artifact_size": artifact["size_in_bytes"], "unit_artifact_created_at": artifact["created_at"],
    }


def verify_archive(raw: bytes, *, expected_digest: str, expected_size: int, packet_files: dict) -> None:
    if (
        not isinstance(raw, bytes) or len(raw) != expected_size or len(raw) > MAX_ARCHIVE_BYTES
        or "sha256:" + hashlib.sha256(raw).hexdigest() != expected_digest
        or not isinstance(packet_files, dict) or set(packet_files) != set(PACKET_FILES)
    ):
        raise UnitQualificationError("downloaded unit artifact archive has conflicting bytes")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if (
                len(members) != len(PACKET_FILES) or {member.filename for member in members} != set(PACKET_FILES)
                or sum(member.file_size for member in members) > MAX_PACKET_BYTES
                or any(member.file_size > MAX_PACKET_FILE_BYTES or member.is_dir() or member.flag_bits & 1
                       or stat.S_ISLNK(member.external_attr >> 16) for member in members)
            ):
                raise UnitQualificationError("unit artifact archive has an unexpected member inventory")
            for member in members:
                if hashlib.sha256(archive.read(member)).hexdigest() != packet_files[member.filename]:
                    raise UnitQualificationError("local unit packet differs from immutable hosted bytes")
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise UnitQualificationError("unit artifact archive is unreadable") from exc
