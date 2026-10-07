#!/usr/bin/env python3
"""Historical CI flakiness/reliability telemetry for `dev`.

Phase 1 of the ci-flakiness-telemetry-and-reliability effort
(`efforts/active/ci-flakiness-telemetry-and-reliability/README.md`). This is
**not** a rebuild of `tools/ci_failure_watchdog.py`'s reactive, single-run
detection+dedup: that script reacts in real time to one `workflow_run`
completion; this script mines the GitHub Actions REST API's run *history*
over a bounded lookback window to find recurring, high-impact noise no
single red run reveals on its own (see the effort README's Phase 0 decision).

It deliberately reuses `ci_failure_watchdog`'s own failure-signature
computation (`signature_key()` / `build_signatures()`) so both tools agree
on "is this the same failure" -- two independent signature schemes would
silently disagree and undermine both efforts' dedup guarantees.

Two signals are computed per distinct failure signature:

- **Rerun-recovery rate** -- of the distinct commits (`head_sha`) a
  signature was ever observed on, what fraction of those commits *also* had
  a later successful run with no code change in between (the same-commit-
  reran-and-passed pattern this repo has directly observed to distinguish a
  flake from a genuine regression).
- **Blocking impact** -- how many other runs on the branch were queued
  behind each failing run before the next success (the promotion pipeline
  only advances on a green run, so a red run stalls everything behind it).

Usage::

    python tools/ci_telemetry.py --db tools/ci_telemetry.sqlite3 refresh   # fetch + persist (needs gh + GH_TOKEN)
    python tools/ci_telemetry.py --db tools/ci_telemetry.sqlite3 report     # print the ranked report from persisted data

`refresh` is the only network-touching subcommand; `report` only reads the
already-populated database, so it works offline and is what CI/local
spot-checks should use after a `refresh`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ci_failure_watchdog", _HERE / "ci_failure_watchdog.py")
ci_failure_watchdog = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules.setdefault(_spec.name, ci_failure_watchdog)
_spec.loader.exec_module(ci_failure_watchdog)

FailureSignature = ci_failure_watchdog.FailureSignature
build_signatures = ci_failure_watchdog.build_signatures
LookupFailed = ci_failure_watchdog.LookupFailed
REPORTABLE_CONCLUSIONS = ci_failure_watchdog.REPORTABLE_CONCLUSIONS
SKIP_JOB_NAMES = ci_failure_watchdog.SKIP_JOB_NAMES

DEFAULT_REPO = ci_failure_watchdog.DEFAULT_REPO
DEFAULT_BRANCH = "dev"
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_DB = _HERE / "ci_telemetry.sqlite3"

# Checks known to be permanently noisy but non-blocking -- surfaced
# explicitly in the ranking rather than silently omitted, per the effort's
# own Plan (Phase 2). Not auto-detected: these are PR check-suite entries,
# a genuinely different API surface (`GET .../commits/{sha}/check-runs`)
# from the `dev`-branch workflow-run history this script otherwise mines,
# and are few enough in practice to track by hand rather than justifying a
# second live-fetch path this phase does not need. Remove an entry here once
# its owning effort resolves it (confirmed via a fresh `refresh` + `report`
# showing it no longer applies).
#
# The sole prior entry (`identifier-leak-guard-unconfigured`, the
# `FORBIDDEN_IDS_FACILITY`/`FORBIDDEN_IDS_WORK`-unset noise) was removed
# 2026-10-06: `efforts/active/ci-identifier-leak-guard/` (#3923) provisioned
# the secrets, and `identifier leak guard` now reports `pass` consistently
# across live PRs (spot-checked #5553, #5440, #5383, #5325) -- it is no
# longer unconditionally red and does not belong in this tuple.
KNOWN_NOISY_NONBLOCKING_CHECKS: tuple[dict, ...] = ()


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunRecord:
    run_id: int
    head_sha: str
    conclusion: str | None
    created_at: datetime
    html_url: str
    attempt: int = 1
    source: str = "dev-push"


@dataclass(frozen=True)
class FailureRecord:
    run_id: int
    head_sha: str
    created_at: datetime
    job_name: str
    signature: FailureSignature
    attempt: int = 1
    source: str = "dev-push"


@dataclass
class SignatureStat:
    key: str
    title: str
    sample_excerpt: str
    occurrences: int = 0
    distinct_shas: int = 0
    recovered_shas: int = 0
    blocking_impact: int = 0
    latest_occurrence: datetime | None = None

    @property
    def recovery_rate(self) -> float:
        return self.recovered_shas / self.distinct_shas if self.distinct_shas else 0.0


# --------------------------------------------------------------------------
# Pure aggregation (no network I/O -- unit-testable in isolation)
# --------------------------------------------------------------------------


def compute_flaky_shas(runs: list[RunRecord]) -> set[str]:
    """head_shas that had both a failing run and a later successful run --
    the same-commit-reran-and-passed signal, i.e. a flake, not a persisting
    regression that needed an actual code fix to go green."""
    by_sha: dict[str, list[RunRecord]] = {}
    for run in runs:
        by_sha.setdefault(run.head_sha, []).append(run)
    flaky: set[str] = set()
    for sha, sha_runs in by_sha.items():
        # Sort by (created_at, run_id, attempt): a rerun's earlier and later
        # attempts share the IDENTICAL top-level `created_at` (the attempts
        # API doesn't expose a distinct per-attempt start time in the
        # fields fetched here), so `created_at` alone is not a stable
        # chronological order between them -- `attempt` as the tie-break
        # guarantees attempt 1 always precedes attempt 2, matching reality,
        # regardless of the order records happened to be fetched/appended in.
        ordered = sorted(sha_runs, key=lambda r: (r.created_at, r.run_id, r.attempt))
        saw_failure = False
        for run in ordered:
            if run.conclusion in REPORTABLE_CONCLUSIONS:
                saw_failure = True
            elif run.conclusion == "success" and saw_failure:
                flaky.add(sha)
                break
    return flaky


def compute_blocking_impact(runs: list[RunRecord]) -> dict[tuple[int, int], int]:
    """For each failing run, how many other runs landed after it and before
    the next successful run -- an approximation of how many pushes/commits
    were stalled behind that failure (the pipeline only promotes on a green
    run). Restricted to ``dev-push`` runs -- PR-time runs are concurrent
    across many unrelated PRs and don't represent a single serialized
    promotion queue, so "blocked" has no meaningful definition for them
    here (their contribution is captured via the recovery-rate signal
    instead, computed over all sources in `compute_flaky_shas`)."""
    ordered = sorted(
        (r for r in runs if r.source == "dev-push"), key=lambda r: (r.created_at, r.run_id, r.attempt)
    )
    impact: dict[tuple[int, int], int] = {}
    for i, run in enumerate(ordered):
        if run.conclusion not in REPORTABLE_CONCLUSIONS:
            continue
        blocked = 0
        for later in ordered[i + 1 :]:
            # A later attempt of the SAME run_id (a rerun) is not a new,
            # separately-stalled commit -- it's the same commit's own retry.
            # A same-run success still legitimately ends the stalled
            # interval (the queue is unblocked); a same-run failure must
            # not be counted as one more run stuck behind itself.
            if later.run_id == run.run_id:
                if later.conclusion == "success":
                    break
                continue
            blocked += 1
            if later.conclusion == "success":
                break
        impact[(run.run_id, run.attempt)] = blocked
    return impact


def compute_signature_stats(
    failures: list[FailureRecord], runs: list[RunRecord]
) -> list[SignatureStat]:
    flaky_shas = compute_flaky_shas(runs)
    blocking_by_run = compute_blocking_impact(runs)

    stats: dict[str, SignatureStat] = {}
    shas_by_key: dict[str, set[str]] = {}
    for failure in failures:
        stat = stats.setdefault(
            failure.signature.key,
            SignatureStat(
                key=failure.signature.key,
                title=failure.signature.title,
                sample_excerpt=failure.signature.excerpt,
            ),
        )
        stat.occurrences += 1
        stat.blocking_impact += blocking_by_run.get((failure.run_id, failure.attempt), 0)
        if stat.latest_occurrence is None or failure.created_at > stat.latest_occurrence:
            stat.latest_occurrence = failure.created_at
        shas_by_key.setdefault(failure.signature.key, set()).add(failure.head_sha)

    for key, shas in shas_by_key.items():
        stat = stats[key]
        stat.distinct_shas = len(shas)
        stat.recovered_shas = len(shas & flaky_shas)

    return list(stats.values())


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    head_sha TEXT NOT NULL,
    conclusion TEXT,
    created_at TEXT NOT NULL,
    html_url TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'dev-push',
    PRIMARY KEY (run_id, attempt)
);
CREATE TABLE IF NOT EXISTS failures (
    run_id INTEGER NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    head_sha TEXT NOT NULL,
    created_at TEXT NOT NULL,
    job_name TEXT NOT NULL,
    signature_key TEXT NOT NULL,
    test_id TEXT,
    title TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'dev-push',
    PRIMARY KEY (run_id, attempt, job_name, signature_key)
);
"""


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def persist_runs(conn: sqlite3.Connection, runs: list[RunRecord]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO runs (run_id, attempt, head_sha, conclusion, created_at, html_url, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (r.run_id, r.attempt, r.head_sha, r.conclusion, r.created_at.isoformat(), r.html_url, r.source)
            for r in runs
        ],
    )
    conn.commit()


