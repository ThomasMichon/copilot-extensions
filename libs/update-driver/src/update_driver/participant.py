"""Ephemeral mutually authenticated local IPC, scoped to kernel lock custody.

The capability is a same-user cooperative execution witness, not a sandbox
against code already able to read the owning process's memory.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import threading
import time
import uuid
from contextvars import ContextVar
from collections.abc import MutableMapping
from multiprocessing.connection import Client, Listener
from pathlib import Path

ENVIRONMENT_KEY = "UPDATE_DRIVER_WITNESS"
MAX_FRAME = 16384
IPC_TIMEOUT = 2.0
_current_custody = ContextVar("update_driver_participant_custody", default=None)
_environment_guard = threading.Lock()


class ParticipantError(RuntimeError):
    """Borrowed transaction authority was not proved or is no longer live."""


def _send(connection, payload: dict) -> None:
    connection.send_bytes(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _receive(connection) -> dict:
    if not connection.poll(IPC_TIMEOUT):
        raise ParticipantError("participant IPC deadline expired")
    value = json.loads(connection.recv_bytes(MAX_FRAME))
    if not isinstance(value, dict):
        raise ParticipantError("malformed participant frame")
    return value


def _mac(key: bytes, label: str, nonce: str) -> str:
    return hmac.new(key, (label + nonce).encode("ascii"), hashlib.sha256).hexdigest()


def _connect(address, family):
    """PipeClient's internal busy-pipe retries must not extend our public deadline."""
    ready, abandoned = threading.Event(), threading.Event()
    guard = threading.Lock()
    result = []

    def connect():
        try:
            connection = Client(address, family=family, authkey=None)
            with guard:
                if abandoned.is_set():
                    connection.close()
                else:
                    result.append(connection)
        except Exception as error:
            result.append(error)
        finally:
            ready.set()

    thread = threading.Thread(target=connect, daemon=True)
    thread.start()
    if not ready.wait(IPC_TIMEOUT):
        with guard:
            abandoned.set()
            if result and not isinstance(result[0], Exception):
                result[0].close()
        raise ParticipantError("participant endpoint connection deadline expired")
    if isinstance(result[0], Exception):
        raise ParticipantError("participant endpoint unavailable") from result[0]
    return result[0]


class Witness:
    """Owned by Transaction; construction/close happen only under OperationLock."""

    def __init__(self, root: Path, request_id: str):
        self.root, self.request_id = str(root), request_id
        self.key = secrets.token_bytes(32)
        self.family = "AF_PIPE" if os.name == "nt" else "AF_UNIX"
        self.address = (
            rf"\\.\pipe\update-driver-{uuid.uuid4().hex}" if os.name == "nt"
            else str(root / f"w-{uuid.uuid4().hex[:12]}.sock")
        )
        # Authentication uses bounded, pickle-free frames below. The stdlib
        # Listener auth handshake can block indefinitely on an abandoned pipe.
        self.listener = Listener(self.address, family=self.family, authkey=None)
        self.active = threading.Event()
        self.active.set()
        self.guard = threading.Lock()
        self.connections = set()
        self.accept_thread = threading.Thread(target=self._accept, daemon=True)
        self.accept_thread.start()

    def environment(self) -> dict[str, str]:
        return {ENVIRONMENT_KEY: json.dumps({
            "version": 1, "address": self.address, "family": self.family,
            "key": self.key.hex(), "root": self.root, "request_id": self.request_id,
        }, separators=(",", ":"))}

    def _accept(self) -> None:
        while self.active.is_set():
            try:
                connection = self.listener.accept()
            except (OSError, EOFError):
                return
            except Exception:
                # An invalid authenticator does not retire the genuine witness.
                continue
            with self.guard:
                if not self.active.is_set():
                    connection.close()
                    return
                self.connections.add(connection)
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection) -> None:
        try:
            server_nonce = secrets.token_hex(32)
            _send(connection, {"nonce": server_nonce})
            proof = _receive(connection)
            client_nonce = proof.get("nonce")
            if (
                not isinstance(client_nonce, str) or len(client_nonce) != 64
                or not hmac.compare_digest(
                    str(proof.get("proof")), _mac(self.key, "client:", server_nonce),
                )
            ):
                return
            _send(connection, {"proof": _mac(self.key, "server:", client_nonce)})
            while self.active.is_set():
                # Idle joined participants remain legitimate until the enclosing
                # stage reasserts; only individual control exchanges are bounded.
                if not connection.poll(.1):
                    continue
                frame = _receive(connection)
                valid = (
                    frame.get("root") == self.root
                    and frame.get("request_id") == self.request_id
                    and isinstance(frame.get("nonce"), str)
                    and len(frame["nonce"]) == 64
                )
                if not valid or not self.active.is_set():
                    _send(connection, {"valid": False})
                    return
                _send(connection, {"valid": True, "nonce": frame["nonce"]})
                if frame.get("close"):
                    return
        except (OSError, EOFError, ValueError, ParticipantError):
            pass
        finally:
            with self.guard:
                self.connections.discard(connection)
            connection.close()

    def close(self) -> None:
        if not self.active.is_set():
            return
        deadline = time.monotonic() + IPC_TIMEOUT
        while self.connections and time.monotonic() < deadline:
            time.sleep(.01)
        unsettled = bool(self.connections)
        self.active.clear()
        try:
            wakeup = _connect(self.address, self.family)
            wakeup.close()
        except ParticipantError:
            pass
        self.listener.close()
        self.accept_thread.join(IPC_TIMEOUT)
        with self.guard:
            for connection in tuple(self.connections):
                connection.close()
            self.connections.clear()
        if unsettled:
            raise ParticipantError("transaction returned with unclosed participants")


