#!/usr/bin/env python3
"""Read-only watchdog candidate adapter; never grants repair authorization.

The SHA is supplied by the workflow's independently verified commit gate,
not inferred from workflow_run.head_sha. The emitter's separate admission
adapter must revalidate that gate and the current run/job/signature before
granting authorization. An issue label or signature anchor is not authority.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from agent_procutil import no_window_kwargs

from . import ci_failure_signatures as watchdog


def candidates(
    repository: str, run_id: int, sha: str, run: dict, jobs: list[dict], fetch_log
) -> list[dict]:
    """Build candidates from authoritative run/jobs and watchdog log parsing."""
    if type(run_id) is not int or run_id <= 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("positive run ID and gate-verified SHA required")
    if (
        run.get("id") != run_id
        or run.get("status") != "completed"
        or run.get("conclusion") not in watchdog.REPORTABLE_CONCLUSIONS
        or run.get("repository", {}).get("full_name") != repository
        or type(run.get("run_attempt")) is not int
        or run["run_attempt"] <= 0
    ):
        raise ValueError("run is not a completed failure in the configured repository")
    result = []
    for job in jobs:
        if (
            job.get("conclusion") not in watchdog.REPORTABLE_CONCLUSIONS
            or job.get("name") in watchdog.SKIP_JOB_NAMES
        ):
            continue
        if (
            job.get("run_id") != run_id
            or job.get("run_attempt") != run["run_attempt"]
            or job.get("status") != "completed"
            or type(job.get("id")) is not int
            or job["id"] <= 0
            or not isinstance(job.get("name"), str)
            or not job["name"]
        ):
            raise ValueError("failed job is stale or belongs to a different run/attempt")
        for signature in watchdog.build_signatures(job["name"], fetch_log(job["id"])):
            result.append(
                {
                    "repository": repository,
                    "run_id": run_id,
                    "run_attempt": run["run_attempt"],
                    "sha": sha,
                    "job_id": job["id"],
                    "signature": signature.key,
                }
            )
    return result


def main(argv: list[str] | None = None) -> int:
    """Read current run evidence and print only JSON candidates on stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--sha", required=True)
    parser.add_argument(
        "--gh-command",
        required=True,
        help="JSON argv prefix of an attributable repository-scoped gh wrapper; no credential values",
    )
    args = parser.parse_args(argv)
    try:
        prefix = json.loads(args.gh_command)
        if (
            not isinstance(prefix, list)
            or not prefix
            or any(not isinstance(p, str) or not p for p in prefix)
        ):
            raise ValueError("gh-command must be an explicit scoped wrapper argv")
        if prefix[0].replace("\\", "/").split("/")[-1].lower() in ("gh", "gh.exe"):
            raise ValueError("ambient gh is not a repository-scoped wrapper")

        def request(argv):
            return subprocess.run(
                [*prefix, *argv],
                capture_output=True,
                text=True,
                timeout=30,
                **no_window_kwargs(),
            )

        response = request(["api", f"repos/{args.repo}/actions/runs/{args.run_id}"])
        if response.returncode != 0:
            raise watchdog.LookupFailed(response.stderr)
        run = json.loads(response.stdout)
        jobs = watchdog._fetch_run_jobs(args.repo, str(args.run_id), run_gh=request)
        result = candidates(
            args.repo,
            args.run_id,
            args.sha,
            run,
            jobs,
            lambda job_id: watchdog._fetch_job_log(args.repo, job_id, run_gh=request),
        )
    except (
        watchdog.LookupFailed,
        ValueError,
        TypeError,
        OSError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"CI source refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
