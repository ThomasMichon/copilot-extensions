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
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("zdd")

_TABLE_FILENAME = "active.json"
_LOCK_FILENAME = "active.lock"
_PROCESS_ROUTING_LOCK = threading.RLock()
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


@contextmanager
def _file_routing_lock(config_dir: str | os.PathLike[str]):
    path = Path(config_dir) / _LOCK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _routing_lock(config_dir: str | os.PathLike[str]):
    """Serialize routing mutations across threads and processes."""
    with _PROCESS_ROUTING_LOCK:
        with _file_routing_lock(config_dir):
            yield


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


def _publish_active_unlocked(
    config_dir: str | os.PathLike[str],
    *,
    bind: str,
    port: int,
    pid: int | None = None,
    version: str | None = None,
    generation: int | None = None,
    demote_existing: bool = False,
) -> Endpoint:
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
    with _routing_lock(config_dir):
        return _publish_active_unlocked(
            config_dir,
            bind=bind,
            port=port,
            pid=pid,
            version=version,
            generation=generation,
            demote_existing=demote_existing,
        )


def restore_previous_if_owner(
    config_dir: str | os.PathLike[str],
    *,
    pid: int,
    generation: int,
) -> bool:
    """Restore ``previous`` iff the caller still owns the active generation."""
    with _routing_lock(config_dir):
        data = read_table(config_dir)
        if not data:
            return False
        active_raw = data.get("active")
        active = (
            Endpoint.from_dict(active_raw)
            if isinstance(active_raw, dict)
            else None
        )
        if active is None or active.pid != pid or active.generation != generation:
            return False

        previous_raw = data.get("previous")
        previous = (
            Endpoint.from_dict(previous_raw)
            if isinstance(previous_raw, dict)
            else None
        )
        table: dict = {"epoch": datetime.now(timezone.utc).isoformat()}
        if previous is not None:
            restored = Endpoint(
                bind=previous.bind,
                port=previous.port,
                pid=previous.pid,
                version=previous.version,
                generation=_next_generation(data),
            )
            table["active"] = restored.to_dict()
        _atomic_write(routing_table_path(config_dir), table)
        return True


def clear_if_owner(config_dir: str | os.PathLike[str], pid: int) -> bool:
    """Remove our active entry on shutdown iff we are still the recorded active.

    Returns True when the table was cleared. A successor that already flipped
    the table (its pid is now active) is left untouched -- we only retract our
    own claim, so a clean exit never blanks a newer daemon's route. Demotes our
    entry to ``previous`` so an in-flight client mid-resolve still has a fallback
    if the successor is not yet listening.
    """
    with _routing_lock(config_dir):
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


def reap_stale_active(
    config_dir: str | os.PathLike[str],
    *,
    service: str | None = None,
    listening: Callable[[str, int], bool] | None = None,
    pid_alive: Callable[[int | None], bool] | None = None,
) -> dict:
    """Reap a stale active endpoint under the routing-table mutation lock."""
    with _routing_lock(config_dir):
        return _reap_stale_active_unlocked(
            config_dir,
            service=service,
            listening=listening,
            pid_alive=pid_alive,
        )


