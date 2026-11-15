"""Bounded resident reads and explicitly advisory durable fallback, never compute."""

from __future__ import annotations

import json
import math
import time

from zdd import routing

from . import status_monitor_cutover as control
from . import config as cfg
from . import worktrees_observation_contract as observations
from . import worktrees_reporting
from .worktrees_projection import SCHEMA_VERSION, _encode_row, read_advisory
from .worktrees_projection_index import ProjectionError, ProjectionUnavailable
from .worktrees_runtime import CONTRACT_VERSION, request_selection

READ_TIMEOUT = 1.0
RECOVERY_WAIT = 0.5


class ResidentRecoveryPending(ProjectionUnavailable):
    """An authenticated resident or admitted recovery has no usable roster yet."""


class _ResidentRetryable(ProjectionUnavailable):
    """A validated resident is temporarily unable to serve a publication."""


class _ResidentUnready(_ResidentRetryable):
    """A validated resident error reports that its first roster is not ready."""


def __getattr__(name: str):
    if name == "monitor":
        from . import status_monitor_runtime

        return status_monitor_runtime
    raise AttributeError(name)


def validate_result(result: dict, request: dict, *, quality: str = "current") -> dict:
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
        if error["code"] == "unready":
            raise _ResidentUnready(f"unready: {error['message']}")
        if error.get("code") in {"draining", "superseded", "unavailable"}:
            raise _ResidentRetryable(f"{error['code']}: {error['message']}")
        raise ProjectionUnavailable(f"{error.get('code')}: {error['message']}")
    rows, head = result.get("worktrees"), result.get("projection")
    if not isinstance(rows, list) or not isinstance(head, dict):
        raise ProjectionUnavailable("incomplete resident current-roster response")
    generation, revision = head.get("generation"), head.get("revision")
    observed_at, count = head.get("observed_at"), head.get("roster_count")
    if (
        head.get("quality") != quality
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
        or availability.get("status") != ("advisory" if quality == "advisory" else "stale")
        or not isinstance(availability.get("reason"), str)
        or not availability["reason"]
        or len(availability["reason"].encode("utf-8")) > 4096
    ):
        raise ProjectionUnavailable("invalid resident current-roster availability")
    if quality == "advisory" and availability is None:
        raise ProjectionUnavailable("unlabeled advisory current-roster result")
    try:
        if "reporting_context" in result:
            worktrees_reporting.validate(result["reporting_context"])
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
            or (
                not selection["include_missing"]
                and (row.get("checkout_present") is not True or row.get("status") == "archived")
            )
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
    if "refresh_id" in request:
        from .worktrees_refresh import receipt_matches

        receipt = result.get("point_refresh")
        if (
            not isinstance(receipt, dict)
            or receipt.get("request_id") != request["refresh_id"]
            or not receipt_matches(receipt, result)
        ):
            raise ProjectionUnavailable("resident did not prove the requested point refresh")
    return result


def resident_endpoint(runtime_home):
    """Reject a confirmed dead route before a short Windows connect can time out."""
    from . import locks

    endpoint = routing.read_active_endpoint(control.routing_dir(runtime_home), verify_listener=False)
    if endpoint is None:
        raise ConnectionError("resident current-roster endpoint is unavailable")
    pid = getattr(endpoint, "pid", None)
    if type(pid) is int and pid > 0 and not locks.lock_is_running({
        "pid": pid, "start_time": getattr(endpoint, "process_start_time", None),
    }):
        raise ConnectionError("resident current-roster endpoint owner exited or was replaced")
    return endpoint


def _control_client() -> control.ControlClient:
    runtime_home = cfg.install_dir()
    endpoint = resident_endpoint(runtime_home)
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
    point_request = {key: value for key, value in request.items() if key != "bootstrap"}
    return validate_result(_control_client().worktrees_read(point_request), request)


def saved_result(request: dict, reason: str, *, db_path=None) -> dict:
    """Read the bounded saved publication without starting or computing anything."""
    project, selection = request_selection(request)
    path = cfg.install_dir() / "worktrees-current.sqlite3" if db_path is None else db_path
    result = worktrees_reporting.read_publication(
        lambda project, **kwargs: read_advisory(path, project, **kwargs), project, **selection,
        contract_version=request["contract_version"],
    )
    result.update(
        ok=True, contract_version=request["contract_version"],
        availability={"status": "advisory", "reason": reason},
        reporting_context=worktrees_reporting.current(project),
    )
    if request["contract_version"] == observations.VERSION:
        result["project"] = project
    return validate_result(result, request, quality="advisory")


