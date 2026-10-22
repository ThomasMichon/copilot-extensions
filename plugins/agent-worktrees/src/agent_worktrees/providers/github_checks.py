"""GitHub observed-check rollup with bounded, actionable failure diagnostics."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from urllib.parse import urlparse

from ..pr_checks import CheckFailure, ChecksReport
from .github_check_pages import check_run_evidence_from_pages

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


def _read_pages(
    argv: list[str], invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> subprocess.CompletedProcess[str]:
    """Keep gh from following later pages with an already-acquired credential."""
    launch = [argument for argument in argv[:-1] if argument != "--paginate"]
    pages: list[str] = []
    seen: set[str] = set()
    records = 0
    for page in range(1, 202):
        proc = invoke([*launch, f"{argv[-1]}?per_page=100&page={page}"])
        pages.append(proc.stdout or "")
        if proc.returncode:
            return subprocess.CompletedProcess(argv, proc.returncode, "\n".join(pages), "")
        try:
            body = json.loads(proc.stdout)
        except (ValueError, TypeError):
            break
        batch = body.get("check_runs") if isinstance(body, dict) else None
        total = body.get("total_count") if isinstance(body, dict) else None
        if (
            not isinstance(batch, list) or type(total) is not int
            or not 0 <= total <= 20_000 or len(batch) > 100
        ):
            break
        signature = json.dumps(batch, sort_keys=True)
        if batch and signature in seen:
            break
        seen.add(signature)
        records += len(batch)
        if records > 20_000:
            break
        if records >= total or len(batch) < 100:
            return subprocess.CompletedProcess(argv, 0, "\n".join(pages), "")
    return subprocess.CompletedProcess(argv, 1, "\n".join(pages), "")


def read_checks(
    repo: str, sha: str, *, host: str,
    invoke: Callable[[list[str]], subprocess.CompletedProcess[str]],
    renew_per_page: bool = False,
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
                if "total_count" in data:
                    total = data["total_count"]
                    if type(total) is not int or total != len(statuses):
                        status_readable = False
            if isinstance(statuses, list) and statuses:
                any_signal = True
                raw = str(data.get("state", "")).strip().lower()
                failure = raw in ("failure", "error")
                pending = raw == "pending"
                if raw not in ("failure", "error", "pending", "success"):
                    status_readable = False
                seen_contexts: set[str] = set()
                for status in statuses:
                    if not isinstance(status, dict):
                        status_readable = False
                        continue
                    state = str(status.get("state", "")).strip().lower()
                    valid_state = state in ("failure", "error", "pending", "success")
                    if not valid_state:
                        status_readable = False
                    context = status.get("context")
                    if isinstance(context, str) and context:
                        # Status arrays are newest-first; a rerun supersedes older context entries.
                        key = context.casefold()
                        if key in seen_contexts:
                            continue
                        seen_contexts.add(key)
                    if not valid_state:
                        continue
                    pending = pending or state == "pending"
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
    argv = [
        "gh", "api", "--paginate", "--hostname", host,
        f"/repos/{repo}/commits/{sha}/check-runs",
    ]
    proc = _read_pages(argv, invoke) if renew_per_page else invoke(argv)
    evidence = check_run_evidence_from_pages(proc.stdout or "")
    runs_readable = evidence.complete and proc.returncode == 0
    any_signal = any_signal or bool(evidence.runs)
    for run in evidence.runs:
        if not isinstance(run, dict):
            runs_readable = False
            continue
        run_status = str(run.get("status", "")).strip().lower()
        if run_status not in ("queued", "in_progress", "completed", "waiting", "requested", "pending"):
            runs_readable = False
            continue
        if run_status != "completed":
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
