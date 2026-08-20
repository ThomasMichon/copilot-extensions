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

from agent_procutil import no_window_kwargs

# On Windows, child processes (rsync, ssh) launched from a windowless parent --
# e.g. pythonw.exe under a Scheduled Task -- each allocate a fresh console
# window that flashes on screen during the sync flow. no_window_kwargs()
# suppresses that allocation. No-op on POSIX, where the flag does not exist and
# no console is spawned. Spread into every external-tool subprocess call as
# ``**NO_WINDOW_KWARGS``.
NO_WINDOW_KWARGS: dict = no_window_kwargs()


@dataclass
class PushResult:
    """Outcome of a :meth:`Target.push`."""

    ok: bool
    detail: str = ""
    file_count: int = 0
    byte_count: int = 0


def rsync_session_filters(include_sessions: set[str] | None) -> list[str]:
    """Build rsync include/exclude args restricting the transfer to session data.

    session-sync archives session data only -- never the rest of the source
    (``~/.copilot``: binaries, installed plugins, OAuth/credential state,
    encryption keys, settings).

    With ``None`` (no repo allowlist) the whole ``session-state`` tree plus the
    global ``session-store.db`` index is transferred and nothing else. With an
    allowlist, only the named ``session-state/<id>`` trees are transferred and
    the global session-store.db is excluded so other repos' sessions never leak
    to the destination.
    """
    if include_sessions is None:
        return [
            "--include=session-state/",
            "--include=session-state/***",
            "--include=session-store.db",
            "--include=session-store.db-wal",
            "--include=session-store.db-shm",
            "--exclude=*",
        ]
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

        Only session data is published -- the ``session-state`` tree plus the
        global ``session-store.db`` index -- never the rest of the source
        (``~/.copilot``: binaries, installed plugins, OAuth/credential state,
        encryption keys, settings).

        ``include_sessions``, when not ``None``, further restricts the transfer
        to the named ``session-state/<id>`` directories (repo-allowlist
        filtering) and drops the global session-store.db, so sessions from
        other repos never leak.
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

    def push_archives(self, archive_root: Path, machine: str) -> PushResult:
        """Publish the compressed archive store under ``{machine}/archived/``.

        The second pair of the two-pair sync model: the on-device archive store
        (compacted ``<id>.tar.gz`` bundles + uncompressed sidecars) is copied to
        a sibling of the uncompressed ``session-state`` tree. Targets that
        cannot do this return an ok no-op.
        """
        return PushResult(ok=True, detail="archive sync unsupported by target")

    def reconcile_hub(self, machine: str, *, dry_run: bool = False) -> int:
        """Drop uncompressed hub sessions whose archive has landed.

        For each ``{machine}/archived/<id>`` archive present (and verified),
        remove the redundant uncompressed ``{machine}/session-state/<id>/``
        directory. Returns the number reclaimed (or, with ``dry_run``, the
        number that would be). No-op for non-hub targets.
        """
        return 0

    def compact_backlog(
        self,
        machine: str,
        min_age_days: int,
        codec: str,
        *,
        tracked_paths: set[str] | None = None,
        dry_run: bool = False,
    ) -> int:
        """Compact cold hub-only sessions in place under ``{machine}/archived/``.

        For historical sessions that only ever lived on the hub (already rotated
        off every device, so no local archive is pushed to cover them). Returns
        the number archived (or, with ``dry_run``, the number eligible).
        ``tracked_paths`` protects hub sessions whose worktree is still tracked.
        No-op for non-hub targets.
        """
        return 0

    @abstractmethod
    def describe(self) -> str:
        """Return a short human-readable description of the destination."""
