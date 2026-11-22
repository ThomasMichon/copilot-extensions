"""Normalize redundant context only after registry-aware destination selection."""

from __future__ import annotations

from machine_transport import resolve_machine_selection

from . import machine_roster


def normalize(project: str, machine: str, environment: str | None) -> tuple[str, str | None]:
    if not environment:
        return machine, environment
    try:
        entries = machine_roster.selected_entries(project)
    except (OSError, ValueError):
        return machine, environment
    selected = resolve_machine_selection(
        entries, machine, environment, allow_physical_context=True,
    )
    try:
        implied = resolve_machine_selection(entries, machine, allow_physical_context=True)
    except ValueError:
        return machine, environment
    if (
        selected.entry is not None and implied.entry is not None
        and selected.entry.key == implied.entry.key
        and selected.target.environment == implied.target.environment
    ):
        return machine, None
    return machine, environment
