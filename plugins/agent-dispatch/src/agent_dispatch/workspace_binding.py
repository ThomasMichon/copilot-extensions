"""Retain ground-owner workspace facts before any session acquisition."""

from __future__ import annotations

import json
import subprocess

from .client import DispatchError


def hold(message: str) -> None:
    from .spawn_factories import SpawnPreparationRetained

    raise SpawnPreparationRetained(message)


def allocation_project(reservation: dict, selected: str | None) -> str | None:
    """Recorded provenance wins over mutable routing; legacy reuse fails closed."""
    if not {"allocation_project", "worktree_path"} <= reservation.keys():
        hold("coordinator cannot retain workspace provenance; upgrade before allocation")
    if reservation.get("worktree"):
        recorded = reservation.get("allocation_project")
        if isinstance(recorded, str) and recorded and reservation.get("worktree_path"):
            return recorded
        if reservation.get("worktree_ownership") != "targeted" or reservation.get("attempt") != 1:
            hold("carried workspace has no allocation provenance; retained for repair")
    return reservation.get("allocation_project") or selected

def allocation_provenance(task_id: str, reservation: dict) -> tuple[str, str, int]:
    """Original allocation provenance is independent of the current task generation."""
    original = reservation.get("allocation_reservation_key")
    if original is None:
        return task_id, str(reservation["key"]), int(reservation["attempt"])
    if not isinstance(original, str) or not original.startswith("dispatch-task:"):
        hold("original allocation reservation provenance is invalid")
    original_task, separator, attempt = original[len("dispatch-task:"):].rpartition(":")
    if (
        not separator or not original_task or len(attempt) > 19
        or not attempt.isascii() or not attempt.isdecimal()
        or not 1 <= int(attempt) <= 2**63 - 1
    ):
        hold("original allocation task/attempt provenance is invalid")
    return original_task, original, int(attempt)


def bind_allocation(client, reservation: dict, project: str | None) -> None:
    """No lost owner response may restore mutable routing as allocation authority."""
    if not project or not reservation.get("creation_key") or not hasattr(client, "record_spawn_allocation"):
        hold("coordinator cannot bind replay-safe allocation intent; upgrade before allocation")
    try:
        bound = client.record_spawn_allocation(reservation["key"], project)
    except DispatchError as exc:
        hold(f"allocation target could not be recorded; preserve the original request: {exc}")
    if (
        not isinstance(bound, dict) or bound.get("allocation_project") != project
        or bound.get("creation_key") != reservation["creation_key"]
    ):
        hold("coordinator did not retain the exact allocation intent")
    reservation["allocation_project"] = project


def record_prepared(
    client, reservation: dict, prepared: dict, *, driver: str,
    creating_host: str | None = None,
) -> None:
    """Persist even incomplete owner results, then refuse unsafe acquisition."""
    worktree = str(prepared["worktree"])
    ownership = str(prepared.get("ownership") or "unknown")
    facts = {
        "worktree": worktree, "worktree_ownership": ownership,
        "allocation_project": prepared.get("project"), "worktree_path": prepared.get("path"),
    }
    conflicting_project = (
        prepared.get("requested_project")
        and prepared["requested_project"] != facts["allocation_project"]
    )
    if conflicting_project:
        facts["allocation_project"] = None
    if any(reservation.get(key) != value for key, value in facts.items()):
        try:
            recorded = client.record_spawn_worktree(
                reservation["key"], worktree, ownership=ownership,
                allocation_project=facts["allocation_project"], worktree_path=facts["worktree_path"],
                creating_host=creating_host if ownership == "created" else None, driver=driver,
            )
        except DispatchError as exc:
            hold(f"workspace {worktree} could not be recorded; retained for repair: {exc}")
        if not isinstance(recorded, dict) or any(recorded.get(key) != value for key, value in facts.items()):
            hold(f"coordinator did not retain exact workspace {worktree} provenance")
    project, path = facts["allocation_project"], facts["worktree_path"]
    if conflicting_project:
        hold(f"workspace {worktree} owner returned conflicting execution project; retained for repair")
    if not isinstance(project, str) or not project or not isinstance(path, str) or not path:
        hold(f"workspace {worktree} owner returned incomplete identity; retained for repair")
    if reservation.get("worktree_path") and reservation["worktree_path"] != path:
        hold(f"workspace {worktree} owner returned conflicting path; retained for repair")


def retain_pending_creation(result: subprocess.CompletedProcess) -> None:
    """Keep the ground owner's typed pending outcome outside the failure budget."""
    if result.returncode != 3:
        return
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return
    creation = data.get("creation") if isinstance(data, dict) else None
    if isinstance(creation, dict) and creation.get("version") == 1 and creation.get("status") == "pending":
        from .spawn_factories import SpawnPreparationPending

        raise SpawnPreparationPending(data.get("error") or "ground creation is pending; replay the same key")

def defer_pending_creation(client, key: str, error: RuntimeError) -> bool:
    from .spawn_factories import SpawnPreparationPending
    import logging

    if not isinstance(error, SpawnPreparationPending):
        return False
    try:
        client.defer_spawn(key, detail=str(error))
    except DispatchError:
        logging.getLogger(__name__).exception("pending allocation bookkeeping failed; reservation %s retained", key)
        return False
    return True


def parse_created_workspace(result: subprocess.CompletedProcess, *, creation_key: str | None) -> dict:
    from .embody import EmbodyUnavailable

    if result.returncode != 0:
        if creation_key is not None:
            retain_pending_creation(result)
        detail = (result.stderr or result.stdout or "").strip()
        raise EmbodyUnavailable(detail or f"agent-worktrees create exited {result.returncode}")
    handle = parse_handle(result)
    try:
        data = json.loads(result.stdout or "{}")
    except (ValueError, TypeError):
        data = {}
    worktree = data.get("worktree") if isinstance(data, dict) else {}
    if isinstance(worktree, dict):
        handle["worktree"] = handle.get("worktree") or worktree.get("id")
        handle["path"] = worktree.get("path")
        handle["project"] = worktree.get("repo")
    if creation_key is not None:
        creation = data.get("creation") if isinstance(data, dict) else None
        if not isinstance(creation, dict) or creation.get("version") != 1 or creation.get("status") != "complete":
            hold("ground owner did not prove replay-safe creation; retain the original request")
    if not handle.get("worktree"):
        raise EmbodyUnavailable("agent-worktrees create returned no worktree id")
    return handle


def parse_handle(result: subprocess.CompletedProcess) -> dict[str, str | None]:
    """Best-effort extract the session/worktree handle from ``embody --json``.

    Returns ``{"session": ..., "worktree": ...}`` (values may be ``None``). Used
    to record a spawn reservation's handle so a supervisor restart can reconcile.
    """
    handle: dict[str, str | None] = {"session": None, "worktree": None}
    try:
        data = json.loads(result.stdout or "{}")
    except (ValueError, TypeError):
        return handle
    if not isinstance(data, dict):
        return handle
    launch = data.get("launch") if isinstance(data.get("launch"), dict) else {}
    worktree_obj = data.get("worktree") if isinstance(data.get("worktree"), dict) else {}
    handle["worktree"] = (
        data.get("worktree_id")
        or worktree_obj.get("id")
        or (data.get("worktree") if isinstance(data.get("worktree"), str) else None)
        or launch.get("worktree_id")
    )
    handle["session"] = data.get("session_id") or data.get("session") or launch.get("session")
    return handle
