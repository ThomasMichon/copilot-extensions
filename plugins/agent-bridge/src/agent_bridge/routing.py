"""Client-facing routing table -- decouples *which port is live* from config.

Agent-bridge is a light-weight Copilot-plugin payload that ``copilot`` itself
may replace at any moment, and a redeploy must not strand live sessions. The
CLI wrapper tools resolve the daemon endpoint through this table so a
zero-downtime redeploy can stand up a **new** daemon on a fresh port, flip the
table atomically, and retire the **old** daemon -- without any client ever
pointing at a dead port.

Why a table rather than a front proxy: a proxy that holds a stable port ships
in the same plugin payload, so updating *it* re-introduces the very downtime it
was meant to remove (you would then need socket hand-off between proxy
generations -- the hardest-on-Windows part of a supervisor split). The routing
table has **no long-lived process to update**: it is a file. The indirection
lives in two places that are already re-read naturally -- the short-lived client
(every CLI invocation re-reads it) and the daemon's publish step (the daemon
runs from the installed venv copy, not the payload folder).

**Backward compatible.** When the table is absent the caller falls back to the
static ``config.yaml`` port, so this module is inert until a daemon publishes
itself. A reader that finds the *active* endpoint dead (no listener) heals by
trying ``previous`` and then the config fallback.

File layout (``<config_dir>/active.json``)::

    {
      "active":   {"bind": "127.0.0.1", "port": 9281, "pid": 1234,
                   "version": "0.4.0", "generation": 7},
      "previous": {"bind": "127.0.0.1", "port": 9282, "pid": 1200,
                   "version": "0.4.0", "generation": 6},
      "epoch": "2026-06-26T22:40:00Z"
    }

Writes are atomic (tmp file + ``os.replace``) so a concurrent reader sees either
the whole old table or the whole new one, never a torn file. ``generation`` is a
monotonically increasing counter giving readers a total order across flips.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("agent-bridge")

_TABLE_FILENAME = "active.json"
# A loopback connect on a live port returns in well under a millisecond; this
# bounds the heal-probe so a stale entry can never hang a CLI invocation.
_PROBE_TIMEOUT_S = 0.25


@dataclass(frozen=True)
class Endpoint:
    """A resolved daemon endpoint recorded in the routing table."""

    bind: str
    port: int
    pid: int | None = None
    version: str | None = None
    generation: int = 0

    @property
    def client_host(self) -> str:
        """The address a client should dial (wildcard binds map to loopback)."""
        if self.bind in ("0.0.0.0", "", None):
            return "127.0.0.1"
        if self.bind == "::":
            return "::1"
        return self.bind

    @property
    def base_url(self) -> str:
        return f"http://{self.client_host}:{self.port}"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None or k == "pid"}

    @classmethod
    def from_dict(cls, data: dict) -> Endpoint | None:
        try:
            return cls(
                bind=str(data["bind"]),
                port=int(data["port"]),
                pid=(int(data["pid"]) if data.get("pid") is not None else None),
                version=(str(data["version"]) if data.get("version") else None),
                generation=int(data.get("generation", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None


def routing_table_path(config_dir: str | os.PathLike[str]) -> Path:
    """Absolute path of the routing table inside ``config_dir``."""
    return Path(config_dir) / _TABLE_FILENAME


def read_table(config_dir: str | os.PathLike[str]) -> dict | None:
    """Read and parse the raw routing table, or ``None`` if absent/unreadable."""
    path = routing_table_path(config_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("Routing table at %s is corrupt -- ignoring", path)
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid: int | None) -> bool:
    """Best-effort liveness check for a recorded daemon pid.

    Conservative: returns ``True`` when liveness cannot be determined, so an
    *unknown* pid never causes a healthy endpoint to be discarded -- the
    listener probe is the authority for "is it actually serving".
    """
    if not pid or pid <= 0:
        return True
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _listening(host: str, port: int, *, timeout: float = _PROBE_TIMEOUT_S) -> bool:
    """Return True if something accepts a TCP connection at ``host:port``."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def read_active_endpoint(
    config_dir: str | os.PathLike[str],
    *,
    verify_listener: bool = True,
) -> Endpoint | None:
    """Resolve the live daemon endpoint from the routing table.

    Returns the ``active`` endpoint when present (and, if ``verify_listener``,
    actually accepting connections). When the active entry is stale it heals to
    ``previous`` if that one is live. Returns ``None`` when the table is absent
    or no recorded endpoint is reachable -- the caller then falls back to the
    static ``config.yaml`` port.
    """
    data = read_table(config_dir)
    if not data:
        return None

    for key in ("active", "previous"):
        raw = data.get(key)
        if not isinstance(raw, dict):
            continue
        ep = Endpoint.from_dict(raw)
        if ep is None:
            continue
        if not verify_listener:
            return ep
        if _listening(ep.client_host, ep.port):
            return ep
        # No listener: only treat as a hard miss when the pid is confirmed dead
        # or unknown. A live pid with no listener yet (mid-startup) still counts
        # as the active endpoint so a racing client waits on it, not the old one.
        if key == "active" and _pid_alive(ep.pid) and ep.pid:
            return ep
        log.debug("Routing table %s endpoint %s:%d not reachable", key,
                  ep.client_host, ep.port)
    return None


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


