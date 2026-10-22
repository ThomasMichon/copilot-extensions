"""Frozen ordinary startup intent; identity is lineage, never prompt digest."""

from __future__ import annotations

import hashlib
import json

from .ordinary_agent_facts import identity
from .queue_records import TaskError

KEYED_NATIVE_PROTOCOL = 45


def startup_seed(task: dict, client, *, route: str = "", all_repos: bool = False) -> str:
    """Use one original-lineage seed for both ordinary launch entry points."""
    from .embody import autopilot_worker_prompt
    if not callable(getattr(client, "get_reservation", None)):
        from .bridge import BridgeUnavailable
        raise BridgeUnavailable("ordinary coordinator reservation read-back is unavailable")
    from .client import DispatchError
    try:
        reservation = client.get_reservation(task["spawn_reservation_key"])
    except (DispatchError, TaskError) as exc:
        from .bridge import BridgeCarriedSessionBusy
        raise BridgeCarriedSessionBusy(f"ordinary startup seed reservation read failed: {exc}") from exc
    origin = reservation.get("allocation_reservation_key") or reservation["key"]
    return autopilot_worker_prompt(
        task["id"], worker_id=f"dispatch-{origin}", route=route,
        repo=None if all_repos else task.get("repo"), all_repos=all_repos,
        explicit_worker_identity=False,
    )


def same_facts(left: object, right: object) -> bool:
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
            right, sort_keys=True, allow_nan=False,
        )
    except (TypeError, ValueError):
        return False


def historical_intent(value: object) -> dict:
    if (
        not isinstance(value, dict) or type(value.get("version")) is not int
        or value["version"] != 1 or not isinstance(value.get("binding"), dict)
        or type(value["binding"].get("admission_generation")) is not int
        or value["binding"]["admission_generation"] < 0
    ):
        raise TaskError("unsupported historical ordinary invocation facts")
    selection = value.get("selection")
    if (
        not isinstance(selection, dict) or set(selection) != {"agent", "charter"}
        or not isinstance(selection.get("agent"), str) or not selection["agent"].strip()
        or selection["charter"] is not None and (
            not isinstance(selection["charter"], str) or not selection["charter"].strip()
        )
    ):
        raise TaskError("unsupported historical ordinary invocation selection")
    return value


def native_payload(invocation: dict) -> dict:
    """Selection is coordinator-owned intent, not an ignored Bridge wire field."""
    historical_intent(invocation)
    return {key: value for key, value in invocation.items() if key != "selection"}


def prepare(
    task: dict, reservation: dict, info: dict, prompt: str, *,
    worktree_record: dict | None = None, agent: str = "task-worker", charter: str | None = None,
) -> dict:
    actual = identity(info)
    origin = reservation.get("allocation_reservation_key") or reservation["key"]
    previous = info.get("startup_invocation")
    if previous is not None:
        previous = historical_intent(previous)
    selection = {"agent": agent, "charter": charter}
    if previous is not None and not same_facts(previous["selection"], selection):
        raise TaskError("ordinary startup agent or charter selection changed")
    generation = (previous["binding"]["admission_generation"]
                  if previous is not None else task["generation"])
    scope = [task["id"], generation, origin, reservation["creation_key"],
             reservation["allocation_revision"]]
    key = hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()
    creation = info.get("creation")
    allocation = creation.get("worktree") if isinstance(creation, dict) else None
    allocation = allocation if isinstance(allocation, dict) else {}
    old_binding = (previous or {}).get("binding", {})
    nonce = (old_binding.get("creation_nonce") or allocation.get("creation_nonce")
             or (worktree_record or {}).get("creation_nonce"))
    if not isinstance(nonce, str) or not nonce.strip():
        raise TaskError("ordinary invocation lacks the retained allocation nonce")
    origin_kind = old_binding.get("origin_kind") or (
        "resumed" if info.get("fact_kind") == "resumed" else "created"
    )
    first_turn = old_binding.get("expected_turn_index")
    if first_turn is None:
        first_turn = info.get("turn_count") if origin_kind == "resumed" else 0
    if type(first_turn) is not int or first_turn < 0:
        raise TaskError("ordinary invocation lacks the exact original turn position")
    rendered = (
        f"{prompt}\n\nUse `--owner-session-id {actual.native}` "
        "on the task's owner-gated start command. This is the retained native "
        "conversation identity; claim and lease rules remain unchanged."
    )
    result = {
        "version": 1, "invocation_key": key, "selection": selection,
        "expected_native_session_id": actual.native, "prompt": rendered,
        "binding": {
            "task_id": task["id"], "admission_generation": generation,
            "original_reservation_key": origin, "creation_key": reservation["creation_key"],
            "allocation_revision": reservation["allocation_revision"],
            "expected_turn_index": first_turn, "origin_kind": origin_kind,
            "worktree_id": actual.worktree, "project": actual.project,
            "path": actual.path, "creation_nonce": nonce,
        },
    }
    if previous is not None and not same_facts(previous, result):
        raise TaskError("ordinary startup invocation facts or exact prompt changed")
    return historical_intent(result)


def validate_storage(invocation: dict, task, reservation, info: dict) -> None:
    historical_intent(invocation)
    binding = invocation.get("binding")
    if not isinstance(binding, dict):
        raise TaskError("ordinary invocation binding is missing")
    if (
        type(binding.get("admission_generation")) is not int
        or type(binding.get("allocation_revision")) is not int
        or type(binding.get("expected_turn_index")) is not int
        or binding["expected_turn_index"] < 0
        or binding.get("origin_kind") not in {"created", "resumed"}
        or binding["admission_generation"] < 0 or binding["allocation_revision"] < 1
        or any(not isinstance(binding.get(key), str) or not binding[key].strip() for key in (
            "task_id", "original_reservation_key", "creation_key", "worktree_id",
            "project", "path", "creation_nonce",
        ))
    ):
        raise TaskError("ordinary invocation binding types are invalid")
    actual = identity(info)
    if (
        reservation["agent_info"] is None
        and binding["expected_turn_index"] != (
            info.get("turn_count") if info.get("fact_kind") == "resumed" else 0
        )
    ):
        raise TaskError("ordinary invocation initial turn position is unproven")
    if (
        binding.get("task_id") != task.id
        or binding.get("admission_generation") != task.generation
        or binding.get("creation_key") != reservation["creation_key"]
        or binding.get("allocation_revision") != reservation["allocation_revision"]
        or binding.get("original_reservation_key")
        != (reservation["allocation_reservation_key"] or reservation["key"])
        or binding.get("worktree_id") != actual.worktree
        or binding.get("project") != actual.project or binding.get("path") != actual.path
        or invocation.get("expected_native_session_id") != actual.native
        or not isinstance(invocation.get("prompt"), str) or not invocation["prompt"]
        or not isinstance(binding.get("creation_nonce"), str) or not binding["creation_nonce"].strip()
    ):
        raise TaskError("ordinary invocation admission fence changed")
    scope = [task.id, task.generation, binding["original_reservation_key"],
             binding["creation_key"], binding["allocation_revision"]]
    expected = hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()
    if invocation.get("invocation_key") != expected:
        raise TaskError("ordinary invocation identity changed")
