"""Publish bounded PSMux presentation options without starting its CLI."""

from __future__ import annotations

import json
from pathlib import Path
import re
import socket
import time

from .mux_observation import _data_dir, _namespace, _read, _remaining

_OPTIONS = frozenset({
    "@aw_ctx", "@aw_seg", "status", "status-interval", "status-left-length",
    "status-right-length", "status-left", "status-right",
})
_LIMIT = 65536


def _exchange(port: int, key: str, command: str, deadline: float) -> str:
    scheme = "AU" + "TH "
    request = (scheme + key + "\n" + command + "\n").encode("utf-8")
    if len(request) > _LIMIT:
        raise ValueError("Oversized native status command")
    with socket.create_connection(("127.0.0.1", port), timeout=_remaining(deadline)) as stream:
        stream.settimeout(_remaining(deadline))
        stream.sendall(request)
        stream.shutdown(socket.SHUT_WR)
        reply = bytearray()
        while True:
            stream.settimeout(_remaining(deadline))
            block = stream.recv(min(4096, _LIMIT + 1 - len(reply)))
            if not block:
                break
            reply.extend(block)
            if len(reply) > _LIMIT:
                raise ValueError("Oversized native status response")
    ack, separator, body = reply.partition(b"\n")
    if not separator or ack.removesuffix(b"\r") != b"OK":
        raise ValueError("Native authentication was not confirmed")
    text = body.decode("utf-8")
    return text.removesuffix("\n").removesuffix("\r")


def apply_psmux_status(
    session: str, incarnation: str, values: dict[str, str], *,
    timeout: float = 2.0, data_dir: Path | None = None,
) -> bool:
    """Write only presentation options to the observed native session.

    Every value is read back. An interrupted or changed target returns false,
    including partial publication; this never starts a provider or sends input.
    """
    if (
        not isinstance(session, str) or not 0 < len(session) <= 200
        or any(
            not (char.isalnum() or char in "_-." or (
                ord(char) > 127 and char.isprintable() and not char.isspace()
            ))
            for char in session
        )
        or session.startswith(".") or session.endswith(".")
        or not isinstance(incarnation, str)
        or not re.fullmatch(r"\$[0-9]+:[0-9]+", incarnation)
        or not isinstance(values, dict) or not values or values.keys() - _OPTIONS
        or any(
            not isinstance(value, str) or len(value) > 8192
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            for value in values.values()
        )
        or not 0 < timeout <= 5
    ):
        return False
    deadline = time.monotonic() + timeout
    try:
        root = data_dir if data_dir is not None else _data_dir()
        namespace = _namespace()
        base = (namespace + "__" if namespace else "") + session
        port_path, key_path = root / (base + ".port"), root / (base + ".key")
        port_raw, key_raw = _read(port_path), _read(key_path)
        port_text, key = port_raw.decode("ascii").strip(), key_raw.decode("utf-8").strip()
        if (
            not port_text.isdecimal() or not 0 < int(port_text) < 65536
            or not key or any(char in key for char in "\r\n\0")
        ):
            return False
        port = int(port_text)
        identity = 'display-message -p "#{session_name}\t#{session_id}:#{session_created}"'
        expected = session + "\t" + incarnation

        def current() -> bool:
            return (
                _read(port_path) == port_raw and _read(key_path) == key_raw
                and _exchange(port, key, identity, deadline) == expected
                and _read(port_path) == port_raw and _read(key_path) == key_raw
            )

        if not current():
            return False
        target = json.dumps(incarnation.partition(":")[0])
        for option, value in values.items():
            if not current():
                return False
            quoted = json.dumps(value, ensure_ascii=False)
            if _exchange(port, key, f"set-option -t {target} {option} {quoted}", deadline):
                return False
            if _exchange(port, key, f"show-options -v -t {target} {option}", deadline) != value:
                return False
        return current()
    except (OSError, ValueError, UnicodeError):
        return False
