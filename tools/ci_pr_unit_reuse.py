"""Read-only, exact-input reuse of source PR units, never release qualification."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import zipfile

from candidate_qualification_receipt import (
    MAX_ARCHIVE_BYTES,
    MAX_PACKET_FILE_BYTES,
    PACKET_FILES,
    UnitQualificationError,
    canonical_digest,
    packet_binding,
    verify_archive,
)
from promotion_unit_contract import (
    UnitEvidenceError,
    admit_manifest,
    decode_json,
    manifest_digest,
    source_sha,
    verify_sources,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/ci.yml"
EXECUTE_STEP = "Qualify exact-input PR units"
UPLOAD_STEP = "Upload freshly executed PR unit evidence"
UNIT_JOB = "Eligible units with coverage"
MAX_LOOKUP_SECONDS = 8
ENVIRONMENT_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "PYTHONHOME",
)


class ReuseUnavailable(ValueError):
    """Reuse is unqualified; the current run must execute fresh units."""


def unit_environment(environment: dict[str, str]) -> dict[str, str]:
    """The source-PR child receives no credentials or event-dependent inputs."""
    result = {key: environment[key] for key in ENVIRONMENT_KEYS if key in environment}
    result.update(
        PYTHONHASHSEED="0",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
    )
    return result


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReuseUnavailable("missing-provider-timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReuseUnavailable("invalid-provider-timestamp") from error
    if parsed.tzinfo is None:
        raise ReuseUnavailable("unqualified-provider-timezone")
    return parsed


def context(
    environment: dict[str, str],
    event: dict,
    manifest: dict,
    runtime: dict,
) -> dict:
    """Bind semantic unit inputs, excluding only the current provider execution."""
    if environment.get("GITHUB_EVENT_NAME") != "pull_request":
        raise ReuseUnavailable("not-a-source-pr")
    if not isinstance(event, dict) or not isinstance(runtime, dict):
        raise ReuseUnavailable("invalid-current-inputs")
    repository = event.get("repository", {})
    pr = event.get("pull_request", {})
    if not isinstance(repository, dict) or not isinstance(pr, dict):
        raise ReuseUnavailable("invalid-pr-event")
    base, head = pr.get("base", {}), pr.get("head", {})
    if (
        not isinstance(repository, dict)
        or not _positive(repository.get("id"))
        or repository.get("full_name") != environment.get("GITHUB_REPOSITORY")
        or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository.get("full_name", ""))
        or not _positive(pr.get("number"))
        or pr["number"] != event.get("number")
        or not isinstance(base, dict)
        or not isinstance(head, dict)
        or not isinstance(base.get("repo"), dict)
        or base["repo"].get("id") != repository["id"]
        or not isinstance(head.get("repo"), dict)
        or not _positive(head["repo"].get("id"))
        or not isinstance(base.get("ref"), str)
        or not base["ref"]
        or base["ref"] == "main"
    ):
        raise ReuseUnavailable("unqualified-pr-identity")
    image = {
        key: environment.get(key)
        for key in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH")
    }
    if (
        image["RUNNER_OS"] != "Linux"
        or any(not isinstance(value, str) or not value for value in image.values())
        or environment.get("GITHUB_SERVER_URL") != "https://github.com"
        or not environment.get("GITHUB_WORKSPACE")
        or set(runtime)
        != {
            "python",
            "executable_sha256",
            "implementation",
            "cache_tag",
            "machine",
            "kernel",
            "distributions",
        }
        or any(
            not isinstance(runtime[key], str) or not runtime[key]
            for key in ("python", "implementation", "cache_tag", "machine", "kernel")
        )
        or not isinstance(runtime["distributions"], dict)
        or not runtime["distributions"]
        or not isinstance(runtime["executable_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", runtime["executable_sha256"])
    ):
        raise ReuseUnavailable("unqualified-toolchain")
    for name, distribution in runtime["distributions"].items():
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(distribution, dict)
            or set(distribution) != {"version", "record_sha256", "files_sha256"}
            or not isinstance(distribution["version"], str)
            or not distribution["version"]
            or any(
                not isinstance(distribution[key], str)
                or not re.fullmatch(r"[0-9a-f]{64}", distribution[key])
                for key in ("record_sha256", "files_sha256")
            )
        ):
            raise ReuseUnavailable("invalid-distribution-binding")
    identity = {
        "profile": "copilot-extensions.source-pr-unit-reuse/v1",
        "repository": repository["full_name"],
        "repository_id": repository["id"],
        "pr": pr["number"],
        "head_repository_id": head["repo"]["id"],
        "source_sha": source_sha(environment.get("GITHUB_SHA", "")),
        "workflow_sha": source_sha(environment.get("GITHUB_WORKFLOW_SHA", "")),
        "base_sha": source_sha(base.get("sha", "")),
        "head_sha": source_sha(head.get("sha", "")),
        "base_ref": base["ref"],
        "source_root": environment["GITHUB_WORKSPACE"],
        "manifest_sha256": manifest_digest(admit_manifest(manifest)),
        "runtime": runtime,
        "image": image,
        "environment": unit_environment(environment),
    }
    return identity


def artifact_name(identity: dict, run_id: int, attempt: int) -> str:
    if not _positive(run_id) or not _positive(attempt):
        raise ReuseUnavailable("invalid-artifact-origin")
    return f"pr-unit-evidence-v1-{manifest_digest(identity)}-{run_id}-{attempt}"


def context_fingerprint(identity: dict) -> dict:
    """Explain cache misses using existing digests, never environment values or paths."""
    return {
        "identity": {
            name: canonical_digest(value)
            for name, value in identity.items()
            if name not in ("runtime", "environment")
        },
        "runtime": {
            name: canonical_digest(value)
            for name, value in identity["runtime"].items()
            if name != "distributions"
        },
        "distributions": identity["runtime"]["distributions"],
        "environment": {
            name: canonical_digest(value)
            for name, value in identity["environment"].items()
        },
    }


def runtime_identity() -> dict:
    distributions = {}
    deadline = time.monotonic() + 5
    total = 0
    prefix = Path(sys.prefix).resolve()
    for distribution in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        record = distribution.read_text("RECORD")
        files = distribution.files
        if (
            not name
            or name in distributions
            or not record
            or not distribution.version
            or not files
        ):
            raise ReuseUnavailable("unqualified-installed-distribution")
        contents = hashlib.sha256()
        for relative in sorted(files, key=str):
            if time.monotonic() >= deadline:
                raise ReuseUnavailable("toolchain-binding-budget-exhausted")
            path = Path(distribution.locate_file(relative)).resolve()
            if not path.is_relative_to(prefix) or not path.is_file():
                raise ReuseUnavailable("unqualified-installed-file")
            total += path.stat().st_size
            if total > 256 * 1024 * 1024:
                raise ReuseUnavailable("oversized-installed-toolchain")
            contents.update(
                str(relative).encode()
                + b"\0"
                + hashlib.sha256(path.read_bytes()).digest()
            )
        distributions[name] = {
            "version": distribution.version,
            "record_sha256": hashlib.sha256(record.encode()).hexdigest(),
            "files_sha256": contents.hexdigest(),
        }
    return {
        "python": sys.version,
        "implementation": sys.implementation.name,
        "executable_sha256": hashlib.sha256(
            Path(sys.executable).read_bytes()
        ).hexdigest(),
        "cache_tag": sys.implementation.cache_tag,
        "machine": platform.machine(),
        "kernel": platform.release(),
        "distributions": distributions,
    }


def producer(
    identity: dict,
    run: dict,
    jobs: list[dict],
    artifact: dict,
    now: datetime,
) -> dict:
    """Require a real fresh execution/upload from one successful exact attempt."""
    if (
        not isinstance(run, dict)
        or not _positive(run.get("id"))
        or not _positive(run.get("run_attempt"))
        or not _positive(run.get("workflow_id"))
        or run.get("path") != WORKFLOW
        or run.get("event") != "pull_request"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("head_sha") != identity["head_sha"]
        or not isinstance(run.get("repository"), dict)
        or run["repository"].get("id") != identity["repository_id"]
        or not isinstance(run.get("head_repository"), dict)
        or run["head_repository"].get("id") != identity["head_repository_id"]
        or not now - timedelta(days=1) <= _timestamp(run.get("created_at")) <= now
        or not isinstance(jobs, list)
        or any(not isinstance(job, dict) for job in jobs)
    ):
        raise ReuseUnavailable("unqualified-provider-run")
    associations = run.get("pull_requests")
    if (
        not isinstance(associations, list)
        or len(associations) != 1
        or not isinstance(associations[0], dict)
    ):
        raise ReuseUnavailable("unqualified-provider-pr-association")
    pr = associations[0]
    base, head = pr.get("base"), pr.get("head")
    if (
        pr.get("number") != identity["pr"]
        or not isinstance(base, dict)
        or not isinstance(head, dict)
        or base.get("sha") != identity["base_sha"]
        or base.get("ref") != identity["base_ref"]
        or head.get("sha") != identity["head_sha"]
        or not isinstance(base.get("repo"), dict)
        or not isinstance(head.get("repo"), dict)
        or base["repo"].get("id") != identity["repository_id"]
        or head["repo"].get("id") != identity["head_repository_id"]
    ):
        raise ReuseUnavailable("conflicting-provider-pr-inputs")
    matches = [job for job in jobs if job.get("name") == UNIT_JOB]
    if len(matches) != 1:
        raise ReuseUnavailable("ambiguous-unit-job")
    job = matches[0]
    if (
        not _positive(job.get("id"))
        or not _positive(job.get("run_id"))
        or job["run_id"] != run["id"]
        or not _positive(job.get("run_attempt"))
        or job["run_attempt"] != run["run_attempt"]
        or job.get("head_sha") != identity["head_sha"]
        or job.get("status") != "completed"
        or job.get("conclusion") != "success"
        or not _positive(job.get("runner_id"))
        or not isinstance(job.get("steps"), list)
        or any(not isinstance(step, dict) for step in job["steps"])
    ):
        raise ReuseUnavailable("unqualified-unit-attempt")
    steps = []
    for name in (EXECUTE_STEP, UPLOAD_STEP):
        found = [step for step in job["steps"] if step.get("name") == name]
        if len(found) != 1 or (
            not _positive(found[0].get("number"))
            or found[0].get("status") != "completed"
            or found[0].get("conclusion") != "success"
        ):
            raise ReuseUnavailable("missing-fresh-execution-or-upload")
        steps.append(found[0])
    if not steps[0]["number"] < steps[1]["number"]:
        raise ReuseUnavailable("invalid-unit-step-order")
    origin = artifact.get("workflow_run", {})
    if (
        not _positive(artifact.get("id"))
        or artifact.get("name")
        != artifact_name(identity, run["id"], run["run_attempt"])
        or artifact.get("expired") is not False
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE_BYTES
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
        or not isinstance(origin, dict)
        or origin.get("id") != run["id"]
        or origin.get("head_sha") != identity["head_sha"]
        or origin.get("repository_id") != identity["repository_id"]
        or origin.get("head_repository_id") != identity["head_repository_id"]
        or not _timestamp(job.get("started_at"))
        <= _timestamp(steps[0].get("started_at"))
        <= _timestamp(steps[0].get("completed_at"))
        <= _timestamp(steps[1].get("started_at"))
        <= _timestamp(artifact.get("created_at"))
        <= _timestamp(steps[1].get("completed_at"))
        <= _timestamp(job.get("completed_at"))
    ):
        raise ReuseUnavailable("unqualified-unit-artifact")
    return {
        "origin_run": str(run["id"]),
        "origin_attempt": str(run["run_attempt"]),
        "origin_job": str(job["id"]),
        "origin_artifact": str(artifact["id"]),
        "artifact_digest": artifact["digest"],
    }


def verify_download(
    raw: bytes, artifact: dict, identity: dict, manifest: dict, root: Path
) -> dict:
    """Verify immutable archive bytes and the unchanged canonical three-file packet."""
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ReuseUnavailable("oversized-unit-archive")
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in PACKET_FILES:
                with archive.open(name) as stream:
                    body = stream.read(MAX_PACKET_FILE_BYTES + 1)
                if len(body) > MAX_PACKET_FILE_BYTES:
                    raise ReuseUnavailable("oversized-unit-packet-file")
                files[name] = body
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError) as error:
        raise ReuseUnavailable("invalid-unit-archive") from error
    verify_archive(
        raw,
        expected_digest=artifact["digest"],
        expected_size=artifact["size_in_bytes"],
        packet_files={
            name: hashlib.sha256(body).hexdigest() for name, body in files.items()
        },
    )
    with tempfile.TemporaryDirectory(prefix="verified-pr-units-") as temporary:
        directory = Path(temporary)
        for name, body in files.items():
            (directory / name).write_bytes(body)
        return packet_binding(
            directory,
            source_sha=identity["source_sha"],
            manifest=manifest,
            source_root=root,
        )


def qualification_epoch(runs: list[dict], current_run: int) -> list[list]:
    """Retry/outcome revisions fence old success without inferring chronology from IDs."""
    revisions = []
    for run in runs:
        attempt = run.get("run_attempt")
        if not _positive(attempt):
            raise ReuseUnavailable("invalid-provider-attempt")
        if run["id"] == current_run:
            if attempt != 1:
                raise ReuseUnavailable("rerun-requires-fresh-execution")
            continue
        if attempt > 1:
            revisions.append(["attempt", run["id"], attempt])
        status, conclusion = run.get("status"), run.get("conclusion")
        if status not in (
            "queued",
            "in_progress",
            "completed",
            "waiting",
            "requested",
            "pending",
        ):
            raise ReuseUnavailable("unknown-provider-status")
        if status == "completed" and conclusion not in (
            "success",
            "failure",
            "neutral",
            "cancelled",
            "skipped",
            "timed_out",
            "action_required",
            "stale",
            "startup_failure",
        ):
            raise ReuseUnavailable("unknown-provider-conclusion")
        if status != "completed" or conclusion != "success":
            revisions.append(["outcome", run["id"], attempt, status, conclusion])
    return sorted(revisions)


def resolve(
    identity: dict, manifest: dict, root: Path, current_run: int, read, now: datetime
) -> dict:
    """Fence complete bounded head history, then inspect at most three artifact inventories."""
    prefix = "repos/" + identity["repository"] + "/actions/"
    listing = read(
        prefix
        + "workflows/ci.yml/runs?event=pull_request&head_sha="
        + identity["head_sha"]
        + "&per_page=10",
    )
    runs = listing.get("workflow_runs")
    if (
        not isinstance(runs, list)
        or len(runs) > 10
        or type(listing.get("total_count")) is not int
        or listing["total_count"] != len(runs)
        or any(
            not isinstance(run, dict) or not _positive(run.get("id")) for run in runs
        )
        or len({run["id"] for run in runs}) != len(runs)
    ):
        raise ReuseUnavailable("invalid-provider-run-inventory")
    identity = {
        **identity,
        "qualification_epoch": qualification_epoch(runs, current_run),
    }
    current_attempt = next(
        (run["run_attempt"] for run in runs if run["id"] == current_run),
        1,
    )
    fresh_name = artifact_name(identity, current_run, current_attempt)
    inspected = 0
    for run in sorted(runs, key=lambda value: value["id"], reverse=True):
        if run["id"] == current_run:
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        if run["id"] > current_run:
            continue
        artifacts = read(prefix + f"runs/{run['id']}/artifacts?per_page=100")
        rows = artifacts.get("artifacts")
        if (
            not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows)
            or artifacts.get("total_count") != len(rows)
        ):
            raise ReuseUnavailable("incomplete-artifact-inventory")
        attempt = run.get("run_attempt")
        if not _positive(attempt):
            raise ReuseUnavailable("invalid-provider-attempt")
        matches = [
            row
            for row in rows
            if row.get("name") == artifact_name(identity, run["id"], attempt)
        ]
        if len(matches) > 1:
            raise ReuseUnavailable("ambiguous-unit-artifact")
        if matches:
            artifact = matches[0]
            jobs = read(
                prefix + f"runs/{run['id']}/attempts/{attempt}/jobs?per_page=100"
            )
            if not isinstance(jobs.get("jobs"), list) or jobs.get("total_count") != len(
                jobs["jobs"]
            ):
                raise ReuseUnavailable("incomplete-job-inventory")
            origin = producer(identity, run, jobs["jobs"], artifact, now)
            raw = read(prefix + f"artifacts/{artifact['id']}/zip", binary=True)
            verified = verify_download(raw, artifact, identity, manifest, root)
            return {
                "mode": "reused",
                "artifact_name": artifact["name"],
                **origin,
                "packet_sha256": verified["packet_digest"],
            }
        inspected += 1
        if inspected == 3:
            break
    return {
        "mode": "executed",
        "artifact_name": fresh_name,
        "reason": "no-qualified-completed-packet",
    }


def _process(arguments: list[str], **kwargs):
    return subprocess.run(
        arguments,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        **kwargs,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True, type=source_sha)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = admit_manifest(
        decode_json((ROOT / "tools/promotion-unit-manifest.json").read_bytes())
    )
    verify_sources(ROOT, manifest["sources"])
    is_pr = os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
    environment = (
        unit_environment(dict(os.environ))
        if is_pr
        else {
            key: value
            for key, value in os.environ.items()
            if key not in {"GH_TOKEN", "GITHUB_TOKEN"}
        }
    )
    result = {"mode": "executed", "artifact_name": "promotion-unit-evidence"}
    attempt = 1
    if is_pr:
        if args.source_sha != source_sha(os.environ.get("GITHUB_SHA", "")):
            raise RuntimeError(
                "current source argument conflicts with the workflow source"
            )
        workspace = os.environ.get("GITHUB_WORKSPACE")
        if not workspace:
            raise RuntimeError("current checkout workspace is unavailable")
        head = (
            _process(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                check=True,
                timeout=5,
            )
            .stdout.decode()
            .strip()
        )
        dirty = _process(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            check=True,
            timeout=5,
        ).stdout
        if head != args.source_sha or dirty or Path(workspace).resolve() != ROOT:
            raise RuntimeError(
                "current checkout is modified or differs from the workflow source"
            )
        run_id, attempt = (
            int(os.environ["GITHUB_RUN_ID"]),
            int(os.environ["GITHUB_RUN_ATTEMPT"]),
        )
        if not _positive(run_id) or not _positive(attempt):
            raise RuntimeError("current provider execution is invalid")
        result["artifact_name"] = f"pr-unit-evidence-unqualified-{run_id}-{attempt}"
    try:
        if not is_pr:
            raise ReuseUnavailable("not-a-source-pr")
        if attempt > 1:
            raise ReuseUnavailable("rerun-requires-fresh-execution")
        event = decode_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
        identity = context(dict(os.environ), event, manifest, runtime_identity())
        print(
            json.dumps(
                {"pr_unit_context": context_fingerprint(identity)}, sort_keys=True
            )
        )
        if not os.environ.get("GH_TOKEN"):
            raise ReuseUnavailable("missing-read-only-provider-token")
        deadline = time.monotonic() + MAX_LOOKUP_SECONDS

        def read(path, binary=False):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReuseUnavailable("provider-lookup-budget-exhausted")
            completed = _process(
                ["gh", "api", "--hostname", "github.com", path]
                + (["--allow-escape-sequences"] if binary else []),
                capture_output=True,
                check=True,
                timeout=remaining,
            )
            if len(completed.stdout) > (MAX_ARCHIVE_BYTES if binary else 1024 * 1024):
                raise ReuseUnavailable("oversized-provider-response")
            if binary:
                return completed.stdout
            value = decode_json(completed.stdout)
            if not isinstance(value, dict):
                raise ReuseUnavailable("invalid-provider-response")
            return value

        resolved = resolve(
            identity,
            manifest,
            ROOT,
            int(os.environ["GITHUB_RUN_ID"]),
            read,
            datetime.now(timezone.utc),
        )
        if time.monotonic() >= deadline:
            raise ReuseUnavailable("provider-lookup-budget-exhausted")
        result.update(resolved)
    except (
        ReuseUnavailable,
        UnitEvidenceError,
        UnitQualificationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        zipfile.BadZipFile,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        result["mode"] = "executed"
        result["reason"] = (
            str(error) if isinstance(error, ReuseUnavailable) else type(error).__name__
        )
        if is_pr:
            print(
                f"::warning::PR unit reuse unavailable ({result['reason']}); fresh units required."
            )
    if result["mode"] == "executed":
        completed = _process(
            [
                sys.executable,
                str(ROOT / "tools/run-promotion-unit-tests.py"),
                "--source-sha",
                args.source_sha,
                "--output-dir",
                str(args.output_dir),
            ],
            env=environment,
            cwd=ROOT,
            check=False,
        )
        if completed.returncode:
            return completed.returncode
        verified = packet_binding(
            args.output_dir,
            source_sha=args.source_sha,
            manifest=manifest,
            source_root=ROOT,
        )
        result.update(
            origin_run=os.environ.get("GITHUB_RUN_ID", ""),
            origin_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            packet_sha256=verified["packet_digest"],
        )
    print(json.dumps(result, sort_keys=True))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in result.items():
            output.write(
                key
                + "="
                + (str(value).lower() if isinstance(value, bool) else str(value))
                + "\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
