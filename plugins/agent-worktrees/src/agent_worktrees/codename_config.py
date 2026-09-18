"""Config for the per-repo codename generator (effort:
``pr-attribution-codenames``, issue #2838).

Kept as its own small module rather than growing ``config.py`` directly --
that module is already near its module-size ceiling (see
``tools/module-size-baseline.json``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .codename import DEFAULT_HOOK_TIMEOUT_SECONDS, is_valid_hook_timeout


@dataclass(frozen=True)
class CodenameConfig:
    """Per-repo codename-generation settings.

    ``hook_command`` is empty by default -- the built-in, organization-neutral
    generator (:mod:`agent_worktrees.codename`) is used. Setting it opts into
    an external generator hook, which is an explicit trust decision: syntax
    validation on the hook's output cannot prove it is semantically
    non-identifying (see :mod:`agent_worktrees.codename`'s module docstring).
    """

    hook_command: str = ""
    hook_timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS


def parse_codename(raw: Any) -> CodenameConfig:
    """Parse the optional ``codename:`` block of a repo config.

    Unknown or missing values fall back to :class:`CodenameConfig` defaults
    (built-in generator, no hook). A malformed, non-finite, non-positive, or
    boolean ``hook_timeout_seconds`` also falls back to the default rather
    than reaching :func:`agent_worktrees.codename.generate_via_hook` --
    ``float("nan")``/``float("inf")`` parse without error but crash
    ``subprocess`` timeout handling downstream, so this is rejected here too
    (defense in depth alongside the runtime check in ``generate_via_hook``).
    """
    if not isinstance(raw, dict):
        return CodenameConfig()
    timeout = raw.get("hook_timeout_seconds", DEFAULT_HOOK_TIMEOUT_SECONDS)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_HOOK_TIMEOUT_SECONDS
    if not is_valid_hook_timeout(timeout):
        timeout = DEFAULT_HOOK_TIMEOUT_SECONDS
    return CodenameConfig(
        hook_command=str(raw.get("hook_command", "")).strip(),
        hook_timeout_seconds=timeout,
    )
