"""Dispatch and verify the main-push consumers suppressed by an Actions-token merge."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote

WORKFLOWS = {
    "ci.yml": ("CI", {"guards + lint", "Promotion snapshot gate"}),
    "workflow-lockdown-guard.yml": ("Workflow lockdown guard", {"refresh-source-attribution"}),
}


class VerificationError(RuntimeError):
    pass


def _api(path: str, *, body: dict | None = None, timeout: float = 60):
    argv = ["gh", "api", path]
    if body is not None:
        argv += ["--method", "POST", "--input", "-"]
    result = subprocess.run(
        argv, input=None if body is None else json.dumps(body),
        check=True, capture_output=True, text=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def _pages(path: str, key: str, *, deadline: float) -> list[dict]:
    rows = []
    for page in range(1, 11):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError("post-merge workflow verification deadline expired")
        separator = "&" if "?" in path else "?"
        data = _api(f"{path}{separator}per_page=100&page={page}", timeout=min(60, remaining))
        batch = data.get(key) if isinstance(data, dict) else None
        if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
            raise VerificationError(f"invalid workflow {key} response")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise VerificationError(f"workflow {key} exceeds the bounded pagination limit")


def _snapshot(repository: str, merge_sha: str, tag: str) -> None:
    ref = _api(f"repos/{repository}/git/ref/tags/{quote(tag, safe='')}")
    target = ref.get("object") if isinstance(ref, dict) else None
    if isinstance(target, dict) and target.get("type") == "tag":
        annotation = _api(f"repos/{repository}/git/tags/{target['sha']}")
        target = annotation.get("object") if isinstance(annotation, dict) else None
    if not isinstance(target, dict) or target.get("type") != "commit" or target.get("sha") != merge_sha:
        raise VerificationError("promotion tag does not point at the exact merged commit")
    comparison = _api(f"repos/{repository}/compare/{merge_sha}...main")
    if not isinstance(comparison, dict) or comparison.get("status") not in ("ahead", "identical"):
        raise VerificationError("the merged release is not on main")


def _runs(repository: str, filename: str, title: str, *, merge_sha: str, deadline: float) -> list[dict]:
    return [
        run for run in _pages(
            f"repos/{repository}/actions/workflows/{filename}/runs?event=workflow_dispatch&head_sha={merge_sha}",
            "workflow_runs", deadline=deadline,
        )
        if run.get("display_title") == title
    ]


def _identity(run: dict, *, workflow_id: int, merge_sha: str, tag: str) -> int:
    if (
        type(run.get("id")) is not int or run["id"] <= 0
        or run.get("workflow_id") != workflow_id
        or run.get("event") != "workflow_dispatch"
        or run.get("head_sha") != merge_sha or run.get("head_branch") != tag
    ):
        raise VerificationError("dispatched workflow run does not match the exact promotion")
    return run["id"]


def verify(
    repository: str, merge_sha: str, tag: str, promotion_run: str, *, wait_seconds: int = 5100,
) -> dict[str, int]:
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+", repository)
        or repository.rsplit("/", 1)[-1] in (".", "..")
        or not re.fullmatch(r"[0-9a-f]{40}", merge_sha)
        or not re.fullmatch(r"promote-[0-9]{14}-[0-9a-f]{8}", tag)
        or not tag.endswith("-" + merge_sha[:8])
        or not re.fullmatch(r"[0-9]{1,20}-[0-9]{1,10}", promotion_run)
        or type(wait_seconds) is not int or wait_seconds <= 0
    ):
        raise VerificationError("invalid immutable promotion identity")
    if not os.environ.get("GH_TOKEN"):
        raise VerificationError("explicit workflow authority is unavailable")
    deadline = time.monotonic() + wait_seconds
    _snapshot(repository, merge_sha, tag)
    pending = {}
    receipts = {}
    for filename, (name, required) in WORKFLOWS.items():
        workflow = _api(f"repos/{repository}/actions/workflows/{filename}")
        if (
            not isinstance(workflow, dict) or type(workflow.get("id")) is not int
            or workflow.get("state") != "active" or workflow.get("name") != name
            or workflow.get("path") != f".github/workflows/{filename}"
        ):
            raise VerificationError(f"required workflow is unavailable: {filename}")
        title = f"{name} promotion {promotion_run} at {merge_sha}"
        existing = _runs(repository, filename, title, merge_sha=merge_sha, deadline=deadline)
        if len(existing) > 1:
            raise VerificationError(f"ambiguous promotion workflow runs: {filename}")
        if existing:
            _identity(existing[0], workflow_id=workflow["id"], merge_sha=merge_sha, tag=tag)
        else:
            _api(f"repos/{repository}/actions/workflows/{filename}/dispatches", body={
                "ref": tag, "inputs": {"promotion_sha": merge_sha, "promotion_run": promotion_run},
            })
        pending[filename] = (workflow["id"], title, required)
    while pending:
        for filename, (workflow_id, title, required) in list(pending.items()):
            runs = _runs(repository, filename, title, merge_sha=merge_sha, deadline=deadline)
            if len(runs) > 1:
                raise VerificationError(f"ambiguous promotion workflow runs: {filename}")
            if not runs:
                continue
            run = runs[0]
            run_id = _identity(run, workflow_id=workflow_id, merge_sha=merge_sha, tag=tag)
            if run.get("status") != "completed":
                continue
            if run.get("conclusion") != "success":
                raise VerificationError(f"{filename} run {run_id} failed: {run.get('conclusion')}")
            jobs = _pages(
                f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest",
                "jobs", deadline=deadline,
            )
            succeeded = {job.get("name") for job in jobs if job.get("conclusion") == "success"}
            if not required <= succeeded:
                raise VerificationError(f"{filename} skipped required jobs: {sorted(required - succeeded)}")
            failed = [
                job.get("name") for job in jobs
                if job.get("conclusion") not in ("success", "skipped")
            ]
            if failed:
                raise VerificationError(f"{filename} has unsuccessful jobs: {failed}")
            receipts[filename] = run_id
            del pending[filename]
            print(f"Verified {filename}: https://github.com/{repository}/actions/runs/{run_id}", flush=True)
        if pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VerificationError("post-merge workflow verification deadline expired")
            time.sleep(min(30, remaining))
    _snapshot(repository, merge_sha, tag)
    return receipts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--merge-sha", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--promotion-run", required=True)
    args = parser.parse_args(argv)
    try:
        verify(args.repository, args.merge_sha, args.tag, args.promotion_run)
    except (VerificationError, subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"::error::post-merge-workflows: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr, end="")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
