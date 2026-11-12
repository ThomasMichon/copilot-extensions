"""Bounded candidate endpoint selection before cutover custody changes."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .conditional import PreparedCutover
    from .cutover import CutoverOrchestrator
    from .cutover_result import CutoverResult
    from .routing import Endpoint


def raw_active(owner: CutoverOrchestrator) -> dict | None:
    table = owner.routing.read_table(owner.config_dir)
    active = table.get("active") if isinstance(table, dict) else None
    return active if isinstance(active, dict) else None


def refuse_current_old(owner: CutoverOrchestrator, result: CutoverResult) -> bool:
    if owner.refuse_old is None:
        return False
    refusal = owner.refuse_old(owner._raw_active())
    if not refusal:
        return False
    result.error = refusal
    result.steps.append(f"refused: {refusal}")
    return True


def validate_routing_lock_timeout(value: float | None) -> float | None:
    if value is not None:
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("routing lock timeout must be finite and nonnegative")
    return value


def routing_deadline(conditional: PreparedCutover | None, timeout: float | None) -> float | None:
    if conditional is not None:
        return conditional.routing_deadline()
    return time.monotonic() + timeout if timeout is not None else None


def routing_lock_kwargs(timeout: float | None) -> dict:
    return {"lock_deadline": time.monotonic() + timeout} if timeout is not None else {}


def candidate_port(
    owner: CutoverOrchestrator, old: Endpoint | None,
    conditional: PreparedCutover | None, result: CutoverResult,
) -> int | None:
    port = owner.pick_free_port()
    if conditional and port == conditional.old.port:
        result.error = "candidate port must differ from predecessor"
    elif owner.retire_failed_predecessor is not None and old is not None:
        for _ in range(7):
            if port != old.port:
                break
            port = owner.pick_free_port()
        if port == old.port:
            result.error = "candidate port must differ from predecessor after 8 selections"
    result.new_port = port
    if result.error:
        result.steps.append(f"refused: {result.error}")
        return None
    return port
