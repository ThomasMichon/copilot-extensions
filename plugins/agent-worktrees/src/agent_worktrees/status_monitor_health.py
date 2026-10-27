"""Compose the elected resident's health without exposing private work items."""

from __future__ import annotations

import os
import sys
from typing import Protocol

from .runtime_identity import PROCESS_GENERATION, PROCESS_VERSION


class HealthSurface(Protocol):
    def health(self) -> dict: ...


def response(
    *, worktrees: HealthSurface, receipts: HealthSurface,
    repositories: HealthSurface, allocations: HealthSurface,
    admission_closed: bool, published: bool, shutdown: bool,
) -> dict:
    roster = worktrees.health()
    receipt = receipts.health()
    repository = repositories.health()
    allocation = allocations.health()
    roster["capabilities"] = [
        *roster["capabilities"], *receipt["capabilities"],
        *repository["capabilities"], *allocation["capabilities"],
    ]
    if shutdown:
        roster["worktrees"]["status"] = "shutdown"
    ready = roster["worktrees"]["status"] in {"ready", "unready"}
    return {
        "status": "draining" if admission_closed else ("ready" if ready else "unavailable"),
        "published": published, "pid": os.getpid(), **roster,
        "promptReceipt": receipt["promptReceipt"],
        "repositories": repository["repositories"],
        "allocationLinker": allocation["allocationLinker"],
        "runtime_generation": PROCESS_GENERATION, "version": PROCESS_VERSION,
        "runtime_slot": os.path.basename(os.path.realpath(sys.prefix)),
    }