def persist_failures(conn: sqlite3.Connection, failures: list[FailureRecord]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO failures "
        "(run_id, attempt, head_sha, created_at, job_name, signature_key, test_id, title, excerpt, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                f.run_id,
                f.attempt,
                f.head_sha,
                f.created_at.isoformat(),
                f.job_name,
                f.signature.key,
                f.signature.test_id,
                f.signature.title,
                f.signature.excerpt,
                f.source,
            )
            for f in failures
        ],
    )
    conn.commit()


def load_runs(conn: sqlite3.Connection) -> list[RunRecord]:
    rows = conn.execute(
        "SELECT run_id, head_sha, conclusion, created_at, html_url, attempt, source FROM runs"
    ).fetchall()
    return [
        RunRecord(
            run_id=row[0],
            head_sha=row[1],
            conclusion=row[2],
            created_at=datetime.fromisoformat(row[3]),
            html_url=row[4],
            attempt=row[5],
            source=row[6],
        )
        for row in rows
    ]


def load_failures(conn: sqlite3.Connection) -> list[FailureRecord]:
    rows = conn.execute(
        "SELECT run_id, head_sha, created_at, job_name, signature_key, test_id, title, excerpt, attempt, source "
        "FROM failures"
    ).fetchall()
    return [
        FailureRecord(
            run_id=row[0],
            head_sha=row[1],
            created_at=datetime.fromisoformat(row[2]),
            job_name=row[3],
            signature=FailureSignature(job_name=row[3], test_id=row[5], key=row[4], excerpt=row[7]),
            attempt=row[8],
            source=row[9],
        )
        for row in rows
    ]


