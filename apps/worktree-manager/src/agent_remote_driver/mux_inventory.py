"""Bounded action-time PSMux observation without its CLI's startup cleanup."""

from __future__ import annotations

import os
import re
import socket
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from . import windows_mux_processes
from .mux_observation_types import ObservationUnavailable as MuxObservationUnavailable

_QUERY = b"display-message -p '#{session_name}:#{session_attached}:#{pid}'\n"
_MEMBERSHIP_QUERY = (
    b"display-message -p '#{session_name}:#{session_attached}:#{pid}:#{session_id}:#{session_created}'\n"
)
_CLIENT_QUERY = b'list-clients -F "C\t#{session_name}"\n'
_LIMIT = 4096
_T = TypeVar("_T")


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


def _registrations(
    root: Path, servers: dict[int, str], deadline: float,
) -> dict[str, tuple[int, str, tuple[int, str] | None]]:
    if not servers:
        return {}
    entries: list[str] = []
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
    bases = {name.rsplit(".", 1)[0] for name in entries if name.endswith((".port", ".pid"))}
    for base in sorted(bases):
        _remaining(deadline)
        identity = None
        try:
            value = _read(root / (base + ".pid"))
        except FileNotFoundError:
            pass
        else:
            match = re.fullmatch(r"([1-9][0-9]*):([1-9][0-9]*)", value)
            if match is None:
                raise MuxObservationUnavailable("Native mux registration identity is malformed")
            pid = int(match[1])
            if pid not in servers:
                continue
            if match[2] != servers[pid]:
                raise MuxObservationUnavailable("Native mux registration identity is ambiguous")
            identity = (pid, match[2])
        port = _read(root / (base + ".port"))
        key = _read(root / (base + ".key"))
        if not port.isdecimal() or not 0 < int(port) < 65536 or not re.fullmatch(r"[a-zA-Z0-9]{16,128}", key):
            raise MuxObservationUnavailable("Native mux registration is malformed")
        result[base] = (int(port), key, identity)
    return result


def _reply(port: int, key: str, deadline: float, query: bytes) -> str:
    with socket.create_connection(("127.0.0.1", port), timeout=_remaining(deadline)) as connection:
        connection.settimeout(_remaining(deadline))
        scheme = b"AU" + b"TH "
        connection.sendall(scheme + key.encode("ascii") + b"\n" + query)
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
    if not output.startswith((b"OK\n", b"OK\r\n")) or not lines or lines[0] != "OK":
        raise MuxObservationUnavailable("Native mux authentication or reply is unconfirmed")
    return "\n".join(lines[1:])


def _query(port: int, key: str, deadline: float) -> tuple[str, int, int]:
    reply = _reply(port, key, deadline, _QUERY)
    name, separator, identity = reply.rpartition(":")
    name, count_separator, count = name.rpartition(":")
    if (
        "\n" in reply or not separator or not count_separator or not name
        or not count.isdecimal() or not identity.isdecimal()
    ):
        raise MuxObservationUnavailable("Native mux reply is malformed")
    return name, int(count), int(identity)


def _query_membership(port: int, key: str, deadline: float) -> tuple[str, int, int, str]:
    initial_identity = _reply(port, key, deadline, _MEMBERSHIP_QUERY)
    client_reply = _reply(port, key, deadline, _CLIENT_QUERY)
    identity_reply = _reply(port, key, deadline, _MEMBERSHIP_QUERY)
    if sum(
        len(reply.encode("utf-8")) for reply in (initial_identity, client_reply, identity_reply)
    ) > _LIMIT:
        raise MuxObservationUnavailable("Native mux membership exceeds its byte budget")
    clients = client_reply.splitlines()
    fields = identity_reply.rsplit(":", 4)
    if len(fields) != 5:
        raise MuxObservationUnavailable("Native mux membership is malformed")
    name, attached, pid, session, created = fields
    if (
        initial_identity != identity_reply or not name or not attached.isdecimal() or not pid.isdecimal()
        or re.fullmatch(r"\$[0-9]+", session) is None or not created.isdecimal()
        or any(line.split("\t") != ["C", name] for line in clients)
        or bool(int(attached)) != bool(clients)
    ):
        raise MuxObservationUnavailable("Native mux membership is unconfirmed")
    return name, len(clients), int(pid), f"{session}:{created}"