class BorrowedCustody:
    """A live authenticated witness; assert again before each installation stage."""

    def __init__(
        self, witness: dict, root: Path, request_id: str | None = None,
        *, transport: str, source_environ: dict[str, str] | None = None,
    ):
        self._connection = None
        self._process_id = os.getpid()
        self._transport = transport
        self._source_environ = source_environ
        self._scope_token = None
        try:
            if (
                witness.get("version") != 1 or witness.get("root") != str(root)
                or witness.get("family") != ("AF_PIPE" if os.name == "nt" else "AF_UNIX")
                or (request_id is not None and witness.get("request_id") != request_id)
            ):
                raise ParticipantError("participant identity or execution space mismatch")
            self.root = root
            self.request_id = witness["request_id"]
            address, key = witness["address"], bytes.fromhex(witness["key"])
            if len(key) != 32:
                raise ParticipantError("malformed participant authenticator")
            if os.name == "nt":
                if not address.startswith(r"\\.\pipe\update-driver-"):
                    raise ParticipantError("participant endpoint is not a local driver pipe")
            elif Path(address).parent != root or not Path(address).name.startswith("w-"):
                raise ParticipantError("participant endpoint is not in the execution space")
            self._connection = _connect(address, witness["family"])
            challenge = _receive(self._connection)
            nonce = challenge.get("nonce")
            if not isinstance(nonce, str) or len(nonce) != 64:
                raise ParticipantError("malformed participant challenge")
            client_nonce = secrets.token_hex(32)
            _send(self._connection, {
                "nonce": client_nonce, "proof": _mac(key, "client:", nonce),
            })
            response = _receive(self._connection)
            if not hmac.compare_digest(
                str(response.get("proof")), _mac(key, "server:", client_nonce),
            ):
                raise ParticipantError("participant owner authentication failed")
            self.assert_owned()
        except Exception as error:
            self.close()
            if isinstance(error, ParticipantError):
                raise
            raise ParticipantError("participant witness unavailable or unauthenticated") from error

    def assert_owned(self) -> None:
        if self._process_id != os.getpid():
            raise ParticipantError("participant custody belongs to a different process")
        if self._connection is None:
            raise ParticipantError("participant witness is closed")
        nonce = secrets.token_hex(32)
        try:
            _send(self._connection, {
                "root": str(self.root), "request_id": self.request_id, "nonce": nonce,
            })
            response = _receive(self._connection)
            if response.get("valid") is not True or response.get("nonce") != nonce:
                raise ParticipantError("participant owner refused custody")
        except (OSError, EOFError, ValueError) as error:
            raise ParticipantError("participant owner lost custody") from error

    def close(self) -> None:
        try:
            if self._scope_token is not None:
                _current_custody.reset(self._scope_token)
                self._scope_token = None
        finally:
            self._transport = None
            self._source_environ = None
            if self._connection is not None:
                connection, self._connection = self._connection, None
                connection.close()

    def __enter__(self):
        if self._scope_token is not None:
            raise ParticipantError("participant custody is already scoped")
        self.assert_owned()
        transport = self._transport
        if transport is None:
            raise ParticipantError("participant transport is no longer available")
        with _environment_guard:
            for environment in (os.environ, self._source_environ):
                if (
                    isinstance(environment, MutableMapping)
                    and environment.get(ENVIRONMENT_KEY) == transport
                ):
                    environment.pop(ENVIRONMENT_KEY, None)
        self._scope_token = _current_custody.set((self, transport))
        self._transport = None
        self._source_environ = None
        return self

    def __exit__(self, *_exc):
        self.close()


def participant(
    *, root: Path | None = None, request_id: str | None = None, witness: str | None = None,
    _source_environ: dict[str, str] | None = None,
) -> BorrowedCustody:
    """Require verified inherited custody; a missing witness always refuses."""
    # Standalone import is also used by the isolated private runner shim.
    root = Path(root) if root is not None else Path.home() / ".copilot-extensions" / "update-driver"
    value = witness if witness is not None else os.environ.get(ENVIRONMENT_KEY)
    try:
        if not value or len(value) > MAX_FRAME:
            raise ParticipantError("participant witness missing or oversized")
        body = json.loads(value)
        if not isinstance(body, dict):
            raise ParticipantError("malformed participant witness")
        return BorrowedCustody(
            body, root, request_id, transport=value, source_environ=_source_environ,
        )
    except (TypeError, ValueError, KeyError) as error:
        raise ParticipantError("malformed participant witness") from error


def borrowed_custody(
    *, root: Path | None = None, environ: dict[str, str] | None = None,
) -> BorrowedCustody | None:
    """Absent means ordinary caller; present but invalid is a hard failure."""
    value = (os.environ if environ is None else environ).get(ENVIRONMENT_KEY)
    if value is None:
        return None
    return participant(root=root, witness=value, _source_environ=environ)


def participant_environment() -> dict[str, str]:
    """Export a reasserted scoped witness only for an explicit trusted update handoff."""
    current = _current_custody.get()
    if current is None:
        return {}
    custody, transport = current
    if custody._process_id != os.getpid():
        return {}
    custody.assert_owned()
    return {ENVIRONMENT_KEY: transport}
