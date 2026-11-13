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


def creation_arguments(prefix: list[str], cohort: dict | None) -> list[str]:
    """Keep unknown peers on the legacy baseline without probing an effect."""
    if cohort is None:
        return []
    if (
        isinstance(cohort, dict) and cohort.get("schema") == _SCHEMA
        and type(cohort.get("version")) is int and cohort["version"] == 1
        and _supports_cohort(tuple(prefix))
    ):
        return ["--dispatch-cohort", json.dumps(cohort, sort_keys=True, separators=(",", ":"))]
    _LOG.warning(
        "Original Dispatch cohort is not transported: selected Worktrees support is unknown "
        "or incompatible; creation retains the legacy unattributed baseline",
    )
    return []
