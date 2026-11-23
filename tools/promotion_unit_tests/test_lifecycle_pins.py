"""Ordinary Windows test custody keeps existing identity pins through teardown."""

from contextlib import contextmanager
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Iterator

import pytest

if TYPE_CHECKING:
    import plugin_test_lifecycle as lifecycle
    from plugin_test_job_observation import ENVIRONMENT
else:
    from tools import plugin_test_lifecycle as lifecycle
    from tools.plugin_test_job_observation import ENVIRONMENT


def _exercise_windows_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_application: bool, cleanup: str,
    observation: bool = False, original_code: int = 0,
) -> None:
    events: list[str] = []
    limits = SimpleNamespace(max_processes=8)

    @contextmanager
    def pins(selected: bool, sandbox: Path, limit: int) -> Iterator[SimpleNamespace]:
        assert selected and sandbox == tmp_path and limit == limits.max_processes
        try:
            yield SimpleNamespace(
                bind=lambda _environment: events.append("bind"),
                observe=lambda: events.append("observe"),
            )
        finally:
            events.append("close-pins")

    @contextmanager
    def lease(**kwargs: Any) -> Iterator[dict[str, str]]:
        try:
            yield {"LEASE": "owned"}
        finally:
            events.append("cleanup")
            kwargs["cleanup_results"].append(SimpleNamespace(status=cleanup))

    def run(_command: list[str], **kwargs: Any) -> int:
        assert kwargs["limits"] is limits
        assert kwargs.get("job_observation", False) is observation
        assert kwargs.get("native_application", False) is native_application
        assert kwargs["env"] == {
            "LEASE": "owned", **({} if native_application else {"CONTAINED": "1"}),
        }
        kwargs["native_observer"]()
        return original_code

    owner = SimpleNamespace(
        os=SimpleNamespace(name="nt", environ={
            "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_TEMP": str(tmp_path),
            **({ENVIRONMENT: "1"} if observation else {}),
        }, getpid=lambda: 123),
        Path=Path, CONTAINED_ENV="CONTAINED", ContainmentError=ValueError,
        UnreapedProcessError=RuntimeError, daemon_test_lifecycle=lease,
        _run_contained_process=run, _read_registry_environment=lambda: None,
        _registry_environment_drift=lambda _snapshot: [], sys=sys,
    )
    monkeypatch.setattr(lifecycle, "_native_instance_pins", pins)
    monkeypatch.setattr(lifecycle, "_native_scope_environment", lambda env: env)

    def execute() -> int:
        return lifecycle.run_contained(
            owner, ["unused"], cwd=tmp_path,
            env={"CONTAINED": "1", **({ENVIRONMENT: "1"} if observation else {})},
            sandbox=tmp_path,
            limits=limits, daemon_purpose="unattended-test", daemon_lease=None,
            qualification=None, native_application=native_application,
        )

    if cleanup == "held":
        with pytest.raises(RuntimeError, match="cleanup remains unsettled"):
            execute()
    else:
        assert execute() == (1 if observation and original_code == 0 else original_code)
    assert events == ["bind", "observe", "cleanup", "close-pins"]


@pytest.mark.parametrize("native_application", [False, True])
@pytest.mark.parametrize("cleanup", ["settled", "held"])
def test_windows_runner_keeps_pins_through_ordinary_and_native_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_application: bool, cleanup: str,
) -> None:
    _exercise_windows_runner(tmp_path, monkeypatch, native_application, cleanup)


@pytest.mark.parametrize("native_application", [False, True])
@pytest.mark.parametrize("original_code", [0, 124])
def test_job_observation_is_diagnostic_and_preserves_original_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_application: bool, original_code: int,
) -> None:
    _exercise_windows_runner(
        tmp_path, monkeypatch, native_application, "settled",
        observation=True, original_code=original_code,
    )


