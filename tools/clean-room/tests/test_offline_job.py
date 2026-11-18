"""Inert offline-job controls; never launch Docker or build a runtime."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

SPEC = importlib.util.spec_from_file_location(
    "clean_room_offline_job", Path(__file__).resolve().parents[1] / "offline_job.py"
)
assert SPEC is not None and SPEC.loader is not None
owner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = owner
SPEC.loader.exec_module(owner)


@dataclass(frozen=True)
class PlanRequest:
    job: Any
    results: Path
    home: Path
    repository: Path

    def __call__(
        self,
        selected: Any = None,
        *,
        results: Path | None = None,
        home: Path | None = None,
        repository: Path | None = None,
        now_epoch: float = 1000.0,
    ) -> Any:
        return owner.plan_job(
            selected or self.job,
            results=self.results if results is None else results,
            host_home=self.home if home is None else home,
            repository=self.repository if repository is None else repository,
            deadline_epoch=1240,
            now_epoch=now_epoch,
        )


@pytest.fixture
def request_plan(tmp_path: Path) -> PlanRequest:
    home = tmp_path / "home"
    repository = tmp_path / "repository"
    results = tmp_path / "results"
    source = tmp_path / "input"
    cache = tmp_path / "cache"
    for directory in (home, repository, results, source, cache):
        directory.mkdir(mode=0o700)
    job = owner.Job(
        name="cr-offline-0123456789ab",
        image="sha256:" + "a" * 64,
        uid=1234,
        gid=1234,
        limits=owner.Limits(896 * 1024**2, 875, 224),
        seconds=240,
        cleanup_seconds=30,
        inputs=(owner.Input("runtime", source),),
        command=("/job/inputs/runtime/python", "-I", "-B", "check.py"),
        cache=cache,
    )

    return PlanRequest(job, results, home, repository)


def test_plan_has_no_fallback_helpers_or_inherited_host_environment(
    request_plan: PlanRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", "/private-home")
    monkeypatch.setenv("PATH", "/private-tools")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "synthetic-unforwarded-value")
    plan = request_plan()
    command = plan.create
    for flag, value in (
        ("--pull", "never"),
        ("--user", "1234:1234"),
        ("--network", "none"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--memory", str(896 * 1024**2)),
        ("--memory-swap", str(896 * 1024**2)),
        ("--cpus", "0.875"),
        ("--pids-limit", "224"),
    ):
        assert command[command.index(flag) + 1] == value
    assert "--read-only" in command
    assert command.count("run") == 1
    assert command[-1] == "1210"  # Reserve is inside the original absolute wall.
    assert "$(/bin/date +%s)" in command[-3]
    assert 'exec /bin/sleep "$left"' in command[-3]
    assert not any(part in command for part in ("--rm", "--privileged", "--publish"))
    assert not any(
        "private-" in part or "synthetic-unforwarded" in part for part in command
    )
    mounts = [
        command[index + 1] for index, item in enumerate(command) if item == "--mount"
    ]
    assert len(mounts) == 3
    assert any("dst=/job/inputs/runtime,readonly" in mount for mount in mounts)
    assert any(
        "dst=/job/cache" in mount and "readonly" not in mount for mount in mounts
    )
    assert list(request_plan.results.iterdir()) == []  # Planning has no effects.
    physical = "b" * 64
    assert plan.exec_argv(physical)[-len(plan.job.command) - 1] == physical
    with pytest.raises(owner.PlanRefusal, match="physical"):
        plan.exec_argv(plan.job.name)


@pytest.mark.parametrize(
    "changes",
    [
        {"image": "example:latest"},
        {"uid": 0},
        {"gid": True},
        {"name": "another-name"},
        {"seconds": 0},
        {"cleanup_seconds": 240},
        {"command": ("python",)},
        {"command": ("/bin/sh", "bad\0argument")},
    ],
)
def test_bad_request_refuses_before_plan(
    request_plan: PlanRequest, changes: dict[str, Any]
) -> None:
    with pytest.raises(owner.PlanRefusal):
        request_plan(replace(request_plan.job, **changes))


@pytest.mark.parametrize("clock", [999.0, 1210.0, float("nan"), float("inf")])
def test_deadline_cannot_extend_or_consume_cleanup_reserve(
    request_plan: PlanRequest, clock: float
) -> None:
    with pytest.raises(owner.PlanRefusal, match="deadline"):
        request_plan(now_epoch=clock)


def test_refuse_broad_linked_overlapping_mounts(
    request_plan: PlanRequest, tmp_path: Path
) -> None:
    for source in (
        Path("/"),
        request_plan.home,
        Path("//" + str(request_plan.home).lstrip("/")),
        request_plan.repository,
        tmp_path,
    ):
        with pytest.raises(owner.PlanRefusal, match="mount source"):
            request_plan(
                replace(request_plan.job, inputs=(owner.Input("runtime", source),))
            )
    link = tmp_path / "linked"
    link.symlink_to(request_plan.job.inputs[0].source, target_is_directory=True)
    with pytest.raises(owner.PlanRefusal, match="link"):
        request_plan(replace(request_plan.job, inputs=(owner.Input("runtime", link),)))
    with pytest.raises(owner.PlanRefusal, match="overlap"):
        request_plan(
            replace(
                request_plan.job, inputs=(owner.Input("runtime", request_plan.results),)
            )
        )
    with pytest.raises(owner.PlanRefusal, match="separate"):
        request_plan(replace(request_plan.job, cache=request_plan.job.inputs[0].source))


def test_duplicate_name_and_dangling_result_refuse(request_plan: PlanRequest) -> None:
    with pytest.raises(owner.PlanRefusal, match="distinct"):
        request_plan(replace(request_plan.job, inputs=request_plan.job.inputs * 2))
    (request_plan.results / "owned-resources.json").symlink_to("missing")
    with pytest.raises(owner.PlanRefusal, match="reserved"):
        request_plan()


def test_partition_counts_host_and_container_instead_of_granting_both_full_budget() -> (
    None
):
    aggregate = owner.Limits(1024**3, 1000, 256)
    host = owner.Limits(128 * 1024**2, 125, 32)
    child = owner.Limits(896 * 1024**2, 875, 224)
    owner.validate_partition(aggregate=aggregate, host=host, container=child)
    for over in (
        replace(child, memory_bytes=child.memory_bytes + 1),
        replace(child, cpu_millis=876),
        replace(child, processes=225),
    ):
        with pytest.raises(owner.PlanRefusal, match="aggregate"):
            owner.validate_partition(aggregate=aggregate, host=host, container=over)


@pytest.mark.parametrize("role", ["results", "cache"])
def test_writable_repository_descendants_are_rejected(
    request_plan: PlanRequest, role: str
) -> None:
    inside = request_plan.repository / role
    inside.mkdir(mode=0o700)
    with pytest.raises(
        owner.PlanRefusal, match="writable root overlaps the repository"
    ):
        if role == "results":
            request_plan(results=inside)
        else:
            request_plan(replace(request_plan.job, cache=inside))
    assert list(inside.iterdir()) == []


def test_narrow_read_only_repository_input_stays_supported(
    request_plan: PlanRequest,
) -> None:
    source = request_plan.repository / "scenario"
    source.mkdir(mode=0o700)
    plan = request_plan(
        replace(request_plan.job, inputs=(owner.Input("scenario", source),))
    )
    assert f"type=bind,src={source},dst=/job/inputs/scenario,readonly" in plan.create


@pytest.mark.parametrize("role", ["results", "cache"])
def test_aliased_repository_boundary_still_rejects_physical_writable_descendants(
    request_plan: PlanRequest, tmp_path: Path, role: str
) -> None:
    alias = tmp_path / "checkout-alias"
    alias.symlink_to(request_plan.repository, target_is_directory=True)
    inside = request_plan.repository / role
    inside.mkdir(mode=0o700)
    with pytest.raises(
        owner.PlanRefusal, match="writable root overlaps the repository"
    ):
        if role == "results":
            request_plan(results=inside, repository=alias)
        else:
            request_plan(replace(request_plan.job, cache=inside), repository=alias)
    assert list(inside.iterdir()) == []


def test_aliased_home_boundary_still_rejects_whole_physical_home(
    request_plan: PlanRequest, tmp_path: Path
) -> None:
    alias = tmp_path / "home-alias"
    alias.symlink_to(request_plan.home, target_is_directory=True)
    with pytest.raises(owner.PlanRefusal, match="mount source"):
        request_plan(
            replace(
                request_plan.job,
                inputs=(owner.Input("home", request_plan.home),),
            ),
            home=alias,
        )


@pytest.mark.parametrize("role", ["input", "results", "cache"])
@pytest.mark.parametrize("character", ['"', "\n", "\r"])
def test_unrepresentable_csv_mount_sources_are_rejected(
    request_plan: PlanRequest, tmp_path: Path, role: str, character: str
) -> None:
    path = tmp_path / ("unrepresentable" + character + "path")
    path.mkdir(mode=0o700)
    with pytest.raises(owner.PlanRefusal, match="unrepresentable mount source"):
        if role == "results":
            request_plan(results=path)
        elif role == "cache":
            request_plan(replace(request_plan.job, cache=path))
        else:
            request_plan(
                replace(request_plan.job, inputs=(owner.Input("runtime", path),))
            )


def test_docker_config_proxy_defaults_are_explicitly_overridden(
    request_plan: PlanRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.com")
    monkeypatch.setenv("no_proxy", "example.com")
    command = request_plan().create
    variables = dict(
        command[index + 1].split("=", 1)
        for index, value in enumerate(command)
        if value == "--env"
    )
    for scheme in ("HTTP", "HTTPS", "FTP", "NO", "ALL"):
        assert variables[f"{scheme}_PROXY"] == ""
        assert variables[f"{scheme.lower()}_proxy"] == ""
