"""Constructor compatibility and recovery-policy options for one cutover."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cutover import CutoverOrchestrator


def writer_reconciliation(
    owner: CutoverOrchestrator, requested: bool | None,
) -> bool:
    if owner.preserve_raw_active and owner.refuse_old is None:
        raise ValueError("raw active preservation requires a publication refusal guard")
    if owner.expected_old is not None and owner.refuse_old is None:
        raise ValueError("an expected predecessor also requires a publication refusal guard")
    if owner.on_published is not None and owner.expected_old is None:
        raise ValueError("publication notification requires an observed predecessor")
    if owner.self_retirement and (
        owner.activate_successor is None or owner.drain_before_publish
    ):
        raise ValueError("self-retirement requires successor activation without predecessor drain")
    if owner.activate_successor is not None and not owner.self_retirement:
        raise ValueError("successor activation requires self-retirement")
    if requested is not None and type(requested) is not bool:
        raise ValueError("writer reconciliation must be a boolean")
    if requested is False and (owner.drain_before_publish or owner.drain_predecessor):
        raise ValueError("writer reconciliation opt-out requires a no-drain cutover")
    return (
        owner.drain_before_publish or owner.expected_old is not None
        if requested is None else requested
    )
