"""Bounded positive owner evidence, never absence, acceptance or replay authority."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import re
import time

from . import engine_client
from .launch_attempts import LaunchAttempts

_ID = re.compile(r"[0-9a-f]{32}")
_CURSOR = re.compile(r"[0-9a-f]{32}(?:\.(?:execution|native))?\.json")
_MAX_PAGES = 10
_DEADLINE_SECONDS = 120
_BOUNDARIES = ("allocation", "launcher_start", "backend_start", "native_session")


def _identity(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("Invalid evidence identity")
    return value


def _opaque(value: object) -> str:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 512
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Invalid evidence reference")
    return value


def _fact(row: dict, attempt_id: str) -> tuple[str, dict] | None:
    operation = _identity(row["operation_id"])
    value = row["evidence"]
    if not isinstance(value, dict):
        raise ValueError("Invalid owner evidence")
    if row["source"] == "native":
        if value["status"] not in ("unknown", "native-session-observed"):
            raise ValueError("Invalid native observation")
        if value["status"] != "native-session-observed":
            return None
        if value.get("prompt_acceptance") != "unknown":
            raise ValueError("Invalid native evidence")
        return "native_session", {
            "operation_id": operation,
            "native_session_id": _opaque(value["native_session_id"]),
            "source_identity": _opaque(value["source_identity"]),
        }
    if row["source"] not in ("resolution", "execution"):
        raise ValueError("Invalid evidence owner")
    if (
        value["attempt_id"] != attempt_id or value["operation_id"] != operation
        or value["schema"] != "worktree-launch-" + row["source"]
        or type(value["version"]) is not int
        or value["version"] not in ((1,) if row["source"] == "resolution" else (1, 2))
    ):
        raise ValueError("Mismatched owner evidence")
    if row["source"] == "resolution":
        return None  # A returned plan is not proof of allocation or process start.
    events = value["events"]
    if (
        not isinstance(events, list) or not 1 <= len(events) <= 4
        or any(not isinstance(event, dict) for event in events)
    ):
        raise ValueError("Invalid execution events")
    spawns = [event for event in events if event["phase"] == "process-spawned"]
    if len(spawns) > 1:
        raise ValueError("Repeated process observation")
    spawn = spawns[0] if spawns else None
    if spawn is None:
        return None
    details = spawn["details"]
    if not isinstance(details, dict) or set(details) != {"boundary", "pid", "process_start_time"}:
        raise ValueError("Invalid process observation")
    at = spawn["at"]
    if not isinstance(at, str) or datetime.fromisoformat(at).tzinfo is None:
        raise ValueError("Invalid process timestamp")
    if (
        details["boundary"] not in ("launcher-command", "backend-command")
        or type(details["pid"]) is not int or details["pid"] <= 0
        or (details["process_start_time"] is not None and (
            not isinstance(details["process_start_time"], str)
            or re.fullmatch(r"(?:0|[1-9][0-9]*)(?::(?:0|[1-9][0-9]{0,5}))?",
                            details["process_start_time"]) is None
        ))
    ):
        raise ValueError("Invalid process evidence")
    parent = value["parent_id"]
    nonce = value["creation_nonce"]
    if parent is not None:
        _identity(parent)
    if nonce is not None:
        _opaque(nonce)
    return ("backend_start" if details["boundary"] == "backend-command" else "launcher_start"), {
        "operation_id": operation, "resolution_id": _identity(value["resolution_id"]),
        "parent_id": parent, "worktree_id": _opaque(value["worktree_id"]),
        "creation_nonce": nonce, "at": at, "pid": details["pid"],
        "process_start_time": details["process_start_time"],
    }


def _allocation(value: dict) -> dict | None:
    request = _identity(value["request_id"])
    if value["prompt_acceptance"] != "unknown" or value["seed_id"] != request:
        raise ValueError("Invalid admission evidence")
    for field in ("creation_started", "creation_complete", "admission_accepted"):
        if type(value[field]) is not bool:
            raise ValueError("Invalid admission observation")
    if value["kind"] not in ("new", "resume"):
        raise ValueError("Invalid admission kind")
    if value["kind"] != "new" or not value["creation_complete"]:
        return None
    return {
        "request_id": request, "worktree_id": _opaque(value["worktree_id"]),
        "seed_id": _identity(value["seed_id"]),
    }


def read_summary(
    store: LaunchAttempts, attempt_id: str, *, include_input: bool = False,
    read_page: Callable[..., dict],
) -> dict:
    """Explicit local read with finite work; each page is an independent observation."""
    detail = store.detail(attempt_id, include_input=include_input, event_limit=1000)
    references = list(dict.fromkeys(
        event["details"]["request_id"] for event in detail["events"]
        if "request_id" in event["details"]
    ))
    errors = []
    requests = []
    for request in references:
        try:
            requests.append(_identity(request))
        except ValueError:
            errors.append({"source": "manager", "code": "invalid-reference"})
    if len(requests) > 16:
        errors.append({"source": "manager", "code": "reference-limit"})
    requests = requests[:16]
    if detail["events_has_more"]:
        errors.append({"source": "manager", "code": "event-limit"})
    facts: dict[str, list[dict]] = {boundary: [] for boundary in _BOUNDARIES}
    engine = {
        "scope": "local-project", "status": "unavailable", "pages_read": 0,
        "receipts_read": 0, "has_more": None, "next_after": None,
    }
    after = None
    deadline = time.monotonic() + _DEADLINE_SECONDS
    admissions = {}
    for _ in range(_MAX_PAGES):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append({"source": "engine", "code": "deadline"})
            break
        try:
            page = read_page(
                detail["project"], attempt_id, limit=100, after=after,
                request_ids=requests, timeout=int(remaining),
            )
            cursor = page["next_after"]
            if (
                (page["has_more"] and (
                    not isinstance(cursor, str) or _CURSOR.fullmatch(cursor) is None
                    or (after is not None and cursor <= after)
                ))
                or (not page["has_more"] and cursor is not None)
            ):
                raise engine_client.EngineError("Invalid launch evidence continuation")
        except engine_client.EngineError:
            errors.append({"source": "engine", "code": "unavailable-or-incompatible"})
            break
        engine.update(
            status="read", pages_read=engine["pages_read"] + 1,
            receipts_read=engine["receipts_read"] + len(page["receipts"]),
            has_more=page["has_more"], next_after=cursor,
        )
        seen = set()
        for row in page["receipts"]:
            try:
                if not isinstance(row, dict) or set(row) != {"source", "operation_id", "evidence"}:
                    raise ValueError("Invalid receipt")
                operation = _identity(row["operation_id"])
                source = row["source"]
                if source not in ("resolution", "execution", "native"):
                    raise ValueError("Invalid receipt owner")
                name = operation + (".json" if source == "resolution" else "." + source + ".json")
                if (
                    name in seen or (after is not None and name <= after)
                    or (cursor is not None and name > cursor)
                ):
                    raise ValueError("Invalid receipt page")
                seen.add(name)
                fact = _fact(row, attempt_id)
                if fact is not None:
                    boundary, value = fact
                    facts[boundary].append(value)
            except (ValueError, TypeError, KeyError):
                errors.append({"source": "engine", "code": "invalid-receipt"})
        for value in page["admissions"]:
            try:
                request = _identity(value["request_id"])
                allocation = _allocation(value)
                if request in admissions and admissions[request] != allocation:
                    errors.append({"source": "admission", "code": "changed-between-pages"})
                admissions[request] = allocation
                if allocation is not None and allocation not in facts["allocation"]:
                    facts["allocation"].append(allocation)
            except (ValueError, TypeError, KeyError):
                errors.append({"source": "admission", "code": "invalid-receipt"})
        for error in page["errors"]:
            # The engine owns source diagnostics; never echo arbitrary process content.
            source = error.get("source") if isinstance(error, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            if source not in ("receipts", "resolution", "execution", "native", "admission"):
                source = "engine"
            if code not in ("missing", "unreadable", "reference-limit"):
                code = "invalid-source-error"
            diagnostic = {"source": source, "code": code}
            if isinstance(error, dict):
                for field in ("operation_id", "request_id"):
                    reference = error.get(field)
                    if isinstance(reference, str) and _ID.fullmatch(reference):
                        diagnostic[field] = reference
            errors.append(diagnostic)
        if not page["has_more"]:
            break
        after = cursor
    else:
        errors.append({"source": "engine", "code": "page-limit"})
    return {
        "schema": "worktree-manager-launch-history", "version": 1,
        "scope": "local-manager", "attempt": detail, "prompt_acceptance": "unknown",
        "engine": engine,
        "summary": {
            "scope": "local-manager-and-project", "observation": "non-atomic",
            "selected_remote": "not-queried", "absence": "unknown",
            "manager_events_has_more": detail["events_has_more"],
            "boundaries": {
                name: {"status": "observed" if values else "unknown", "evidence": values}
                for name, values in facts.items()
            },
            "errors": errors,
        },
    }
