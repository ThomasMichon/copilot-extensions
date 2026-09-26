"""Per-session Copilot model flags for agent-codespaces launches.

The resolver itself (the caller's ``~/.copilot/settings.json`` model, effort,
and context tier, with env overrides and an opt-out) lives in the shared
``venue_copilot.models`` library so agent-containers and agent-ssh detached
sessions mirror it identically; this module re-exports it and adds the
``copilot --acp`` shell-suffix form used by the stdio ACP launcher and
agent-bridge's CodeSpace Session-Host dispatch (the ``acp-model-flags`` seam).
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

from venue_copilot.models import (  # noqa: F401 -- re-exported public seam
    MODEL_FLAGS,
    model_copilot_args,
    normalized_config as _normalized_config,
    resolve_model_config,
)

log = logging.getLogger("agent-codespaces")


def build_model_flags(cfg: dict[str, Any] | None = None) -> str:
    """Build a shell-safe suffix for ``copilot --acp`` model flags.

    When ``cfg`` is ``None``, resolves the caller configuration with
    :func:`resolve_model_config`; otherwise the supplied mapping is normalized
    directly. Present keys are emitted as ``--model``, ``--reasoning-effort``,
    and ``--context`` with values quoted via :func:`shlex.quote`. The returned
    suffix includes a single leading space, or ``""`` when no flags apply.
    """
    resolved = resolve_model_config() if cfg is None else _normalized_config(cfg)
    parts: list[str] = []
    for key, flag in MODEL_FLAGS:
        value = resolved.get(key)
        if value:
            parts.extend([flag, shlex.quote(value)])
    if not parts:
        log.debug("No model flags to propagate to ACP launch")
        return ""
    suffix = " " + " ".join(parts)
    log.info("Propagating model flags to ACP launch: %s", suffix.strip())
    return suffix