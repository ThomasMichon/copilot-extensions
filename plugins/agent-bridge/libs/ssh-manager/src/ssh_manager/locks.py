"""Cross-process advisory locks that serialize SSH access to a target.

``agent-codespaces ssh`` runs as a separate OS process per invocation, and the
agent-bridge daemon spawns one for every CodeSpace dispatch. Because all access
to a given CodeSpace funnels through a single credential-relay reverse-forward
(one relay port per host), two concurrent invocations against the **same**
target collide on that port and tear each other's SSH connection down -- a live
dispatch can be collapsed by an ad-hoc diagnostic ``ssh --remote-cmd`` to the
same CodeSpace.

A per-target cross-process lock makes that race deterministic: the first
invocation holds the lock for the lifetime of its SSH operation, and any later
invocation either

* **blocks** with an actionable :class:`TargetBusyError` naming the in-flight
  holder (pid / op / age), or
* **takes over** when ``force=True`` -- terminating the prior holder and
  reclaiming the lock.

The lock is a small JSON file under ``~/.ssh-manager/locks/<target>.lock`` whose
contents identify the holder. A holder whose pid is no longer alive is treated
as **stale** and reclaimed automatically, so a crashed process never wedges a
target permanently.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("ssh-manager")

# Windows GetExitCodeProcess sentinel for a process that is still running.
_STILL_ACTIVE = 259


def locks_dir() -> Path:
    """Directory holding per-target lock files."""
    return Path.home() / ".ssh-manager" / "locks"


def _sanitize(target: str) -> str:
    """Map an arbitrary target name to a filesystem-safe lock key."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in target)
    return safe[:80] or "target"


def pid_alive(pid: int) -> bool:
    """Return True if a local process with ``pid`` currently exists.

    Cross-platform and side-effect free. On Windows, ``os.kill(pid, 0)`` would
    *terminate* the process (Windows maps any signal to TerminateProcess), so we
    query the process handle via the Win32 API instead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        # PROCESS_QUERY_LIMITED_INFORMATION -- minimal rights, works across
        # integrity levels for processes we own.
        access = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(access, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                return True  # exists but couldn't read state -- assume alive
            return exit_code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def _terminate(pid: int, *, grace: float = 3.0) -> None:
    """Best-effort terminate a local process and wait briefly for it to exit."""
    if pid <= 0 or not pid_alive(pid):
        return
    if sys.platform == "win32":
        import ctypes

        PROCESS_TERMINATE = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            try:
                ctypes.windll.kernel32.TerminateProcess(handle, 1)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    else:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    # Hard kill if still alive after the grace period.
    if pid_alive(pid) and sys.platform != "win32":
        import signal

        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


@dataclass
class LockHolder:
    """Identity of the process currently holding a target lock."""

    pid: int
    op: str
    target: str
    started_at: float
    host: str = ""

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)

    @classmethod
    def from_json(cls, raw: str) -> LockHolder | None:
        try:
            data = json.loads(raw)
            return cls(
                pid=int(data["pid"]),
                op=str(data.get("op", "ssh")),
                target=str(data.get("target", "")),
                started_at=float(data.get("started_at", 0.0)),
                host=str(data.get("host", "")),
            )
        except (ValueError, KeyError, TypeError):
            return None


class TargetBusyError(RuntimeError):
    """Raised when a target lock is held by another live process."""

    def __init__(self, target: str, holder: LockHolder) -> None:
        self.target = target
        self.holder = holder
        super().__init__(
            f"SSH target '{target}' is busy: held by pid {holder.pid} "
            f"(op={holder.op}, age={holder.age_seconds:.0f}s)"
        )

    def user_message(self) -> str:
        """A multi-line, actionable message for a CLI/agent caller.

        Deliberately worded so an LLM caller makes a judgement call -- wait and
        observe the in-flight operation (it may be doing exactly what is needed)
        versus deliberately taking it over.
        """
        h = self.holder
        return (
            f"[BUSY] An SSH operation is already in progress against "
            f"'{self.target}'.\n"
            f"  holder: pid {h.pid}, op={h.op}, running for {h.age_seconds:.0f}s\n"
            f"  This is usually a live agent-bridge dispatch. A second "
            f"connection would collide on the credential relay and can collapse "
            f"the in-flight session.\n"
            f"  Decide:\n"
            f"    - WAIT / OBSERVE: monitor it (e.g. 'agent-bridge read <id>') "
            f"instead of opening a second SSH; it may already be doing the work.\n"
            f"    - TAKE OVER: re-run with --force to terminate the in-flight "
            f"operation and reclaim the target (discards its in-progress work)."
        )


class TargetLock:
    """A cross-process advisory lock keyed by SSH target name.

    Use as a context manager or via explicit ``acquire()`` / ``release()``.
    Holding the lock means "this process owns SSH access to ``target``".
    """

    def __init__(
        self,
        target: str,
        *,
        op: str = "ssh",
        directory: Path | None = None,
    ) -> None:
        self.target = target
        self.op = op
        self._dir = directory or locks_dir()
        self._held = False

    @property
    def path(self) -> Path:
        return self._dir / f"{_sanitize(self.target)}.lock"

    def read_holder(self) -> LockHolder | None:
        """Return the current holder, or None if unheld/unreadable."""
        try:
            return LockHolder.from_json(self.path.read_text(encoding="utf-8"))
        except OSError:
            return None

    def _write_self(self) -> None:
        holder = LockHolder(
            pid=os.getpid(),
            op=self.op,
            target=self.target,
            started_at=time.time(),
            host="",
        )
        self.path.write_text(
            json.dumps(asdict(holder)), encoding="utf-8"
        )

    def acquire(self, *, force: bool = False) -> TargetLock:
        """Acquire the lock, blocking-with-error rather than waiting.

        Reclaims a stale lock (holder pid no longer alive) automatically. If a
        *live* holder exists and ``force`` is False, raises
        :class:`TargetBusyError`. If ``force`` is True, terminates the holder and
        takes over.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        # Bounded retry loop: each iteration either wins the O_EXCL create or
        # resolves an existing lock (stale-reclaim / force-evict / busy).
        for _ in range(50):
            try:
                fd = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError:
                holder = self.read_holder()
                if holder is None:
                    # Unreadable/partial lock file -- treat as stale.
                    self._force_unlink()
                    continue
                if holder.pid == os.getpid():
                    # Re-entrant within the same process.
                    self._held = True
                    return self
                if not pid_alive(holder.pid):
                    log.info(
                        "Reclaiming stale SSH lock on %s (dead pid %d)",
                        self.target, holder.pid,
                    )
                    self._force_unlink()
                    continue
                if force:
                    log.warning(
                        "Force-evicting SSH lock holder pid %d on %s",
                        holder.pid, self.target,
                    )
                    _terminate(holder.pid)
                    self._force_unlink()
                    continue
                raise TargetBusyError(self.target, holder) from None
            else:
                os.close(fd)
                self._write_self()
                self._held = True
                return self
        # Lost too many races -- surface whatever holder we can see.
        holder = self.read_holder() or LockHolder(
            pid=-1, op="unknown", target=self.target, started_at=time.time()
        )
        raise TargetBusyError(self.target, holder)

    def _force_unlink(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def release(self) -> None:
        """Release the lock if this process holds it."""
        if not self._held:
            return
        holder = self.read_holder()
        if holder is not None and holder.pid == os.getpid():
            self._force_unlink()
        self._held = False

    def __enter__(self) -> TargetLock:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()
