"""Single-instance lease -- at most one live daemon owns a service on a host.

A second start of the same service against the same lease key must *refuse*
instead of spawning a duplicate daemon. Duplicate daemons otherwise accumulate
as zombies that re-bind service ports and defeat restarts.

The lease takes an **OS-level, exclusive, non-blocking** lock on a lock file and
holds it for the life of the process. Unlike a PID-file + liveness heuristic, an
OS byte-range lock is released *automatically by the kernel* when the holder
dies (graceful exit, crash, kill, or power loss), so a stale lock can never
wedge startup -- there is nothing to "detect" or "reclaim". Ownership is thus
**liveness-reconciled by construction**: a lease held by a dead process is
immediately acquirable, and a live owner is never displaced by accident.

Keying is caller-chosen. The typical key is a service's config dir plus,
optionally, the bound **port** -- keying on the port lets an active and a
passive daemon coexist on one config dir during a zero-downtime cutover (they
bind different ports, so they take different locks), while two starts on the
*same* port still collide.

Cross-platform:
* POSIX -- ``fcntl.flock(LOCK_EX | LOCK_NB)`` (whole-file advisory lock).
* Windows -- ``msvcrt.locking(LK_NBLCK)`` on a single byte at a high, sparse
  offset (``_WIN_LOCK_OFFSET``) that holds no data, so the holder's pid text at
  offset 0 stays readable by a contender (msvcrt locks are mandatory and would
  otherwise block reads of the locked range).

Extracted from agent-bridge's ``singleton`` module (which proved the design in
production) so any Copilot CLI plugin or multi-machine service reuses one
implementation instead of reinventing it.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path
from typing import BinaryIO, TextIO

_DEFAULT_LOGGER = logging.getLogger("single_instance_lease")

# Fixed-width pid record at offset 0 so we never need to truncate the file
# (truncation would race the Windows lock byte if they shared a range).
_PID_FIELD_WIDTH = 20
# Lock a single byte far past the pid record on Windows. The range need not be
# backed by real data -- byte-range locks may extend beyond EOF -- and keeping
# it disjoint from offset 0 lets a contender still read the holder's pid.
_WIN_LOCK_OFFSET = 1 << 30


def _sanitize(name: str) -> str:
    """Reduce an arbitrary service name to a safe lock-filename stem."""
    keep = [c if (c.isalnum() or c in "-_.") else "-" for c in name.strip()]
    stem = "".join(keep).strip("-.") or "service"
    return stem


class AlreadyRunningError(RuntimeError):
    """Raised when another live process already holds the lease."""

    def __init__(self, lock_path: Path, holder_pid: int | None) -> None:
        self.lock_path = lock_path
        self.holder_pid = holder_pid
        who = f"pid {holder_pid}" if holder_pid else "an unknown process"
        super().__init__(f"another instance ({who}) already holds {lock_path}")


def _acquire_os_lock(fh: BinaryIO | TextIO) -> None:
    """Take an exclusive, non-blocking OS lock. Raises OSError on contention."""
    if sys.platform == "win32":
        import msvcrt

        fh.seek(_WIN_LOCK_OFFSET)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        fh.seek(0)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_os_lock(fh: BinaryIO | TextIO) -> None:
    """Best-effort release of the OS lock (kernel also frees it on exit)."""
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(_WIN_LOCK_OFFSET)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def read_owner_pid(lock_path: str | os.PathLike[str]) -> int | None:
    """Read the pid recorded at offset 0 of a lease lock file (diagnostics).

    Returns ``None`` when the file is absent, empty, or unparsable. This is a
    best-effort read for naming the holder in an error/log message; the OS lock
    -- not this record -- is the authority for ownership.
    """
    try:
        with open(lock_path, encoding="ascii") as f:
            txt = f.read(_PID_FIELD_WIDTH + 8).strip()
        return int(txt.split()[0]) if txt else None
    except (OSError, ValueError, IndexError):
        return None


class SingleInstance:
    """Hold a lease for the process lifetime.

    Usage::

        lease = SingleInstance("~/.agent-vault", service="agent-vault")
        lease.acquire()          # raises AlreadyRunningError if one is live
        try:
            ...                  # run the server; keep `lease` referenced
        finally:
            lease.release()

    The instance MUST stay referenced while the daemon runs -- if it is garbage
    collected the underlying handle closes and the OS lock is released.

    :param lock_dir: directory the lock file lives in (created if missing).
    :param service: service name; used for the default lock filename and logs.
    :param port: optional port to include in the key so an active/passive pair
        can coexist on one ``lock_dir``. ``None`` keeps the un-suffixed name.
    :param lock_name: explicit lock filename, overriding the derived name.
    :param logger: logger for debug lines (default ``single_instance_lease``).
    """

    def __init__(
        self,
        lock_dir: str | os.PathLike[str],
        *,
        service: str = "service",
        port: int | None = None,
        lock_name: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.log = logger or _DEFAULT_LOGGER
        if lock_name is None:
            stem = _sanitize(service)
            lock_name = f"{stem}.lock" if port is None else f"{stem}.{port}.lock"
        self.lock_path = Path(lock_dir) / lock_name
        self._fh: TextIO | None = None
        self._owner_pid: int | None = None

    def acquire(self, *, expected_file_identity: tuple[int, int] | None = None) -> None:
        """Acquire a lease, optionally requiring an already-existing exact inode."""
        if expected_file_identity is None:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # O_RDWR|O_CREAT (not "a+") so writes honor an explicit seek(0) -- POSIX
        # append mode would force every write to EOF and clobber the pid record.
        flags = os.O_RDWR | (
            os.O_CREAT if expected_file_identity is None else getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open(str(self.lock_path), flags, 0o644)
        fh = os.fdopen(fd, "r+", encoding="ascii")

        def require_expected_file() -> None:
            if expected_file_identity is None:
                return
            opened = os.fstat(fh.fileno())
            current = self.lock_path.lstat()
            if (
                not stat.S_ISREG(current.st_mode)
                or getattr(current, "st_file_attributes", 0) & 0x400
                or not os.path.samestat(opened, current)
                or (opened.st_dev, opened.st_ino) != expected_file_identity
            ):
                raise PermissionError("single-instance lease no longer names the expected file")

        try:
            require_expected_file()
            try:
                _acquire_os_lock(fh)
            except OSError as exc:
                raise AlreadyRunningError(self.lock_path, read_owner_pid(self.lock_path)) from exc
            require_expected_file()
        except BaseException:
            try:
                fh.close()
            except OSError:
                pass
            raise

        # We own the lease. Record our pid (fixed width, no truncate) so a future
        # contender can name us in its error message.
        try:
            fh.seek(0)
            fh.write(f"{os.getpid():<{_PID_FIELD_WIDTH}}")
            fh.flush()
            os.fsync(fh.fileno())
        except OSError:
            pass
        self._fh = fh
        self._owner_pid = os.getpid()
        self.log.debug("Acquired single-instance lease: %s", self.lock_path)

    def held_file_identity(self) -> tuple[int, int]:
        """Identify this process's held file, refusing path replacement or links.

        This does not acquire another descriptor, transfer custody, or make a
        PID record authoritative. Callers must still serialize their lifecycle.
        """
        if self._fh is None or self._owner_pid != os.getpid():
            raise PermissionError("single-instance lease is not held by this process")
        try:
            opened = os.fstat(self._fh.fileno())
            current = self.lock_path.lstat()
        except (OSError, ValueError) as error:
            raise PermissionError("single-instance lease identity is unavailable") from error
        if (
            not stat.S_ISREG(current.st_mode)
            or getattr(current, "st_file_attributes", 0) & 0x400
            or not os.path.samestat(opened, current)
        ):
            raise PermissionError("single-instance lease path no longer names its held file")
        return opened.st_dev, opened.st_ino

    @property
    def held(self) -> bool:
        """True while this process holds the lease."""
        return self._fh is not None

    def release(self) -> None:
        """Release the lease (idempotent)."""
        if self._fh is None:
            return
        # A fork shares the parent's flock description; only its owner may unlock it.
        if self._owner_pid == os.getpid():
            _release_os_lock(self._fh)
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None
        self._owner_pid = None
        self.log.debug("Released single-instance lease: %s", self.lock_path)

    def __enter__(self) -> SingleInstance:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
