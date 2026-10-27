"""Bounded resident reads and explicitly advisory durable fallback, never compute."""

from __future__ import annotations

import json
import math
import time

from zdd import routing

from . import status_monitor_cutover as control
from . import config as cfg
from . import worktrees_observation_contract as observations
from .worktrees_projection import SCHEMA_VERSION, _encode_row, read_advisory
from .worktrees_projection_index import ProjectionError, ProjectionUnavailable
from .worktrees_runtime import CONTRACT_VERSION, request_selection

READ_TIMEOUT = 1.0
RECOVERY_WAIT = 0.5


def __getattr__(name: str):
    if name == "monitor":
        from . import status_monitor_runtime

        return status_monitor_runtime
    raise AttributeError(name)


def validate_result(result: dict, request: dict) -> dict:
    if not isinstance(result, dict) or type(result.get("contract_version")) is not int:
        raise ProjectionUnavailable("resident lacks worktrees-current-v1; update the engine")
    if result["contract_version"] not in (CONTRACT_VERSION, observations.VERSION) or (
        result["contract_version"] != request["contract_version"]
        and not (result.get("ok") is False and result["contract_version"] == CONTRACT_VERSION)
    ):
        raise ProjectionUnavailable(
            "incompatible resident current-roster contract; update the engine"
        )
    if result.get("ok") is True and result["contract_version"] == observations.VERSION and (
        result.get("project") != request["project"]
    ):
        raise ProjectionUnavailable("resident observation response changed project")
    if result.get("ok") is not True:
        error = result.get("error")
        if (
            result.get("ok") is not False
            or not isinstance(error, dict)
            or not isinstance(error.get("code"), str) or not error["code"]
            or not isinstance(error.get("message"), str) or not error["message"]
        ):
            raise ProjectionUnavailable("malformed resident current-roster error")
        if error.get("code") == "invalid-request":
            raise ProjectionError(error["message"])
        raise ProjectionUnavailable(f"{error.get('code')}: {error['message']}")
    rows, head = result.get("worktrees"), result.get("projection")
    if not isinstance(rows, list) or not isinstance(head, dict):
        raise ProjectionUnavailable("incomplete resident current-roster response")
    generation, revision = head.get("generation"), head.get("revision")
    observed_at, count = head.get("observed_at"), head.get("roster_count")
    if (
        head.get("quality") != "current"
        or type(head.get("schema_version")) is not int
        or head["schema_version"] != SCHEMA_VERSION
        or not isinstance(generation, str)
        or len(generation) != 32
        or any(c not in "0123456789abcdef" for c in generation)
        or type(revision) is not int
        or revision < 1
        or type(count) is not int
        or count < len(rows)
        or type(head.get("selected_count")) is not int
        or head["selected_count"] != len(rows)
        or type(observed_at) not in (int, float)
        or not math.isfinite(observed_at)
        or observed_at < 0
    ):
        raise ProjectionUnavailable("invalid resident current-roster provenance")
    identities = set()
    availability = result.get("availability")
    if availability is not None and (
        not isinstance(availability, dict)
        or availability.get("status") != "stale"
        or not isinstance(availability.get("reason"), str)
        or not availability["reason"]
        or len(availability["reason"].encode("utf-8")) > 4096
    ):
        raise ProjectionUnavailable("invalid resident current-roster availability")
    try:
        if result["contract_version"] == observations.VERSION:
            observations.validate_rows(rows, result.get("observations"), revision)
        for row in rows:
            if result["contract_version"] == observations.VERSION:
                wid = row["id"]
            else:
                wid, _encoded = _encode_row(row)
            if wid in identities:
                raise ProjectionError("duplicate current-roster row")
            identities.add(wid)
    except ProjectionError as exc:
        raise ProjectionUnavailable(f"invalid resident current-roster row: {exc}") from exc
    _project, selection = request_selection(request)
    for row in rows:
        if (
            (
                selection["status_filter"] is not None
                and row.get("status") != selection["status_filter"]
            )
            or (
                selection["platform_filter"] is not None
                and row.get("platform") != selection["platform_filter"]
            )
            or (not selection["include_missing"] and row.get("checkout_present") is not True)
            or (selection["codename"] is not None and row.get("codename") != selection["codename"])
            or (
                selection["worktree_id"] is not None
                and not row["id"].endswith(selection["worktree_id"])
            )
        ):
            raise ProjectionUnavailable("resident current-roster response violates selection")
    if selection["worktree_id"] is not None or selection["codename"] is not None:
        if len(rows) > 1:
            raise ProjectionUnavailable("point read returned multiple current-roster rows")
    elif (
        selection["status_filter"] is None
        and selection["platform_filter"] is None
        and selection["include_missing"]
        and len(rows) != count
    ):
        raise ProjectionUnavailable("incomplete resident current roster")
    return result