# --------------------------------------------------------------------------
# Network I/O (GitHub Actions REST API via `gh`)
# --------------------------------------------------------------------------

# PR-triggered `ci.yml` runs carry a job name that must never become a
# per-run failure signature of its own: `PR gate (required check)` is a
# redundant aggregate of whatever real job already failed (double-counting
# it). `identifier leak guard` used to be skipped here too, while it was
# the *already-known*, unconditionally red, non-blocking check tracked
# once via `KNOWN_NOISY_NONBLOCKING_CHECKS` above -- that rationale is gone
# now that entry has been removed (2026-10-06, #3923 provisioned the
# denylist secrets and the check reports real pass/fail): a genuine future
# leak-guard failure should surface through ordinary mining like any other
# check, not be silently skipped.
PR_SKIP_JOB_NAMES = frozenset({"PR gate (required check)"})


def _fetch_prior_attempts(repo: str, run: dict, source: str) -> list[RunRecord]:
    """A rerun (`gh run rerun --failed`) reuses the SAME run id at a higher
    `run_attempt` -- it does not appear as a new entry in the runs list, so
    the earlier failing attempt(s) would otherwise be invisible, and the
    same-commit-reran-and-passed signal (`compute_flaky_shas`) would never
    fire. Fetch each attempt below the latest via the attempts endpoint to
    recover their own conclusion."""
    latest_attempt = run.get("run_attempt", 1)
    if latest_attempt <= 1:
        return []
    records: list[RunRecord] = []
    for attempt in range(1, latest_attempt):
        out = ci_failure_watchdog._run_gh(
            ["api", f"repos/{repo}/actions/runs/{run['id']}/attempts/{attempt}"]
        )
        if out.returncode != 0:
            continue

        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            continue
        records.append(
            RunRecord(
                run_id=run["id"],
                attempt=attempt,
                head_sha=run["head_sha"],
                conclusion=data.get("conclusion"),
                created_at=datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")),
                html_url=run["html_url"],
                source=source,
            )
        )
    return records


