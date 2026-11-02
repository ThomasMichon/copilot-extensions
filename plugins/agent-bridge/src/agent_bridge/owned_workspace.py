"""Compose origin-local Worktrees allocation before an owned SSH embodiment."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from .acquisition_authority import worktree_command
from .acquisition_contract import AcquisitionRefused
from .relationship_authority import canonical_owner
from .spawn_target import REQUEST_OVERRIDES_KEY, SpawnTarget

if TYPE_CHECKING:
    from .db import Database
    from .session_manager import Session

_FIELD = "owned_workspace"
_LOG = logging.getLogger(__name__)


def selects_new_owned_ssh(target: SpawnTarget) -> bool:
    return bool(
        target.type == "ssh" and target.project and target.caller_owner_ref
        and not target.worktree_id and not target.explicit_cwd and not target.native_resume_id
        and not target.spawn_command and not target.codespace and not target.container
    )


async def acquire_start(session: Session) -> asyncio.Lock | None:
    """Serialize this original startup with the existing stop/resume owner."""
    if not selects_new_owned_ssh(session.target):
        return None
    lock = session._lifecycle_lock
    await lock.acquire()
    return lock


def arm(target: SpawnTarget, session_id: str) -> SpawnTarget:
    """Persist one logical allocation identity in the existing session target."""
    if not selects_new_owned_ssh(target):
        return target
    owner = canonical_owner(target.caller_owner_ref)
    if (
        len(owner.split("/")) != 3 or not target.execution_space_key
        or target.execution_space_key == owner.split("/")[0]
    ):
        raise AcquisitionRefused(
            "allocation_destination_unproven",
            "owned SSH creation requires distinct canonical origin and destination execution spaces",
            effect="not_started",
        )
    venue = dict(target.venue or {})
    overrides = dict(venue.get(REQUEST_OVERRIDES_KEY) or {})
    if _FIELD in overrides:
        raise AcquisitionRefused("allocation_conflict", "new session already carries allocation intent")
    overrides[_FIELD] = {
        "version": 1, "creation_key": f"bridge-session:{session_id}",
        "creator": owner, "machine": target.execution_space_key, "project": target.project,
        "caller_worktree": target.caller_worktree,
    }
    venue[REQUEST_OVERRIDES_KEY] = overrides
    return replace(target, venue=venue)


def _intent(target: SpawnTarget) -> dict[str, Any]:
    value = (target.venue or {}).get(REQUEST_OVERRIDES_KEY, {}).get(_FIELD)
    if not isinstance(value, dict) or set(value) != {
        "version", "creation_key", "creator", "machine", "project", "caller_worktree",
    } or type(value["version"]) is not int or value["version"] != 1:
        raise AcquisitionRefused(
            "allocation_intent_missing",
            "owned SSH allocation has no retained intent; inspect the original legacy operation before replacement",
        )
    if (
        not isinstance(value["creation_key"], str) or not value["creation_key"].strip()
        or len(value["creation_key"]) > 1024
        or value["creator"] != canonical_owner(target.caller_owner_ref)
        or value["machine"] != target.execution_space_key or value["project"] != target.project
        or value["caller_worktree"] != target.caller_worktree
    ):
        raise AcquisitionRefused("allocation_conflict", "original owned allocation facts changed")
    return value


def retains_allocation(session: Session) -> bool:
    """Keep the original allocation fence after its child has been recorded."""
    target = session.target
    if _FIELD not in (target.venue or {}).get(REQUEST_OVERRIDES_KEY, {}):
        return False
    intent = _intent(target)
    if intent["creation_key"] != f"bridge-session:{session.session_id}":
        raise AcquisitionRefused("allocation_conflict", "allocation belongs to another bridge session")
    return True


def can_resume_allocation(session: Session) -> bool:
    """Only an unallocated retained target proves native launch has not begun."""
    return bool(
        not session.acp_session_id and session.client is None
        and selects_new_owned_ssh(session.target) and retains_allocation(session)
    )


def _result(value: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    creation, saved, allocation = value.get("creation"), value.get("worktree"), value.get("allocation")
    if (
        not isinstance(creation, dict) or type(creation.get("version")) is not int
        or creation["version"] != 1 or creation.get("status") != "complete"
        or not isinstance(saved, dict) or not isinstance(allocation, dict)
        or type(allocation.get("version")) is not int or allocation["version"] != 1
        or allocation.get("creator") != intent["creator"]
        or allocation.get("creation_key") != intent["creation_key"]
        or not isinstance(allocation.get("request_id"), str) or not allocation["request_id"]
        or allocation.get("linkage") not in {"pending", "bound", "confirmed", "unknown"}
        or allocation.get("receiving_status") not in {"allocated", "confirmed"}
    ):
        raise AcquisitionRefused("allocation_unproven", "origin did not prove the original receiving creation")
    destination = allocation.get("target")
    options = destination.get("options") if isinstance(destination, dict) else None
    if (
        not isinstance(destination, dict) or destination.get("machine") != intent["machine"]
        or destination.get("project") != intent["project"] or not isinstance(options, dict)
        or options.get("kind") != "bridge" or options.get("interface") != "acp"
        or options.get("origin") != "delegate"
        or options.get("caller_worktree") != intent["caller_worktree"]
        or saved.get("repo") != intent["project"] or saved.get("owner_ref") != intent["creator"]
        or saved.get("execution_machine") != intent["machine"]
        or saved.get("kind") != "bridge" or saved.get("caller_worktree") != intent["caller_worktree"]
        or any(not isinstance(saved.get(key), str) or not saved[key].strip()
               for key in ("id", "path", "creation_nonce"))
    ):
        raise AcquisitionRefused("allocation_conflict", "receiving creation differs from the selected destination")
    path = saved["path"]
    posix, windows = PurePosixPath(path), PureWindowsPath(path)
    if (
        "\0" in path or not (posix.is_absolute() or windows.is_absolute())
        or ".." in posix.parts or ".." in windows.parts
    ):
        raise AcquisitionRefused("allocation_unproven", "receiving creation returned an invalid worktree path")
    return saved


def require_capability(capabilities: dict[str, Any], intent: dict[str, Any]) -> str:
    ordinary = capabilities.get("ordinary_create")
    if (
        capabilities.get("schema") != "copilot-extensions.worktree-allocation"
        or type(capabilities.get("version")) is not int or capabilities["version"] != 1
        or capabilities.get("role") != "origin" or not isinstance(ordinary, dict)
        or type(ordinary.get("version")) is not int or ordinary["version"] != 1
        or ordinary.get("destination_selectors") != ["machine", "repo"]
        or ordinary.get("creation_key") != "opaque-v1"
        or ordinary.get("result") != "ordinary-worktree+allocation-v1"
        or ordinary.get("kinds") != ["session", "bridge"]
        or ordinary.get("caller_worktree") is not True
        or ordinary.get("creator_identity") is not True
        or ordinary.get("claim_linkage") != "non-blocking" or ordinary.get("agent_launch") is not False
    ):
        raise AcquisitionRefused(
            "allocation_capability_missing", "origin Worktrees lacks bridge-shaped destination creation",
            effect="not_started",
        )
    creator = capabilities.get("creator")
    if (
        not isinstance(creator, str) or len(creator.split("/")) != 3
        or creator.split("/")[1:] != intent["creator"].split("/")[1:]
        or creator.split("/")[0] == intent["machine"]
    ):
        raise AcquisitionRefused(
            "allocation_origin_unproven", "origin command did not prove its actual local creator",
            effect="not_started",
        )
    return creator


async def prepare_session(session: Session, db: Database) -> None:
    """Keep unknown effects recoverable under the same key before any native spawn."""
    target = session.target
    if not selects_new_owned_ssh(target):
        return
    intent = _intent(target)
    owner_ref = target.caller_owner_ref
    if not isinstance(owner_ref, str):
        raise AcquisitionRefused("allocation_conflict", "original allocation owner is unavailable")
    project = intent["creator"].split("/")[1]
    capabilities = await worktree_command([
        "worktree-allocation", "capabilities", "--owner-ref", owner_ref, "--json",
    ], project)
    creator = require_capability(capabilities, intent)
    args = [
        "create", "--machine", intent["machine"], "--repo", intent["project"],
        "--creation-key", intent["creation_key"], "--owner-ref", owner_ref,
        "--bridge", "--interface", "acp", "--origin", "delegate", "--json",
    ]
    if intent["caller_worktree"] is not None:
        args += ["--caller-worktree", intent["caller_worktree"]]
    result = await worktree_command(args, project, timeout=210)
    saved = _result(result, {**intent, "creator": creator})
    target.worktree_id, target.cwd = saved["id"], saved["path"]
    db.update_session_target(session.session_id, target.to_json(), target.cwd)
    _LOG.info(
        "Owned receiving workspace is durable; origin linkage snapshot=%s request=%s",
        result["allocation"]["linkage"], result["allocation"]["request_id"],
    )
