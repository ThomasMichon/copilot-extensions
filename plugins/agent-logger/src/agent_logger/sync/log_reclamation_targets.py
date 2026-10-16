"""Resolve the entire explicitly selected readback set before inspecting revisions."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agent_logger.sync.log_archive_readback import ProcessLogReadback
from agent_logger.sync.log_reclamation_policy import LogReclamationSettings
from agent_logger.sync.targets import build_target

if TYPE_CHECKING:
    from agent_logger.config import Config


def resolve_required_log_targets(
    cfg: Config, settings: LogReclamationSettings, machine: str,
) -> tuple[ProcessLogReadback, ...]:
    """Resolve all destinations once; never omit an unsupported or aliased target.

    These pass-local observations do not publish anything or authorize retirement.
    Every destination is pinned again by its read-only verification operation.
    """
    if not settings.enabled:
        return ()
    names = settings.required_targets
    if not names or cfg.sync_target not in names or len(set(names)) != len(names):
        raise ValueError("reclamation requires distinct targets including the active target")
    if (
        not machine or machine in {".", ".."}
        or any(char in machine for char in "/\\:\0")
    ):
        raise ValueError("reclamation requires a local machine name, not a namespace")
    source = cfg.process_logs_source.resolve(strict=True)
    resolved: list[ProcessLogReadback] = []
    identities: set[str] = set()
    for name in names:
        target = build_target(name, cfg.target_options(name))
        readback = target.process_log_readback(machine)
        if readback.directory is None:
            raise ValueError(f"required target {name!r} does not support process-log readback")
        destination = readback.directory.resolve(strict=False)
        if source.is_relative_to(destination) or destination.is_relative_to(source):
            raise ValueError(f"required target {name!r} aliases the process-log source")
        identity = os.path.normcase(str(destination))
        if identity in identities:
            raise ValueError("required process-log targets resolve to the same destination")
        identities.add(identity)
        resolved.append(readback)
    return tuple(resolved)
