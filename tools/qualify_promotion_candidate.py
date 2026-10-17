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
from validate_materialized_candidate import matrix, validate  # noqa: E402
from verify_promotion_workflows import (  # noqa: E402
    VerificationError, _api, _identity, _pages, _promotion_created_at, _runs,
)

SCHEMA = "copilot-extensions.candidate-qualification"
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


def verify_receipt(
    receipt: dict, *, repository: str, candidate_sha: str, dev_sha: str,
    main_before: str, control_sha: str, promotion_run: str, candidate_root: Path,
) -> str:
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
    for name in (
        "repository", "candidate-ref", "candidate-sha", "dev-sha", "main-before",
        "control-sha", "promotion-run",
    ):
        parser.add_argument(f"--{name}", required=True)
    for name in ("candidate-root", "dev-root", "receipt"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = qualify(**{
            key: value for key, value in vars(args).items() if key not in ("receipt", "github_output")
        })
        args.receipt.write_bytes(receipt_bytes(receipt))
        digest = receipt_digest(receipt)
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(
                    f"qualification_run={receipt['run_id']}\n"
                    f"qualification_attempt={receipt['run_attempt']}\nqualification_digest={digest}\n"
                )
                for field in ("candidate_sha", "dev_sha", "main_before", "control_sha", "tree"):
                    output.write(f"{field}={receipt[field]}\n")
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
