"""Pure locus availability policy with caller-supplied machine equivalence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .related import Locus

MachineMatch = Callable[[str, str], bool]


def venue_machines(venue: dict[str, Any]) -> list[str]:
    """Machine keys a venue is restricted to (empty list = unrestricted)."""
    raw = venue.get("machines") if isinstance(venue, dict) else None
    if isinstance(raw, list):
        return [str(m).strip() for m in raw if str(m).strip()]
    if raw:
        return [str(raw).strip()]
    return []


def locus_here(
    locus: Locus,
    current_machine: str,
    *,
    preferred: tuple[str, str],
    match: MachineMatch,
    venue_available: Callable[[dict[str, Any], str], bool],
) -> tuple[str, bool, bool]:
    """Return ``(kind, expects_local_checkout, available_here)`` for a locus.

    ``expects_local_checkout`` is True only for the ``local`` / ``machine`` kinds
    (a CodeSpace/container is provisioned from its venue, not the machine's local
    registry, so a missing registry entry there is not a defect).
    """
    kind, target = preferred
    kind = kind or "local"
    if kind == "codespace":
        return kind, False, True
    if kind == "container":
        return kind, False, venue_available(locus.container, current_machine)
    if kind == "machine":
        return kind, True, match(target, current_machine)
    ms = locus.machines
    here = (not ms) or any(match(m, current_machine) for m in ms)
    return kind, True, here
