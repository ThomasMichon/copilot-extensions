"""Native Linux admission and bounded CLI I/O for offline clean-room jobs."""

from __future__ import annotations

import json
import math
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from offline_job import Job, Limits, PlanRefusal

PROC_CGROUP = Path("/proc/self/cgroup")
CGROUP_ROOT = Path("/sys/fs/cgroup")


@dataclass(frozen=True)
class Budget:
    end: float
    work_end: float

    def remaining(
        self, *, cleanup: bool = False, maximum: float | None = None
    ) -> float:
        remaining = (self.end if cleanup else self.work_end) - time.monotonic()
        if remaining <= 0:
            raise PlanRefusal("offline-job deadline exhausted")
        return remaining if maximum is None else min(remaining, maximum)


class CommandFailure(PlanRefusal):
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        super().__init__("Docker command failed; inspect retained command output")


class Runner(Protocol):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout: float,
        output: Path | None = None,
    ) -> str: ...


def read_limited(path: Path, limit: int = 65536) -> str:
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise PlanRefusal("control output exceeded its bound")
    return content.decode("utf-8")


def registered_home() -> Path:
    if sys.platform != "linux":
        raise PlanRefusal("offline-job owner supports Linux only")
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)


def private_directory(path: Path) -> None:
    if (
        path.anchor != "/"
        or ".." in path.parts
        or any(part.is_symlink() for part in (path, *path.parents))
    ):
        raise PlanRefusal("private directory has indirect ancestry")
    info = path.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise PlanRefusal("private directory custody differs")


def write_json(path: Path, value: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def admit_host(job: Job, expected: Limits) -> Limits:
    if (
        sys.platform != "linux"
        or os.getuid() != os.geteuid()
        or os.getgid() != os.getegid()
        or os.getuid() != job.uid
        or os.getgid() != job.gid
        or not job.uid
        or not job.gid
    ):
        raise PlanRefusal("requires the selected ordinary Linux caller")
    lines = read_limited(PROC_CGROUP, 4096).splitlines()
    if len(lines) != 1 or not lines[0].startswith("0::/"):
        raise PlanRefusal("requires an existing cgroup-v2 host boundary")
    relative = Path(lines[0][3:]).relative_to("/")
    if ".." in relative.parts:
        raise PlanRefusal("host cgroup ancestry differs")
    group = CGROUP_ROOT / relative
    try:
        memory = int(read_limited(group / "memory.max", 128).strip())
        processes = int(read_limited(group / "pids.max", 128).strip())
        quota, period = map(int, read_limited(group / "cpu.max", 128).split())
        swap = read_limited(group / "memory.swap.max", 128).strip()
    except ValueError as error:
        raise PlanRefusal("host resource limits are not finite") from error
    if (
        memory <= 0
        or processes <= 0
        or quota <= 0
        or period <= 0
        or memory > expected.memory_bytes
        or processes > expected.processes
        or quota * 1000 > period * expected.cpu_millis
        or swap != "0"
    ):
        raise PlanRefusal("native host limits exceed the declared partition")
    return Limits(memory, (quota * 1000 + period - 1) // period, processes)


class Docker:
    """Synchronous one-job harness I/O, never called from an event loop."""

    def __init__(self, control: Path) -> None:
        if sys.platform != "linux":
            raise PlanRefusal("offline-job CLI owner supports Linux only")
        executable = shutil.which("docker", path=os.defpath)
        if executable is None:
            raise PlanRefusal("existing system Docker CLI is unavailable")
        self.executable = executable
        self.control = control
        self.sequence = 0
        private_directory(control / "client-config")

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout: float,
        output: Path | None = None,
    ) -> str:
        if (
            arguments[:2] != ("docker", "--host")
            or len(arguments) < 3
            or not arguments[2].startswith("unix:///")
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise PlanRefusal("Docker invocation lacks the explicit local binding")
        self.sequence += 1
        stdout = output or self.control / f"control-{self.sequence:04d}.stdout"
        stderr = self.control / f"control-{self.sequence:04d}.stderr"
        out_descriptor = os.open(stdout, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(out_descriptor, "wb") as out:
            err_descriptor = os.open(
                stderr, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            with os.fdopen(err_descriptor, "wb") as err:
                result = subprocess.run(
                    (
                        self.executable,
                        "--config",
                        str(self.control / "client-config"),
                        *arguments[1:],
                    ),
                    stdout=out,
                    stderr=err,
                    timeout=timeout,
                    check=False,
                    env={
                        "HOME": str(registered_home()),
                        "PATH": os.defpath,
                        "LANG": "C.UTF-8",
                    },
                )
        if result.returncode:
            raise CommandFailure(result.returncode)
        return read_limited(stdout) if output is None else ""
