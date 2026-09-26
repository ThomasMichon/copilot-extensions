#!/usr/bin/env python3
"""Report (never fix) a red `dev` validation run.

Phase 1 of the promotion-failure-reactive-fix-agent effort
(`efforts/active/promotion-failure-reactive-fix-agent/README.md`):
`validate-and-promote.yml`'s `report-failure` job invokes this after any of
`full`/`worktree-manager`/`guards-full-sweep` fails on a genuine `dev`
commit. It finds the failed job(s) in the *current* run, extracts a compact
failure signature (the failing pytest node id when one is parseable, else a
whole-job signature keyed off the log tail), dedupes against an already-open
tracking issue via a hidden `Signature: <hash>` anchor line (mirroring
`tools/module-health-watchdog.py`'s own `Module: <path>` anchor pattern), and
either files a new issue or -- rate-limited -- comments on the existing one.

This script **never attempts a fix**. It only ever reports or files, exactly
like `module-health-watchdog.py`; Phase 2 (an actual fix-attempt mechanism)
is separate, gated work the effort's own Plan blocks behind a vision
reconciliation this script does not touch.

Usage::

    python tools/ci_failure_watchdog.py --run-id 123 --sha abc123            # dry run, prints only
    python tools/ci_failure_watchdog.py --run-id 123 --sha abc123 --file-issue  # actually file/comment (needs gh + GH_TOKEN)
    python tools/ci_failure_watchdog.py --run-id 123 --sha abc123 --file-issue --rate-limit-hours 12

Exit code is 0 for detection/reporting and a successful (or skipped/deduped)
filing; nonzero only when `--file-issue` was asked to actually file/comment
and the `gh` call itself failed -- mirroring `module-health-watchdog.py`'s
own contract so a scheduled/CI run never reports success while silently
doing nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

ISSUE_LABEL = "ci-failure-signature"
DEFAULT_REPO = "ThomasMichon/copilot-extensions"
DEFAULT_RATE_LIMIT_HOURS = 6

# The three control jobs that surround this one in validate-and-promote.yml --
# never treat their own failure (or this job's own in-progress/null
# conclusion) as a signal to report on. Keep in sync with the workflow file's
# actual job `name:` values.
SKIP_JOB_NAMES = frozenset(
    {
        "gate (confirm this is a dev commit)",
        "Promote dev -> main",
        "report failure (Phase 1 watchdog; detection + dedup only, no fix attempt)",
    }
)

# Job conclusions that mean "the run went red because of this job" --
# `timed_out` (a job that exceeded its own `timeout-minutes`) turns the run
# red exactly like `failure` and must be reportable too.
REPORTABLE_CONCLUSIONS = frozenset({"failure", "timed_out"})

_FAILED_TEST_RE = re.compile(
    # A pytest node id is `<path with no spaces>::<name with no spaces or
    # brackets>` optionally followed by a `[...]` parametrize suffix, whose
    # *contents* may contain spaces (but not `]`). Capturing this shape
    # directly -- rather than capturing the whole rest of the line and
    # splitting on `` - `` afterwards -- is what makes this safe against a
    # failure *reason* that itself contains `` - `` (e.g. "AssertionError:
    # left - right"), which would otherwise be mistaken for the node-id/
    # reason separator. The optional `` - <reason>`` tail is discarded.
    r"^FAILED (\S+::[^\[\s]+(?:\[[^\]]*\])?)(?: - .*)?$",
    re.MULTILINE,
)
_TIMESTAMP_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ?", re.MULTILINE)


def _strip_timestamps(text: str) -> str:
    """Strip GitHub Actions' own per-line ISO-8601 timestamp prefix.

    The job-level Actions log timestamps **every** line -- including
    pytest's own ``FAILED <nodeid>`` summary lines, e.g.
    ``2026-09-26T05:08:32.6650925Z FAILED tests/x.py::test_y - ...`` -- so
    matching must happen on the timestamp-stripped text, never the raw log,
    or a `^FAILED` anchor never matches a single real Actions log line.
    This is also what makes the whole-job fallback signature dedupe
    correctly across runs (see `build_signatures`).
    """
    return _TIMESTAMP_PREFIX_RE.sub("", text)


@dataclass(frozen=True)
class FailureSignature:
    job_name: str
    test_id: str | None  # None => whole-job signature (no parseable test id)
    key: str  # stable dedup key
    excerpt: str  # short log excerpt for the issue/comment body

    @property
    def title(self) -> str:
        if self.test_id:
            return f"CI failure: {self.test_id}"
        return f"CI failure: {self.job_name}"


def extract_failed_test_ids(log_text: str) -> list[str]:
    """Return de-duplicated pytest ``FAILED <path>::<test>`` node ids found
    in ``log_text``, in first-seen order.

    Matches against the timestamp-stripped text (see `_strip_timestamps`) --
    a real Actions log timestamps every line, so an un-stripped ``^FAILED``
    anchor never matches at all. `_FAILED_TEST_RE`'s node-id shape (a real
    `::` boundary, no unescaped `` - `` inside it) already excludes
    `tools/run-plugin-tests.py`'s own non-test wrapper summary line
    (``FAILED plugins: <name>``), which would otherwise produce a
    misleading extra signature/issue instead of falling through to the
    whole-job signature.
    """
    seen: dict[str, None] = {}
    for match in _FAILED_TEST_RE.finditer(_strip_timestamps(log_text)):
        candidate = match.group(1)
        if "::" not in candidate:
            continue
        seen.setdefault(candidate, None)
    return list(seen)


def _excerpt_around(log_text: str, needle: str, before: int = 15, after: int = 3) -> str:
    lines = log_text.splitlines()
    for i, line in enumerate(lines):
        if needle in line:
            start = max(0, i - before)
            end = min(len(lines), i + after)
            return "\n".join(lines[start:end])
    return "\n".join(lines[-40:])


def signature_key(job_name: str, test_id: str | None, fallback_text: str = "") -> str:
    # Always incorporate job_name, even for a parseable test id: this repo
    # vendors shared libs into multiple plugins (`full` runs each plugin as
    # its own job), so the identical test node id can legitimately fail in
    # several different jobs at once -- those are different failures (a
    # different plugin's copy) and must never collapse into one issue.
    basis = f"{job_name}:{test_id}" if test_id else f"{job_name}:{fallback_text[:500]}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def build_signatures(job_name: str, log_text: str) -> list[FailureSignature]:
    """One signature per distinct failing test id in ``log_text``; if none
    are found (a non-pytest failure -- e.g. a script/tool crash in
    ``guards-full-sweep``), a single whole-job signature keyed off the log
    tail instead."""
    test_ids = extract_failed_test_ids(log_text)
    if not test_ids:
        excerpt = "\n".join(log_text.splitlines()[-40:])
        # Hash the timestamp-stripped excerpt (stable across runs); keep the
        # raw, timestamped excerpt for the human-facing issue/comment body.
        stable_basis = _strip_timestamps(excerpt)
        return [
            FailureSignature(
                job_name=job_name,
                test_id=None,
                key=signature_key(job_name, None, stable_basis),
                excerpt=excerpt,
            )
        ]
    return [
        FailureSignature(
            job_name=job_name,
            test_id=test_id,
            key=signature_key(job_name, test_id),
            excerpt=_excerpt_around(log_text, f"FAILED {test_id}"),
        )
        for test_id in test_ids
    ]


class LookupFailed(RuntimeError):
    """A `gh` call that could not be completed reliably -- distinct from a
    clean "no match"/"nothing failed". Callers must never treat this the
    same as a confirmed negative (that would risk filing a real duplicate,
    or silently skipping a real failure, on a transient API hiccup)."""


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def _fetch_run_jobs(repo: str, run_id: str) -> list[dict]:
    out = _run_gh(["api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"])
    if out.returncode != 0:
        raise LookupFailed(out.stderr.strip() or f"gh api jobs exited {out.returncode}")
    try:
        return json.loads(out.stdout)["jobs"]
    except (json.JSONDecodeError, KeyError) as error:
        raise LookupFailed(f"unparseable jobs response: {error}") from error


def _fetch_job_log(repo: str, job_id: int) -> str:
    out = _run_gh(["api", f"repos/{repo}/actions/jobs/{job_id}/logs"])
    if out.returncode != 0:
        raise LookupFailed(out.stderr.strip() or f"gh api logs exited {out.returncode}")
    return out.stdout


def _existing_issue(repo: str, key: str) -> dict | None:
    out = _run_gh(
        [
            "issue", "list",
            "--repo", repo,
            "--label", ISSUE_LABEL,
            "--state", "open",
            "--search", f'"Signature: {key}"',
            "--json", "number,updatedAt",
        ]
    )
    if out.returncode != 0:
        raise LookupFailed(out.stderr.strip() or f"gh issue list exited {out.returncode}")
    try:
        matches = json.loads(out.stdout or "[]")
    except json.JSONDecodeError as error:
        raise LookupFailed(f"unparseable gh issue list output: {error}") from error
    return matches[0] if matches else None


def _last_occurrence_at(issue: dict) -> datetime:
    """The most recent occurrence marker for rate-limiting.

    Deliberately just ``updatedAt`` (bumped by GitHub on any new comment,
    not only on filing) rather than fetching real comment objects: `gh
    issue list --json comments` returns only a **comment count**, not
    comment objects with timestamps (that shape only exists on `gh issue
    view <#> --json comments` for a single issue) -- using `updatedAt`
    avoids a schema mismatch entirely rather than adding a second `gh`
    round-trip just to recover a timestamp GitHub already tracks for us.
    """
    return datetime.fromisoformat(issue["updatedAt"].replace("Z", "+00:00"))


def is_rate_limited(issue: dict, now: datetime, hours: int) -> bool:
    return (now - _last_occurrence_at(issue)) < timedelta(hours=hours)


def _issue_body(sig: FailureSignature, repo: str, run_id: str, sha: str) -> str:
    where = f" on test `{sig.test_id}`" if sig.test_id else ""
    return (
        "## Summary\n\n"
        f"Job **{sig.job_name}** failed{where} during a `dev` validation run.\n\n"
        f"Signature: {sig.key}\n\n"
        f"- Run: https://github.com/{repo}/actions/runs/{run_id}\n"
        f"- Commit: `{sha}`\n\n"
        "## Log excerpt\n\n"
        f"```\n{sig.excerpt}\n```\n\n"
        "## Status\n\n"
        "Filed automatically by `tools/ci_failure_watchdog.py` "
        "(promotion-failure-reactive-fix-agent effort, Phase 1: detection + "
        "dedup only). **No fix has been attempted.** Per this repo's "
        "own contribution rule (`contributing-to-copilot-extensions`), "
        "whoever encounters a failing test on their PR build is "
        "responsible for fixing it -- this issue exists so a "
        "persistently-recurring failure doesn't need rediscovering from "
        "scratch on each red run, and accumulates future occurrences as "
        "comments rather than duplicate issues.\n"
    )


def _file_issue(repo: str, sig: FailureSignature, run_id: str, sha: str) -> bool:
    out = _run_gh(
        [
            "issue", "create",
            "--repo", repo,
            "--title", sig.title,
            "--label", ISSUE_LABEL,
            "--body", _issue_body(sig, repo, run_id, sha),
        ]
    )
    if out.returncode != 0:
        print(f"[ERROR] gh issue create failed: {out.stderr.strip()}", file=sys.stderr)
        return False
    print(f"[OK] filed {out.stdout.strip()}")
    return True


def _comment_occurrence(repo: str, issue_number: int, sig: FailureSignature, run_id: str, sha: str) -> bool:
    body = (
        f"Recurred: https://github.com/{repo}/actions/runs/{run_id} (commit `{sha}`).\n\n"
        f"```\n{sig.excerpt}\n```\n"
    )
    out = _run_gh(["issue", "comment", str(issue_number), "--repo", repo, "--body", body])
    if out.returncode != 0:
        print(f"[ERROR] gh issue comment failed: {out.stderr.strip()}", file=sys.stderr)
        return False
    print(f"[OK] commented on #{issue_number}")
    return True


def process_signature(
    repo: str,
    sig: FailureSignature,
    run_id: str,
    sha: str,
    rate_limit_hours: int,
    file_issue: bool,
) -> int:
    if not file_issue:
        print(f"[INFO] dry run -- would report signature {sig.key} ({sig.title})")
        return 0
    try:
        existing = _existing_issue(repo, sig.key)
    except LookupFailed as error:
        # Never treat "couldn't confirm" as "confirmed absent" -- abort this
        # signature without filing; the next red run tries again.
        print(
            f"[WARN] existing-issue lookup failed for {sig.key}, aborting without filing: {error}",
            file=sys.stderr,
        )
        return 0
    if existing is None:
        return 0 if _file_issue(repo, sig, run_id, sha) else 1
    if is_rate_limited(existing, datetime.now(timezone.utc), rate_limit_hours):
        print(
            f"[OK] #{existing['number']} already tracked and within the "
            f"{rate_limit_hours}h rate limit -- not commenting."
        )
        return 0
    return 0 if _comment_occurrence(repo, existing["number"], sig, run_id, sha) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"owner/name (default {DEFAULT_REPO})")
    parser.add_argument(
        "--run-id", required=True,
        help="the CURRENT validate-and-promote run id (github.run_id) -- its own failed jobs are what get reported",
    )
    parser.add_argument(
        "--sha", required=True,
        help=(
            "the already-verified dev SHA this run validated "
            "(needs.gate.outputs.sha) -- never re-derive this from a "
            "workflow_run payload; carry the value gate already confirmed"
        ),
    )
    parser.add_argument("--rate-limit-hours", type=int, default=DEFAULT_RATE_LIMIT_HOURS)
    parser.add_argument(
        "--file-issue", action="store_true",
        help="actually file/comment via `gh` (default: dry-run/report only)",
    )
    args = parser.parse_args(argv)

    try:
        jobs = _fetch_run_jobs(args.repo, args.run_id)
    except LookupFailed as error:
        print(f"[WARN] could not fetch run jobs, aborting: {error}", file=sys.stderr)
        return 0

    # A job that exceeds its own `timeout-minutes` gets conclusion
    # `timed_out`, not `failure` -- it still turns the run red (and is
    # exactly what happened with the reenter_runtime flake this effort was
    # kicked off by), so it must be reportable too, not silently dropped.
    failed_jobs = [
        job for job in jobs
        if job.get("conclusion") in REPORTABLE_CONCLUSIONS and job.get("name") not in SKIP_JOB_NAMES
    ]
    if not failed_jobs:
        print("[OK] no failed (non-control) jobs found in this run -- nothing to report.")
        return 0

    exit_code = 0
    for job in failed_jobs:
        try:
            log_text = _fetch_job_log(args.repo, job["id"])
        except LookupFailed as error:
            print(f"[WARN] could not fetch log for job {job['name']!r}: {error}", file=sys.stderr)
            continue
        for sig in build_signatures(job["name"], log_text):
            rc = process_signature(
                args.repo, sig, args.run_id, args.sha, args.rate_limit_hours, args.file_issue
            )
            exit_code = exit_code or rc
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
