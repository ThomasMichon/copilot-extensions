"""Qualify the exact generated consumer before the atomic Main publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candidate_qualification_inventory import required_jobs  # noqa: E402
from materialized_candidate_inventory import CandidateValidationError  # noqa: E402
from validate_materialized_candidate import _git as _source_git, matrix, validate  # noqa: E402
from verify_promotion_workflows import (  # noqa: E402
    VerificationError, _api, _identity, _pages, _promotion_created_at, _runs,
)

SCHEMA = "copilot-extensions.candidate-qualification"
LEGACY_PROFILE = "legacy-ci-v1"
UNIT_PROFILE = "source-units-v2"
CANDIDATE_UNIT_PROFILE = "candidate-units-v3"
_SHA = re.compile(r"[0-9a-f]{40}")


def receipt_bytes(receipt: dict) -> bytes:
    return (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def receipt_digest(receipt: dict) -> str:
    return hashlib.sha256(receipt_bytes(receipt)).hexdigest()


def _snapshot(repository: str, candidate_ref: str, candidate_sha: str, main_before: str) -> None:
    for branch, expected in ((candidate_ref, candidate_sha), ("main", main_before)):
        ref = _api(f"repos/{repository}/git/ref/heads/{quote(branch, safe='')}")
        target = ref.get("object") if isinstance(ref, dict) else None
        if not isinstance(target, dict) or target.get("type") != "commit" or target.get("sha") != expected:
            raise VerificationError(f"qualification source/base changed: {branch}")


def _complete_run(
    repository: str, run: dict, *, workflow_id: int, candidate_ref: str,
    candidate_sha: str, expected: set[str], allowed_skips: set[str], deadline: float,
    promotion_run: str,
) -> tuple[int, int, list[dict]]:
    if run.get("display_title") != f"CI promotion {promotion_run} at {candidate_sha}":
        raise VerificationError("candidate CI run belongs to a different owning promotion attempt")
    run_id = _identity(
        run, repository=repository, workflow_id=workflow_id,
        merge_sha=candidate_sha, branch=candidate_ref,
    )
    attempt = run.get("run_attempt")
    if type(attempt) is not int or attempt <= 0:
        raise VerificationError("qualification run has no immutable attempt")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise VerificationError(f"candidate CI run {run_id} did not succeed: {run.get('conclusion')}")
    jobs = _pages(
        f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs",
        "jobs", deadline=deadline,
    )
    rows: dict[str, dict] = {}
    for job in jobs:
        name = job.get("name")
        if (
            not isinstance(name, str) or name in rows
            or type(job.get("id")) is not int or job["id"] <= 0
            or job.get("status") != "completed"
        ):
            raise VerificationError("candidate CI returned duplicate or incomplete job receipts")
        if name in expected:
            if job.get("conclusion") != "success":
                raise VerificationError(f"required candidate CI row did not succeed: {name}")
        elif name not in allowed_skips or job.get("conclusion") != "skipped":
            raise VerificationError(f"candidate CI has an unqualified or unexpected row: {name}")
        rows[name] = job
    if expected - rows.keys():
        raise VerificationError(f"candidate CI omitted required rows: {sorted(expected - rows.keys())}")
    return run_id, attempt, [
        {"name": name, "id": rows[name]["id"], "conclusion": "success"} for name in sorted(expected)
    ]


def qualify(
    *, repository: str, candidate_ref: str, candidate_sha: str, dev_sha: str,
    main_before: str, control_sha: str, promotion_run: str,
    candidate_root: Path, dev_root: Path, wait_seconds: int = 5100,
) -> dict:
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+", repository)
        or repository.rsplit("/", 1)[-1] in (".", "..")
        or not re.fullmatch(r"release/promote-[0-9]{1,20}", candidate_ref)
        or any(not _SHA.fullmatch(value) for value in (candidate_sha, dev_sha, main_before, control_sha))
        or not re.fullmatch(r"[0-9]{1,20}-[0-9]{1,10}", promotion_run)
        or type(wait_seconds) is not int or wait_seconds <= 0
        or not os.environ.get("GH_TOKEN")
    ):
        raise VerificationError("invalid candidate qualification identity or missing authority")
    if candidate_ref != f"release/promote-{promotion_run.split('-')[0]}":
        raise VerificationError("candidate branch does not belong to the owning promotion")
    identity = validate(candidate_root, candidate_sha, dev_sha, main_before)
    matrix(candidate_root, dev_root, dev_sha)
    expected, allowed_skips, workflow_digest = required_jobs(candidate_root)
    _snapshot(repository, candidate_ref, candidate_sha, main_before)
    workflow = _api(f"repos/{repository}/actions/workflows/ci.yml")
    if (
        not isinstance(workflow, dict) or type(workflow.get("id")) is not int
        or workflow.get("state") != "active" or workflow.get("name") != "CI"
        or workflow.get("path") != ".github/workflows/ci.yml"
    ):
        raise VerificationError("canonical candidate CI workflow is unavailable")
    deadline = time.monotonic() + wait_seconds
    created_at = _promotion_created_at(repository, promotion_run)
    title = f"CI promotion {promotion_run} at {candidate_sha}"

    def runs() -> list[dict]:
        found = _runs(
            repository, "ci.yml", title, merge_sha=candidate_sha, branch=candidate_ref,
            created_at=created_at, deadline=deadline,
        )
        if len(found) > 1:
            raise VerificationError("ambiguous candidate qualification CI runs")
        return found

    existing = runs()
    if existing:
        _identity(
            existing[0], repository=repository, workflow_id=workflow["id"],
            merge_sha=candidate_sha, branch=candidate_ref,
        )
    else:
        _api(f"repos/{repository}/actions/workflows/ci.yml/dispatches", body={
            "ref": candidate_ref,
            "inputs": {
                "promotion_sha": candidate_sha, "promotion_run": promotion_run,
                "promotion_phase": "candidate",
            },
        })
    while True:
        found = runs()
        if found:
            run = found[0]
            _identity(
                run, repository=repository, workflow_id=workflow["id"],
                merge_sha=candidate_sha, branch=candidate_ref,
            )
            if run.get("status") == "completed":
                run_id, attempt, jobs = _complete_run(
                    repository, run, workflow_id=workflow["id"], candidate_ref=candidate_ref,
                    candidate_sha=candidate_sha, expected=expected, allowed_skips=allowed_skips,
                    deadline=deadline, promotion_run=promotion_run,
                )
                break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError("candidate qualification deadline expired")
        time.sleep(min(30, remaining))
    if validate(candidate_root, candidate_sha, dev_sha, main_before) != identity:
        raise VerificationError("candidate changed during qualification")
    matrix(candidate_root, dev_root, dev_sha)
    _snapshot(repository, candidate_ref, candidate_sha, main_before)
    return {
        "schema": SCHEMA, "version": 1, "repository": repository,
        **identity, "candidate_ref": candidate_ref, "control_sha": control_sha,
        "promotion_run": promotion_run, "workflow_id": workflow["id"],
        "workflow_digest": workflow_digest, "run_id": run_id, "run_attempt": attempt,
        "jobs": jobs,
    }


def _source_snapshot(root: Path, source: str) -> None:
    if (
        root.is_symlink() or not root.is_dir()
        or Path(_source_git(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve()
        or _source_git(root, "rev-parse", "HEAD") != source
        or _source_git(root, "status", "--porcelain", "--untracked-files=no")
    ):
        raise VerificationError("source-unit manifest checkout is not the exact clean admitted Dev")


def _unit_artifact_bytes(repository: str, artifact_id: int, *, timeout: float) -> bytes:
    env = os.environ.copy()
    read_token = env.pop("ARTIFACT_VERIFY_TOKEN", None)
    if read_token is not None:
        if not read_token:
            raise VerificationError("explicit unit artifact read authority is unavailable")
        env["GH_TOKEN"] = read_token
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}/zip"],
        check=True, capture_output=True, timeout=timeout, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.stdout


def qualify_units(
    *, repository: str, candidate_ref: str, candidate_sha: str, dev_sha: str,
    main_before: str, control_sha: str, promotion_run: str,
    candidate_root: Path, dev_root: Path, unit_packet_dir: Path, unit_manifest: str,
    unit_artifact_id: int, unit_artifact_digest: str,
    profile: str = UNIT_PROFILE,
) -> dict:
    from candidate_qualification_receipt import (
        artifact_identity, input_equivalence, load_manifest, owner_identity, packet_binding,
        producer_binding, regular_bytes, verify_archive,
    )

    run_id, attempt = owner_identity(
        repository, candidate_ref, candidate_sha, dev_sha, main_before, control_sha, promotion_run,
    )
    artifact_identity(unit_artifact_id, unit_artifact_digest)
    if not (os.environ.get("ARTIFACT_VERIFY_TOKEN") or os.environ.get("GH_TOKEN")):
        raise VerificationError("source-unit qualification requires provider read authority")
    identity = validate(candidate_root, candidate_sha, dev_sha, main_before)
    _snapshot(repository, candidate_ref, candidate_sha, main_before)
    _source_snapshot(dev_root, dev_sha)
    manifest, source_manifest_digest = load_manifest(dev_root, unit_manifest)
    if regular_bytes(dev_root, unit_manifest) != regular_bytes(candidate_root, unit_manifest):
        raise VerificationError("candidate does not preserve the admitted source-unit manifest bytes")
    if profile not in (UNIT_PROFILE, CANDIDATE_UNIT_PROFILE):
        raise VerificationError("unknown unit execution profile")
    source_root, packet_arguments = dev_root, {}
    if profile == CANDIDATE_UNIT_PROFILE:
        from promotion_candidate_units import derive_manifest

        manifest, _development, _candidate = derive_manifest(
            manifest, development_root=dev_root, candidate_root=candidate_root,
            dev_sha=dev_sha, candidate_tree=identity["tree"],
        )
        source_root = candidate_root
        packet_arguments["development_root"] = dev_root
    packet = packet_binding(
        unit_packet_dir, source_sha=dev_sha, manifest=manifest, source_root=source_root,
        **packet_arguments,
    )
    inputs = input_equivalence(source_root, candidate_root, manifest)
    deadline = time.monotonic() + 120

    def read(path: str):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError("source-unit provider evidence deadline expired")
        return _api(path, timeout=min(30, remaining))

    workflow = read(f"repos/{repository}/actions/workflows/validate-and-promote.yml")
    run = read(f"repos/{repository}/actions/runs/{run_id}")
    jobs = _pages(
        f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs", deadline=deadline,
    )
    artifact = read(f"repos/{repository}/actions/artifacts/{unit_artifact_id}")
    producer = producer_binding(
        repository=repository, run_id=run_id, attempt=attempt, control_sha=control_sha,
        artifact_id=unit_artifact_id, artifact_digest=unit_artifact_digest,
        workflow=workflow, run=run, jobs=jobs, artifact=artifact,
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise VerificationError("source-unit artifact readback deadline expired")
    verify_archive(
        _unit_artifact_bytes(repository, unit_artifact_id, timeout=min(60, remaining)),
        expected_digest=producer["unit_artifact_digest"],
        expected_size=producer["unit_artifact_size"], packet_files=packet["packet_files"],
    )
    if (
        validate(candidate_root, candidate_sha, dev_sha, main_before) != identity
        or load_manifest(dev_root, unit_manifest)[1] != source_manifest_digest
        or regular_bytes(dev_root, unit_manifest) != regular_bytes(candidate_root, unit_manifest)
        or input_equivalence(source_root, candidate_root, manifest) != inputs
        or packet_binding(
            unit_packet_dir, source_sha=dev_sha, manifest=manifest, source_root=source_root,
            **packet_arguments,
        ) != packet
    ):
        raise VerificationError("source-unit qualification inputs changed during admission")
    _source_snapshot(dev_root, dev_sha)
    _snapshot(repository, candidate_ref, candidate_sha, main_before)
    return {
        "schema": SCHEMA, "version": 3 if profile == CANDIDATE_UNIT_PROFILE else 2,
        "profile": profile, "repository": repository,
        **identity, "candidate_ref": candidate_ref, "control_sha": control_sha,
        "promotion_run": promotion_run, "unit_manifest": unit_manifest,
        "source_manifest_sha256": source_manifest_digest, "unit_inputs_digest": inputs,
        **packet, **producer,
    }


def verify_receipt(
    receipt: dict, *, repository: str, candidate_sha: str, dev_sha: str,
    main_before: str, control_sha: str, promotion_run: str, candidate_root: Path,
    expected_profile: str = LEGACY_PROFILE, dev_root: Path | None = None,
    unit_packet_dir: Path | None = None, unit_manifest: str | None = None,
) -> str:
    if expected_profile in (UNIT_PROFILE, CANDIDATE_UNIT_PROFILE):
        if (
            not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA
            or type(receipt.get("version")) is not int
            or receipt["version"] != (3 if expected_profile == CANDIDATE_UNIT_PROFILE else 2)
            or receipt.get("profile") != expected_profile or dev_root is None
            or unit_packet_dir is None or unit_manifest is None
        ):
            raise VerificationError("explicit source-unit v2 qualification evidence is required")
        expected = qualify_units(
            repository=repository, candidate_ref=receipt.get("candidate_ref"),
            candidate_sha=candidate_sha, dev_sha=dev_sha, main_before=main_before,
            control_sha=control_sha, promotion_run=promotion_run, candidate_root=candidate_root,
            dev_root=dev_root, unit_packet_dir=unit_packet_dir, unit_manifest=unit_manifest,
            unit_artifact_id=receipt.get("unit_artifact_id"),
            unit_artifact_digest=receipt.get("unit_artifact_digest"),
            **({"profile": expected_profile} if expected_profile == CANDIDATE_UNIT_PROFILE else {}),
        )
        if receipt != expected:
            raise VerificationError("source-unit qualification receipt is absent, invalid or stale")
        return receipt_digest(receipt)
    if expected_profile != LEGACY_PROFILE:
        raise VerificationError("unknown qualification profile")
    if (
        not isinstance(receipt, dict) or type(receipt.get("version")) is not int
        or receipt["version"] != 1 or receipt.get("profile", LEGACY_PROFILE) != LEGACY_PROFILE
    ):
        raise VerificationError("explicit legacy CI qualification evidence is required")
    identity = validate(candidate_root, candidate_sha, dev_sha, main_before)
    expected, allowed_skips, workflow_digest = required_jobs(candidate_root)
    if (
        not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA
        or type(receipt.get("version")) is not int or receipt["version"] != 1
        or receipt.get("repository") != repository
        or receipt.get("control_sha") != control_sha
        or receipt.get("promotion_run") != promotion_run
        or not _SHA.fullmatch(control_sha)
        or not re.fullmatch(r"[0-9]{1,20}-[0-9]{1,10}", promotion_run)
        or any(receipt.get(key) != value for key, value in identity.items())
        or receipt.get("workflow_digest") != workflow_digest
        or type(receipt.get("run_id")) is not int or receipt["run_id"] <= 0
        or type(receipt.get("workflow_id")) is not int or receipt["workflow_id"] <= 0
        or not isinstance(receipt.get("candidate_ref"), str)
        or receipt["candidate_ref"] != f"release/promote-{promotion_run.split('-')[0]}"
    ):
        raise VerificationError("candidate qualification receipt is absent, invalid or stale")
    _snapshot(repository, receipt["candidate_ref"], candidate_sha, main_before)
    workflow = _api(f"repos/{repository}/actions/workflows/ci.yml")
    if (
        not isinstance(workflow, dict) or workflow.get("id") != receipt["workflow_id"]
        or workflow.get("state") != "active" or workflow.get("name") != "CI"
        or workflow.get("path") != ".github/workflows/ci.yml"
    ):
        raise VerificationError("qualification receipt does not name the canonical CI workflow")
    run = _api(f"repos/{repository}/actions/runs/{receipt['run_id']}")
    if not isinstance(run, dict):
        raise VerificationError("candidate qualification run is unavailable")
    run_id, attempt, jobs = _complete_run(
        repository, run, workflow_id=receipt["workflow_id"], candidate_ref=receipt["candidate_ref"],
        candidate_sha=candidate_sha, expected=expected, allowed_skips=allowed_skips,
        deadline=time.monotonic() + 120, promotion_run=promotion_run,
    )
    if run_id != receipt["run_id"] or attempt != receipt.get("run_attempt") or jobs != receipt.get("jobs"):
        raise VerificationError("candidate qualification receipt no longer matches its successful attempt")
    return receipt_digest(receipt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=(LEGACY_PROFILE, UNIT_PROFILE, CANDIDATE_UNIT_PROFILE), required=True)
    for name in (
        "repository", "candidate-ref", "candidate-sha", "dev-sha", "main-before",
        "control-sha", "promotion-run",
    ):
        parser.add_argument(f"--{name}", required=True)
    for name in ("candidate-root", "dev-root", "receipt"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--unit-packet-dir", type=Path)
    parser.add_argument("--unit-manifest", help="canonical manifest path relative to the immutable Dev checkout")
    parser.add_argument("--unit-artifact-id", type=int)
    parser.add_argument("--unit-artifact-digest")
    args = parser.parse_args(argv)
    unit_fields = ("unit_packet_dir", "unit_manifest", "unit_artifact_id", "unit_artifact_digest")
    if args.profile in (UNIT_PROFILE, CANDIDATE_UNIT_PROFILE) and any(getattr(args, key) is None for key in unit_fields):
        parser.error("source-units-v2 requires packet directory, source manifest and exact artifact ID/digest")
    if args.profile == LEGACY_PROFILE and any(getattr(args, key) is not None for key in unit_fields):
        parser.error("legacy qualification cannot consume source-unit evidence")
    try:
        arguments = {
            key: value for key, value in vars(args).items()
            if key not in ("profile", "receipt", "github_output") and (
                args.profile in (UNIT_PROFILE, CANDIDATE_UNIT_PROFILE) or key not in unit_fields
            )
        }
        if args.profile == CANDIDATE_UNIT_PROFILE:
            arguments["profile"] = args.profile
        receipt = (qualify_units if args.profile in (UNIT_PROFILE, CANDIDATE_UNIT_PROFILE) else qualify)(**arguments)
        args.receipt.write_bytes(receipt_bytes(receipt))
        digest = receipt_digest(receipt)
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(
                    f"qualification_run={receipt['run_id']}\n"
                    f"qualification_attempt={receipt['run_attempt']}\nqualification_digest={digest}\n"
                    f"qualification_promotion_run={receipt['promotion_run']}\n"
                )
                for field in ("candidate_sha", "dev_sha", "main_before", "control_sha", "tree"):
                    output.write(f"{field}={receipt[field]}\n")
                output.write(f"qualification_profile={args.profile}\n")
                if args.profile in (UNIT_PROFILE, CANDIDATE_UNIT_PROFILE):
                    for field in (
                        "unit_job_id", "unit_artifact_id", "unit_artifact_digest", "packet_digest",
                        "unit_manifest_digest", "unit_inputs_digest",
                    ):
                        output.write(f"qualification_{field}={receipt[field]}\n")
    except (
        CandidateValidationError, VerificationError, subprocess.SubprocessError,
        OSError, ValueError,
    ) as error:
        print(f"::error::candidate-qualification: {error}", file=sys.stderr)
        return 1
    print(f"candidate-qualification: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
