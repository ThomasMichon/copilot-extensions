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

import platform
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from agent_procutil import no_window_kwargs

from agent_logger.sync.detritus import rsync_exclude

# On Windows, child processes (rsync, ssh) launched from a windowless parent --
# e.g. pythonw.exe under a Scheduled Task -- each allocate a fresh console
# window that flashes on screen during the sync flow. no_window_kwargs()
# suppresses that allocation. No-op on POSIX, where the flag does not exist and
# no console is spawned. Spread into every external-tool subprocess call as
# ``**NO_WINDOW_KWARGS``.
NO_WINDOW_KWARGS: dict = no_window_kwargs()

_IS_WINDOWS = platform.system() == "Windows"
_WSL_PROBE_TIMEOUT = 15


def wsl_rsync_available(*, require_ssh: bool = True) -> bool:
    """Whether a usable WSL distro with the required tools is reachable.

    Windows has no native rsync distribution; the practical options are an
    MSYS2/Cygwin-runtime ``rsync.exe``, or running rsync inside WSL. WSL is
    strongly preferred when present: rsync and ssh both run in the *same*
    Linux runtime there, sidestepping two distinct MSYS2/Cygwin-class bugs --
    a cross-runtime ``-e ssh`` child corrupting the rsync protocol handshake,
    and the rsync argument parser misreading a bare ``C:\\...`` local source
    path as a ``host:path`` remote spec. Always ``False`` on POSIX (nothing
    to prefer over the system rsync already on ``PATH``).

    ``require_ssh`` gates whether ``ssh`` must also be present: the ``ssh``/
    ``ssh-tunnel`` targets need it (rsync shells out to it via ``-e``), but
    ``ingest`` speaks rsync's own daemon protocol directly and never invokes
    ssh at all -- requiring it there would reject a WSL distro that has rsync
    but happens to lack an ssh client.
    """
    if not _IS_WINDOWS:
        return False
    if shutil.which("wsl.exe") is None:
        return False
    check = "command -v rsync" + (" && command -v ssh" if require_ssh else "")
    try:
        proc = subprocess.run(
            ["wsl.exe", "--", "sh", "-c", check],
            capture_output=True,
            timeout=_WSL_PROBE_TIMEOUT,
            check=False,
            **NO_WINDOW_KWARGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def wsl_posix_path(path: Path) -> str | None:
    """Convert a Windows path to its WSL POSIX form via the authoritative ``wslpath``.

    Returns ``None`` on any failure so callers can treat the conversion as a
    hard error rather than silently handing rsync a malformed argument.
    """
    try:
        proc = subprocess.run(
            ["wsl.exe", "--", "wslpath", "-u", str(path)],
            capture_output=True,
            text=True,
            timeout=_WSL_PROBE_TIMEOUT,
            check=False,
            **NO_WINDOW_KWARGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    converted = proc.stdout.strip()
    if proc.returncode != 0 or not converted:
        return None
    return converted


@dataclass
class RsyncRuntime:
    """Where/how to run rsync for one push: native, or WSL-wrapped on Windows.

    Resolved once per push (rather than re-probed per call site) so a single
    push only pays for the WSL availability/``wslpath`` subprocess probes
    once.
    """

    command_prefix: list[str]
    use_wsl: bool

    def source_arg(self, source: Path) -> str | None:
        """Render *source* as an rsync source argument, trailing slash included.

        Returns ``None`` when running under WSL and the ``wslpath`` conversion
        failed -- callers must treat that as a push error, never fall back to
        the raw Windows path (which WSL rsync would misparse or simply fail
        to find).
        """
        if self.use_wsl:
            converted = wsl_posix_path(source)
            if converted is None:
                return None
            return converted.rstrip("/") + "/"
        return f"{source}/"


def resolve_rsync_runtime(*, require_ssh: bool = True) -> RsyncRuntime:
    """Resolve the rsync runtime to use for this push: WSL-wrapped when available.

    ``require_ssh`` is forwarded to :func:`wsl_rsync_available` -- pass
    ``False`` for a target (``ingest``) that never shells out to ssh.
    """
    use_wsl = wsl_rsync_available(require_ssh=require_ssh)
    return RsyncRuntime(command_prefix=["wsl.exe", "--"] if use_wsl else [], use_wsl=use_wsl)


@dataclass
class PushResult:
    """Outcome of a :meth:`Target.push`."""

    ok: bool
    detail: str = ""
    file_count: int = 0
    byte_count: int = 0
    excluded_file_count: int = 0
    excluded_byte_count: int = 0
    excluded_roots: tuple[str, ...] = ()
    excluded_measurement_complete: bool = True


@dataclass
class SyncStatus:
    """Latest persisted result exposed by a sync target."""

    supported: bool
    metadata: dict | None = None
    error: str = ""


@dataclass
class FleetSyncStatus:
    """Latest persisted results for every machine exposed by a sync target."""

    supported: bool
    machines: dict[str, SyncStatus] = field(default_factory=dict)
    error: str = ""


#: Sidecar files never transferred by an rsync-based target, mirroring the
#: filesystem targets' ``_EXCLUDE_NAMES``/``_EXCLUDE_SUFFIXES`` (legacy lock
#: names, plus any ``.lock``/``.tmp``/``.hold`` suffix -- including Copilot's
#: restrictive-ACL ``inuse.<pid>.hold`` live-session marker). Must precede the
#: recursive ``--include=session-state/***`` rules below: rsync applies the
#: first matching filter rule, so an exclude listed after a matching include
#: never takes effect.
_SIDECAR_EXCLUDES = (
    "--exclude=.lock",
    "--exclude=lock",
    "--exclude=*.lock",
    "--exclude=*.tmp",
    "--exclude=*.hold",
)


def rsync_session_filters(
    include_sessions: set[str] | None,
    detritus_roots: tuple[Path, ...] = (),
) -> list[str]:
    """Build rsync include/exclude args restricting the transfer to session data.

    session-sync archives session data only -- never the rest of the source
    (``~/.copilot``: binaries, installed plugins, OAuth/credential state,
    encryption keys, settings). A canonical projection may also carry
    per-session ``provenance/<id>.json`` sidecars.

    With ``None`` (no repo allowlist) the whole ``session-state`` and
    ``provenance`` trees plus the global ``session-store.db`` index are
    transferred and nothing else. With an allowlist, only the named
    ``session-state/<id>`` trees and matching provenance sidecars are
    transferred; the global session-store.db is excluded so other repos'
    sessions never leak to the destination.
    """
    exclusions = [*_SIDECAR_EXCLUDES, *(rsync_exclude(root) for root in detritus_roots)]
    if include_sessions is None:
        return [
            *exclusions,
            "--include=session-state/",
            "--include=session-state/***",
            "--include=provenance/",
            "--include=provenance/*.json",
            "--include=session-store.db",
            "--include=session-store.db-wal",
            "--include=session-store.db-shm",
            "--exclude=*",
        ]
    filters = [*exclusions, "--include=session-state/"]
    for sid in sorted(include_sessions):
        filters.append(f"--include=session-state/{sid}/")
        filters.append(f"--include=session-state/{sid}/***")
    filters.append("--include=provenance/")
    for sid in sorted(include_sessions):
        filters.append(f"--include=provenance/{sid}.json")
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
    #: Whether filtered rescue publication enforces destination-side ordering.
    rescue_compare_and_set: bool = False

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}

    @abstractmethod
    def push(
        self, source: Path, machine: str, include_sessions: set[str] | None = None
    ) -> PushResult:
        """Publish *source* under the target's ``{machine}/`` subpath.

        Only session data is published -- the ``session-state`` tree, optional
        per-session ``provenance/`` sidecars, plus the global
        ``session-store.db`` index -- never the rest of the source
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

    def sync_status(self, machine: str) -> SyncStatus:
        """Return the latest persisted sync result when the target exposes one."""
        return SyncStatus(supported=False)

    def fleet_sync_status(self) -> FleetSyncStatus:
        """Return persisted sync results for every visible machine."""
        return FleetSyncStatus(supported=False)

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
