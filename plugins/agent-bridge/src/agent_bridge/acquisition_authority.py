"""Read owner facts through the existing dispatch namespace and worktree CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from ._peer_launch import ContextRefused
from .acquisition_contract import AcquisitionRefused, AcquisitionRequest
from .resolver_diagnostics import resolver_failure_detail, structured_resolver_error
from .session_lifecycle_cli import _agent_worktrees_environment, _agent_worktrees_launch_prefix
from .worktree_head import parse_head_payload

log = logging.getLogger("agent-bridge")


def _bounded_log_text(text: str | None, limit: int) -> str | None:
    if text is None or len(json.dumps(text, ensure_ascii=True)) <= limit:
        return text

    def clipped(keep: int) -> str:
        # Keep the error lead-in and trailing stage/code labels after redaction.
        tail = keep // 2
        return text[:keep - tail] + "..." + (text[-tail:] if tail else "")

    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(clipped(middle), ensure_ascii=True)) <= limit:
            low = middle
        else:
            high = middle - 1
    return clipped(low)


async def worktree_command(
    args: list[str], project: str | None, *, before_spawn: Callable[[], None] | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    try:
        prefix = _agent_worktrees_launch_prefix()
    except ContextRefused as exc:
        raise AcquisitionRefused("worktrees_unavailable", str(exc)) from exc
    if not prefix:
        raise AcquisitionRefused("worktrees_unavailable", "worktree owner capability is unavailable")
    argv = [*prefix, *(["--project", project] if project else []), *args]
    env = _agent_worktrees_environment()
    from agent_procutil import no_window_kwargs

    started = time.monotonic()
    correlation_id = uuid.uuid4().hex
    if before_spawn is not None:
        before_spawn()
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env, **no_window_kwargs(),
    )

    def log_failure(reason: str, stdout: bytes | None = None, stderr: bytes | None = None) -> None:
        operation = args[0] if args and args[0] in {
            "create", "record", "head-session", "bind-session",
        } else "other"
        # The existing formatter owns parsing, redaction and per-field bounds.
        # No raw response or argument list is serialized.
        log.error("worktree_owner_failure %s", json.dumps({
            "operation": operation,
            "correlation_id": correlation_id,
            "reason": reason,
            "exit_code": proc.returncode,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "capture_complete": stdout is not None and stderr is not None,
            "stdout_bytes": len(stdout) if stdout is not None else None,
            "stderr_bytes": len(stderr) if stderr is not None else None,
            "owner_error": _bounded_log_text(structured_resolver_error(
                stdout.decode(errors="replace"), environment=env,
            ), 4608) if stdout is not None else None,
            "stderr": _bounded_log_text(resolver_failure_detail(
                "", stderr.decode(errors="replace"), environment=env,
            ), 2048) if stderr else None,
        }, ensure_ascii=True))

    try:
        if "--bridge-plugin-request" in args:
            # The producer cooperatively cancels this bounded preparation and
            # awaits this owner before releasing its operation files/locks.
            stdout, stderr = await proc.communicate()
        else:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        # The owner may still be launching. Never terminate it or retry a launch.
        log_failure("owner_operation_pending")
        raise AcquisitionRefused("owner_operation_pending", "worktree owner operation timed out") from exc
    except asyncio.CancelledError:
        log_failure("capture_cancelled")
        raise
    except OSError:
        log_failure("capture_failed")
        raise
    if proc.returncode:
        if "--bridge-plugin-request" in args:
            try:
                failure = json.loads(stdout)
            except (ValueError, TypeError):
                failure = None
            if (
                isinstance(failure, dict) and failure.get("plugin_preparation_version") == 1
                and failure.get("native_launch_started") is False
                and failure.get("worktree_id") == args[args.index("--worktree-id") + 1]
            ):
                log_failure("plugin_preparation_failed", stdout, stderr)
                raise AcquisitionRefused(
                    "plugin_preparation_failed", str(failure.get("error") or "plugin preparation failed before launch"),
                )
        if proc.returncode == 3 and args[0] == "create" and "--creation-key" in args:
            try:
                failure = json.loads(stdout)
            except (ValueError, TypeError):
                failure = None
            creation = failure.get("creation") if isinstance(failure, dict) else None
            if isinstance(creation, dict) and creation.get("version") == 1 and creation.get("status") == "pending":
                log_failure("owner_operation_pending", stdout, stderr)
                raise AcquisitionRefused("owner_operation_pending", "original keyed workspace allocation is pending")
        detail = resolver_failure_detail(
            stdout.decode(errors="replace"), stderr.decode(errors="replace"), environment=env,
        )
        log_failure("owner_refused", stdout, stderr)
        raise AcquisitionRefused(
            "owner_refused", f"worktree owner exit_code={proc.returncode}: {detail}"[:1500],
        )
    try:
        result = json.loads(stdout)
    except (ValueError, TypeError) as exc:
        log_failure("owner_response_invalid", stdout, stderr)
        raise AcquisitionRefused("owner_response_invalid", "worktree owner returned invalid JSON") from exc
    if not isinstance(result, dict):
        log_failure("owner_response_invalid", stdout, stderr)
        raise AcquisitionRefused("owner_response_invalid", "worktree owner returned no object")
    if args[0] == "bind-session" and (
        result.get("bound") is not True or result.get("is_head") is not True
        or result.get("worktree_id") != args[args.index("--worktree-id") + 1]
        or result.get("session") != args[args.index("--session-id") + 1]
        or result.get("head_session") != result.get("session")
    ):
        log_failure("native_binding_unproven", stdout, stderr)
        raise AcquisitionRefused("native_binding_unproven", "owner did not bind the exact native head")
    return result


async def read_worktree_record(
    worktree_id: str, project: str | None, *,
    command: Callable[[list[str], str | None], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Read exact owner metadata, never a roster or launch/no-start attestation."""
    result = await (command or worktree_command)(
        ["record", "--worktree-id", worktree_id, "--json"], project,
    )
    if not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1:
        raise AcquisitionRefused("owner_response_invalid", "worktree owner must support record JSON version 1")
    if result.get("ok") is False:
        raise AcquisitionRefused("owner_refused", f"exact worktree record read failed: {result.get('error')}")
    row = result.get("worktree")
    if result.get("ok") is not True or not isinstance(row, dict):
        raise AcquisitionRefused("owner_response_invalid", "owner returned no successful exact worktree record")
    if not {
        "id", "repo", "path", "status", "creation_nonce", "session_count", "last_session_id",
        "execution_leg", "sessions_indexed", "execution_leg_opaque",
        "session_backend", "session_backend_opaque",
    } <= row.keys():
        raise AcquisitionRefused("owner_response_invalid", "owner returned an incomplete exact worktree record")
    if (
        row.get("id") != worktree_id
        or not isinstance(row.get("repo"), str) or not row["repo"].strip()
        or not isinstance(row.get("path"), str) or not row["path"].strip()
    ):
        raise AcquisitionRefused("owner_response_invalid", "owner did not resolve the exact workspace identity")
    path = row["path"]
    posix, windows = PurePosixPath(path), PureWindowsPath(path)
    if (
        "\0" in path or not (posix.is_absolute() or windows.is_absolute())
        or ".." in posix.parts or ".." in windows.parts
        or not isinstance(row.get("status"), str)
        or row["status"] not in {
            "active", "complete", "completed", "pushed", "finalizing",
            "finalized", "orphaned", "archived",
        }
    ):
        raise AcquisitionRefused("owner_response_invalid", "owner returned an invalid workspace path or status")
    count = row.get("session_count")
    if count is not None and (type(count) is not int or count < 0):
        raise AcquisitionRefused("owner_response_invalid", "owner returned invalid recorded session history")
    last_session = row.get("last_session_id")
    if last_session is not None and (
        not isinstance(last_session, str) or not last_session.strip()
    ):
        raise AcquisitionRefused("owner_response_invalid", "owner returned invalid last_session_id")
    nonce = row.get("creation_nonce")
    if nonce is not None and not isinstance(nonce, str):
        raise AcquisitionRefused("owner_response_invalid", "owner returned invalid creation_nonce")
    for field in ("sessions_indexed", "execution_leg_opaque", "session_backend_opaque"):
        if type(row[field]) is not bool:
            raise AcquisitionRefused("owner_response_invalid", f"owner returned invalid {field}")
    return row