def _wait_current(request: dict, *, recover: bool) -> dict:
    """Wait for usable resident data; the invoking process owns overall lifetime."""
    request_selection(request)
    next_recovery = 0.0
    delay = 0.05
    while True:
        try:
            result = _resident(request)
        except (_ResidentRetryable, TimeoutError):
            pass
        except ConnectionError:
            from . import status_monitor_runtime as monitor

            now = time.monotonic()
            if recover and now >= next_recovery and monitor._status_monitor_enabled():
                # The existing owner checks live identity and coalesces startup.
                next_recovery = now + 5.0
                monitor._ensure_status_monitor()
        else:
            if result.get("availability") is None:
                return result
        time.sleep(delay)
        delay = min(delay * 2, 1.0)


def read_current(request: dict, *, recover: bool = True, wait: bool = False) -> dict:
    if wait or "refresh_id" in request:
        return _wait_current(request, recover=recover)
    request_selection(request)
    unavailable = None
    recovery_pending = False
    try:
        return _resident(request)
    except ProjectionError as exc:
        # A resident that predates the demand-free marker rejects it; a passive
        # read then degrades to the labeled saved roster, never to a demand read.
        if request.get("passive") is not True:
            raise
        unavailable = f"resident rejected the passive read: {exc}"
    except _ResidentUnready as exc:
        unavailable = str(exc)
        recovery_pending = True
    except (ConnectionError, FileNotFoundError) as exc:
        from . import status_monitor_runtime as monitor

        unavailable = str(exc)
        if recover and monitor._status_monitor_enabled():
            started = monitor._ensure_status_monitor()
            if started:
                try:
                    return saved_result(request, "Resident starting; showing saved worktrees")
                except (ProjectionError, ProjectionUnavailable):
                    pass
            deadline = time.monotonic() + RECOVERY_WAIT if started else 0.0
            while time.monotonic() < deadline:
                try:
                    return _resident(request)
                except (ConnectionError, FileNotFoundError):
                    time.sleep(0.05)
                except _ResidentUnready as recovery_error:
                    unavailable = str(recovery_error)
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
                recovery_pending = bool(started)
    except (OSError, ValueError, RecursionError, ProjectionUnavailable) as exc:
        unavailable = str(exc)
    from . import status_monitor_runtime as monitor

    try:
        return saved_result(
            request, unavailable or "Resident unavailable; showing saved worktrees",
            db_path=monitor._aw_runtime_home() / "worktrees-current.sqlite3",
        )
    except (ProjectionError, ProjectionUnavailable) as exc:
        failure = ResidentRecoveryPending if recovery_pending else ProjectionUnavailable
        raise failure(f"{unavailable}; no usable advisory roster: {exc}") from exc


def emit_current(args) -> int:
    from . import config as cfg
    from . import list_cache, output

    if getattr(args, "profile_assignment_history", False):
        return output._json_error("current-roster reads cannot include profile history")
    if getattr(args, "refresh", False) and not getattr(args, "worktree_id", None):
        return output._json_error("--refresh requires --worktree-id")
    passive = bool(getattr(args, "passive", False))
    if passive and any(getattr(args, flag, False) for flag in ("follow", "glance", "refresh")):
        return output._json_error("--passive cannot be combined with --follow, --glance or --refresh")
    if getattr(args, "refresh", False) and getattr(args, "follow", False):
        return output._json_error("--refresh cannot be combined with --follow")
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
    if passive:
        request["passive"] = True
    if getattr(args, "refresh", False):
        import uuid

        from .worktrees_refresh import CAPABILITY as REFRESH_CAPABILITY

        try:
            health = read_health()
            capabilities = health.get("capabilities") if isinstance(health, dict) else None
            if not isinstance(capabilities, list) or REFRESH_CAPABILITY not in capabilities:
                raise ProjectionUnavailable(
                    f"selected resident lacks {REFRESH_CAPABILITY}; update the engine and retry"
                )
        except (OSError, ValueError, RecursionError, ProjectionUnavailable) as exc:
            return output._json_error(str(exc))
        request["refresh_id"] = uuid.uuid4().hex
    key = list_cache.cache_key(
        args, project=request["project"], tracking_status=args.tracking_status
    )
    if not passive:
        list_cache.note_demand(
            key, args, project=request["project"], tracking_status=args.tracking_status
        )
    if getattr(args, "follow", False):
        from .worktrees_feed_client import FeedFailure, relay

        try:
            return (
                relay(request, bootstrap=True) if getattr(args, "cache_only", False)
                else relay(request, wait=True)
            )
        except BrokenPipeError:
            return 0
        except (OSError, ValueError, RecursionError, ProjectionError, ProjectionUnavailable) as exc:
            if isinstance(exc, FeedFailure):
                code, retry = exc.code, exc.retry
            elif isinstance(exc, ResidentRecoveryPending):
                code, retry = "unready", True
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
        # A passive read makes one bounded resident attempt and otherwise
        # returns the labeled saved roster; it never starts or awaits a resident.
        result = read_current(request, recover=False) if passive else read_current(request, wait=True)
    except (OSError, ValueError, RecursionError, ProjectionError, ProjectionUnavailable) as exc:
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
                    **({"reporting_context": result["reporting_context"]}
                       if "reporting_context" in result else {}),
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
