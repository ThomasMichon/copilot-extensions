"""Fixed Unix socket custody, independent of the advertised generation."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import stat
import tempfile

from agent_procutil._daemon_instance_identity import birth_identity, identity_state

from .config import installation_id


def socket_identity(path: str) -> tuple[int, int] | None:
    try:
        info = os.lstat(path)
        if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
            return info.st_dev, info.st_ino
    except OSError:
        pass
    return None


class UnixAlias:
    def __init__(self, root: Path, path: str) -> None:
        self.path = path
        self.record = root / "legacy-unix-owner.json"
        self.previous: dict | None = None

    def capture_previous(self, pid: int | None) -> None:
        """Retain a fixed-primary legacy owner's native incarnation before exit."""
        try:
            identity = socket_identity(self.path)
            if identity is not None and type(pid) is int and pid > 0:
                self.previous = {
                    "pid": pid, "birth": birth_identity(pid),
                    "device": identity[0], "inode": identity[1],
                }
        except (OSError, ValueError):
            pass

    def publish(self) -> None:
        identity = socket_identity(self.path)
        if identity is None:
            raise OSError("Fixed Unix socket identity unavailable")
        payload = {
            "schema": 1, "path": self.path, "installation": installation_id(),
            "pid": os.getpid(), "birth": birth_identity(os.getpid()),
            "device": identity[0], "inode": identity[1],
        }
        self.record.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, pending = tempfile.mkstemp(prefix=".unix-owner-", dir=self.record.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, self.record)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(pending)

    def read(self) -> dict | None:
        try:
            descriptor = os.open(self.record, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 4096
                ):
                    return None
                raw = stream.read(4097)
                if len(raw) > 4096:
                    return None
                payload = json.loads(raw)
            if (
                not isinstance(payload, dict) or type(payload.get("schema")) is not int
                or payload["schema"] != 1 or payload.get("path") != self.path
                or payload.get("installation") != installation_id()
                or type(payload.get("pid")) is not int or payload["pid"] <= 0
                or not isinstance(payload.get("birth"), str) or not payload["birth"]
                or any(type(payload.get(key)) is not int for key in ("device", "inode"))
                or socket_identity(self.path) != (payload["device"], payload["inode"])
            ):
                return None
            return payload
        except (OSError, ValueError):
            return None

    def owned_by_current(self) -> bool:
        payload = self.read()
        try:
            return bool(
                payload is not None and payload["pid"] == os.getpid()
                and payload["birth"] == birth_identity(os.getpid())
            )
        except (OSError, ValueError):
            return False

    def dead_identity(self) -> tuple[int, int] | None:
        payload = self.read()
        if payload is None and self.previous is not None and not os.path.lexists(self.record):
            previous = self.previous
            if socket_identity(self.path) == (previous["device"], previous["inode"]):
                payload = previous
        if payload is not None:
            try:
                if identity_state(payload["pid"], payload["birth"]) in {"dead", "reused"}:
                    return payload["device"], payload["inode"]
            except (OSError, ValueError):
                pass
        return None