def _reap_stale_active_unlocked(
    config_dir: str | os.PathLike[str],
    *,
    service: str | None = None,
    listening: Callable[[str, int], bool] | None = None,
    pid_alive: Callable[[int | None], bool] | None = None,
) -> dict:
    """Dead-port watchdog: proactively retire an advertised-but-dead active endpoint.

    :func:`read_active_endpoint` heals a stale ``active`` only for the one reader
    that happens to probe it. This is the **proactive** counterpart a watchdog
    loop runs on a schedule: when the routing table's ``active`` names a port
    with **no listener** *and* a pid that is **not alive**, the endpoint is
    advertised-but-dead (exactly the state that wedged the review pipeline for
    days). It is retired here -- ``previous`` is promoted to ``active`` when it is
    itself live, otherwise the table is cleared so consumers fall back to the
    static config -- and a durable lifecycle record is written.

    A ``live-pid-but-no-listener`` active (a daemon mid-startup) is deliberately
    left alone, matching :func:`read_active_endpoint`'s conservatism: the pid is
    the authority for "still coming up", the listener for "actually serving".

    ``listening(host, port)`` and ``pid_alive(pid)`` are injectable for testing
    and default to the module probes. Returns a diagnosis dict
    ``{"reaped": bool, "reason": str, "dead_port": int|None,
    "promoted_port": int|None}``. Fail-open on IO.
    """
    _listen = listening or _listening
    _alive = pid_alive or _pid_alive
    result: dict = {"reaped": False, "reason": "", "dead_port": None,
                    "promoted_port": None}
    try:
        data = read_table(config_dir)
        if not data or not isinstance(data.get("active"), dict):
            result["reason"] = "no active endpoint"
            return result
        active = Endpoint.from_dict(data["active"])
        if active is None:
            result["reason"] = "active endpoint unparseable"
            return result
        if _listen(active.client_host, active.port):
            result["reason"] = "active endpoint is listening"
            return result
        # No listener. Reap only on POSITIVE evidence of death: a recorded pid
        # that is confirmed not alive. A missing pid means we cannot prove the
        # daemon is dead (it may have published without one, or be mid-startup),
        # and a live pid means it may still be binding -- in both cases leave it
        # alone, matching read_active_endpoint's conservatism (the listener is
        # the authority for "serving", the pid for "still coming up").
        if not active.pid:
            result["reason"] = "no listener but pid unknown -- not reaping (unconfirmed)"
            return result
        if _alive(active.pid):
            result["reason"] = "no listener but pid alive (starting)"
            return result

        # Advertised-but-dead: retire it. Promote a live `previous` if we have
        # one, else clear the table so readers fall back to config.
        result["dead_port"] = active.port
        prev_raw = data.get("previous")
        prev = Endpoint.from_dict(prev_raw) if isinstance(prev_raw, dict) else None
        promoted = False
        if prev is not None and prev.port != active.port and \
                _listen(prev.client_host, prev.port):
            _publish_active_unlocked(
                config_dir, bind=prev.bind, port=prev.port, pid=prev.pid,
                version=prev.version, demote_existing=False,
            )
            promoted = True
            result["promoted_port"] = prev.port
            result["reason"] = (
                f"reaped dead active {active.client_host}:{active.port}; "
                f"promoted previous {prev.client_host}:{prev.port}"
            )
        else:
            _atomic_write(
                routing_table_path(config_dir),
                {"epoch": datetime.now(timezone.utc).isoformat()},
            )
            result["reason"] = (
                f"reaped dead active {active.client_host}:{active.port}; "
                f"no live previous, cleared table (readers fall back to config)"
            )
        result["reaped"] = True
        log.warning(
            "Dead-port watchdog reaped advertised-but-dead active %s:%d "
            "(pid %s); %s", active.client_host, active.port, active.pid,
            "promoted previous" if promoted else "cleared table",
        )
        _emit_reap(config_dir, service, active, result)
        return result
    except Exception as exc:  # noqa: BLE001 -- watchdog is best-effort, never fatal
        result["reason"] = f"error: {exc}"
        return result


def _emit_reap(
    config_dir: str | os.PathLike[str],
    service: str | None,
    dead: Endpoint,
    result: dict,
) -> None:
    """Write a durable lifecycle record for a watchdog reap (fail-open)."""
    try:
        from zdd import lifecycle

        lifecycle.record(
            config_dir,
            lifecycle.WATCHDOG_REAP,
            service=service,
            outcome=lifecycle.OK,
            port=dead.port,
            version=dead.version,
            detail={"dead_pid": dead.pid,
                    "promoted_port": result.get("promoted_port")},
        )
    except Exception:  # noqa: BLE001 -- lifecycle logging is best-effort
        pass
