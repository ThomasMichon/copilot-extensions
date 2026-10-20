"""Retained claims cross the owning Worktrees command boundary, never its store."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import sys
from typing import Any

from agent_procutil import no_window_kwargs, spawn_in_kill_on_close_job

from ._peer_launch import ContextRefused
from .local_cache_refresh import _kill_tree
from .session_lifecycle_cli import _agent_worktrees_environment, _agent_worktrees_launch_prefix

SCHEMA = "copilot-extensions.resource-relationship"
NAMESPACE = "bridge-relationship"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_TIMEOUT = 120
_CLEANUP_TIMEOUT = 5
log = logging.getLogger("agent-bridge")


class RelationshipRefused(ValueError):
    """A managed request cannot proceed without confirmed relationship authority."""


class RelationshipUnknown(RelationshipRefused):
    """The original transaction may have committed; reconcile its stable key."""


def native_identity(native_id: str) -> dict[str, str]:
    """The binding is already in recovery; bound subjects add a fixed-size ID."""
    return {"native_id_sha256": hashlib.sha256(native_id.encode("utf-8")).hexdigest()}


def _subject(metadata: dict[str, Any]) -> str:
    envelope = {
        key: metadata[key] for key in ("version", "token", "creator", "request_id", "recovery")
    }
    envelope["phase"] = "bound" if "identity" in metadata else "pending"
    if "identity" in metadata:
        envelope["identity"] = metadata["identity"]
    encoded = base64.urlsafe_b64encode(json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")).decode("ascii").rstrip("=")
    return NAMESPACE + ":l1." + encoded


def require_binding_capacity(claim: dict[str, Any]) -> None:
    """Check the eventual bound callback before any peer connection."""
    metadata = claim.get("producer")
    if not isinstance(metadata, dict):
        raise RelationshipUnknown("Relationship capacity has no confirmed claim metadata")
    if len(_subject({**metadata, "identity": {"native_id_sha256": "0" * 64}})) > 4096:
        raise RelationshipRefused("Managed relationship cannot fit its bound callback subject")


def canonical_owner(value: str) -> str:
    """Session provenance is not a different worktree owner."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise RelationshipRefused("Managed relationship requires a qualified owner")
    owner = value.partition("#")[0]
    parts = owner.split("/")
    if len(parts) < 3 or any(not part or part in {".", ".."} for part in parts):
        raise RelationshipRefused("Managed relationship requires a qualified owner")
    if len(owner) > 1024 or any(ord(char) < 33 or char == "\\" for char in owner):
        raise RelationshipRefused("Invalid qualified relationship owner")
    return owner


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate relationship response key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Non-finite relationship response")


async def _retire_command(proc, job, group_identity: str | None) -> None:
    if job is not None:
        cleanup = asyncio.create_task(asyncio.to_thread(job.terminate_and_wait, _CLEANUP_TIMEOUT))
        cancelled = None
        try:
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError as exc:
                    cancelled = exc
            cleanup.result()
        except (OSError, RuntimeError, ValueError) as exc:
            if cancelled is not None:
                raise cancelled from exc
            raise
        if cancelled is not None:
            raise cancelled
    else:
        await asyncio.wait_for(_kill_tree(proc, None, group_identity), _CLEANUP_TIMEOUT)