@pytest.mark.parametrize("change", [
    {"name": "posix"}, {"qualification": object()},
    {"GITHUB_ACTIONS": ""}, {"RUNNER_ENVIRONMENT": "self-hosted"}, {"CONTAINED": "1"},
])
def test_job_observation_rejects_non_disposable_owners_before_any_effect(
    tmp_path: Path, change: dict[str, Any],
) -> None:
    environment = {
        ENVIRONMENT: "1", "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        **{key: value for key, value in change.items() if key not in ("name", "qualification")},
    }
    owner = SimpleNamespace(
        os=SimpleNamespace(name=change.get("name", "nt"), environ=environment),
        CONTAINED_ENV="CONTAINED", ContainmentError=ValueError,
    )
    with pytest.raises(ValueError, match="disposable hosted Windows owner"):
        lifecycle.run_contained(
            owner, [], cwd=tmp_path, env={}, sandbox=tmp_path, limits=None,
            daemon_purpose="unattended-test", daemon_lease=None,
            qualification=change.get("qualification"), native_application=False,
        )


def test_pinned_inventory_is_not_redecoded_but_new_admission_is_still_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import _daemon_instance_identity as identity, daemon_instances

    record = {"instance_id": "a" * 32, "run_id": "owned", "pid": "99", "birth": "expected"}
    (tmp_path / (record["instance_id"] + ".instance")).touch()
    reads: list[Path] = []

    def inspect(root: Path) -> list[dict[str, str]]:
        reads.append(root)
        return [record]

    process = SimpleNamespace(_handle=123, close=lambda: None, poll=lambda: None)
    monkeypatch.setattr(daemon_instances, "inspect_instances", inspect)
    monkeypatch.setattr(identity, "_windows_handle", lambda _pid: process)
    monkeypatch.setattr(identity, "_windows_birth", lambda _handle: "expected")
    pins = lifecycle._NativeInstancePins(tmp_path, 2)
    pins.run_id = "owned"
    try:
        pins.observe()
        pins.observe()
        assert reads == [tmp_path]
        (tmp_path / ("b" * 32 + ".instance")).touch()
        pins.observe()
        assert reads == [tmp_path, tmp_path]
    finally:
        pins.close()


@pytest.mark.parametrize(("present", "returncode"), [(False, 0), (True, 0), (False, None)])
def test_pin_release_needs_both_receipt_settlement_and_original_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, present: bool, returncode: int | None,
) -> None:
    from agent_procutil import _daemon_instance_identity as identity, daemon_instances

    record = {"instance_id": "a" * 32, "run_id": "owned", "pid": "99", "birth": "expected"}
    path = tmp_path / (record["instance_id"] + ".instance")
    path.touch()
    closed: list[bool] = []
    process = SimpleNamespace(_handle=123, close=lambda: closed.append(True), poll=lambda: returncode)
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _root: [record] if path.exists() else [])
    monkeypatch.setattr(identity, "_windows_handle", lambda _pid: process)
    monkeypatch.setattr(identity, "_windows_birth", lambda _handle: "expected")
    pins = lifecycle._NativeInstancePins(tmp_path, 2)
    pins.run_id = "owned"
    try:
        pins.observe()
        if not present:
            path.unlink()
        pins.observe()
        released = not present and returncode is not None
        assert bool(closed) is released
        assert bool(pins.processes) is not released
    finally:
        pins.close()
    assert closed == [True]


def test_settled_sequential_instances_do_not_exhaust_the_concurrent_pin_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import _daemon_instance_identity as identity, daemon_instances

    current: list[dict[str, str]] = []
    closed: list[int] = []
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _root: current)
    monkeypatch.setattr(identity, "_windows_handle", lambda pid: SimpleNamespace(
        _handle=pid, close=lambda: closed.append(pid), poll=lambda: 0,
    ))
    monkeypatch.setattr(identity, "_windows_birth", lambda _handle: "expected")
    pins = lifecycle._NativeInstancePins(tmp_path, 2)
    pins.run_id = "owned"
    try:
        for number in range(3):
            key = f"{number:032x}"
            path = tmp_path / (key + ".instance")
            path.touch()
            current[:] = [{"instance_id": key, "run_id": "owned", "pid": str(number + 1), "birth": "expected"}]
            pins.observe()
            path.unlink()
        current.clear()
        pins.observe()
        assert not pins.processes
        assert closed == [1, 2, 3]
    finally:
        pins.close()