async def verify_dispatch(request: AcquisitionRequest, resolver: Any) -> dict[str, Any]:
    """Current generation fences admission; historical generation is not lineage."""
    try:
        resolved = await resolver.resolve_async(f"dispatch:{request.task_id}")
    except (KeyError, ValueError, RuntimeError) as exc:
        raise AcquisitionRefused("dispatch_authority_unavailable", str(exc)) from exc
    venue = resolved.venue or {}
    task = venue.get("task")
    reservation = venue.get("acquisition_reservation")
    if not isinstance(task, dict) or not isinstance(reservation, dict):
        raise AcquisitionRefused("dispatch_capability_missing", "dispatch has no acquisition authority")
    if (
        task.get("id") != request.task_id
        or task.get("generation") != request.task_generation
        or task.get("hold_at") is not None
        or task.get("status") not in {"queued", "claimed", "started", "suspended"}
        or reservation.get("key") != request.reservation_key
        or reservation.get("worktree") != request.worktree_id
        or reservation.get("worktree_ownership") != request.allocation
        or reservation.get("conversation_retired") is not request.conversation_retired
        or reservation.get("state") not in {"reserving", "spawned"}
        or reservation.get("release_requested")
        or resolved.worktree_id != request.worktree_id
        or request.version == 2 and (
            reservation.get("allocation_project") != request.project
            or reservation.get("worktree_path") != request.worktree_path
        )
    ):
        raise AcquisitionRefused("dispatch_fence_changed", "task/reservation admission fence changed")
    return venue


