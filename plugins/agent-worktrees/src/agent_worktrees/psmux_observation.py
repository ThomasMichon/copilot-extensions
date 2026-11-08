"""Bounded action-time PSMux observation without its CLI's startup cleanup."""

from __future__ import annotations

import os
import re
import socket
import stat
import time
from pathlib import Path

from . import windows_mux_processes
from .mux_list_result import MuxObservationUnavailable

_QUERY = b"display-message -p '#{session_name}:#{session_attached}:#{pid}'\n"
_LIMIT = 4096


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MuxObservationUnavailable("Native mux observation deadline exceeded")
    return min(1.0, remaining)


def _read(path: Path) -> str:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise MuxObservationUnavailable("Native mux registration is not a regular file")
    with path.open("rb") as stream:
        value = stream.read(_LIMIT + 1)
    if len(value) > _LIMIT:
        raise MuxObservationUnavailable("Native mux registration exceeds its byte budget")
    return value.decode("ascii").strip()


def _registrations(root: Path, servers: dict[int, str], deadline: float) -> dict:
    entries = []
    try:
        with os.scandir(root) as iterator:
            for entry in iterator:
                _remaining(deadline)
                if len(entries) >= _LIMIT:
                    raise MuxObservationUnavailable("Native mux registration budget exceeded")
                entries.append(entry.name)
    except FileNotFoundError:
        if servers:
            raise MuxObservationUnavailable("Live mux servers have no readable registrations") from None
        return {}
    result = {}
    for name in sorted(entries):
        if not name.endswith(".pid"):
            continue
        _remaining(deadline)
        value = _read(root / name)
        match = re.fullmatch(r"([1-9][0-9]*):([1-9][0-9]*)", value)
        if match is None or int(match[1]) not in servers:
            continue
        pid = int(match[1])
        if match[2] != servers[pid] or pid in result:
            raise MuxObservationUnavailable("Native mux registration identity is ambiguous")
        base = name[:-4]
        port = _read(root / (base + ".port"))
        key = _read(root / (base + ".key"))
        if not port.isdecimal() or not 0 < int(port) < 65536 or not re.fullmatch(r"[a-zA-Z0-9]{16,128}", key):
            raise MuxObservationUnavailable("Native mux registration is malformed")
        result[pid] = (base, int(port), key)
    if result.keys() != servers.keys():
        raise MuxObservationUnavailable("A live mux server is missing its registration")
    return result


def _query(port: int, key: str, deadline: float) -> tuple[str, int, int]:
    with socket.create_connection(("127.0.0.1", port), timeout=_remaining(deadline)) as connection:
        connection.settimeout(_remaining(deadline))
        connection.sendall(b"AUTH " + key.encode("ascii") + b"\n" + _QUERY)
        connection.shutdown(socket.SHUT_WR)
        output = bytearray()
        while True:
            connection.settimeout(_remaining(deadline))
            block = connection.recv(min(1024, _LIMIT + 1 - len(output)))
            if not block:
                break
            output.extend(block)
            if len(output) > _LIMIT:
                raise MuxObservationUnavailable("Native mux reply exceeds its byte budget")
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise MuxObservationUnavailable("Native mux reply is not UTF-8") from exc
    if len(lines) != 2 or lines[0] != "OK":
        raise MuxObservationUnavailable("Native mux authentication or reply is unconfirmed")
    name, separator, identity = lines[1].rpartition(":")
    name, count_separator, count = name.rpartition(":")
    if not separator or not count_separator or not name or not count.isdecimal() or not identity.isdecimal():
        raise MuxObservationUnavailable("Native mux reply is malformed")
    return name, int(count), int(identity)


def observe(*, timeout: float = 5) -> dict[str, int]:
    """Confirm names only with complete, stable native-server coverage.

    Registration absence alone cannot exclude an orphaned live server. Query
    every enumerated server, including warm servers, then recheck coverage and
    process births. No CLI, registry repair, pane command or cache is involved.
    """
    deadline = time.monotonic() + min(timeout, 5)
    root = Path(os.environ["PSMUX_DATA_DIR"]) if os.environ.get("PSMUX_DATA_DIR") else Path.home() / ".psmux"
    if not root.is_absolute():
        raise MuxObservationUnavailable("Native mux data directory must be absolute")
    try:
        servers = windows_mux_processes.servers()
        registrations = _registrations(root, servers, deadline)
        result = {}
        for pid, (base, port, key) in registrations.items():
            name, clients, reported_pid = _query(port, key, deadline)
            if reported_pid != pid or not (base == name or base.endswith("__" + name)):
                raise MuxObservationUnavailable("Native mux reply disagrees with its registration")
            if name in result:
                raise MuxObservationUnavailable("Native mux session name is ambiguous")
            result[name] = clients
        if (
            windows_mux_processes.servers() != servers
            or _registrations(root, servers, deadline) != registrations
        ):
            raise MuxObservationUnavailable("Native mux identity changed during observation")
        _remaining(deadline)
        return result
    except (OSError, ValueError) as exc:
        raise MuxObservationUnavailable("Native mux observation is unavailable") from exc
