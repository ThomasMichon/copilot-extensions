"""One bounded repository-map read; never rebuild or provision on a reader."""

from __future__ import annotations

import math
import time
from pathlib import Path

from zdd import routing

from . import config as cfg, output, status_monitor_cutover as control, status_monitor_runtime as monitor
from .repository_map import CONTRACT_VERSION, MAX_AGE, valid_repository_name

READ_TIMEOUT = 1.0


def validate_result(result: dict, scope: str, *, now: float | None = None) -> dict:
    if (
        not isinstance(result, dict)
        or type(result.get("repository_map_version")) is not int
        or result["repository_map_version"] != CONTRACT_VERSION
    ):
        raise ValueError("resident lacks the supported repository-map contract")
    if result.get("ok") is not True:
        error = result.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        raise ValueError(message if isinstance(message, str) else "repository map is unavailable")
    head, entries = result.get("projection"), result.get("repos")
    if not isinstance(head, dict) or not isinstance(entries, list):
        raise ValueError("repository map has no complete projection")
    generation, observed = head.get("generation"), head.get("observed_at")
    if (
        head.get("scope") != scope
        or not isinstance(generation, str) or len(generation) != 32
        or any(c not in "0123456789abcdef" for c in generation)
        or type(head.get("revision")) is not int or head["revision"] < 1
        or not isinstance(head.get("source_revision"), str) or not head["source_revision"]
        or type(observed) not in (float, int) or not math.isfinite(observed) or observed < 0
        or result.get("platform") != cfg.detect_platform()
    ):
        raise ValueError("invalid repository-map provenance")
    if (
        result.get("inventory_authority") != "complete"
        or head.get("quality") != "current"
        or result.get("availability") is not None
        or not 0 <= (time.time() if now is None else now) - observed <= MAX_AGE
    ):
        availability = result.get("availability")
        reason = availability.get("reason") if isinstance(availability, dict) else None
        detail = f": {reason[:4096]}" if isinstance(reason, str) and reason else ""
        raise ValueError(f"repository map is stale or incomplete{detail}")
    names = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not valid_repository_name(entry.get("name"))
            or entry["name"] in names
            or entry.get("class") not in {"reference", "singleton", "worktree", "knowledge"}
        ):
            raise ValueError("invalid repository-map entry identity")
        names.add(entry["name"])
        if entry.get("registration") == "ready":
            if not isinstance(entry.get("path"), str) or not Path(entry["path"]).is_absolute():
                raise ValueError("repository map has no absolute registered root")
        elif (
            entry.get("registration") != "unavailable"
            or entry.get("path") is not None
            or not isinstance(entry.get("reason"), str) or not entry["reason"]
        ):
            raise ValueError("repository map has an invalid resolution state")
    return result


def read_current() -> dict:
    root = monitor._aw_runtime_home().resolve()
    endpoint = routing.read_active_endpoint(control.routing_dir(root), verify_listener=False)
    if endpoint is None:
        raise ValueError("repository-map resident is unavailable; no inline discovery was performed")
    with control.control_token_path(root).open(encoding="utf-8") as stream:
        token = stream.read(4097).strip()
    if not token or len(token) > 4096:
        raise ValueError("repository-map authentication is unavailable")
    client = control.ControlClient(endpoint.base_url, token=token, timeout=READ_TIMEOUT)
    return validate_result(client.repositories_read({
        "repository_map_version": CONTRACT_VERSION, "scope": str(root),
    }), str(root))


def command(argv: list[str]) -> int:
    if argv != ["--json"]:
        output.err("Usage: repos map --json")
        return 2
    try:
        result = read_current()
    except (OSError, ValueError, RuntimeError) as exc:
        return output._json_error(f"Repository map unavailable: {exc}", exit_code=2)
    output._json_output(result)
    return 0
