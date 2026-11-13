"""Negotiate the optional original-cohort carrier with the selected Worktrees."""

from __future__ import annotations

from functools import lru_cache
import json
import logging
from pathlib import Path
import subprocess

from .procutil import OutputLimitExceeded, agent_worktrees_environment, run_background_capture

_LOG = logging.getLogger(__name__)
_SCHEMA = "dispatch-assignment-cohort"


def _carrier_json(cohort: object) -> str | None:
    """Observe the v1 receiver's bounded carrier without importing another plugin."""
    if (
        not isinstance(cohort, dict) or set(cohort) != {"schema", "version", "emitter", "pool"}
        or cohort["schema"] != _SCHEMA or type(cohort["version"]) is not int or cohort["version"] != 1
    ):
        return None
    for field, strings, counters in (
        ("emitter", {"repo", "source", "request_id", "producer_id", "task_id"}, {"generation"}),
        ("pool", {"task_id", "registration_id", "machine", "env", "reservation_key"},
         {"task_generation", "catalog_revision"}),
    ):
        reference = cohort[field]
        if reference is None:
            continue
        if not isinstance(reference, dict) or set(reference) != strings | counters or any(
            not isinstance(reference[key], str) or not reference[key].strip()
            or len(reference[key]) > 512 or "\0" in reference[key] for key in strings
        ) or any(
            type(reference[key]) is not int or not 0 <= reference[key] <= 2**63 - 1 for key in counters
        ):
            return None
    encoded = json.dumps(cohort, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return encoded if len(encoded.encode("utf-8")) <= 16 * 1024 else None


@lru_cache(maxsize=32)
def _supports_cohort(prefix: tuple[str, ...]) -> bool:
    # A mutable shim can select a different runtime on its next invocation.
    # Only the resolver's direct interpreter form can retain this observation.
    if len(prefix) < 3 or prefix[-2:] != ("-m", "agent_worktrees") or not Path(prefix[0]).is_absolute():
        return False
    try:
        result = run_background_capture(
            [*prefix, "worktree-allocation", "capabilities", "--json"],
            timeout=5, max_output=64 * 1024, env=agent_worktrees_environment(),
        )
        if result is None or result.returncode != 0:
            return False
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, OutputLimitExceeded, ValueError, TypeError):
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
    if _carrier_json(cohort) is not None and _supports_cohort(tuple(prefix)):
        return "cohort-v1"
    _LOG.warning("Original Dispatch cohort is unrepresentable or support is unknown; retaining the legacy unattributed baseline")
    return "baseline"


def creation_arguments(prefix: list[str], cohort: dict | None, transport: str = "baseline") -> list[str]:
    """Replay the retained form, never renegotiate immutable creation facts."""
    if transport == "baseline":
        return []
    encoded = _carrier_json(cohort)
    if transport == "cohort-v1" and encoded is not None and _supports_cohort(tuple(prefix)):
        return ["--dispatch-cohort", encoded]
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
