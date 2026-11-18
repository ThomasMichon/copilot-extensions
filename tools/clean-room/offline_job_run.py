"""Opt-in synchronous Linux owner; importing this module performs no effects."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from offline_job import Job, Limits, PlanRefusal, _overlap, plan_job, validate_partition
from offline_job_host import (
    Budget,
    CommandFailure,
    Docker,
    Runner,
    admit_host,
    private_directory,
    registered_home,
    write_json,
)
from offline_job_inspect import (
    object_response,
    require_configuration,
    require_owned,
    require_running,
    require_stopped,
)


@dataclass
class RunReport:
    name: str
    image: str
    deadline_epoch: int
    observed_host: Limits
    result: str = "fail"
    stage: str = "admission"
    container_id: str | None = None
    creation_attempted: bool = False
    configuration_verified: bool = False
    execution_completed: bool = False
    execution_exit_code: int | None = None
    owner_stop_verified: bool = False
    problem: str | None = None
    cleanup_problem: str | None = None
    elapsed_seconds: float = 0


def run_job(
    job: Job,
    *,
    results: Path,
    control: Path,
    repository: Path,
    deadline_epoch: int,
    aggregate: Limits,
    host: Limits,
    docker_socket: Path = Path("/var/run/docker.sock"),
    runner: Runner | None = None,
) -> RunReport:
    """Retain a fresh owned attempt; never remove containers, data or old attempts.

    The caller owns authorization, its outer finite lifetime, input provenance
    and independent postflight. This owner's stop check is not that postflight.
    """
    started = time.monotonic()
    wall = time.time()
    validate_partition(aggregate=aggregate, host=host, container=job.limits)
    plan = plan_job(
        job,
        results=results,
        host_home=registered_home(),
        repository=repository,
        deadline_epoch=deadline_epoch,
        now_epoch=wall,
        docker_socket=docker_socket,
    )
    budget = Budget(
        started + deadline_epoch - wall,
        started + deadline_epoch - wall - job.cleanup_seconds,
    )
    observed_host = admit_host(job, host)
    private_directory(results)
    if control.anchor != "/" or ".." in control.parts or control.is_symlink():
        raise PlanRefusal("control directory has indirect ancestry")
    private_directory(control.parent)
    roots = [results, repository.resolve(), *(item.source for item in job.inputs)]
    if job.cache is not None:
        roots.append(job.cache)
    if any(_overlap(control, root) for root in roots):
        raise PlanRefusal("control directory overlaps a payload bind or repository")
    budget.remaining()
    control.mkdir(mode=0o700)  # Atomic reservation; existing attempts never replay.
    report = RunReport(job.name, job.image, deadline_epoch, observed_host)
    active: Runner | None = runner
    mutation_unknown = False
    primary_pass = False

    def inspect(physical: str, *, cleanup: bool = False) -> dict[str, Any]:
        assert active is not None
        return object_response(
            active.run(
                (
                    *plan.docker,
                    "container",
                    "inspect",
                    "--format",
                    "{{json .}}",
                    physical,
                ),
                timeout=budget.remaining(cleanup=cleanup, maximum=5 if cleanup else 10),
            )
        )

    try:
        (control / "client-config").mkdir(mode=0o700)
        active = active or Docker(control)
        write_json(
            control / "owned-resources.json",
            {
                "schema": "clean-room.offline-job/v1",
                "name": job.name,
                "image": job.image,
                "uid": job.uid,
                "gid": job.gid,
                "deadline_epoch": deadline_epoch,
                "results": str(results),
                "host_limits": asdict(host),
                "container_limits": asdict(job.limits),
            },
        )
        report.stage = "fresh-name"
        collision = active.run(
            (
                *plan.docker,
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                f"name=^/{job.name}$",
                "--format",
                "{{.ID}}",
            ),
            timeout=budget.remaining(maximum=10),
        )
        if collision.strip():
            raise PlanRefusal("container name already exists; no adoption")
        report.stage = "cached-image"
        image = object_response(
            active.run(
                (*plan.docker, "image", "inspect", "--format", "{{json .}}", job.image),
                timeout=budget.remaining(maximum=10),
            )
        )
        if (
            image["Id"] != job.image
            or image["Config"].get("Volumes") not in (None, {})
            or image["Config"].get("Healthcheck") not in (None, {"Test": ["NONE"]})
        ):
            raise PlanRefusal("cached image identity or implicit actors differ")
        for name in ("home", "tmp"):
            (results / name).mkdir(mode=0o700)
        report.stage = "create"
        arguments = list(plan.create)
        offset = len(plan.docker)
        if arguments[offset : offset + 2] != ["run", "--detach"]:
            raise PlanRefusal("unsupported planning command shape")
        arguments[offset : offset + 2] = ["create"]
        report.creation_attempted = True
        mutation_unknown = True
        physical = active.run(
            tuple(arguments), timeout=budget.remaining(maximum=10)
        ).strip()
        if re.fullmatch(r"[a-f0-9]{64}", physical) is None:
            raise PlanRefusal("creation did not return a full physical ID")
        report.container_id = physical
        mutation_unknown = False
        write_json(control / "physical-id.json", {"container_id": physical})
        snapshot = inspect(physical)
        require_owned(plan, physical, snapshot)
        require_configuration(plan, snapshot)
        require_stopped(snapshot)
        report.configuration_verified = True
        report.stage = "start"
        mutation_unknown = True
        active.run(
            (*plan.docker, "container", "start", physical),
            timeout=budget.remaining(maximum=10),
        )
        mutation_unknown = False
        snapshot = inspect(physical)
        require_owned(plan, physical, snapshot)
        require_configuration(plan, snapshot)
        require_running(snapshot)
        report.stage = "execute"
        active.run(
            plan.exec_argv(physical),
            timeout=budget.remaining(),
            output=control / "runner.log",
        )
        report.execution_completed = True
        report.execution_exit_code = 0
        report.stage = "execution-postflight"
        snapshot = inspect(physical)
        require_owned(plan, physical, snapshot)
        require_configuration(plan, snapshot)
        require_running(snapshot)
        primary_pass = True
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        report.problem = (
            str(error) if isinstance(error, PlanRefusal) else type(error).__name__
        )
        if report.stage == "execute" and isinstance(error, CommandFailure):
            report.execution_exit_code = error.returncode
    finally:
        if report.container_id is not None and active is not None:
            try:
                physical = report.container_id
                snapshot = inspect(physical, cleanup=True)
                require_owned(plan, physical, snapshot)
                if snapshot["State"]["Running"] is True:
                    active.run(
                        (*plan.docker, "container", "stop", "--time", "2", physical),
                        timeout=budget.remaining(cleanup=True, maximum=5),
                    )
                snapshot = inspect(physical, cleanup=True)
                require_owned(plan, physical, snapshot)
                require_stopped(snapshot)
                report.owner_stop_verified = not mutation_unknown
                try:
                    require_configuration(plan, snapshot)
                    if snapshot["State"]["OOMKilled"] is not False:
                        raise PlanRefusal("owned container exhausted memory")
                except (ValueError, KeyError, TypeError, AttributeError) as error:
                    primary_pass = False
                    if report.problem is None:
                        report.stage = "stop-postflight"
                        report.problem = (
                            str(error)
                            if isinstance(error, PlanRefusal)
                            else type(error).__name__
                        )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                subprocess.SubprocessError,
            ) as error:
                report.cleanup_problem = (
                    str(error)
                    if isinstance(error, PlanRefusal)
                    else type(error).__name__
                )
        else:
            report.owner_stop_verified = not report.creation_attempted
        if primary_pass and report.owner_stop_verified:
            report.result = "pass"
        elif mutation_unknown or not report.owner_stop_verified:
            report.result = "unknown"
        report.elapsed_seconds = time.monotonic() - started
        if report.elapsed_seconds > deadline_epoch - wall:
            if report.result == "pass":
                report.result = "fail"
            report.cleanup_problem = "owner exceeded the original wall budget"
        write_json(control / "cr-report.json", asdict(report))
    budget.remaining(cleanup=True)
    return report
