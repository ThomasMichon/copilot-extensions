# Vendored from libs/endpoint-rendezvous (the canonical shared module). Kept
# in-package -- not a distribution dependency -- so agent-dispatch installs as a
# self-contained git dependency (the facility deploys it that way) with no
# external package to resolve. Sync changes from the canonical source.
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
import json
import os
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1

# Windows GetExitCodeProcess sentinel for a process that is still running.
_STILL_ACTIVE = 259

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

    def __post_init__(self) -> None:
        if self.transport not in VALID_TRANSPORTS:
            raise ValueError(
                f"unknown transport {self.transport!r}; expected one of {VALID_TRANSPORTS}"
            )
        if not self.address:
            raise ValueError("endpoint address must be non-empty")

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
        """The on-disk JSON record (keys per the pattern doc; ``endpoint`` = address)."""
        return {
            "schema": SCHEMA,
            "transport": self.transport,
            "endpoint": self.address,
            "pid": self.pid,
            "started_at": self.started_at,
        }

    @classmethod
    def from_record(cls, data: dict, *, source: str = "file") -> Endpoint:
        return cls(
            transport=str(data["transport"]),
            address=str(data["endpoint"]),
            pid=int(data["pid"]) if data.get("pid") is not None else None,
            started_at=(str(data["started_at"]) if data.get("started_at") is not None else None),
            source=source,
        )


def default_runtime_dir(app: str) -> Path:
    """The conventional runtime dir for an app, e.g. ``~/.agent-dispatch/run``."""
    return Path.home() / f".{app}" / "run"


def endpoint_file(runtime_dir: Path | str) -> Path:
    """The rendezvous file path inside ``runtime_dir``."""
    return Path(runtime_dir) / "endpoint.json"


def write_endpoint(
    runtime_dir: Path | str,
    transport: str,
    address: str,
    *,
    pid: int | None = None,
    started_at: str | None = None,
) -> Path:
    """Advertise a bound endpoint by writing the rendezvous file **atomically**.

    Writes a temp file in the same directory and ``os.replace()``\\ s it over the
    target, so a concurrent reader never sees a half-written record. Call it on
    every bind (the port/pid may change; newest bind wins). Returns the file path.
    """
    ep = Endpoint(
        transport=transport,
        address=address,
        pid=pid if pid is not None else os.getpid(),
        started_at=started_at or utc_now_iso(),
    )
    d = Path(runtime_dir)
    d.mkdir(parents=True, exist_ok=True)
    target = endpoint_file(d)
    tmp = d / f".endpoint.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(ep.to_record()), encoding="utf-8")
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
    os.replace(tmp, target)  # atomic within the same filesystem
    return target


def clear_endpoint(runtime_dir: Path | str) -> None:
    """Remove the rendezvous file on graceful shutdown (best-effort).

    A client must still treat a *present-but-stale* file as "not running", because
    a crash skips this cleanup -- see :func:`is_stale`.
    """
    with contextlib.suppress(OSError):
        endpoint_file(runtime_dir).unlink()


def read_endpoint(runtime_dir: Path | str) -> Endpoint | None:
    """Read + parse the rendezvous file; ``None`` if absent, unreadable, or malformed."""
    try:
        raw = endpoint_file(runtime_dir).read_text(encoding="utf-8")
    except OSError:
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
