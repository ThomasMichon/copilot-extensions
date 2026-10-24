"""Rendezvous (port-mapping) files for discoverable, collision-free local endpoints.

A *service-bearing* Copilot CLI plugin needs its clients -- its own CLI, sibling
plugins, and agents on the box -- to reach it without hardcoding a fixed loopback
TCP port. Pinning a port collides with siblings, with the ``127.0.0.1`` a Windows
host shares with its WSL guest, and with OS reservations (Hyper-V/WinNAT excluded
ranges) that hold an address with no listener.

The **rendezvous file** is the discovery seam: a small JSON file a service writes
when it binds and every client reads to find it -- the "port-mapping file"
convention. It lets a service move to an OS-native endpoint (a Unix socket / named
pipe) or an OS-assigned ephemeral port while clients keep resolving it with no
edit.

On-disk format (``<runtime_dir>/endpoint.json``), matching
``docs/patterns/local-endpoint-discovery.md``::

    {
      "schema": 1,
      "transport": "unix" | "pipe" | "tcp",
      "endpoint": "/home/u/.agent-x/run/x.sock" | "\\\\.\\pipe\\agent-x" | "127.0.0.1:52731",
      "pid": 48213,
      "started_at": "2026-07-16T22:41:09Z"
    }

The client-side :func:`resolve` implements the **cutover fallback ladder** an
in-place migration off a fixed port needs: an explicit override, then the
rendezvous file, then a legacy fixed constant. A not-yet-migrated service (no
file) is still reached via the legacy default; a migrated one is discovered.

Pure standard library; no runtime dependencies.
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import socket
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

SCHEMA = 1
_ENDPOINT_LOCK_TIMEOUT = 5.0
_LOG = logging.getLogger(__name__)

# Windows GetExitCodeProcess sentinel for a process that is still running.
_STILL_ACTIVE = 259

# Windows OpenProcess failure code for a query denied by the OS -- e.g. a process
# in another logon session or owned by another user. Denied != gone.
_ERROR_ACCESS_DENIED = 5


def _openprocess_denied_means_alive(last_error: int) -> bool:
    """Interpret an ``OpenProcess`` failure.

    A denied query (``ERROR_ACCESS_DENIED`` -- the target runs in another logon
    session or is owned by another user) means the process **exists**; any other
    failure is treated as gone. This is the Windows analogue of POSIX
    ``os.kill(pid, 0)`` raising ``PermissionError`` (alive) vs ``ProcessLookupError``
    (dead), and it is the fix for a coordinator started under a Scheduled Task
    (``LogonType S4U``) looking "dead" to a client in another session.
    """
    return last_error == _ERROR_ACCESS_DENIED


VALID_TRANSPORTS = ("unix", "pipe", "tcp")


class EndpointUnavailable(RuntimeError):
    """No endpoint could be resolved for a service (fail loud, don't mask)."""


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 ``...Z`` string (second precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def pid_alive(pid: int | None) -> bool:
    """Return True if a local process with ``pid`` currently exists.

    Cross-platform and side-effect free. On Windows, ``os.kill(pid, 0)`` would
    *terminate* the process, so query the process handle via the Win32 API.
    """
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        access = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(access, False, pid)
        if not handle:
            # OpenProcess failed. Distinguish a denied query from a genuine
            # "no such process": a process in another logon session / owned by
            # another user denies the query but is alive -- mirror the POSIX
            # PermissionError branch below.
            return _openprocess_denied_means_alive(ctypes.get_last_error())
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


@dataclass(frozen=True)
class Endpoint:
    """A resolved local endpoint: which transport, and the address for it.

    ``transport`` is one of ``unix`` (Unix domain socket path), ``pipe`` (Windows
    named pipe name), or ``tcp`` (``host:port``). ``source`` records how the
    endpoint was resolved (``env`` / ``file`` / ``legacy``) so a caller can log or
    branch on provenance.
    """

    transport: str
    address: str
    pid: int | None = None
    started_at: str | None = None
    source: str = "file"
    alt: tuple[Endpoint, ...] = ()

    def __post_init__(self) -> None:
        if self.transport not in VALID_TRANSPORTS:
            raise ValueError(
                f"unknown transport {self.transport!r}; expected one of {VALID_TRANSPORTS}"
            )
        if not self.address:
            raise ValueError("endpoint address must be non-empty")

    def usable(self, accept: Callable[[str], bool]) -> Endpoint | None:
        """Pick the endpoint dialable in the caller's context.

        Returns ``self`` when its transport passes ``accept``; otherwise the first
        :attr:`alt` whose transport passes (a service that binds several transports
        advertises the native one as primary and the cross-boundary one -- e.g. TCP
        for a WSL guest that can't open the host's named pipe -- as an alternate);
        else ``None``. The chosen alternate inherits this endpoint's ``source`` so
        the caller keeps the resolved provenance.
        """
        if accept(self.transport):
            return self
        for e in self.alt:
            if accept(e.transport):
                return Endpoint(
                    transport=e.transport, address=e.address, source=self.source
                )
        return None

    @classmethod
    def parse(cls, spec: str, *, source: str = "file") -> Endpoint:
        """Parse a ``"<transport>:<address>"`` spec, e.g. ``"tcp:127.0.0.1:9847"``.

        Only the first ``:`` separates transport from address, so a ``host:port``
        or a pipe path with its own colons is preserved intact.
        """
        transport, sep, address = spec.partition(":")
        if not sep:
            raise ValueError(f"malformed endpoint spec {spec!r}; expected '<transport>:<address>'")
        return cls(transport=transport.strip(), address=address.strip(), source=source)

    def to_spec(self) -> str:
        """The inverse of :meth:`parse`."""
        return f"{self.transport}:{self.address}"

    @property
    def tcp_host_port(self) -> tuple[str, int]:
        """Split a ``tcp`` endpoint's address into ``(host, port)``."""
        if self.transport != "tcp":
            raise ValueError(f"tcp_host_port on non-tcp endpoint ({self.transport})")
        host, _, port = self.address.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(f"malformed tcp address {self.address!r}; expected 'host:port'")
        return host, int(port)

    def to_record(self) -> dict:
        """The on-disk JSON record (keys per the pattern doc; ``endpoint`` = address).

        A service that also listens on secondary transports advertises them in an
        optional ``alt`` array (``[{transport, endpoint}, ...]``); the key is
        omitted when there are none, so a single-endpoint record is byte-identical
        to before and old readers ignore the field.
        """
        rec = {
            "schema": SCHEMA,
            "transport": self.transport,
            "endpoint": self.address,
            "pid": self.pid,
            "started_at": self.started_at,
        }
        if self.alt:
            rec["alt"] = [{"transport": e.transport, "endpoint": e.address} for e in self.alt]
        return rec

    @classmethod
    def from_record(cls, data: dict, *, source: str = "file") -> Endpoint:
        alt = tuple(
            cls(transport=t, address=a, source=source)
            for entry in (data.get("alt") or [])
            for t, a in [
                (str(entry.get("transport", "")).strip(), str(entry.get("endpoint", "")).strip())
            ]
            if t in VALID_TRANSPORTS and a
        )
        return cls(
            transport=str(data["transport"]),
            address=str(data["endpoint"]),
            pid=int(data["pid"]) if data.get("pid") is not None else None,
            started_at=(str(data["started_at"]) if data.get("started_at") is not None else None),
            source=source,
            alt=alt,
        )


def default_runtime_dir(app: str) -> Path:
    """The conventional runtime dir for an app, e.g. ``~/.agent-dispatch/run``."""
    return Path.home() / f".{app}" / "run"


def endpoint_file(runtime_dir: Path | str) -> Path:
    """The rendezvous file path inside ``runtime_dir``."""
    return Path(runtime_dir) / "endpoint.json"


@contextlib.contextmanager
def _endpoint_lock(runtime_dir: Path | str) -> Iterator[None]:
    """Serialize publication and owner-checked removal across processes."""
    path = Path(runtime_dir) / ".endpoint.lock"
    # Keep this inode: unlinking a lock file would split concurrent holders.
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        if sys.platform == "win32":
            import msvcrt
        else:
            import fcntl
        deadline = time.monotonic() + _ENDPOINT_LOCK_TIMEOUT
        while True:
            try:
                if sys.platform == "win32":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EINTR):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"endpoint mutation lock timed out: {path}") from exc
                time.sleep(min(0.01, remaining))
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _replace_endpoint(source: Path, destination: Path) -> None:
    """Atomically replace the name without invalidating an open reader."""
    if sys.platform != "win32":
        os.replace(source, destination)
        return
    import ctypes
    from ctypes import wintypes

    class RenameInfo(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    # DELETE access; open the staged name itself, not a substituted source link.
    handle = kernel.CreateFileW(str(source), 0x10000, 0x7, None, 3, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name = str(destination.absolute()).encode("utf-16-le", errors="surrogatepass")
        offset = RenameInfo.FileName.offset
        buffer = ctypes.create_string_buffer(max(ctypes.sizeof(RenameInfo), offset + len(name) + 2))
        info = RenameInfo.from_buffer(buffer)
        info.Flags = 0x1 | 0x2  # REPLACE_IF_EXISTS | POSIX_SEMANTICS
        info.FileNameLength = len(name)
        ctypes.memmove(ctypes.addressof(buffer) + offset, name, len(name))
        if not kernel.SetFileInformationByHandle(handle, 22, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def write_endpoint(
    runtime_dir: Path | str,
    transport: str,
    address: str,
    *,
    pid: int | None = None,
    started_at: str | None = None,
    alt: Iterable[tuple[str, str]] | None = None,
) -> Path:
    """Advertise a bound endpoint by writing the rendezvous file **atomically**.

    Writes a temp file in the same directory and atomically replaces the
    target, so a concurrent reader never sees a half-written record. Call it on
    every bind (the port/pid may change; newest bind wins). ``alt`` names any
    secondary ``(transport, address)`` endpoints the service also listens on (for
    cross-boundary discovery); pass ``None``/empty for the common single-endpoint
    case. Returns the file path.
    """
    ep = Endpoint(
        transport=transport,
        address=address,
        pid=pid if pid is not None else os.getpid(),
        started_at=started_at or utc_now_iso(),
        alt=tuple(Endpoint(transport=t, address=a) for t, a in (alt or ())),
    )
    d = Path(runtime_dir)
    d.mkdir(parents=True, exist_ok=True)
    target = endpoint_file(d)
    tmp = d / f".endpoint.{os.getpid()}.tmp"
    with _endpoint_lock(d):
        tmp.write_text(json.dumps(ep.to_record()), encoding="utf-8")
        if sys.platform != "win32":
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
        _replace_endpoint(tmp, target)
    return target


def clear_endpoint(runtime_dir: Path | str, *, owner_pid: int | None = None) -> None:
    """Remove the rendezvous file unless another PID owns it (best-effort).

    A record owned by a different PID is preserved because it may belong to a
    successor that has already advertised during handoff. A client must still
    treat a *present-but-stale* file as "not running", because a crash skips this
    cleanup -- see :func:`is_stale`.
    """
    path = endpoint_file(runtime_dir)
    acquiring = False
    removed = False
    try:
        if not path.parent.exists():
            return
        acquiring = True
        with _endpoint_lock(runtime_dir):
            acquiring = False
            record = read_endpoint(runtime_dir)
            pid = owner_pid if owner_pid is not None else os.getpid()
            if record is not None and record.pid is not None and record.pid != pid:
                return
            record = read_endpoint(runtime_dir)
            if record is not None and record.pid is not None and record.pid != pid:
                return
            path.unlink(missing_ok=True)
            removed = True
    except OSError as exc:
        if acquiring and isinstance(exc, TimeoutError):
            _LOG.warning("Endpoint cleanup skipped: mutation lock timed out for %s", path)
        elif removed:
            _LOG.warning("Endpoint removed but mutation lock release failed for %s", path, exc_info=True)
        else:
            _LOG.warning("Endpoint cleanup failed for %s", path, exc_info=True)


def _open_endpoint(path: Path) -> BinaryIO:
    """Keep an atomic publisher from being blocked by a discovery reader."""
    if sys.platform != "win32":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # GENERIC_READ; share read/write/delete so replacement preserves this snapshot.
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def read_endpoint(runtime_dir: Path | str) -> Endpoint | None:
    """Read + parse the rendezvous file; ``None`` if absent, unreadable, or malformed."""
    try:
        with _open_endpoint(endpoint_file(runtime_dir)) as handle:
            raw = handle.read().decode("utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        data = json.loads(raw)
        if int(data.get("schema", 0)) != SCHEMA:
            return None
        return Endpoint.from_record(data)
    except (ValueError, TypeError, KeyError):
        return None


def is_stale(ep: Endpoint | None, *, probe: Callable[[Endpoint], bool] | None = None) -> bool:
    """True if the endpoint is known-dead.

    Staleness is decided by evidence, never assumed: a recorded ``pid`` that is no
    longer alive makes it stale; if a ``probe`` callable is supplied, a probe that
    returns False (e.g. connection refused) makes it stale. With neither signal
    available it is treated as *not* stale (the caller then finds out on connect
    and can fail loud).
    """
    if ep is None:
        return True
    if ep.pid is not None and not pid_alive(ep.pid):
        return True
    if probe is not None:
        with contextlib.suppress(Exception):
            return not probe(ep)
    return False


def connect_probe(ep: Endpoint, *, timeout: float = 0.5) -> bool:
    """Best-effort liveness probe: can we open the endpoint's socket?

    Handles ``tcp`` and ``unix``. For ``pipe`` (and anything unknown) it returns
    True (unprobed) -- pid-liveness is the signal there. Intended to be passed as
    the ``probe`` argument to :func:`resolve` / :func:`is_stale`.
    """
    try:
        if ep.transport == "tcp":
            host, port = ep.tcp_host_port
            # A wildcard/unspecified bind (permitted by check_bind_safety()
            # when a token is configured) is not a dialable *destination* --
            # connecting to it directly fails on most OSes regardless of
            # whether anything is actually listening, so an endpoint
            # advertised on 0.0.0.0/:: would otherwise be misclassified as
            # stale before any caller-supplied health check even runs
            # (review follow-up on ThomasMichon/copilot-extensions#3066).
            if host in ("0.0.0.0", ""):
                host = "127.0.0.1"
            elif host in ("::", "[::]"):
                host = "::1"
            with socket.create_connection((host, port), timeout=timeout):
                return True
        if ep.transport == "unix" and hasattr(socket, "AF_UNIX"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            try:
                s.connect(ep.address)
                return True
            finally:
                s.close()
    except OSError:
        return False
    return True  # pipe / unknown -- not probed


def _coerce(value: str | Endpoint | None, *, source: str) -> Endpoint | None:
    if value is None:
        return None
    if isinstance(value, Endpoint):
        return value
    return Endpoint.parse(value, source=source)


def resolve(
    runtime_dir: Path | str,
    *,
    override: str | Endpoint | None = None,
    legacy: str | Endpoint | None = None,
    probe: Callable[[Endpoint], bool] | None = None,
) -> Endpoint:
    """Resolve a service's endpoint via the cutover fallback ladder.

    Order: **override** (an explicit operator/env choice) -> the **rendezvous
    file** (if present and not stale) -> a **legacy** fixed constant (the
    backwards-compatible default while a service migrates). ``override`` and
    ``legacy`` accept either an :class:`Endpoint` or a ``"<transport>:<address>"``
    spec string.

    Raises :class:`EndpointUnavailable` if nothing resolves -- fail loud, never a
    masked "service unavailable".
    """
    ov = _coerce(override, source="env")
    if ov is not None:
        return ov

    ep = read_endpoint(runtime_dir)
    if ep is not None and not is_stale(ep, probe=probe):
        return ep

    lg = _coerce(legacy, source="legacy")
    if lg is not None:
        return lg

    raise EndpointUnavailable(
        f"no endpoint for service at {endpoint_file(runtime_dir)}: "
        "no override, no live rendezvous file, and no legacy default"
    )
