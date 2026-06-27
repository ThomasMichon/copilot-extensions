"""Single-instance guard -- at most one daemon per config dir.

A second ``agent-bridge start`` against the same config dir (same port + db)
must *refuse* instead of spawning a duplicate daemon. Duplicate daemons
otherwise accumulate as zombies that re-bind the service/relay ports and defeat
restarts (the root cause behind the installer's flaky restart -- see #129).

The guard takes an **OS-level, exclusive, non-blocking** lock on
``<config_dir>/agent-bridge.lock`` and holds it for the life of the process.
Unlike a PID-file + liveness heuristic, an OS byte-range lock is released
*automatically by the kernel* when the holder dies (graceful exit, crash, kill,
or power loss), so a stale lock can never wedge startup -- there is nothing to
"detect" or "reclaim".

Keying on the **config dir** (not the plugin/venv folder) is deliberate: the
primary daemon (``~/.agent-bridge``) and the elevated sub-daemon
(``~/.agent-bridge/elevated``) have distinct config dirs, so each is allowed its
own single instance while two *primaries* can never coexist.

Cross-platform:
* POSIX -- ``fcntl.flock(LOCK_EX | LOCK_NB)`` (whole-file advisory lock).
* Windows -- ``msvcrt.locking(LK_NBLCK)`` on a single byte at a high, sparse
  offset (``_WIN_LOCK_OFFSET``) that holds no data, so the holder's pid text at
  offset 0 stays readable by a contender (msvcrt locks are mandatory and would
  otherwise block reads of the locked range).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("agent-bridge")

_LOCK_FILENAME = "agent-bridge.lock"
# Fixed-width pid record at offset 0 so we never need to truncate the file
# (truncation would race the Windows lock byte if they shared a range).
_PID_FIELD_WIDTH = 20
# Lock a single byte far past the pid record on Windows. The range need not be
# backed by real data -- byte-range locks may extend beyond EOF -- and keeping
# it disjoint from offset 0 lets a contender still read the holder's pid.
_WIN_LOCK_OFFSET = 1 << 30


class AlreadyRunningError(RuntimeError):
    """Raised when another live daemon already holds the config dir's lock."""

    def __init__(self, lock_path: Path, holder_pid: int | None) -> None:
        self.lock_path = lock_path
        self.holder_pid = holder_pid
        who = f"pid {holder_pid}" if holder_pid else "an unknown process"
        super().__init__(
            f"another agent-bridge daemon ({who}) already holds {lock_path}"
        )


def _acquire_os_lock(fh) -> None:  # noqa: ANN001 -- file object
    """Take an exclusive, non-blocking OS lock. Raises OSError on contention."""
    if sys.platform == "win32":
        import msvcrt

        fh.seek(_WIN_LOCK_OFFSET)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        fh.seek(0)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_os_lock(fh) -> None:  # noqa: ANN001 -- file object
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


def _read_holder_pid(lock_path: Path) -> int | None:
    """Read the holder pid recorded at offset 0 (for diagnostics only)."""
    try:
        with open(lock_path, encoding="ascii") as f:
            txt = f.read(_PID_FIELD_WIDTH + 8).strip()
        return int(txt.split()[0]) if txt else None
    except (OSError, ValueError, IndexError):
        return None


class SingleInstance:
    """Hold a config-dir-scoped daemon singleton lock for the process lifetime.

    Usage::

        guard = SingleInstance(config_dir())
        guard.acquire()          # raises AlreadyRunningError if a daemon is live
        try:
            ...                  # run the server; keep `guard` referenced
        finally:
            guard.release()

    The instance MUST stay referenced while the daemon runs -- if it is garbage
    collected the underlying handle closes and the OS lock is released.
    """

    def __init__(
        self,
        config_dir: str | os.PathLike[str],
        port: int | None = None,
    ) -> None:
        # Key the lock on the *port*, not just the config dir, so an active and
        # a passive daemon can coexist on the same config dir (shared db, auth,
        # routing table) during a zero-downtime cutover -- they bind different
        # ports, so they take different locks. Two starts on the *same* port
        # still collide (the duplicate-start guard, #129). ``port=None`` keeps
        # the legacy single-lock filename for callers that don't opt in.
        if port is None:
            self.lock_path = Path(config_dir) / _LOCK_FILENAME
        else:
            self.lock_path = Path(config_dir) / f"agent-bridge.{port}.lock"
        self._fh = None

    def acquire(self) -> None:
        """Acquire the singleton lock or raise :class:`AlreadyRunningError`."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # O_RDWR|O_CREAT (not "a+") so writes honor an explicit seek(0) -- POSIX
        # append mode would force every write to EOF and clobber the pid record.
        fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fh = os.fdopen(fd, "r+", encoding="ascii")
        try:
            _acquire_os_lock(fh)
        except OSError as exc:
            holder = _read_holder_pid(self.lock_path)
            try:
                fh.close()
            except OSError:
                pass
            raise AlreadyRunningError(self.lock_path, holder) from exc

        # We own the lock. Record our pid (fixed width, no truncate) so a future
        # contender can name us in its error message.
        try:
            fh.seek(0)
            fh.write(f"{os.getpid():<{_PID_FIELD_WIDTH}}")
            fh.flush()
            os.fsync(fh.fileno())
        except OSError:
            pass
        self._fh = fh
        log.debug("Acquired daemon singleton lock: %s", self.lock_path)

    def release(self) -> None:
        """Release the lock (idempotent)."""
        if self._fh is None:
            return
        _release_os_lock(self._fh)
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None
        log.debug("Released daemon singleton lock: %s", self.lock_path)

    def __enter__(self) -> SingleInstance:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