def _next_generation(data: dict | None) -> int:
    if not data:
        return 1
    best = 0
    for key in ("active", "previous"):
        raw = data.get(key)
        if isinstance(raw, dict):
            try:
                best = max(best, int(raw.get("generation", 0)))
            except (TypeError, ValueError):
                pass
    return best + 1


def publish_active(
    config_dir: str | os.PathLike[str],
    *,
    bind: str,
    port: int,
    pid: int | None = None,
    version: str | None = None,
    generation: int | None = None,
    demote_existing: bool = False,
) -> Endpoint:
    """Publish ``host:port`` as the active endpoint, atomically.

    When ``demote_existing`` is set and the current active endpoint is a
    *different* port, it is recorded as ``previous`` (the cutover flip). When it
    is the same port (a plain restart re-announcing itself) it is simply
    replaced. ``generation`` defaults to one past the highest recorded value.
    """
    path = routing_table_path(config_dir)
    current = read_table(config_dir) or {}
    gen = generation if generation is not None else _next_generation(current)

    new_active = Endpoint(
        bind=bind, port=port, pid=pid, version=version, generation=gen
    )
    table: dict = {"active": new_active.to_dict()}

    if demote_existing:
        prev_raw = current.get("active")
        prev = Endpoint.from_dict(prev_raw) if isinstance(prev_raw, dict) else None
        if prev is not None and prev.port != port:
            table["previous"] = prev.to_dict()

    table["epoch"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(path, table)
    log.info(
        "Published active endpoint %s:%d (gen %d, pid %s)",
        new_active.client_host, port, gen, pid,
    )
    return new_active


def clear_if_owner(config_dir: str | os.PathLike[str], pid: int) -> bool:
    """Remove our active entry on shutdown iff we are still the recorded active.

    Returns True when the table was cleared. A successor that already flipped
    the table (its pid is now active) is left untouched -- we only retract our
    own claim, so a clean exit never blanks a newer daemon's route. Demotes our
    entry to ``previous`` so an in-flight client mid-resolve still has a fallback
    if the successor is not yet listening.
    """
    data = read_table(config_dir)
    if not data:
        return False
    active = Endpoint.from_dict(data.get("active", {})) \
        if isinstance(data.get("active"), dict) else None
    if active is None or active.pid != pid:
        return False
    path = routing_table_path(config_dir)
    table: dict = {"previous": active.to_dict(),
                   "epoch": datetime.now(timezone.utc).isoformat()}
    try:
        _atomic_write(path, table)
    except OSError:
        return False
    log.info("Cleared active endpoint for pid %d on shutdown", pid)
    return True
