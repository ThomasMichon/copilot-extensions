"""Exact selected-machine detail; private input requires an explicit opt-in."""

from __future__ import annotations

import json
import math

from machine_transport import resolve_machine_selection

from . import engine_client, launch_attempts, machine_roster
from . import launch_history_sweep as sweep
from .launch_history_cli import validate_engine_evidence
from .launch_history_summary import EvidenceProjection, _CURSOR


def query_argv(args) -> list[str]:
    try:
        launch_attempts._identity(args.attempt_id)
        if (
            not args.sweep_project or not args.source_machine or len(args.source_machine) != 1
            or any(not name.strip() or len(name) > 256 or "\0" in name for name in args.source_machine)
            or args.summary or args.limit != 100
            or any(getattr(args, field) is not None for field in sweep._FILTERS)
            or not 1 <= args.event_limit <= 1000 or args.after_sequence < 0
            or not 1 <= args.receipt_limit <= 100
            or (args.receipt_after is not None and _CURSOR.fullmatch(args.receipt_after) is None)
        ):
            raise ValueError("Invalid detail query")
    except (ValueError, TypeError, AttributeError) as exc:
        raise sweep.SweepError("invalid-query") from exc
    query = [
        "launch-attempts", args.attempt_id, "--json",
        "--event-limit", str(args.event_limit), "--after-sequence", str(args.after_sequence),
        "--receipt-limit", str(args.receipt_limit),
    ]
    if args.receipt_after is not None:
        query += ["--receipt-after", args.receipt_after]
    if args.include_input:
        query.append("--include-input")
    return query


def _unique_object(pairs) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response field")
        result[key] = value
    return result


def _constant(value: str):
    raise ValueError("Non-finite response value")


def _attempt(value: dict, args) -> dict:
    fields = sweep._FIELDS | {"events", "events_has_more", "next_event_sequence"}
    if args.include_input:
        fields |= {"input"}
    if not isinstance(value, dict) or set(value) != fields or value["attempt_id"] != args.attempt_id:
        raise ValueError("Invalid exact attempt")
    sweep.validate_metadata({key: value[key] for key in sweep._FIELDS})
    if (
        not isinstance(value["events"], list) or len(value["events"]) > args.event_limit
        or type(value["events_has_more"]) is not bool
    ):
        raise ValueError("Invalid event page")
    previous = args.after_sequence
    for event in value["events"]:
        if (
            not isinstance(event, dict)
            or set(event) != {"sequence", "observed_at", "owner", "phase", "details"}
            or type(event["sequence"]) is not int or event["sequence"] <= previous
            or type(event["observed_at"]) not in (int, float)
            or not math.isfinite(event["observed_at"]) or event["observed_at"] < 0
        ):
            raise ValueError("Invalid event")
        launch_attempts._validate_observation(event["owner"], event["phase"], event["details"])
        previous = event["sequence"]
    cursor = value["next_event_sequence"]
    if (
        (value["events_has_more"] and (
            len(value["events"]) != args.event_limit or type(cursor) is not int or cursor != previous
        ))
        or (not value["events_has_more"] and cursor is not None)
    ):
        raise ValueError("Invalid event continuation")
    if args.include_input:
        private = value["input"]
        if (
            not value["inputs_available"] or not isinstance(private, dict)
            or set(private) != {"prompt", "options"}
            or not isinstance(private["prompt"], str) or "\0" in private["prompt"]
            or not isinstance(private["options"], dict)
        ):
            raise ValueError("Invalid protected input")
    return value