async def verify_worktree(request: AcquisitionRequest) -> tuple[dict[str, Any], str | None]:
    row = await read_worktree_record(request.worktree_id, request.project)
    if request.version == 2 and (
        row.get("repo") != request.project or row.get("path") != request.worktree_path
    ):
        raise AcquisitionRefused("workspace_identity_conflict", "worktree owner changed execution identity")
    if row.get("status") == "finalizing":
        raise AcquisitionRefused("transition_pending", "worktree finalization is in progress")
    if row.get("status") in {"finalized", "complete", "completed", "archived"}:
        raise AcquisitionRefused("worktree_terminal", "worktree is terminal")
    head = parse_head_payload(json.dumps(await worktree_command(
        ["head-session", "--worktree-id", request.worktree_id, "--json"], request.project,
    )))
    if not head.tracked:
        raise AcquisitionRefused("head_unknown", "ground-layer native head is unknown")
    if head.occupied and head.state != "active":
        raise AcquisitionRefused("transition_pending", "ground-layer handoff is pending")
    return row, head.head_session


async def bind_dispatch_receipt(
    request: AcquisitionRequest, result: Any, resolver: Any,
) -> None:
    provider = resolver.namespace_resolvers.get("dispatch")
    run = getattr(provider, "_run", None)
    if run is None:
        raise AcquisitionRefused("dispatch_capability_missing", "dispatch cannot persist acquisition receipts")
    receipt = {
        field: getattr(result, field) for field in (
            "task_id", "task_generation", "worktree_id", "session_id",
            "native_session_id", "mode", "delivery_id",
        )
    }
    receipt["reservation_key"] = request.reservation_key
    output = await run(["acquisition-bind", "--request-json", json.dumps(receipt)])
    if output is None or output[0]:
        raise AcquisitionRefused("dispatch_receipt_refused", "dispatch did not accept native acquisition authority")
    try:
        actual = json.loads(output[1])
    except ValueError as exc:
        raise AcquisitionRefused("dispatch_receipt_unproven", "dispatch receipt response is invalid") from exc
    if actual != receipt:
        raise AcquisitionRefused("dispatch_receipt_unproven", "dispatch receipt response changed native authority")


async def mark_dispatch_acquisition(request: AcquisitionRequest, resolver: Any) -> None:
    provider = resolver.namespace_resolvers.get("dispatch")
    run = getattr(provider, "_run", None)
    if run is None:
        raise AcquisitionRefused("dispatch_capability_missing", "dispatch acquisition marking unavailable")
    facts = {field: getattr(request, field) for field in ("reservation_key", "task_id", "task_generation")}
    response = await run(["acquisition-mark", "--request-json", json.dumps(facts)])
    if not response or response[0] or json.loads(response[1]) != facts:
        raise AcquisitionRefused("dispatch_marker_refused", "dispatch acquisition marker was not accepted")
