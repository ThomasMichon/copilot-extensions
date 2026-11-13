"""Negotiate the optional original-cohort carrier with the selected Worktrees."""

from __future__ import annotations

from functools import lru_cache
import json
import logging
from pathlib import Path
import subprocess

from .procutil import agent_worktrees_environment, no_window_kwargs

_LOG = logging.getLogger(__name__)
_SCHEMA = "dispatch-assignment-cohort"


@lru_cache(maxsize=32)
def _supports_cohort(prefix: tuple[str, ...]) -> bool:
    # A mutable shim can select a different runtime on its next invocation.
    # Only the resolver's direct interpreter form can retain this observation.
    if len(prefix) < 3 or prefix[-2:] != ("-m", "agent_worktrees") or not Path(prefix[0]).is_absolute():
        return False
    try:
        result = subprocess.run(
            [*prefix, "worktree-allocation", "capabilities", "--json"],
            check=False, capture_output=True, text=True, timeout=5,
            env=agent_worktrees_environment(), **no_window_kwargs(),
        )
        if result.returncode != 0:
            return False
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return False
    if not isinstance(value, dict):
        return False
    cohort = value.get("dispatch_cohort")
    return bool(
        value.get("schema") == "copilot-extensions.worktree-allocation"
        and type(value.get("version")) is int and value["version"] == 1
        and value.get("role") == "origin"
        and isinstance(cohort, dict) and cohort.get("schema") == _SCHEMA
        and type(cohort.get("version")) is int and cohort["version"] == 1
    )


def selection(prefix: list[str], cohort: dict | None) -> str:
    """Choose an optional wire form once, before the allocation binding commits."""
    if cohort is None:
        return "baseline"
    if (
        isinstance(cohort, dict) and cohort.get("schema") == _SCHEMA
        and type(cohort.get("version")) is int and cohort["version"] == 1
        and _supports_cohort(tuple(prefix))
    ):
        return "cohort-v1"
    _LOG.warning("Original Dispatch cohort support is unknown; retaining the legacy unattributed baseline")
    return "baseline"


def creation_arguments(prefix: list[str], cohort: dict | None, transport: str = "baseline") -> list[str]:
    """Replay the retained form, never renegotiate immutable creation facts."""
    if transport == "baseline":
        return []
    if (
        transport == "cohort-v1" and isinstance(cohort, dict) and cohort.get("schema") == _SCHEMA
        and type(cohort.get("version")) is int and cohort["version"] == 1
        and _supports_cohort(tuple(prefix))
    ):
        return ["--dispatch-cohort", json.dumps(cohort, sort_keys=True, separators=(",", ":"))]
    from .workspace_binding import hold

    hold("original cohort-bearing creation requires a capable Worktrees runtime; preserve its wire form")


def prepare_creation(client, reservation: dict, project: str | None) -> list[str] | None:
    """Persist selection through the existing allocation owner before effects."""
    from .embody import _agent_worktrees_launch_prefix
    from .workspace_binding import bind_allocation

    prefix = _agent_worktrees_launch_prefix()
    transport = None
    if "allocation_cohort_transport" in reservation:
        transport = reservation["allocation_cohort_transport"]
        if transport is None:
            transport = selection(prefix or [], reservation.get("allocation_cohort"))
        creation_arguments(prefix or [], reservation.get("allocation_cohort"), transport)
    bind_allocation(client, reservation, project, cohort_transport=transport)
    return prefix