def validate_detail(output: bytes, args) -> tuple[dict, bool]:
    """Return only admitted intent and the existing prompt-free evidence projection."""
    try:
        if len(output) > sweep.MAX_RESPONSE_BYTES:
            raise sweep.SweepError("response-too-large")
        value = json.loads(output, object_pairs_hook=_unique_object, parse_constant=_constant)
        if (
            not isinstance(value, dict)
            or value.get("schema") != "worktree-manager-launch-history"
            or type(value.get("version")) is not int or value["version"] != 1
            or value.get("scope") != "local-manager"
        ):
            raise ValueError("Invalid target detail")
        if (
            set(value) == {"schema", "version", "scope", "errors", "error"}
            and value["errors"] == [{"source": "manager", "code": "unreadable-or-expired"}]
        ):
            raise sweep.SweepError("unreadable-or-expired")
        if (
            set(value) != {"schema", "version", "scope", "attempt", "prompt_acceptance", "engine"}
            or value["prompt_acceptance"] != "unknown"
        ):
            raise ValueError("Invalid detail envelope")
        attempt = _attempt(value["attempt"], args)
        engine = value["engine"]
        unavailable = {
            "scope": "local-project", "status": "unavailable", "code": "unavailable-or-incompatible",
        }
        partial = engine == unavailable
        if not partial:
            if not isinstance(engine, dict) or engine.get("status") != "read":
                raise ValueError("Invalid engine status")
            page = validate_engine_evidence(
                {key: item for key, item in engine.items() if key != "status"},
                args.attempt_id, limit=args.receipt_limit,
            )
            projection = EvidenceProjection()
            projection.add_page(page, args.attempt_id, after=args.receipt_after)
            engine = {
                "scope": "local-project", "status": "read",
                "has_more": page["has_more"], "next_after": page["next_after"],
                "observation": "non-atomic", "absence": "unknown",
                **projection.report(),
            }
            partial = bool(projection.errors)
        return {
            "attempt": attempt, "engine": engine, "prompt_acceptance": "unknown",
        }, partial
    except sweep.SweepError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError,
            engine_client.EngineError) as exc:
        raise sweep.SweepError("incompatible-response") from exc


async def read_remote_detail(args, *, read_source=None) -> dict:
    query = query_argv(args)
    source = {
        "selection": args.source_machine[0], "machine": None, "environment": None,
        "identity_basis": "configured-ssh-profile", "status": "unavailable",
    }
    report = {
        "schema": "worktree-manager-launch-history-remote-detail", "version": 1,
        "scope": "selected-configured-ssh-manager", "roster_project": args.sweep_project,
        "attempt_id": args.attempt_id, "atomic": False, "prompt_acceptance": "unknown",
        "source": source, "errors": [],
        "limits": {
            "sources": 1, "manager_events": args.event_limit, "engine_receipts": args.receipt_limit,
            "source_timeout_seconds": sweep.SOURCE_TIMEOUT, "response_bytes": sweep.MAX_RESPONSE_BYTES,
        },
    }
    try:
        try:
            entries = machine_roster.selected_entries(args.sweep_project)
        except (OSError, ValueError) as exc:
            raise sweep.SweepError("roster-unavailable") from exc
        try:
            selection = resolve_machine_selection(entries, args.source_machine[0])
        except ValueError as exc:
            raise sweep.SweepError("ambiguous-or-invalid-selection") from exc
        if selection.entry is None:
            raise sweep.SweepError("not-configured")
        source["machine"] = selection.entry.key
        source["environment"] = selection.target.environment
        output, return_code = await (read_source or sweep.query_source_result)(
            selection.entry, query, timeout=sweep.SOURCE_TIMEOUT,
        )
        if type(return_code) is not int or return_code not in (0, 1):
            raise sweep.SweepError("target-reader-unavailable")
        detail, partial = validate_detail(output, args)
        source["detail"] = detail
        source["target_exit_code"] = return_code
        source["status"] = "partial" if partial or return_code else "read"
        if source["status"] == "partial":
            source["code"] = "incomplete-evidence"
    except sweep.SweepError as exc:
        source["code"] = str(exc)
    except (OSError, ValueError, RuntimeError):
        source["code"] = "transport-unavailable"
    return report
