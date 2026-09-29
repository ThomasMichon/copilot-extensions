"""Process-wide serialization for the cutover/deploy path (effort
``agent-bridge-unified-zdd-cutover``, Phase 2).

``CutoverOrchestrator.run`` reads the routing table's breadcrumb/``active``
state, spawns a passive daemon, then flips and drains -- but nothing before
this primitive stopped **two concurrent invocations** (two operators, or an
``agent-bridge deploy``/``service restart`` racing an installer-driven
``deploy``) from doing all of that against the *same* ``config_dir`` at once.
Two orchestrators can both read the same predecessor breadcrumb/routing
state and race: one can demote the other's freshly-flipped generation while
both still report success, or roll back against state the other invocation
now owns. This was already possible with ``agent-bridge deploy`` alone,
before the Phase 1 change routed ``service restart`` onto the same path too
(see the effort README's Phase 2 checklist) -- widened exposure, not a new
bug -- but it is a real gap either way.

This module closes it with a plain **OS-level, exclusive, non-blocking**
lock, held only for the duration of one ``CutoverOrchestrator.run()`` call --
deliberately narrower in scope than a whole-process
:mod:`single_instance_lease` daemon lease (which is held for a process's
entire lifetime). The mechanism is the same well-proven approach (a byte-range
lock the kernel releases automatically on death, so a crashed cutover can
never wedge a later one) reimplemented here, in miniature and stdlib-only, so
:mod:`zdd` keeps its "no runtime dependencies beyond stdlib" contract (see
``libs/zdd/pyproject.toml``) rather than taking a package dependency on
``single_instance_lease`` for one lock class.

Cross-platform:
* POSIX -- ``fcntl.flock(LOCK_EX | LOCK_NB)`` (whole-file advisory lock).
* Windows -- ``msvcrt.locking(LK_NBLCK)`` on a single byte at a high, sparse
  offset that holds no data, mirroring ``single_instance_lease``'s approach
  (mandatory Windows locks would otherwise block a contender's read of the
  holder's pid at offset 0).

The lock file is named ``zdd-cutover.lock``, deliberately namespaced rather
than a bare ``cutover.lock`` -- ``worktree-manager``'s own
``mux_daemon_cutover.py`` already ships an equivalent bespoke lock
(``_acquire_cutover_lock``/``_CutoverLease``) at exactly
``<routing_dir>/cutover.lock`` (added in #4497, independently of this
effort). A bare name would have opened the SAME file under a second, distinct
``open()`` in the same process the moment a consumer wraps
``CutoverOrchestrator.run()`` in its own outer serialization -- POSIX
``flock`` is per-open-file-description, so this orchestrator's own lock
acquisition would then contend with (and, with a blocking wait, deadlock
against) a lock its own caller already held on the identical path. A future
phase may consolidate ``worktree-manager`` onto this shared primitive
instead of its own hand-rolled copy; until then the distinct filename keeps
the two independent and harmless to use together.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_LOCK_FILENAME = "zdd-cutover.lock"
_PID_FIELD_WIDTH = 20
_WIN_LOCK_OFFSET = 1 << 30


class CutoverLockedError(Exception):
    """Raised when another process already holds the cutover lock."""

    def __init__(self, lock_path: Path, holder_pid: int | None) -> None:
        self.lock_path = lock_path
        self.holder_pid = holder_pid
        who = f"pid {holder_pid}" if holder_pid else "another process"
        super().__init__(
            f"a cutover is already in progress ({who} holds {lock_path})"
        )


def lock_path(config_dir: str | os.PathLike[str]) -> Path:
    """Absolute path of the cutover lock file inside ``config_dir``."""
    return Path(config_dir) / _LOCK_FILENAME


def _acquire_os_lock(fh) -> None:  # fh: an open file object
    if sys.platform == "win32":
        import msvcrt

        fh.seek(_WIN_LOCK_OFFSET)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        fh.seek(0)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_os_lock(fh) -> None:  # fh: an open file object
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


def read_holder_pid(path: Path) -> int | None:
    """Best-effort read of the pid recorded by the current lock holder."""
    try:
        with open(path, encoding="ascii") as f:
            txt = f.read(_PID_FIELD_WIDTH + 8).strip()
        return int(txt.split()[0]) if txt else None
    except (OSError, ValueError, IndexError):
        return None


class CutoverLock:
    """Hold the cutover lock for the life of one ``with`` block.

    Usage::

        try:
            with CutoverLock(config_dir):
                orchestrator.run(...)
        except CutoverLockedError as exc:
            ...  # lock still held after the wait budget; report and exit

    Unlike :class:`single_instance_lease.SingleInstance`, this lock is meant
    to be acquired and released **within a single call** (one cutover
    attempt), not held for a whole daemon's lifetime -- so a crashed
    orchestrator releases it immediately (the kernel frees the OS lock when
    the holding process dies), and a later cutover in the same process is
    free to acquire it again.

    ``acquire`` **waits** (poll-retries) up to ``timeout`` seconds rather
    than failing on first contention -- a deliberate choice, not merely a
    convenience: a legitimate caller may trigger a second cutover while a
    first is still in flight (a fast second update superseding a first, or
    two operators acting close together) and expects the second to
    *succeed once the first finishes*, not to be flatly refused. Only
    exhausting the full wait budget without ever acquiring the lock raises
    :class:`CutoverLockedError`. ``timeout=0`` (the default) preserves the
    original fail-immediately behavior for a caller that wants that instead.
    """

    def __init__(self, config_dir: str | os.PathLike[str]) -> None:
        self.path = lock_path(config_dir)
        self._fh = None

    def acquire(self, *, timeout: float = 0.0, poll: float = 0.2) -> None:
        """Acquire the lock, waiting up to ``timeout`` seconds on contention.

        Raises :class:`CutoverLockedError` only once ``timeout`` has elapsed
        without ever acquiring the lock (or immediately, for the default
        ``timeout=0``).
        """
        import time

        deadline = time.monotonic() + timeout
        while True:
            try:
                self._try_acquire()
                return
            except CutoverLockedError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(poll)

    def _try_acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        fh = os.fdopen(fd, "r+", encoding="ascii")
        try:
            _acquire_os_lock(fh)
        except OSError as exc:
            holder = read_holder_pid(self.path)
            try:
                fh.close()
            except OSError:
                pass
            raise CutoverLockedError(self.path, holder) from exc

        try:
            fh.seek(0)
            fh.write(f"{os.getpid():<{_PID_FIELD_WIDTH}}")
            fh.flush()
            os.fsync(fh.fileno())
        except OSError:
            pass
        self._fh = fh

    @property
    def held(self) -> bool:
        return self._fh is not None

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

    def __enter__(self) -> CutoverLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
