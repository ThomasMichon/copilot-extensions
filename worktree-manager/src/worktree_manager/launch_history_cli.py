"""Read-only Manager intent and local engine evidence, with private input opt-in."""

from __future__ import annotations

import argparse
import json

from . import engine_client
from .launch_attempts import MANAGER_OUTCOMES, LaunchAttemptError, LaunchAttempts

_SCHEMA = "worktree-launch-attempt-inspection"


def _error(code: str, message: str, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({
            "schema": "worktree-manager-launch-history", "version": 1, "scope": "local-manager",
            "errors": [{"source": "manager", "code": code}], "error": message,
        }))
    else:
        print("error: " + message)


def engine_evidence(
    project: str, attempt_id: str, *, limit: int, after: str | None,
    request_ids: list[str], timeout: int | None = None,
) -> dict:
    """Use the versioned process boundary; never import the engine or echo errors."""
    args = ["launch-attempts", attempt_id, "--json", "--limit", str(limit)]
    if after is not None:
        args += ["--after", after]
    for request_id in request_ids:
        args += ["--request-id", request_id]
    value = engine_client.run_json(project, args, **({"timeout": timeout} if timeout is not None else {}))
    if (
        set(value) != {
            "schema", "version", "attempt_id", "scope", "receipts", "admissions", "errors",
            "has_more", "next_after", "prompt_acceptance",
        }
        or value.get("schema") != _SCHEMA or type(value.get("version")) is not int
        or value["version"] != 1 or value.get("attempt_id") != attempt_id
        or value.get("scope") != "local-project"
        or value.get("prompt_acceptance") != "unknown"
        or not all(isinstance(value.get(key), list) for key in ("receipts", "admissions", "errors"))
        or len(value["receipts"]) > limit or len(value["admissions"]) > 16
        or type(value.get("has_more")) is not bool
    ):
        raise engine_client.EngineError("Invalid launch evidence response")
    return value


def read_detail(
    store: LaunchAttempts, attempt_id: str, *, include_input: bool = False,
    event_limit: int = 100, after_sequence: int = 0,
    receipt_limit: int = 100, receipt_after: str | None = None,
) -> dict:
    """Join one explicit local detail request without replaying or repairing."""
    detail = store.detail(
        attempt_id, include_input=include_input,
        event_limit=event_limit, after_sequence=after_sequence,
    )
    request_ids = list(dict.fromkeys(
        event["details"]["request_id"] for event in detail["events"]
        if "request_id" in event["details"]
    ))
    report = {
        "schema": "worktree-manager-launch-history", "version": 1,
        "scope": "local-manager", "attempt": detail, "prompt_acceptance": "unknown",
        "engine": {"scope": "local-project", "status": "unavailable"},
    }
    try:
        if len(request_ids) > 16:
            raise engine_client.EngineError("Too many admission references in event page")
        report["engine"] = {
            "status": "read",
            **engine_evidence(
                detail["project"], attempt_id, limit=receipt_limit,
                after=receipt_after, request_ids=request_ids,
            ),
        }
    except engine_client.EngineError:
        report["engine"]["code"] = "unavailable-or-incompatible"
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="worktree-manager launch-attempts",
        description="Read local saved launch intent; detail joins local engine receipts, never retries.",
    )
    parser.add_argument("attempt_id", nargs="?")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-input", action="store_true", help="Explicitly recover protected input in detail only")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--before", type=float)
    parser.add_argument("--before-id", help="Tie-breaker returned with next_before for list pagination")
    parser.add_argument("--after", type=float)
    for name in ("project", "action", "machine", "environment", "provider", "venue"):
        parser.add_argument("--" + name)
    parser.add_argument(
        "--outcome", choices=MANAGER_OUTCOMES,
        help="Latest Manager boundary only; returned is not Copilot success or prompt acceptance",
    )
    parser.add_argument("--event-limit", type=int, default=100)
    parser.add_argument("--after-sequence", type=int, default=0)
    parser.add_argument("--receipt-limit", type=int, default=100)
    parser.add_argument("--receipt-after")
    parser.add_argument(
        "--summary", action="store_true",
        help="Bounded positive local owner evidence across pages; not prompt acceptance or a remote sweep",
    )
    args = parser.parse_args(argv)
    filters = {
        key: getattr(args, key) for key in (
            "before", "before_id", "after", "project", "action", "machine", "environment", "provider", "outcome",
        )
    }
    filters["venue_id"] = args.venue
    if (
        (args.include_input and args.attempt_id is None)
        or (args.attempt_id is not None and any(value is not None for value in filters.values()))
        or not 1 <= args.receipt_limit <= 100
        or not 1 <= args.limit <= 100
        or (args.summary and (
            args.attempt_id is None or args.event_limit != 100 or args.after_sequence != 0
            or args.receipt_limit != 100 or args.receipt_after is not None
        ))
    ):
        _error(
            "invalid-query",
            "Private input and summary require detail; summary cannot use page selectors; filters and limits must be valid.",
            json_output=args.json,
        )
        return 2
    store = LaunchAttempts()
    try:
        if args.attempt_id is None:
            rows = store.entries(limit=args.limit + 1, **filters)
            has_more = len(rows) > args.limit
            rows = rows[:args.limit]
            report = {
                "schema": "worktree-manager-launch-history", "version": 1,
                "scope": "local-manager", "attempts": rows, "has_more": has_more,
                "next_before": rows[-1]["submitted_at"] if has_more else None,
                "next_before_id": rows[-1]["attempt_id"] if has_more else None,
            }
        elif args.summary:
            from .launch_history_summary import read_summary
            report = read_summary(
                store, args.attempt_id, include_input=args.include_input, read_page=engine_evidence,
            )
        else:
            report = read_detail(
                store, args.attempt_id, include_input=args.include_input,
                event_limit=args.event_limit, after_sequence=args.after_sequence,
                receipt_limit=args.receipt_limit, receipt_after=args.receipt_after,
            )
    except LaunchAttemptError:
        _error(
            "unreadable-or-expired", "Could not read protected launch history; no launch was attempted.",
            json_output=args.json,
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # A partial result is useful evidence, but not an empty-success response.
    return 1 if (
        report.get("engine", {}).get("status") == "unavailable"
        or report.get("summary", {}).get("errors")
    ) else 0
