"""GitHub observed-check rollup with bounded, actionable failure diagnostics."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from urllib.parse import urlparse

from ..pr_checks import CheckFailure, ChecksReport
from .github_check_pages import check_runs_from_pages

MAX_FAILURES = 64
MAX_TEXT_CHARS = 2048
FAILED_CONCLUSIONS = frozenset({
    "failure", "timed_out", "action_required", "cancelled", "startup_failure", "stale",
})


def _text(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    suffix = " ... [truncated]"[:limit] if len(value) > limit else ""
    budget = max(0, limit - len(suffix))
    clean = "".join(char if char.isprintable() else " " for char in value[:budget])
    return clean.strip() + suffix


def _url(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS:
        return ""
    if any(not char.isprintable() or char.isspace() for char in value):
        return ""
    try:
        parsed = urlparse(value)
        valid = parsed.scheme in ("http", "https") and bool(parsed.netloc)
        return value if valid and not parsed.username and not parsed.password else ""
    except ValueError:
        return ""


def read_checks(
    repo: str, sha: str, *, host: str,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> ChecksReport:
    """Read existing status/check-run endpoints once; failure dominates pending.

    This is observed evidence, including optional reviewer checks. Neither a
    green rollup nor its diagnostic list proves required merge-policy readiness.
    """
    if not sha:
        return ChecksReport()
    failures: list[CheckFailure] = []
    failure_count = 0
    any_signal = failure = pending = False
    status_readable = runs_readable = False

    def add(item: CheckFailure) -> None:
        nonlocal failure_count
        failure_count += 1
        if len(failures) < MAX_FAILURES:
            failures.append(item)

    proc = invoke([
        "gh", "api", "--hostname", host, f"/repos/{repo}/commits/{sha}/status",
    ])
    if proc.returncode == 0:
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            statuses = data.get("statuses")
            if isinstance(statuses, list):
                status_readable = True
            if isinstance(statuses, list) and statuses:
                any_signal = True
                raw = str(data.get("state", "")).strip().lower()
                failure = raw in ("failure", "error")
                pending = raw == "pending"
                if raw not in ("failure", "error", "pending", "success"):
                    status_readable = False
                for status in statuses:
                    if not isinstance(status, dict):
                        status_readable = False
                        continue
                    state = str(status.get("state", "")).strip().lower()
                    if state in ("failure", "error"):
                        failure = True
                        add(CheckFailure(
                            _text(status.get("context")) or "(unnamed commit status)",
                            state, "commit-status", _url(status.get("target_url")),
                            _text(status.get("description")),
                        ))
                if failure and not failures:
                    add(CheckFailure(
                        "combined commit status", raw, "commit-status",
                        description="Provider aggregate failed without an identified failing status.",
                    ))
                total = data.get("total_count")
                if type(total) is int and total > len(statuses):
                    status_readable = False

    proc = invoke([
        "gh", "api", "--paginate", "--hostname", host,
        f"/repos/{repo}/commits/{sha}/check-runs",
    ])
    runs = check_runs_from_pages(proc.stdout or "") if proc.returncode == 0 else None
    if isinstance(runs, list):
        runs_readable = True
        any_signal = any_signal or bool(runs)
        for run in runs:
            if not isinstance(run, dict):
                runs_readable = False
                continue
            if str(run.get("status", "")).strip().lower() != "completed":
                pending = True
                continue
            conclusion = str(run.get("conclusion", "")).strip().lower()
            if conclusion in FAILED_CONCLUSIONS:
                failure = True
                output = run.get("output")
                output = output if isinstance(output, dict) else {}
                description = output.get("summary") or output.get("title") or ""
                add(CheckFailure(
                    _text(run.get("name")) or "(unnamed check run)",
                    conclusion, "check-run",
                    _url(run.get("details_url")) or _url(run.get("html_url")),
                    _text(description),
                ))
            elif conclusion not in ("success", "neutral", "skipped"):
                runs_readable = False

    complete = status_readable and runs_readable
    state = (
        "failure" if failure else "pending" if pending
        else ("success" if any_signal else "none") if complete else ""
    )
    return ChecksReport(state, tuple(failures), complete, failure_count - len(failures))