async def command(
    operation: str, owner: str | None = None, payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        prefix = _agent_worktrees_launch_prefix(allow_path_fallback=False)
    except (ContextRefused, OSError, ValueError) as exc:
        raise RelationshipRefused("Worktrees relationship authority is unavailable") from exc
    if not prefix:
        raise RelationshipRefused("Worktrees relationship authority is unavailable")
    args = [*prefix, "resource-relationship", operation, "--json"]
    if owner is not None:
        args.extend(["--owner-ref", canonical_owner(owner)])
    data = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else b""
    if len(data) > 16384:
        raise RelationshipRefused("Relationship request exceeds the authority input limit")
    try:
        proc, job = await spawn_in_kill_on_close_job(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=_agent_worktrees_environment(),
            start_new_session=sys.platform != "win32",
            **no_window_kwargs(),
        )
    except (OSError, RuntimeError) as exc:
        raise RelationshipRefused("Worktrees relationship command could not start") from exc
    group_identity = None
    try:
        if job is not None and not callable(getattr(job, "terminate_and_wait", None)):
            proc.stdin.close()
            job.close()
            await asyncio.wait_for(proc.wait(), _CLEANUP_TIMEOUT)
            raise RelationshipRefused("Confirmed native relationship retirement is unavailable")
        if job is None:
            if sys.platform != "win32":
                from zdd.diagnostics import process_start_time
                group_identity = process_start_time(proc.pid)
            if group_identity is None:
                # Transaction commands read stdin before resolving or mutating an owner.
                proc.stdin.close()
                await _retire_command(proc, job, None)
                raise RelationshipRefused("Native relationship command custody is unavailable")
        try:
            stdout, _stderr = await asyncio.wait_for(proc.communicate(data), _TIMEOUT)
        except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError, OSError) as exc:
            cleanup_error = None
            try:
                await _retire_command(proc, job, group_identity)
                await asyncio.wait_for(proc.communicate(), _CLEANUP_TIMEOUT)
                if proc.returncode is None:
                    raise RuntimeError("Relationship command has not exited")
            except (TimeoutError, asyncio.TimeoutError, OSError, RuntimeError) as error:
                cleanup_error = error
                log.warning("Relationship command cleanup is unconfirmed; original outcome remains unknown")
            if isinstance(exc, asyncio.CancelledError):
                if cleanup_error is not None:
                    raise asyncio.CancelledError(
                        "Relationship command cancelled; native cleanup is unconfirmed",
                    ) from cleanup_error
                raise
            message = "Relationship command outcome is unknown; inspect the original request"
            if cleanup_error is not None:
                message += "; native cleanup is unconfirmed"
            raise RelationshipUnknown(message) from cleanup_error
        try:
            await _retire_command(proc, job, group_identity)
        except (TimeoutError, asyncio.TimeoutError, OSError, RuntimeError) as exc:
            raise RelationshipUnknown("Relationship command cleanup was not confirmed") from exc
    finally:
        if job is not None:
            job.close()
    try:
        if len(stdout) > 65536:
            raise ValueError("Oversized relationship response")
        result = json.loads(
            stdout.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RelationshipUnknown("Relationship authority returned an invalid response") from exc
    if not isinstance(result, dict):
        raise RelationshipUnknown("Relationship authority returned no response object")
    if (
        result.get("schema") != SCHEMA
        or type(result.get("version")) is not int or result["version"] != 1
    ):
        raise RelationshipRefused("Worktrees does not support the retained relationship contract")
    if proc.returncode or result.get("error"):
        raise RelationshipRefused("Worktrees refused the retained relationship transaction")
    return result


async def require_capability() -> None:
    result = await command("capabilities")
    if (
        result.get("lifetime") != "retained-relationship"
        or type(result.get("metadata_version")) is not int or result["metadata_version"] != 2
        or not isinstance(result.get("operations"), list)
        or not {"reserve", "inspect", "bind", "close"}.issubset(result["operations"])
        or result.get("ref_encoding") != "namespace:l1.base64url"
        or result.get("inspect_absence") is not True
    ):
        raise RelationshipRefused("Worktrees retained relationship capability is incompatible")


def confirm_absence(result: dict[str, Any], *, owner: str, request_id: str) -> bool:
    """Only exact read-only authority evidence establishes no reservation."""
    if "absent" not in result:
        return False
    expected = {
        "schema": SCHEMA, "version": 1, "ok": True,
        "owner": owner, "request_id": request_id, "absent": True,
    }
    if (
        result.get("absent") is not True or result.get("ok") is not True
        or type(result.get("version")) is not int or result != expected
    ):
        raise RelationshipUnknown("Relationship authority did not confirm exact reservation absence")
    return True


def confirm(
    result: dict[str, Any], *, owner: str, request_id: str, recovery: dict[str, Any],
    token: str | None = None, identity: dict[str, Any] | None = None,
    closed: bool = False,
) -> dict[str, Any]:
    """Check exact subject evidence before treating a write response as authority."""
    metadata = result.get("producer")
    if not isinstance(metadata, dict):
        raise RelationshipUnknown("Relationship confirmation has no protected metadata")
    actual_token = result.get("token")
    expected = {
        "version": 2, "lifetime": "retained-relationship", "namespace": NAMESPACE,
        "creator": owner, "request_id": request_id, "recovery": recovery,
        "token": actual_token, "phase": result.get("phase"),
    }
    if identity is not None:
        expected["identity"] = identity
    if closed and "closeout" in metadata:
        expected["closeout"] = metadata["closeout"]
    ref = _subject(expected)
    if closed:
        closeout = metadata.get("closeout")
        proof = closeout.get("relationship_verification") if isinstance(closeout, dict) else None
        if (
            not isinstance(closeout, dict) or closeout.get("available") is not True
            or type(closeout.get("exists")) is not bool or not isinstance(proof, dict)
            or type(proof.get("version")) is not int or proof["version"] != 1
            or proof.get("subject") != ref or proof.get("retired") is not True
            or proof.get("quiescent") is not True
        ):
            raise RelationshipUnknown("Relationship close has no exact provider retirement proof")
    if (
        result.get("ok") is not True or result.get("owner") != owner
        or result.get("creator") != owner or result.get("request_id") != request_id
        or not isinstance(actual_token, str) or not _TOKEN.fullmatch(actual_token)
        or token is not None and token != actual_token
        or result.get("phase") not in ({"closed"} if closed else {"bound"} if identity is not None else {"pending"})
        or json.dumps(metadata, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False)
        or result.get("ref") != ref
        or len(result["ref"]) > 4096
    ):
        raise RelationshipUnknown("Relationship authority did not confirm the exact requested subject")
    return result
