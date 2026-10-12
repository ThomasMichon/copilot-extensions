"""Private, atomic manager state and an exec-preserving Linux ownership lease."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

LEASE_ENV = "ZDD_SINGLETON_LEASE_FD"


class ManagerAlreadyRunning(RuntimeError):
    """The manager state directory already has an exclusive live owner."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_time: str
    boot_id: str

    @classmethod
    def parse(cls, value: object) -> ProcessIdentity:
        if not isinstance(value, dict):
            raise ValueError("process identity must be an object")
        pid, token, boot = value.get("pid"), value.get("start_time"), value.get("boot_id")
        if type(pid) is not int or pid <= 0:
            raise ValueError("process identity requires a positive integer pid")
        if not isinstance(token, str) or not token.isascii() or not token.isdigit():
            raise ValueError("process identity requires a numeric start token")
        if not isinstance(boot, str) or not boot.strip():
            raise ValueError("process identity requires a boot identifier")
        return cls(pid, token, boot)


@dataclass(frozen=True)
class ManagerState:
    owner: ProcessIdentity
    watched: ProcessIdentity
    phase: Literal["watching", "discovering"] = "watching"
    deadline: float | None = None
    exit_code: int = 1
    schema_version: int = 1

    @classmethod
    def parse(cls, value: object) -> ManagerState:
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
            raise ValueError("manager state must have an integer schema_version")
        if value["schema_version"] != 1:
            raise ValueError("unsupported manager state schema")
        phase = value.get("phase")
        if phase not in ("watching", "discovering"):
            raise ValueError("unknown manager phase")
        deadline = value.get("deadline")
        if phase == "discovering":
            if (
                not isinstance(deadline, (int, float)) or isinstance(deadline, bool)
                or not math.isfinite(deadline)
            ):
                raise ValueError("discovery requires a finite monotonic deadline")
        elif deadline is not None:
            raise ValueError("watching state cannot have a discovery deadline")
        code = value.get("exit_code")
        if type(code) is not int:
            raise ValueError("manager state requires an integer exit code")
        return cls(
            ProcessIdentity.parse(value.get("owner")),
            ProcessIdentity.parse(value.get("watched")), phase, deadline, code,
        )


class StateStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "manager.json"

    def read(self) -> ManagerState | None:
        try:
            contents = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return ManagerState.parse(json.loads(contents))

    def write(self, state: ManagerState) -> None:
        ManagerState.parse(asdict(state))
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=".manager-", dir=self.directory)
        temporary = Path(filename)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(state), handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if sys.platform == "linux":
                directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)


class ManagerLease:
    """Take ownership of the exec-transferred descriptor, including on failure."""

    def __init__(self, directory: Path) -> None:
        if sys.platform != "linux":
            raise NotImplementedError("singleton manager lease currently requires Linux")
        import fcntl

        inherited = os.environ.pop(LEASE_ENV, None)
        fd: int | None = None
        try:
            if inherited is not None:
                candidate_fd = int(inherited)
                os.fstat(candidate_fd)
                fd = candidate_fd
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            stat = directory.stat()
            if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
                raise PermissionError("manager state directory must be private to its owner")
            path = directory / "manager.lock"
            if fd is None:
                fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            else:
                descriptor, expected = os.fstat(fd), path.stat()
                if (descriptor.st_dev, descriptor.st_ino) != (expected.st_dev, expected.st_ino):
                    raise ValueError("inherited singleton lease does not match manager state")
                from .diagnostics import process_start_time

                saved = StateStore(directory).read()
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                if (
                    saved is None or saved.owner.pid != os.getpid()
                    or saved.owner.start_time != process_start_time(os.getpid())
                    or saved.owner.boot_id != boot_id
                ):
                    raise ValueError("inherited singleton lease requires same-process exec state")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.set_inheritable(fd, False)
            self.fd = fd
        except BaseException as exc:
            if fd is not None:
                os.close(fd)
            if isinstance(exc, BlockingIOError):
                raise ManagerAlreadyRunning(f"singleton manager already owns {directory}") from exc
            raise

    def close(self) -> None:
        os.close(self.fd)

    def prepare_exec(self) -> None:
        os.set_inheritable(self.fd, True)
