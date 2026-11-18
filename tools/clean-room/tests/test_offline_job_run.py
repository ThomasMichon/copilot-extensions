"""Synthetic lifecycle/host controls: never invoke Docker or a package runtime."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux cgroup-v2 owner")

RIG = Path(__file__).resolve().parents[1]
for module_name in (
    "offline_job",
    "offline_job_host",
    "offline_job_inspect",
    "offline_job_run",
):
    module_path = RIG / f"{module_name}.py"
    existing = sys.modules.get(module_name)
    if existing is None:
        specification = importlib.util.spec_from_file_location(module_name, module_path)
        assert specification is not None and specification.loader is not None
        existing = importlib.util.module_from_spec(specification)
        sys.modules[module_name] = existing
        specification.loader.exec_module(existing)
    assert Path(str(existing.__file__)).resolve() == module_path

plans = sys.modules["offline_job"]
host = sys.modules["offline_job_host"]
owner = sys.modules["offline_job_run"]
PHYSICAL = "a" * 64


@dataclass
class FakeDocker:
    plan: Any
    case: str = ""
    calls: list[tuple[str, ...]] = field(default_factory=list)
    running: bool = False
    executed: bool = False

    def snapshot(self) -> dict[str, Any]:
        job = self.plan.job
        running = self.running and not (self.case == "expires" and self.executed)
        mounts = [
            {
                "Type": "bind",
                "Source": str(self.plan.results),
                "Destination": "/job/out",
                "RW": True,
            },
            *(
                {
                    "Type": "bind",
                    "Source": str(item.source),
                    "Destination": item.target,
                    "RW": False,
                }
                for item in job.inputs
            ),
        ]
        if job.cache is not None:
            mounts.append(
                {
                    "Type": "bind",
                    "Source": str(job.cache),
                    "Destination": "/job/cache",
                    "RW": True,
                }
            )
        values: dict[str, Any] = {
            "Id": PHYSICAL if self.case != "foreign-id" else "b" * 64,
            "Name": "/" + job.name,
            "Image": job.image,
            "RestartCount": 0,
            "Config": {
                "User": f"{job.uid}:{job.gid}",
                "Labels": {"copilot-extensions.offline-job": job.name},
                "Entrypoint": ["/bin/sh"],
                "Cmd": list(self.plan.create[-4:]),
                "WorkingDir": "/job/out",
                "Env": [
                    self.plan.create[index + 1]
                    for index, value in enumerate(self.plan.create)
                    if value == "--env"
                ],
            },
            "HostConfig": {
                "Memory": job.limits.memory_bytes,
                "MemorySwap": job.limits.memory_bytes,
                "NanoCpus": job.limits.cpu_millis * 1_000_000,
                "PidsLimit": job.limits.processes,
                "Init": self.case != "bad-init",
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "Privileged": False,
                "CapDrop": ["ALL"],
                "CapAdd": None,
                "SecurityOpt": ["no-new-privileges"],
                "PidMode": "",
                "UTSMode": "",
                "IpcMode": "private",
                "AutoRemove": False,
                "PublishAllPorts": False,
                "PortBindings": {},
                "RestartPolicy": {"Name": "no"},
            },
            "Mounts": mounts,
            "State": {
                "Running": running,
                "Restarting": False,
                "Paused": False,
                "Pid": 12345 if running else 0,
                "OOMKilled": self.case == "oom-at-stop"
                and self.executed
                and not running,
            },
        }
        if self.case == "bad-limits" or (
            self.case == "changed-after-exec" and self.executed
        ):
            values["HostConfig"]["Memory"] += 1
        return values

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout: float,
        output: Path | None = None,
    ) -> str:
        assert 0 < timeout <= 240
        assert arguments[:3] == self.plan.docker
        self.calls.append(arguments)
        operation = arguments[3:]
        if operation[:2] == ("container", "ls"):
            return "b" * 64 if self.case == "collision" else ""
        if operation[:2] == ("image", "inspect"):
            if self.case == "missing-image":
                raise host.CommandFailure(1)
            return json.dumps(
                {
                    "Id": self.plan.job.image,
                    "Config": {
                        "Volumes": {"/implicit": {}} if self.case == "volume" else None,
                        "Healthcheck": {"Test": ["CMD", "unexpected"]}
                        if self.case == "healthcheck"
                        else None,
                    },
                }
            )
        if operation[:1] == ("create",):
            assert "--detach" not in operation
            assert (
                "--pull" in operation
                and operation[operation.index("--pull") + 1] == "never"
            )
            if self.case == "create-timeout":
                raise subprocess.TimeoutExpired("synthetic-create", timeout)
            return "invalid" if self.case == "missing-id" else PHYSICAL
        if operation[:2] == ("container", "inspect"):
            assert operation[-1] == PHYSICAL
            return json.dumps(self.snapshot())
        if operation[:2] == ("container", "start"):
            assert operation[-1] == PHYSICAL
            self.running = True
            if self.case == "start-timeout":
                raise subprocess.TimeoutExpired("synthetic-start", timeout)
            return PHYSICAL
        if operation[:1] == ("exec",):
            assert PHYSICAL in operation and output is not None
            output.write_text("synthetic execution output\n", encoding="utf-8")
            self.executed = True
            if self.case == "exec-failure":
                raise host.CommandFailure(7)
            if self.case == "exec-overflow":
                raise host.CaptureOverflow()
            return ""
        if operation[:2] == ("container", "stop"):
            assert operation[-1] == PHYSICAL
            if self.case == "stop-failure":
                raise host.CommandFailure(1)
            self.running = False
            return PHYSICAL
        raise AssertionError(f"unexpected synthetic Docker operation: {operation}")


@dataclass
class Fixture:
    job: Any
    results: Path
    control: Path
    repository: Path
    deadline: int
    docker: FakeDocker

    def run(self, **changes: Any) -> Any:
        return owner.run_job(
            self.job,
            **{
                "results": self.results,
                "control": self.control,
                "repository": self.repository,
                "deadline_epoch": self.deadline,
                "aggregate": plans.Limits(1024**3, 1000, 256),
                "host": plans.Limits(128 * 1024**2, 125, 32),
                "runner": self.docker,
                **changes,
            },
        )


@pytest.fixture
def packet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    results, repository, source, cache = (
        tmp_path / name for name in ("results", "repository", "inputs", "cache")
    )
    for path in (results, repository, source, cache):
        path.mkdir(mode=0o700)
    job = plans.Job(
        "cr-offline-aabbccddeeff",
        "sha256:" + "c" * 64,
        1234,
        1234,
        plans.Limits(896 * 1024**2, 875, 224),
        240,
        30,
        (plans.Input("scenario", source),),
        ("/bin/true",),
        cache,
    )
    deadline = int(time.time()) + 240
    plan = plans.plan_job(
        job,
        results=results,
        host_home=Path.home(),
        repository=repository,
        deadline_epoch=deadline,
        now_epoch=time.time(),
    )
    monkeypatch.setattr(owner, "admit_host", lambda job, limits: limits)

    def refuse_real_cli(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "an inert lifecycle control reached the real Docker backend"
        )

    monkeypatch.setattr(host.Docker, "run", refuse_real_cli)
    return Fixture(
        job, results, tmp_path / "control", repository, deadline, FakeDocker(plan)
    )


def test_create_capture_inspect_start_execute_and_stop_are_ordered_and_retained(
    packet: Fixture,
) -> None:
    report = packet.run()
    assert report.result == "pass"
    assert report.owner_stop_verified and report.configuration_verified
    assert report.execution_completed and report.execution_exit_code == 0
    operations = [arguments[3] for arguments in packet.docker.calls]
    assert operations.index("create") < operations.index("exec")
    create_index = operations.index("create")
    assert packet.docker.calls[create_index + 1][3:5] == ("container", "inspect")
    assert packet.docker.calls[create_index + 2][3:5] == ("container", "start")
    assert sum(call[3] == "create" for call in packet.docker.calls) == 1
    assert not any(
        part in ("rm", "build", "pull", "restart")
        for call in packet.docker.calls
        for part in call[3:]
    )
    record = json.loads((packet.control / "owned-resources.json").read_text())
    assert record["name"] == packet.job.name and record["image"] == packet.job.image
    assert json.loads((packet.control / "physical-id.json").read_text()) == {
        "container_id": PHYSICAL
    }
    assert (packet.control / "client-config").is_dir()
    assert packet.results.is_dir()  # Payload and stopped container are not removed.
    before = len(packet.docker.calls)
    with pytest.raises(plans.PlanRefusal, match="reserved"):
        packet.run()
    assert len(packet.docker.calls) == before


@pytest.mark.parametrize("role", ["results", "inputs", "cache", "repository"])
def test_control_metadata_and_cli_config_never_enter_payload_binds(
    packet: Fixture,
    role: str,
) -> None:
    roots = {
        "results": packet.results,
        "inputs": packet.job.inputs[0].source,
        "cache": packet.job.cache,
        "repository": packet.repository,
    }
    with pytest.raises(plans.PlanRefusal, match="overlaps"):
        packet.run(control=roots[role] / "control")
    assert packet.docker.calls == []


def test_redirected_home_cannot_hide_a_whole_registered_home_mount(
    packet: Fixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "synthetic-home"))
    packet.job = replace(
        packet.job, inputs=(plans.Input("home", host.registered_home()),)
    )
    with pytest.raises(plans.PlanRefusal, match="mount source"):
        packet.run()
    assert packet.docker.calls == [] and not packet.control.exists()


@pytest.mark.parametrize(
    "case", ["collision", "missing-image", "volume", "healthcheck"]
)
def test_preflight_refusal_never_creates_adopts_or_removes(
    packet: Fixture,
    case: str,
) -> None:
    packet.docker.case = case
    report = packet.run()
    assert report.result == "fail" and report.owner_stop_verified
    assert not report.creation_attempted
    assert not any(
        call[3] in ("create", "exec", "rm", "pull", "build")
        for call in packet.docker.calls
    )


@pytest.mark.parametrize(
    "case",
    ["create-timeout", "missing-id", "foreign-id", "start-timeout", "stop-failure"],
)
def test_unknown_mutation_or_unqualified_identity_never_passes_or_replays(
    packet: Fixture,
    case: str,
) -> None:
    packet.docker.case = case
    report = packet.run()
    assert report.result == "unknown" and not report.owner_stop_verified
    assert sum(call[3] == "create" for call in packet.docker.calls) == 1
    assert not any(
        call[3] in ("rm", "build", "pull", "restart") for call in packet.docker.calls
    )
    if case in ("create-timeout", "missing-id", "foreign-id"):
        assert not any(
            call[3] in ("exec",)
            or call[3:5] in (("container", "start"), ("container", "stop"))
            for call in packet.docker.calls
        )


@pytest.mark.parametrize(
    "case",
    [
        "bad-limits",
        "bad-init",
        "changed-after-exec",
        "expires",
        "exec-failure",
        "exec-overflow",
        "oom-at-stop",
    ],
)
def test_configuration_expiry_or_execution_failure_is_not_a_pass(
    packet: Fixture,
    case: str,
) -> None:
    packet.docker.case = case
    report = packet.run()
    assert report.result == "fail" and report.owner_stop_verified
    if case in ("bad-limits", "bad-init"):
        assert not any(
            call[3:5] == ("container", "start") for call in packet.docker.calls
        )
    if case == "exec-failure":
        assert report.execution_exit_code == 7
    if case == "exec-overflow":
        assert not report.execution_completed
        assert report.problem == "captured output reached its finite limit"
        assert any(call[3:5] == ("container", "stop") for call in packet.docker.calls)
    assert sum(call[3] == "create" for call in packet.docker.calls) == 1


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("memory.max", "134217729"),
        ("memory.max", "max"),
        ("memory.swap.max", "1"),
        ("pids.max", "33"),
        ("cpu.max", "12600 100000"),
        ("cpu.max", "max 100000"),
        ("positive", ""),
    ],
)
def test_host_partition_reads_real_interface_shape_without_creating_a_boundary(
    packet: Fixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    value: str,
) -> None:
    group = tmp_path / "cgroups/case"
    group.mkdir(parents=True)
    values = {
        "memory.max": "134217728",
        "memory.swap.max": "0",
        "pids.max": "32",
        "cpu.max": "12500 100000",
    }
    if field_name != "positive":
        values[field_name] = value
    for name, content in values.items():
        (group / name).write_text(content, encoding="ascii")
    proc = tmp_path / "proc-cgroup"
    proc.write_text("0::/case\n", encoding="ascii")
    monkeypatch.setattr(host, "PROC_CGROUP", proc)
    monkeypatch.setattr(host, "CGROUP_ROOT", group.parent)
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        monkeypatch.setattr(host.os, name, lambda: 1234)
    expected = plans.Limits(128 * 1024**2, 125, 32)
    if field_name == "positive":
        assert host.admit_host(packet.job, expected) == expected
    else:
        with pytest.raises(plans.PlanRefusal):
            host.admit_host(packet.job, expected)
    assert packet.docker.calls == []


@pytest.mark.parametrize("oversized", [False, True])
def test_cli_uses_private_config_clean_environment_and_bounded_retained_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    oversized: bool,
) -> None:
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    (control / "client-config").mkdir(mode=0o700)
    monkeypatch.setattr(host.shutil, "which", lambda *args, **kwargs: "/usr/bin/docker")
    monkeypatch.setenv("DOCKER_CONTEXT", "synthetic-unselected-context")
    monkeypatch.setenv("GH_TOKEN", "synthetic-unforwarded-value")
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def execute(
        arguments: tuple[str, ...], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((arguments, kwargs))
        kwargs["stdout"].write(b"x" * 65537 if oversized else b'{"synthetic":true}\n')
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(host.subprocess, "run", execute)
    backend = host.Docker(control)
    command = ("docker", "--host", "unix:///var/run/docker.sock", "version")
    if oversized:
        with pytest.raises(host.CaptureOverflow):
            backend.run(command, timeout=3)
    else:
        result = backend.run(command, timeout=3)
        assert json.loads(result) == {"synthetic": True}
    arguments, options = calls[0]
    assert arguments[:3] == (
        "/usr/bin/docker",
        "--config",
        str(control / "client-config"),
    )
    assert options["timeout"] == 3 and options["check"] is False
    assert options["cwd"] == control
    assert options["env"] == {
        "HOME": str(host.registered_home()),
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
    }
    assert (control / "control-0001.stdout").is_file()
    assert (control / "control-0001.stderr").is_file()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("execution", [False, True])
def test_real_helper_cannot_write_past_either_capture_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream: str,
    execution: bool,
) -> None:
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    (control / "client-config").mkdir(mode=0o700)
    helper = tmp_path / "noisy-helper"
    helper.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import os, sys\n"
        "descriptor = 1 if sys.argv[-1] == 'stdout' else 2\n"
        "for _ in range(128):\n"
        "    os.write(descriptor, b'x' * 16384)\n",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    monkeypatch.setattr(host.shutil, "which", lambda *args, **kwargs: str(helper))
    backend = host.Docker(control)
    output = control / "runner.log" if execution else None
    limit = host.EXECUTION_CAPTURE_BYTES if execution else host.CONTROL_CAPTURE_BYTES
    with pytest.raises(host.CaptureOverflow):
        backend.run(
            ("docker", "--host", "unix:///var/run/docker.sock", "synthetic", stream),
            timeout=5,
            output=output,
        )
    stdout = output or control / "control-0001.stdout"
    stderr = control / "control-0001.stderr"
    assert stdout.stat().st_size <= limit
    assert stderr.stat().st_size <= limit


def test_threaded_cli_caller_refuses_before_subprocess_or_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    (control / "client-config").mkdir(mode=0o700)
    monkeypatch.setattr(host.shutil, "which", lambda *args, **kwargs: "/usr/bin/docker")
    monkeypatch.setattr(host.threading, "active_count", lambda: 2)

    def refuse_process(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("threaded caller reached subprocess dispatch")

    monkeypatch.setattr(host.subprocess, "run", refuse_process)
    backend = host.Docker(control)
    with pytest.raises(plans.PlanRefusal, match="single-threaded"):
        backend.run(
            ("docker", "--host", "unix:///var/run/docker.sock", "version"), timeout=3
        )
    assert sorted(path.name for path in control.iterdir()) == ["client-config"]
