"""Compose the elected resident's health without exposing private work items."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from zdd.cutover import CutoverError

from .runtime_identity import PROCESS_GENERATION, PROCESS_VERSION
from .runtime_monitor_retirements import CAPABILITY


class HealthSurface(Protocol):
    def health(self) -> dict: ...


def check_successor(
    host: str, port: int, client_factory: Callable[[str], HealthSurface], *,
    require_async_retirement: bool = False,
) -> bool:
    try:
        status = client_factory(f"http://{host}:{port}").health()
        if status.get("status") != "ready":
            return False
        if require_async_retirement and CAPABILITY not in status.get("capabilities", []):
            raise CutoverError("Incoming monitor does not support asynchronous predecessor retirement")
        roster = status.get("worktrees")
        if not isinstance(roster, dict):
            return True
        return roster.get("status") == "ready" or (
            status.get("published") is False and roster.get("status") == "unready"
        )
    except CutoverError:
        raise
    except Exception:
        return False


def response(
    *, worktrees: HealthSurface, receipts: HealthSurface,
    repositories: HealthSurface, allocations: HealthSurface,
    admission_closed: bool, published: bool, shutdown: bool,
    retirements: dict | None = None,
) -> dict:
    roster = worktrees.health()
    receipt = receipts.health()
    repository = repositories.health()
    allocation = allocations.health()
    roster["capabilities"] = [
        *roster["capabilities"], *receipt["capabilities"],
        *repository["capabilities"], *allocation["capabilities"],
        CAPABILITY,
    ]
    if shutdown:
        roster["worktrees"]["status"] = "shutdown"
    ready = roster["worktrees"]["status"] in {"ready", "unready"}
    environment = Path(sys.prefix).resolve()
    slot = environment.parent if environment.name == ".venv" else environment
    return {
        "status": "draining" if admission_closed else ("ready" if ready else "unavailable"),
        "published": published, "pid": os.getpid(), **roster,
        "promptReceipt": receipt["promptReceipt"],
        "repositories": repository["repositories"],
        "allocationLinker": allocation["allocationLinker"],
        "runtime_generation": PROCESS_GENERATION, "version": PROCESS_VERSION,
        "runtime_slot": slot.name,
        "predecessorRetirement": retirements,
    }