def _control_client() -> control.ControlClient:
    runtime_home = cfg.install_dir()
    endpoint = routing.read_active_endpoint(control.routing_dir(runtime_home), verify_listener=False)
    if endpoint is None:
        raise ConnectionError("resident current-roster endpoint is unavailable")
    # An ordinary reader must never create or replace the owner's credential.
    with control.control_token_path(runtime_home).open(encoding="utf-8") as token_file:
        token = token_file.read(4097).strip()
    if not token or len(token) > 4096:
        raise ProjectionUnavailable("resident current-roster authentication is unavailable")
    return control.ControlClient(endpoint.base_url, token=token, timeout=READ_TIMEOUT)


def read_health() -> dict:
    """Read the actual authenticated resident's capabilities, without an advisory fallback."""
    return _control_client().health()


def _resident(request: dict) -> dict:
    return validate_result(_control_client().worktrees_read(request), request)


def read_current(request: dict, *, recover: bool = True) -> dict:
    project, selection = request_selection(request)
    unavailable = None
    try:
        return _resident(request)
    except ProjectionError:
        raise
    except (ConnectionError, FileNotFoundError) as exc:
        from . import status_monitor_runtime as monitor

        unavailable = str(exc)
        if recover and monitor._status_monitor_enabled():
            started = monitor._ensure_status_monitor()
            deadline = time.monotonic() + RECOVERY_WAIT if started else 0.0
            while time.monotonic() < deadline:
                try:
                    return _resident(request)
                except (ConnectionError, FileNotFoundError):
                    time.sleep(0.05)
                except ProjectionError:
                    raise
                except (
                    OSError,
                    ValueError,
                    RecursionError,
                    ProjectionUnavailable,
                ) as recovery_error:
                    unavailable = str(recovery_error)
                    break
            else:
                unavailable = "resident launch/recovery did not produce a usable current roster"
    except (OSError, ValueError, RecursionError, ProjectionUnavailable) as exc:
        unavailable = str(exc)
    from . import status_monitor_runtime as monitor

    try:
        result = read_advisory(
            monitor._aw_runtime_home() / "worktrees-current.sqlite3",
            project,
            **selection,
            contract_version=request["contract_version"],
        )
    except (ProjectionError, ProjectionUnavailable) as exc:
        raise ProjectionUnavailable(f"{unavailable}; no usable advisory roster: {exc}") from exc
    result["availability"] = {"status": "advisory", "reason": unavailable}
    result["contract_version"] = request["contract_version"]
    if request["contract_version"] == observations.VERSION:
        result["project"] = project
    result["ok"] = True
    return result


def emit_current(args) -> int:
    from . import config as cfg
    from . import list_cache, output

    if getattr(args, "profile_assignment_history", False):
        return output._json_error("current-roster reads cannot include profile history")
    if getattr(args, "refresh", False) and not getattr(args, "worktree_id", None):
        return output._json_error("--refresh requires --worktree-id")
    request = {
        "contract_version": CONTRACT_VERSION,
        "project": cfg.project_name(),
        "worktree_id": getattr(args, "worktree_id", None),
        "codename": None
        if getattr(args, "worktree_id", None)
        else getattr(args, "codename", None),
        "status_filter": (
            None if getattr(args, "tracking_status", "all") == "all" else args.tracking_status
        ),
        "platform_filter": None
        if getattr(args, "include_other_platforms", False)
        else cfg.detect_platform(),
        "include_missing": bool(getattr(args, "all", False)),
    }
    key = list_cache.cache_key(
        args, project=request["project"], tracking_status=args.tracking_status
    )
    list_cache.note_demand(
        key, args, project=request["project"], tracking_status=args.tracking_status
    )
    if getattr(args, "follow", False):
        from .worktrees_feed_client import FeedFailure, relay

        try:
            return relay(request)
        except BrokenPipeError:
            return 0
        except (OSError, ValueError, RecursionError, ProjectionError, ProjectionUnavailable) as exc:
            if isinstance(exc, FeedFailure):
                code, retry = exc.code, exc.retry
            elif isinstance(exc, TimeoutError):
                code, retry = "timeout", False
            elif isinstance(exc, ConnectionError):
                code, retry = "disconnected", True
            elif isinstance(exc, (ValueError, RecursionError, ProjectionUnavailable)):
                code, retry = "malformed-peer", False
            else:
                code, retry = "unavailable", False
            print(json.dumps({
                "type": "error",
                "contract_version": CONTRACT_VERSION,
                "error": {"code": code, "message": str(exc), "retry": retry},
            }), flush=True)
            return 1
    try:
        result = read_current(request)
    except (ProjectionError, ProjectionUnavailable) as exc:
        return output._json_error(str(exc))
    if getattr(args, "stream", False):
        head = result["projection"]
        print(
            json.dumps(
                {
                    "type": "begin",
                    "contract_version": CONTRACT_VERSION,
                    "projection": head,
                    "availability": result.get("availability"),
                    "selected_count": len(result["worktrees"]),
                }
            ),
            flush=True,
        )
        for row in result["worktrees"]:
            print(
                json.dumps(
                    {
                        "type": "worktree",
                        "phase": "current",
                        "wt": row,
                        "projection": head,
                    }
                ),
                flush=True,
            )
        print(json.dumps({"type": "done", "projection": head}), flush=True)
    else:
        output._json_output(result)
    return 0