def fetch_runs(repo: str, branch: str | None, lookback_days: int, event: str = "push") -> list[RunRecord]:
    source = "dev-push" if event == "push" else "pr"
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    runs: list[RunRecord] = []
    page = 1
    branch_qs = f"branch={branch}&" if branch else ""
    while True:
        out = ci_failure_watchdog._run_gh(
            [
                "api",
                f"repos/{repo}/actions/runs?{branch_qs}event={event}&per_page=100&page={page}",
            ]
        )
        if out.returncode != 0:
            raise LookupFailed(out.stderr.strip() or f"gh api runs exited {out.returncode}")

        payload = json.loads(out.stdout)
        page_runs = payload.get("workflow_runs", [])
        if not page_runs:
            break
        stop = False
        for run in page_runs:
            created_at = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            if created_at < cutoff:
                stop = True
                continue
            runs.append(
                RunRecord(
                    run_id=run["id"],
                    attempt=run.get("run_attempt", 1),
                    head_sha=run["head_sha"],
                    conclusion=run.get("conclusion"),
                    created_at=created_at,
                    html_url=run["html_url"],
                    source=source,
                )
            )
            runs.extend(_fetch_prior_attempts(repo, run, source))
        if stop or len(page_runs) < 100:
            break
        page += 1
    return runs


def fetch_failures_for_run(repo: str, run: RunRecord) -> list[FailureRecord]:
    if run.conclusion not in REPORTABLE_CONCLUSIONS:
        return []
    # The attempt-scoped jobs endpoint works uniformly for the latest
    # attempt as well as any prior one -- always use it (never the
    # attempt-less jobs endpoint) so a prior, since-superseded failing
    # attempt's own jobs/logs are recoverable, not just the latest attempt's.
    out = ci_failure_watchdog._run_gh(
        ["api", f"repos/{repo}/actions/runs/{run.run_id}/attempts/{run.attempt}/jobs?per_page=100"]
    )
    if out.returncode != 0:
        return []

    try:
        jobs = json.loads(out.stdout)["jobs"]
    except (json.JSONDecodeError, KeyError):
        return []
    skip_names = SKIP_JOB_NAMES | PR_SKIP_JOB_NAMES if run.source == "pr" else SKIP_JOB_NAMES
    failed_jobs = [
        job
        for job in jobs
        if job.get("conclusion") in REPORTABLE_CONCLUSIONS and job.get("name") not in skip_names
    ]
    records: list[FailureRecord] = []
    for job in failed_jobs:
        try:
            log_text = ci_failure_watchdog._fetch_job_log(repo, job["id"])
        except LookupFailed:
            continue
        for sig in build_signatures(job["name"], log_text):
            records.append(
                FailureRecord(
                    run_id=run.run_id,
                    head_sha=run.head_sha,
                    created_at=run.created_at,
                    job_name=job["name"],
                    signature=sig,
                    attempt=run.attempt,
                    source=run.source,
                )
            )
    return records


# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------


