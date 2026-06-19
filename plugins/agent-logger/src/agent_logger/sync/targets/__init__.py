"""Sync target registry."""

from __future__ import annotations

from agent_logger.sync.targets.base import DoctorResult, PushResult, Target
from agent_logger.sync.targets.filesystem import LocalTarget, OneDriveTarget
from agent_logger.sync.targets.ingest import IngestTarget
from agent_logger.sync.targets.ssh import SshTarget, SshTunnelTarget

_REGISTRY: dict[str, type[Target]] = {
    LocalTarget.name: LocalTarget,
    OneDriveTarget.name: OneDriveTarget,
    SshTarget.name: SshTarget,
    SshTunnelTarget.name: SshTunnelTarget,
    IngestTarget.name: IngestTarget,
}

TARGET_NAMES = tuple(_REGISTRY)

__all__ = [
    "TARGET_NAMES",
    "DoctorResult",
    "PushResult",
    "Target",
    "build_target",
]


def build_target(name: str, options: dict | None = None) -> Target:
    """Instantiate the target registered under *name*.

    Raises :class:`ValueError` for an unknown target name.
    """
    try:
        cls = _REGISTRY[name]
    except KeyError:
        valid = ", ".join(TARGET_NAMES)
        raise ValueError(f"unknown sync target {name!r}; valid: {valid}") from None
    return cls(options)