def _observe(
    query: Callable[[int, str, float], tuple[str, int, int, _T]], *,
    timeout: float, data_dir: Path | None, parallel: bool = False,
) -> dict[str, tuple[str, int, _T]]:
    deadline = time.monotonic() + min(timeout, 5)
    root = data_dir if data_dir is not None else (
        Path(os.environ["PSMUX_DATA_DIR"]) if os.environ.get("PSMUX_DATA_DIR") else Path.home() / ".psmux"
    )
    if not root.is_absolute():
        raise MuxObservationUnavailable("Native mux data directory must be absolute")
    try:
        servers = windows_mux_processes.servers()
        registrations = _registrations(root, servers, deadline)
        result = {}
        covered = set()

        def read(
            item: tuple[str, tuple[int, str, tuple[int, str] | None]],
        ) -> tuple[str, tuple[str, int, int, _T] | None]:
            base, (port, key, identity) = item
            try:
                return base, query(port, key, deadline)
            except (OSError, MuxObservationUnavailable):
                if identity is not None:
                    raise
                # Stale legacy hints cannot hide an uncovered live server below.
                return base, None

        items = list(registrations.items())
        if parallel and items:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
                replies = list(pool.map(read, items))
        else:
            replies = list(map(read, items))
        for base, reply in replies:
            if reply is None:
                continue
            name, clients, reported_pid, detail = reply
            identity = registrations[base][2]
            if (
                reported_pid not in servers
                or identity is not None and identity != (reported_pid, servers[reported_pid])
                or not (base == name or base.endswith("__" + name))
            ):
                raise MuxObservationUnavailable("Native mux reply disagrees with its registration")
            if reported_pid in covered:
                raise MuxObservationUnavailable("Native mux registration identity is ambiguous")
            covered.add(reported_pid)
            result[base] = (name, clients, detail)
        if covered != servers.keys():
            raise MuxObservationUnavailable("A live mux server is missing its registration")
        if (
            windows_mux_processes.servers() != servers
            or _registrations(root, servers, deadline) != registrations
        ):
            raise MuxObservationUnavailable("Native mux identity changed during observation")
        _remaining(deadline)
        return result
    except (OSError, ValueError) as exc:
        raise MuxObservationUnavailable("Native mux observation is unavailable") from exc


def observe_registrations(
    *, timeout: float = 5, data_dir: Path | None = None,
) -> dict[str, tuple[str, int]]:
    """Keep legacy name/count reads and complete native coverage unchanged."""
    def query(port: int, key: str, deadline: float) -> tuple[str, int, int, None]:
        name, clients, pid = _query(port, key, deadline)
        return name, clients, pid, None

    return {
        base: (name, clients)
        for base, (name, clients, _) in _observe(
            query, timeout=timeout, data_dir=data_dir,
        ).items()
    }


def observe_memberships(
    *, timeout: float = 1, data_dir: Path | None = None,
) -> dict[str, tuple[str, int, str]]:
    """Fence real client rows with one covered finite batch of identity reads."""
    return _observe(
        _query_membership, timeout=timeout, data_dir=data_dir, parallel=True,
    )


def observe(
    *, timeout: float = 5, data_dir: Path | None = None, qualified: bool = False,
) -> dict[str, int]:
    """Preserve legacy name counts and optional qualified registration keys."""
    result = {}
    for base, (name, clients) in observe_registrations(timeout=timeout, data_dir=data_dir).items():
        identity = base if qualified else name
        if identity in result:
            raise MuxObservationUnavailable("Native mux session name is ambiguous")
        result[identity] = clients
    return result
