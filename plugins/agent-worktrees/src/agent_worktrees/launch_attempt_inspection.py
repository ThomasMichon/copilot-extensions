"""Bounded, read-only launch evidence; never current liveness or replay authority."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from . import config as cfg, launch_attempt, launch_execution, launch_native_observation
from . import launch_request, output

SCHEMA = "worktree-launch-attempt-inspection"
MAX_ENTRIES = 1000
_NAME = re.compile(r"([0-9a-f]{32})(\.(execution|native))?\.json")


def _page_name(value: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise ValueError("Invalid launch evidence cursor")
    return value


def _safe_file(path: Path) -> None:
    for candidate in (path, *path.parents):
        metadata = candidate.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Indirect launch evidence")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("Invalid launch evidence file")


def _admission(directory: Path, attempt_id: str, request_id: str) -> dict:
    path = launch_request._admission_path(directory, request_id)
    _safe_file(path)
    with path.open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("Launch admission exceeds its read budget")
    receipt = launch_request._validate_admission(json.loads(raw), request_id)
    if receipt.get("attempt_id") != attempt_id:
        raise ValueError("Launch admission belongs to another attempt")
    # Do not inspect seed text or require the old checkout still to exist.
    return {
        "request_id": request_id, "worktree_id": receipt["worktree_id"],
        "seed_id": receipt["seed_id"], "kind": receipt["kind"],
        "creation_started": receipt.get("creation_started", False),
        "creation_complete": receipt.get("creation_complete", False),
        "admission_accepted": receipt.get("accepted", False),
        "prompt_acceptance": "unknown",
    }


def inspect(
    directory: Path, attempt_id: str, *, limit: int = 100,
    after: str | None = None, request_ids: tuple[str, ...] = (),
) -> dict:
    """Read one attempt on this machine, with bounded work and per-source errors."""
    launch_attempt.identity(attempt_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid launch evidence page limit")
    if after is not None:
        _page_name(after)
    if len(request_ids) > 16:
        raise ValueError("Too many launch admission references")
    for request_id in request_ids:
        launch_attempt.identity(request_id)
    result = {
        "schema": SCHEMA, "version": 1, "attempt_id": attempt_id,
        "scope": "local-project", "receipts": [], "admissions": [], "errors": [],
        "has_more": False, "next_after": None, "prompt_acceptance": "unknown",
    }
    names = []
    try:
        # A refused receipt directory must not hide an independently safe admission.
        folder = launch_attempt._path(directory, attempt_id, "0" * 32).parent
        with os.scandir(folder) as entries:
            for index, entry in enumerate(entries):
                if index == MAX_ENTRIES:
                    raise ValueError("Launch evidence directory exceeds its read budget")
                if _NAME.fullmatch(entry.name) and (after is None or entry.name > after):
                    names.append(entry.name)
    except FileNotFoundError:
        result["errors"].append({"source": "receipts", "code": "missing"})
    except (OSError, ValueError):
        result["errors"].append({"source": "receipts", "code": "unreadable"})
        names = []  # Never publish an arbitrary partial directory as a complete page.
    names.sort()
    references = dict.fromkeys(request_ids)
    result["has_more"] = len(names) > limit
    if result["has_more"]:
        result["next_after"] = names[limit - 1]
    for name in names[:limit]:
        match = _NAME.fullmatch(name)
        operation_id, _, kind = match.groups()
        kind = kind or "resolution"
        try:
            if kind == "resolution":
                value = launch_attempt.read(directory, attempt_id, operation_id)
                for event in value["events"]:
                    request_id = event["details"].get("request_id")
                    if request_id is not None:
                        launch_attempt.identity(request_id)
                        if len(references) < 16 or request_id in references:
                            references[request_id] = None
                        else:
                            result["errors"].append({"source": "admission", "code": "reference-limit"})
            elif kind == "execution":
                value = launch_execution.read(directory, attempt_id, operation_id)
            else:
                # Validate even an orphan observation rather than masking corrupt state
                # behind the joined reader's missing-execution result.
                launch_native_observation.read(directory, attempt_id, operation_id)
                value = launch_native_observation.joined(directory, attempt_id, operation_id)
            result["receipts"].append({
                "source": kind, "operation_id": operation_id, "evidence": value,
            })
        except FileNotFoundError:
            result["errors"].append({
                "source": kind, "operation_id": operation_id, "code": "missing",
            })
        except (OSError, ValueError, TypeError, KeyError):
            result["errors"].append({
                "source": kind, "operation_id": operation_id, "code": "unreadable",
            })
    for request_id in references:
        try:
            result["admissions"].append(_admission(directory, attempt_id, request_id))
        except FileNotFoundError:
            result["errors"].append({
                "source": "admission", "request_id": request_id, "code": "missing",
            })
        except (OSError, ValueError, TypeError, KeyError):
            result["errors"].append({
                "source": "admission", "request_id": request_id, "code": "unreadable",
            })
    return result


def add_parsers(sub) -> None:
    parser = sub.add_parser("launch-attempts", help="Read prompt-free local launch receipts")
    parser.add_argument("attempt_id")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after", default=None)
    parser.add_argument("--request-id", action="append", default=[])
    parser.add_argument("--json", action="store_true")


def cmd_launch_attempts(args) -> int:
    try:
        report = inspect(
            cfg.tracking_dir(), args.attempt_id, limit=args.limit,
            after=args.after, request_ids=tuple(args.request_id),
        )
    except (OSError, ValueError, TypeError):
        return output._json_error("Could not inspect local launch evidence", exit_code=2)
    if args.json:
        output._json_output(report)
    else:
        print(json.dumps(report, indent=2))
    return 0
