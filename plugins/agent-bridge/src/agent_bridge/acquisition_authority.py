"""Read owner facts through the existing dispatch namespace and worktree CLI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from ._peer_launch import ContextRefused
from .acquisition_contract import AcquisitionRefused, AcquisitionRequest
from .session_lifecycle_cli import _agent_worktrees_environment, _agent_worktrees_launch_prefix
from .worktree_head import parse_head_payload


async def worktree_command(args: list[str], project: str | None) -> dict[str, Any]:
    try:
        prefix = _agent_worktrees_launch_prefix()
    except ContextRefused as exc:
        raise AcquisitionRefused("worktrees_unavailable", str(exc)) from exc
    if not prefix:
        raise AcquisitionRefused("worktrees_unavailable", "worktree owner capability is unavailable")
    argv = [*prefix, *(["--project", project] if project else []), *args]
    env = _agent_worktrees_environment()
    from agent_procutil import no_window_kwargs

    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env, **no_window_kwargs(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 120)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        # The owner may still be launching. Never terminate it or retry a launch.
        raise AcquisitionRefused("owner_operation_pending", "worktree owner operation timed out") from exc
    if proc.returncode:
        if proc.returncode == 3 and args[0] == "create" and "--creation-key" in args:
            try:
                failure = json.loads(stdout)
            except (ValueError, TypeError):
                failure = None
            creation = failure.get("creation") if isinstance(failure, dict) else None
            if isinstance(creation, dict) and creation.get("version") == 1 and creation.get("status") == "pending":
                raise AcquisitionRefused("owner_operation_pending", "original keyed workspace allocation is pending")
        raise AcquisitionRefused(
            "owner_refused", stderr.decode(errors="replace").strip() or stdout.decode(errors="replace"),
        )
    try:
        result = json.loads(stdout)
    except (ValueError, TypeError) as exc:
        raise AcquisitionRefused("owner_response_invalid", "worktree owner returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise AcquisitionRefused("owner_response_invalid", "worktree owner returned no object")
    if args[0] == "bind-session" and (
        result.get("bound") is not True or result.get("is_head") is not True
        or result.get("worktree_id") != args[args.index("--worktree-id") + 1]
        or result.get("session") != args[args.index("--session-id") + 1]
        or result.get("head_session") != result.get("session")
    ):
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
