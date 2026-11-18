"""Inert plans for cached, credential-free Linux clean-room jobs.

This module never invokes Docker or creates files. A lifecycle owner must still
admit the caller, cached image, fresh name, actual limits and physical identity.
"""

from __future__ import annotations

import math
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class PlanRefusal(ValueError):
    """The requested offline job cannot preserve the declared boundary."""


@dataclass(frozen=True)
class Limits:
    memory_bytes: int
    cpu_millis: int
    processes: int

    def validate(self) -> None:
        if (
            any(
                type(value) is not int or value <= 0
                for value in (self.memory_bytes, self.cpu_millis, self.processes)
            )
            or self.memory_bytes < 6 * 1024**2
        ):
            raise PlanRefusal(
                "limits must be finite positive integers; memory >= 6 MiB"
            )


def validate_partition(*, aggregate: Limits, host: Limits, container: Limits) -> None:
    """Check declared arithmetic, not whether a host supervisor enforced it."""
    for limits in (aggregate, host, container):
        limits.validate()
    if any(
        outer < driver + child
        for outer, driver, child in (
            (aggregate.memory_bytes, host.memory_bytes, container.memory_bytes),
            (aggregate.cpu_millis, host.cpu_millis, container.cpu_millis),
            (aggregate.processes, host.processes, container.processes),
        )
    ):
        raise PlanRefusal("host and container limits exceed the aggregate budget")


@dataclass(frozen=True)
class Input:
    name: str
    source: Path

    @property
    def target(self) -> str:
        return f"/job/inputs/{self.name}"


@dataclass(frozen=True)
class Job:
    name: str
    image: str
    uid: int
    gid: int
    limits: Limits
    seconds: int
    cleanup_seconds: int
    inputs: tuple[Input, ...]
    command: tuple[str, ...]
    cache: Path | None = None


@dataclass(frozen=True)
class Plan:
    job: Job
    results: Path
    deadline_epoch: int
    docker: tuple[str, ...]
    create: tuple[str, ...]

    def exec_argv(self, container_id: str) -> tuple[str, ...]:
        if re.fullmatch(r"[a-f0-9]{64}", container_id) is None:
            raise PlanRefusal("execution requires a full physical container ID")
        return (
            *self.docker,
            "exec",
            "--workdir",
            "/job/out",
            container_id,
            *self.job.command,
        )


