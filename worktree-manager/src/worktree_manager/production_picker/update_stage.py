"""Lazy process-boundary reader for the engine's staged-update indicator.

This remains a lazy seam because the picker imports the module on startup but
only ever *calls* :func:`indicator_state` after first paint. The call now goes
through the pinned CLI contract (``stage-update --indicator-state --json``)
instead of importing ``agent_worktrees.update_stage`` in-process. An older
engine lacking that additive flag degrades this purely cosmetic glyph to
``"idle"`` rather than failing Picker startup.
"""
from __future__ import annotations

from . import context as picker_context
from . import engine_group_a


def indicator_state() -> str:
    try:
        return engine_group_a.update_stage_indicator_state(picker_context.project())
    except engine_group_a.engine_client.EngineFeatureUnavailable:
        return "idle"
