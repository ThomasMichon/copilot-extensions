"""Bounded candidate endpoint selection before cutover custody changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .conditional import ConditionalCutover
    from .cutover import CutoverOrchestrator
    from .cutover_result import CutoverResult
    from .routing import Endpoint


def candidate_port(
    owner: CutoverOrchestrator, old: Endpoint | None,
    conditional: ConditionalCutover | None, result: CutoverResult,
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