def _overlap(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def _source(path: Path, *, forbidden: tuple[Path, ...]) -> None:
    if (
        path.anchor != "/"
        or ".." in path.parts
        or any(character in str(path) for character in (",", '"', "\r", "\n", "\0"))
        or any(path == denied or denied.is_relative_to(path) for denied in forbidden)
    ):
        raise PlanRefusal("broad, indirect or unrepresentable mount source")
    try:
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise PlanRefusal("mount source has link indirection")
        mode = path.stat().st_mode
    except OSError as error:
        raise PlanRefusal("mount source is unavailable") from error
    if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
        raise PlanRefusal("mount source is not an ordinary file or directory")


def plan_job(
    job: Job,
    *,
    results: Path,
    host_home: Path,
    repository: Path,
    deadline_epoch: int,
    now_epoch: float,
    docker_socket: Path = Path("/var/run/docker.sock"),
) -> Plan:
    """Render a fixed plan without treating its declarations as native evidence."""
    job.limits.validate()
    if (
        re.fullmatch(r"cr-offline-[a-f0-9]{12}", job.name) is None
        or re.fullmatch(r"sha256:[a-f0-9]{64}", job.image) is None
        or any(type(value) is not int or value <= 0 for value in (job.uid, job.gid))
        or type(job.seconds) is not int
        or type(job.cleanup_seconds) is not int
        or not 1 <= job.cleanup_seconds < job.seconds <= 86400
    ):
        raise PlanRefusal("job identity, immutable image or finite wall budget differs")
    if (
        type(deadline_epoch) is not int
        or not math.isfinite(now_epoch)
        or not job.cleanup_seconds < deadline_epoch - now_epoch <= job.seconds
    ):
        raise PlanRefusal("deadline must retain the cleanup reserve inside the wall")
    for path in (host_home, repository, docker_socket):
        if path.anchor != "/" or ".." in path.parts:
            raise PlanRefusal("host boundaries must be absolute")
    try:
        host_home = host_home.resolve(strict=True)
        repository = repository.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PlanRefusal("host boundaries are unavailable") from error
    if not host_home.is_dir() or not repository.is_dir():
        raise PlanRefusal("home and repository boundaries must be directories")
    forbidden = (
        host_home,
        repository,
        docker_socket,
        Path("/run"),
        Path("/var/run"),
        Path("/var/lib/docker"),
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
    )
    _source(results, forbidden=forbidden)
    if _overlap(results, repository):
        raise PlanRefusal("writable root overlaps the repository")
    if not results.is_dir() or stat.S_IMODE(results.stat().st_mode) != 0o700:
        raise PlanRefusal("results must be an existing private directory")
    for name in ("owned-resources.json", "cr-report.json", "runner.log", "home", "tmp"):
        path = results / name
        if path.exists() or path.is_symlink():
            raise PlanRefusal("results already contain a reserved job object")
    if not 1 <= len(job.inputs) <= 16:
        raise PlanRefusal("offline jobs require a bounded explicit input roster")
    names: set[str] = set()
    sources: list[Path] = []
    mounts: list[str] = []
    for item in job.inputs:
        if (
            re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", item.name) is None
            or item.name in names
        ):
            raise PlanRefusal("input names must be distinct safe path components")
        names.add(item.name)
        _source(item.source, forbidden=forbidden)
        if _overlap(item.source, results):
            raise PlanRefusal("read-only inputs overlap writable results")
        sources.append(item.source)
        mounts.extend(
            ("--mount", f"type=bind,src={item.source},dst={item.target},readonly")
        )
    if job.cache is not None:
        _source(job.cache, forbidden=forbidden)
        if _overlap(job.cache, repository):
            raise PlanRefusal("writable root overlaps the repository")
        if (
            not job.cache.is_dir()
            or _overlap(job.cache, results)
            or any(_overlap(job.cache, source) for source in sources)
        ):
            raise PlanRefusal("cache must be a separate explicit directory")
        mounts.extend(("--mount", f"type=bind,src={job.cache},dst=/job/cache"))
    if (
        not job.command
        or any(not part or "\0" in part for part in job.command)
        or not PurePosixPath(job.command[0]).is_absolute()
    ):
        raise PlanRefusal("command must contain an explicit absolute executable")
    environment = {
        "HOME": "/job/out/home",
        "TMPDIR": "/job/out/tmp",
        "PATH": "/job/inputs/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "UV_NO_CONFIG": "1",
        "UV_OFFLINE": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_CACHE_DIR": "/job/cache" if job.cache is not None else "/job/out/cache",
    }
    # Docker's client configuration can inject these independently of host env.
    for scheme in ("HTTP", "HTTPS", "FTP", "NO", "ALL"):
        environment[f"{scheme}_PROXY"] = ""
        environment[f"{scheme.lower()}_proxy"] = ""
    docker = ("docker", "--host", f"unix://{docker_socket}")
    work_deadline = deadline_epoch - job.cleanup_seconds
    # Absolute expiry survives a slow create or loss of the host-side client.
    expiry = 'left=$(( $1 - $(/bin/date +%s) - 1 )); [ "$left" -gt 0 ] && exec /bin/sleep "$left"'
    create = (
        *docker,
        "run",
        "--detach",
        "--init",
        "--pull",
        "never",
        "--name",
        job.name,
        "--label",
        "copilot-extensions.clean-room=1",
        "--label",
        f"copilot-extensions.offline-job={job.name}",
        "--user",
        f"{job.uid}:{job.gid}",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--memory",
        str(job.limits.memory_bytes),
        "--memory-swap",
        str(job.limits.memory_bytes),
        "--cpus",
        f"{job.limits.cpu_millis // 1000}.{job.limits.cpu_millis % 1000:03d}",
        "--pids-limit",
        str(job.limits.processes),
        "--workdir",
        "/job/out",
        "--mount",
        f"type=bind,src={results},dst=/job/out",
        *mounts,
        *(
            part
            for key, value in environment.items()
            for part in ("--env", f"{key}={value}")
        ),
        "--entrypoint",
        "/bin/sh",
        job.image,
        "-c",
        expiry,
        "offline-expiry",
        str(work_deadline),
    )
    return Plan(job, results, deadline_epoch, docker, create)
