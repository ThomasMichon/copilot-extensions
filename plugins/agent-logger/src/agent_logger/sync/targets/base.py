"""Target abstraction for session-sync.

A *target* is a destination for raw Copilot session data. Every target
takes a local source tree and publishes it under a per-machine subpath,
so any consumer (a local orchestrator, a fleet hub, or a bespoke service)
sees the same ``{machine}/...`` layout regardless of transport.

Concrete targets:

- :class:`~agent_logger.sync.targets.filesystem.LocalTarget` -- a dotfolder
  under ``$HOME`` (default, zero-dependency).
- :class:`~agent_logger.sync.targets.filesystem.OneDriveTarget` -- a
  subfolder under the resolved OneDrive root (fleet hub without a NAS).
- :class:`~agent_logger.sync.targets.ssh.SshTarget` -- rsync/ssh to an
  arbitrary ``user@host:path`` (optionally via a jump host).
- :class:`~agent_logger.sync.targets.ingest.IngestTarget` -- an rsync-daemon
  sink with an optional HTTP notify (the shape a processing service exposes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PushResult:
    """Outcome of a :meth:`Target.push`."""

    ok: bool
    detail: str = ""
    file_count: int = 0
    byte_count: int = 0


def rsync_session_filters(include_sessions: set[str] | None) -> list[str]:
    """Build rsync include/exclude args restricting to allowed sessions.

    With ``None`` there is no restriction. Otherwise only the named
    ``session-state/<id>`` trees are transferred; everything else (including
    a global session-store.db) is excluded so other repos' sessions never
    leak to the destination.
    """
    if include_sessions is None:
        return []
    filters = ["--include=session-state/"]
    for sid in sorted(include_sessions):
        filters.append(f"--include=session-state/{sid}/")
        filters.append(f"--include=session-state/{sid}/***")
    filters.append("--exclude=*")
    return filters


@dataclass
class DoctorResult:
    """Outcome of a :meth:`Target.doctor` readiness check."""

    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))
        if not ok:
            self.ok = False


class Target(ABC):
    """Base class for all sync targets."""

    #: Registry name used in config (``sync.target``).
    name: str = "base"

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}

    @abstractmethod
    def push(
        self, source: Path, machine: str, include_sessions: set[str] | None = None
    ) -> PushResult:
        """Publish *source* under the target's ``{machine}/`` subpath.

        ``include_sessions``, when not ``None``, restricts the transfer to
        the named ``session-state/<id>`` directories (repo-allowlist
        filtering); everything else under *source* is skipped to avoid
        leaking sessions from other repos.
        """

    @abstractmethod
    def doctor(self) -> DoctorResult:
        """Check that the target is reachable/usable without transferring."""

    def prune(self, machine: str, retention_days: int | None) -> int:
        """Remove session data older than *retention_days*.

        Returns the number of session directories removed. ``None`` or a
        non-positive value means "retain everything" and is a no-op.
        Targets that cannot prune (e.g. push-only remotes) return ``0``.
        """
        return 0

    @abstractmethod
    def describe(self) -> str:
        """Return a short human-readable description of the destination."""