def render_report(stats: list[SignatureStat]) -> str:
    lines: list[str] = []
    lines.append("# CI flakiness/reliability telemetry report\n")

    lines.append("## Ranked by frequency (occurrences)\n")
    for stat in sorted(stats, key=lambda s: s.occurrences, reverse=True):
        lines.append(
            f"- `{stat.key}` **{stat.title}** -- {stat.occurrences} occurrence(s) across "
            f"{stat.distinct_shas} commit(s); rerun-recovery {stat.recovery_rate:.0%} "
            f"({stat.recovered_shas}/{stat.distinct_shas}); blocking impact "
            f"{stat.blocking_impact} run(s) stalled"
        )

    lines.append("\n## Ranked by blocking impact\n")
    for stat in sorted(stats, key=lambda s: s.blocking_impact, reverse=True):
        lines.append(
            f"- `{stat.key}` **{stat.title}** -- blocked {stat.blocking_impact} run(s); "
            f"{stat.occurrences} occurrence(s), rerun-recovery {stat.recovery_rate:.0%}"
        )

    lines.append("\n## Known noisy, non-blocking checks (tracked separately, not run-history-derived)\n")
    if not KNOWN_NOISY_NONBLOCKING_CHECKS:
        lines.append("- none currently tracked\n")
    for entry in KNOWN_NOISY_NONBLOCKING_CHECKS:
        lines.append(f"- `{entry['key']}` **{entry['title']}** -- {entry['note']}")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def replace_source_snapshot(conn: sqlite3.Connection, sources: set[str]) -> None:
    """Drop every persisted row for the given sources before a fresh
    `refresh` re-persists them -- `INSERT OR REPLACE` alone never removes a
    row that's fallen out of the requested lookback window (or a source
    `--dev-only` no longer fetches), so a repeated refresh would otherwise
    accumulate stale, out-of-window data forever and silently corrupt every
    subsequent `report`."""
    placeholders = ",".join("?" for _ in sources)
    conn.execute(f"DELETE FROM runs WHERE source IN ({placeholders})", tuple(sources))
    conn.execute(f"DELETE FROM failures WHERE source IN ({placeholders})", tuple(sources))
    conn.commit()


def cmd_refresh(args: argparse.Namespace) -> int:
    conn = open_db(Path(args.db))
    sources = {"dev-push"} if args.dev_only else {"dev-push", "pr"}
    try:
        runs = fetch_runs(args.repo, args.branch, args.lookback_days, event="push")
        if not args.dev_only:
            runs += fetch_runs(args.repo, None, args.lookback_days, event="pull_request")
    except LookupFailed as error:
        print(f"[ERROR] could not fetch run history: {error}", file=sys.stderr)
        return 1
    replace_source_snapshot(conn, sources)
    persist_runs(conn, runs)
    print(f"[OK] persisted {len(runs)} run(s)/attempt(s)")

    # Only ever mine jobs/logs for runs that actually failed -- a clean
    # success carries nothing to sign; skip the (comparatively expensive)
    # per-run jobs+logs calls for the overwhelming common case.
    all_failures: list[FailureRecord] = []
    for run in runs:
        all_failures.extend(fetch_failures_for_run(args.repo, run))
    persist_failures(conn, all_failures)
    print(f"[OK] persisted {len(all_failures)} failure signature occurrence(s)")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    conn = open_db(Path(args.db))
    runs = load_runs(conn)
    failures = load_failures(conn)
    if not runs:
        print("[WARN] no run history persisted yet -- run `refresh` first.", file=sys.stderr)
        return 1
    stats = compute_signature_stats(failures, runs)
    print(render_report(stats))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--dev-only", action="store_true",
        help="only mine dev-push runs -- skip PR-triggered ci.yml history (faster, but loses the rerun-recovery signal, which is observed almost exclusively on PR-time reruns, not dev-push runs)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("refresh", help="fetch fresh run history from GitHub and persist it")
    subparsers.add_parser("report", help="print the ranked report from already-persisted data")

    args = parser.parse_args(argv)
    if args.command == "refresh":
        return cmd_refresh(args)
    return cmd_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
