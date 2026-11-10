"""Launch-scoped, live caller admission; no durable receipt or replay authority."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

from .osutil import process_start_time

CONTEXT_ENV = "AGENT_DRIVER_STARTUP_ADMISSION"
_LIMIT = 1024 * 1024
_REASON_LIMIT = 512


def _bounded(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _REASON_LIMIT else text[: _REASON_LIMIT - 3] + "..."


def refusal_reason(exc: BaseException) -> str:
    """A bounded, single-line description of why the caller refused admission."""
    message = str(exc)
    return _bounded(f"{type(exc).__name__}: {message}" if message else type(exc).__name__)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate startup admission field")
        result[key] = value
    return result


def _receive(connection: socket.socket) -> dict[str, Any]:
    def read(size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = connection.recv(size - len(chunks))
            if not chunk:
                raise EOFError("Startup admission channel closed")
            chunks.extend(chunk)
        return bytes(chunks)

    size = struct.unpack("!I", read(4))[0]
    if not 0 < size <= _LIMIT:
        raise ValueError("Unsupported startup admission frame")
    value = json.loads(read(size), object_pairs_hook=_unique)
    if type(value) is not dict:
        raise ValueError("Unsupported startup admission document")
    return value


def _send(connection: socket.socket, value: dict[str, Any]) -> None:
    data = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > _LIMIT:
        raise ValueError("Startup admission frame exceeds its bound")
    connection.sendall(struct.pack("!I", len(data)) + data)


class StartupAdmissionServer:
    """Only the retained Host may ask its original live caller to check effects."""

    def __init__(
        self, check: Callable[[dict[str, Any]], None], *,
        environment_keys: tuple[str, ...], timeout: float,
    ) -> None:
        birth = process_start_time(os.getpid())
        if birth is None:
            raise ValueError("Startup admission requires caller birth identity")
        self._check = check
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(0.1)
        self._deadline = time.monotonic() + timeout
        self._nonce = secrets.token_hex(32)
        self._environment_keys = environment_keys
        self._connection: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self.error: Exception | None = None
        self.context = json.dumps({
            "version": 1, "port": self._listener.getsockname()[1],
            "nonce": self._nonce, "parent_pid": os.getpid(), "parent_birth": birth,
            "environment_keys": list(environment_keys),
        }, separators=(",", ":"))

    def start(self, process: Any) -> None:
        birth = process_start_time(process.pid)
        if birth is None:
            self.close()
            raise ValueError("Startup admission requires retained Host birth identity")
        self._thread = threading.Thread(target=self._serve, args=(process, birth), daemon=True)
        self._thread.start()

    def _serve(self, process: Any, birth: str) -> None:
        try:
            while not self._closed.is_set() and time.monotonic() < self._deadline:
                if process.poll() is not None:
                    return
                try:
                    self._connection, _address = self._listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            with self._connection as connection:
                connection.settimeout(max(0.001, self._deadline - time.monotonic()))
                while not self._closed.is_set() and time.monotonic() < self._deadline:
                    try:
                        value = _receive(connection)
                    except EOFError:
                        return
                    if (
                        set(value) != {
                            "version", "nonce", "host_pid", "parent_pid",
                            "argv", "cwd", "session_id", "environment",
                        }
                        or type(value["version"]) is not int or value["version"] != 1
                        or type(value["nonce"]) is not str
                        or not hmac.compare_digest(value["nonce"], self._nonce)
                        or type(value["host_pid"]) is not int or value["host_pid"] != process.pid
                        or type(value["parent_pid"]) is not int or value["parent_pid"] != os.getpid()
                        or process.poll() is not None or process_start_time(process.pid) != birth
                        or type(value["argv"]) is not list
                        or not all(type(arg) is str for arg in value["argv"])
                        or type(value["cwd"]) is not str or type(value["session_id"]) is not str
                        or type(value["environment"]) is not dict
                        or set(value["environment"]) != set(self._environment_keys)
                        or not all(v is None or type(v) is str for v in value["environment"].values())
                    ):
                        raise ValueError("Startup admission Host or request changed")
                    try:
                        self._check(value)
                    except Exception as exc:
                        self.error = exc
                        _send(connection, {
                            "version": 1, "accepted": False, "reason": refusal_reason(exc),
                        })
                        return
                    _send(connection, {"version": 1, "accepted": True})
        except (OSError, ValueError) as exc:
            if not self._closed.is_set():
                self.error = exc
        finally:
            self.close()

    def close(self) -> None:
        self._closed.set()
        self._listener.close()
        if self._connection is not None:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._connection.close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)


class StartupAdmissionClient:
    def __init__(self, raw: str) -> None:
        value = json.loads(raw, object_pairs_hook=_unique)
        if (
            type(value) is not dict or set(value) != {
                "version", "port", "nonce", "parent_pid", "parent_birth", "environment_keys",
            }
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["port"]) is not int or not 0 < value["port"] < 65536
            or type(value["nonce"]) is not str or len(value["nonce"]) != 64
            or type(value["parent_pid"]) is not int or value["parent_pid"] != os.getppid()
            or type(value["parent_birth"]) is not str
            or process_start_time(os.getppid()) != value["parent_birth"]
            or type(value["environment_keys"]) is not list
            or not all(type(key) is str and key for key in value["environment_keys"])
            or len(set(value["environment_keys"])) != len(value["environment_keys"])
        ):
            raise ValueError("Unsupported startup admission caller")
        self._context = value
        self._connection = socket.create_connection(("127.0.0.1", value["port"]), timeout=15)

    def check(
        self, argv: list[str], cwd: str | None, session_id: str, environment: dict[str, str],
    ) -> None:
        value = self._context
        if (
            os.getppid() != value["parent_pid"]
            or process_start_time(value["parent_pid"]) != value["parent_birth"]
        ):
            raise ValueError("Original startup admission caller changed")
        _send(self._connection, {
            "version": 1, "nonce": value["nonce"],
            "host_pid": os.getpid(), "parent_pid": os.getppid(),
            "argv": argv, "cwd": cwd or os.getcwd(), "session_id": session_id,
            "environment": {key: environment.get(key) for key in value["environment_keys"]},
        })
        result = _receive(self._connection)
        if (
            set(result) not in ({"version", "accepted"}, {"version", "accepted", "reason"})
            or type(result["version"]) is not int or result["version"] != 1
            or type(result["accepted"]) is not bool
        ):
            raise ValueError("Original caller refused startup admission")
        if result["accepted"] is not True:
            reason = result.get("reason")
            if type(reason) is str and reason:
                raise ValueError(
                    f"Original caller refused startup admission: {_bounded(reason)}"
                )
            raise ValueError("Original caller refused startup admission")

    def close(self) -> None:
        self._connection.close()
