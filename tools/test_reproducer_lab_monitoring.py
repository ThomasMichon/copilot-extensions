"""Synthetic reader seams and local probe files; never invoke real Docker."""

from __future__ import annotations

import copy
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from test_reproducer_lab_boundary import declaration, inspection

LAB = Path(__file__).parent / "reproducer-lab"
sys.path.insert(0, str(LAB))
import boundary
import guest_usage
import reader
import storage


def experiment() -> boundary.Experiment:
    return boundary.parse_declaration(declaration())


def resources() -> storage.Resources:
    return storage.Resources("c" * 64, tuple(
        storage.VolumeIdentity(name, "2026-01-01T00:00:00Z", f"/volumes/{name}")
        for name in experiment().volumes.values()
    ))


def usage(**changes: Any) -> storage.Usage:
    return replace(storage.Usage(resources(), 10, 11, 100, 50, 1024**3), **changes)


def monitor() -> storage.StorageMonitor:
    return storage.StorageMonitor(
        experiment(), resources(), storage.MonitoringPolicy(10, 60, 1024), now=0,
    )


def test_monitor_requires_measurement_again_on_resume() -> None:
    first = monitor()
    assert first.refusals(now=1) == ("fresh storage measurement is unavailable",)
    first.record(usage(), now=11)
    assert first.refusals(now=11) == ()
    assert monitor().refusals(now=11) == ("fresh storage measurement is unavailable",)
    assert not hasattr(first, "ready")


@pytest.mark.parametrize("field,threshold,message", [
    ("persistent_bytes", experiment().budgets.persistent_bytes, "persistent usage"),
    ("output_bytes", experiment().budgets.output_bytes, "evidence usage"),
    ("minimum_free_bytes", 1024, "backing free space"),
])
def test_exact_threshold_refuses_new_work(field: str, threshold: int, message: str) -> None:
    observer = monitor()
    observer.record(usage(**{field: threshold}), now=11)
    assert any(message in reason for reason in observer.refusals(now=11))
    safe = threshold + 1 if field == "minimum_free_bytes" else threshold - 1
    observer.record(usage(**{field: safe}), now=11)
    assert observer.refusals(now=11) == ()


def test_slow_sample_age_cadence_lifetime_and_loss() -> None:
    observer = monitor()
    observer.record(usage(), now=11)
    assert observer.refusals(now=19.999) == ()
    assert "stale" in observer.refusals(now=20)[0]
    assert "expired" in observer.refusals(now=60)[0]
    observer.close()
    assert "closed" in observer.refusals(now=61)[0]
    assert observer.latest is None


@pytest.mark.parametrize("changes", [
    {"resources": replace(resources(), container_id="d" * 64)},
    {"started": -1}, {"finished": 9}, {"started": float("nan")},
    {"persistent_bytes": True}, {"output_bytes": -1}, {"minimum_free_bytes": 1.5},
])
def test_invalid_measurement_clears_previous_success(changes: dict[str, Any]) -> None:
    observer = monitor()
    observer.record(usage(), now=11)
    with pytest.raises(ValueError):
        observer.record(usage(**changes), now=11)
    assert observer.latest is None
    assert observer.refusals(now=11)


@pytest.mark.parametrize("values", [
    (0, 60, 1024), (True, 60, 1024), (61, 60, 1024),
    (10, 3601, 1024), (10, 9, 1024), (10, 60, -1), (10, 60, 4 * 1024**3 + 1),
])
def test_policy_requires_finite_bounded_inputs(values: tuple[int, int, int]) -> None:
    with pytest.raises(ValueError):
        storage.MonitoringPolicy(*values)


def volume_rows() -> list[dict[str, Any]]:
    return [
        {"Name": volume.name, "CreatedAt": volume.created_at, "Mountpoint": volume.mountpoint,
         "Driver": "local", "Scope": "local", "Options": None}
        for volume in resources().volumes
    ]


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.container = inspection()
        self.volumes = volume_rows()
        self.counts: object = {"usage": [1, 2, 3, 4], "free": [5000, 6000, 7000, 8000]}
        self.failure: Exception | None = None
        self.result: subprocess.CompletedProcess[str] | None = None

    def __call__(self, argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        import json

        self.calls.append((argv, timeout))
        if self.failure is not None:
            raise self.failure
        if self.result is not None:
            return self.result
        if argv[1] == "container":
            value: object = [self.container]
        elif argv[1] == "volume":
            value = self.volumes
        else:
            assert argv[1:4] == ["exec", "--user", "1000:1000"]
            assert argv[4] == "c" * 64
            assert argv[5:8] == ["/usr/bin/python3", "-I", "-c"]
            assert argv[8] == (LAB / "guest_usage.py").read_text(encoding="utf-8")
            value = self.counts
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")


def test_reader_pins_physical_selection_and_nonroot_probe() -> None:
    fake = FakeDocker()
    host = reader.DockerReader(Path("/synthetic/docker"), run=fake, clock=lambda: 10)
    assert host.inspect(experiment(), "c" * 64) == resources()
    result = host.measure(experiment(), resources())
    assert result == storage.Usage(resources(), 10, 10, 10, 4, 5000)
    assert all(0 < timeout <= 10 for _argv, timeout in fake.calls)
    assert [argv[1] for argv, _timeout in fake.calls] == [
        "container", "volume", "container", "volume", "exec", "container", "volume",
    ]
    assert all("create" not in argv and "start" not in argv for argv, _timeout in fake.calls)


@pytest.mark.parametrize("field,value", [
    ("Name", "foreign"), ("Driver", "nfs"), ("Scope", "global"),
    ("Options", {"device": "/host"}), ("CreatedAt", ""), ("Mountpoint", "relative"),
])
def test_nonordinary_or_unidentified_volumes_refuse(field: str, value: object) -> None:
    fake = FakeDocker()
    fake.volumes[0][field] = value
    host = reader.DockerReader(Path("/synthetic/docker"), run=fake)
    with pytest.raises(reader.ReadError):
        host.inspect(experiment(), "c" * 64)
    assert all(argv[1] != "exec" for argv, _timeout in fake.calls)


def test_duplicate_and_replaced_volumes_refuse_without_probe() -> None:
    fake = FakeDocker()
    fake.volumes[1] = copy.deepcopy(fake.volumes[0])
    host = reader.DockerReader(Path("/synthetic/docker"), run=fake)
    with pytest.raises(reader.ReadError):
        host.measure(experiment(), resources())
    fake.volumes = volume_rows()
    fake.volumes[0]["CreatedAt"] = "2026-01-02T00:00:00Z"
    with pytest.raises(reader.ReadError, match="retained resources"):
        host.measure(experiment(), resources())
    assert all(argv[1] != "exec" for argv, _timeout in fake.calls)


def test_replaced_resource_after_probe_invalidates_observation() -> None:
    fake = FakeDocker()

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        result = fake(argv, timeout=timeout)
        if argv[1] == "exec":
            fake.volumes[0]["CreatedAt"] = "2026-01-02T00:00:00Z"
        return result

    host = reader.DockerReader(Path("/synthetic/docker"), run=run)
    with pytest.raises(reader.ReadError, match="changed during"):
        host.measure(experiment(), resources())


@pytest.mark.parametrize("counts", [
    None, {}, {"usage": [1] * 4}, {"usage": [1] * 3, "free": [1] * 4},
    {"usage": [True] * 4, "free": [1] * 4}, {"usage": [1] * 4, "free": [-1] * 4},
])
def test_incomplete_usage_is_explicitly_unavailable(counts: object) -> None:
    fake = FakeDocker()
    fake.counts = counts
    with pytest.raises(reader.ReadError):
        reader.DockerReader(Path("/synthetic/docker"), run=fake).measure(experiment(), resources())


@pytest.mark.parametrize("failure", [
    OSError("synthetic sensitive payload"),
    subprocess.TimeoutExpired("synthetic", 1),
    UnicodeError("synthetic sensitive payload"),
    RuntimeError("unconfirmed cleanup"),
])
def test_failed_refresh_cannot_reuse_last_success(failure: Exception) -> None:
    fake = FakeDocker()
    fake.failure = failure
    observer = monitor()
    observer.record(usage(), now=11)
    host = reader.DockerReader(Path("/synthetic/docker"), run=fake, clock=lambda: 11)
    with pytest.raises(reader.ReadError) as caught:
        host.refresh(observer)
    assert "sensitive payload" not in str(caught.value)
    assert observer.latest is None
    assert observer.refusals(now=11)


def test_expired_or_closed_monitor_does_not_launch_observer() -> None:
    fake = FakeDocker()
    observer = monitor()
    host = reader.DockerReader(Path("/synthetic/docker"), run=fake, clock=lambda: 60)
    with pytest.raises(reader.ReadError):
        host.refresh(observer)
    observer.close()
    with pytest.raises(reader.ReadError):
        host.refresh(observer)
    assert fake.calls == []


@pytest.mark.parametrize("result", [
    subprocess.CompletedProcess([], 1, "not-json", "sensitive payload"),
    subprocess.CompletedProcess([], 0, "not-json", ""),
    subprocess.CompletedProcess([], 0, "{}", ""),
])
def test_reader_failure_is_not_a_clean_empty_inventory(
    result: subprocess.CompletedProcess[str],
) -> None:
    fake = FakeDocker()
    fake.result = result
    with pytest.raises(reader.ReadError) as caught:
        reader.DockerReader(Path("/synthetic/docker"), run=fake).inspect(experiment(), "c" * 64)
    assert "sensitive payload" not in str(caught.value)


def test_static_refusal_precedes_any_guest_probe() -> None:
    fake = FakeDocker()
    fake.container["HostConfig"]["Privileged"] = True
    with pytest.raises(reader.ReadError, match="unprivileged"):
        reader.DockerReader(Path("/synthetic/docker"), run=fake).measure(experiment(), resources())
    assert len(fake.calls) == 1


@pytest.mark.parametrize("change", ["subpath", "missing", "malformed", "foreign", "duplicate"])
def test_whole_volume_declarations_are_required_before_probe(change: str) -> None:
    fake = FakeDocker()
    mounts = fake.container["HostConfig"]["Mounts"]
    if change == "subpath":
        mounts[0]["VolumeOptions"] = {"Subpath": "visible-only"}
    elif change == "missing":
        del fake.container["HostConfig"]["Mounts"]
    elif change == "malformed":
        mounts[0]["VolumeOptions"] = None
    elif change == "foreign":
        mounts[0]["Source"] = "foreign-volume"
    else:
        mounts[1] = copy.deepcopy(mounts[0])
    with pytest.raises(reader.ReadError, match="volume"):
        reader.DockerReader(Path("/synthetic/docker"), run=fake).measure(experiment(), resources())
    assert all(argv[1] != "exec" for argv, _timeout in fake.calls)


@pytest.mark.parametrize("source", [None, "/volumes/subtree", "/volumes/foreign", ""])
def test_effective_mount_source_must_match_the_observed_volume_root(source: object) -> None:
    fake = FakeDocker()
    fake.container["Mounts"][0]["Source"] = source
    with pytest.raises(reader.ReadError, match="full volume root"):
        reader.DockerReader(Path("/synthetic/docker"), run=fake).measure(experiment(), resources())
    assert all(argv[1] != "exec" for argv, _timeout in fake.calls)


def test_explicit_empty_subpath_still_exposes_the_full_volume() -> None:
    fake = FakeDocker()
    fake.container["HostConfig"]["Mounts"][0]["VolumeOptions"] = {"Subpath": ""}
    host = reader.DockerReader(Path("/synthetic/docker"), run=fake)
    assert host.inspect(experiment(), "c" * 64) == resources()


def test_one_deadline_bounds_the_whole_measurement() -> None:
    fake = FakeDocker()
    now = [0]

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        result = fake(argv, timeout=timeout)
        now[0] += 10
        return result

    host = reader.DockerReader(Path("/synthetic/docker"), run=run, clock=lambda: now[0])
    with pytest.raises(reader.ReadError, match="deadline exhausted"):
        host.measure(experiment(), resources())
    assert len(fake.calls) == 3


def test_final_docker_settlement_cannot_outlive_observation_deadline() -> None:
    fake = FakeDocker()
    now = [0]

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        result = fake(argv, timeout=timeout)
        if len(fake.calls) == 5:
            now[0] = 30
        return result

    observer = monitor()
    host = reader.DockerReader(Path("/synthetic/docker"), run=run, clock=lambda: now[0])
    with pytest.raises(reader.ReadError, match="deadline exhausted"):
        host.refresh(observer)
    assert observer.latest is None
    assert observer.refusals(now=30)
    assert len(fake.calls) == 5


def test_json_processing_is_inside_the_observation_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocker()
    now = [0]
    original_loads = reader.json.loads

    def loads(value: str) -> object:
        parsed = original_loads(value)
        now[0] = 30
        return parsed

    monkeypatch.setattr(reader.json, "loads", loads)
    host = reader.DockerReader(Path("/synthetic/docker"), run=fake, clock=lambda: now[0])
    with pytest.raises(reader.ReadError, match="deadline exhausted"):
        host.inspect(experiment(), "c" * 64)


def test_final_sample_timestamp_must_still_be_inside_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker()
    now = [0]
    host = reader.DockerReader(Path("/synthetic/docker"), run=fake, clock=lambda: now[0])
    inspections = [0]

    def inspect(*_args: object, **_kwargs: object) -> storage.Resources:
        inspections[0] += 1
        if inspections[0] == 2:
            now[0] = 31
        return resources()

    monkeypatch.setattr(host, "inspect", inspect)
    with pytest.raises(reader.ReadError, match="deadline exhausted"):
        host.measure(experiment(), resources())


def test_selection_is_explicit_and_invalid_id_never_invokes_docker() -> None:
    fake = FakeDocker()
    with pytest.raises(ValueError, match="absolute"):
        reader.DockerReader(Path("docker"), run=fake)
    host = reader.DockerReader(Path("/synthetic/docker"), run=fake)
    with pytest.raises(ValueError, match="physical container ID"):
        host.inspect(experiment(), "reproducer-lab-synthetic")
    assert fake.calls == []


def test_shared_finite_process_owner_has_explicit_capture_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(LAB.parents[1] / "libs" / "agent-procutil" / "src"))
    from agent_procutil import owned_process

    calls: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    monkeypatch.setattr(owned_process, "run_caller_owned", run)
    reader._run(["/synthetic/docker", "container", "inspect", "c" * 64], timeout=7)
    assert calls == [{
        "timeout": 7, "max_stdout_bytes": 256 * 1024, "max_stderr_bytes": 16 * 1024,
        "input_text": None, "capture": True,
    }]


def test_probe_counts_all_roots_including_hidden_and_sparse_files(tmp_path: Path) -> None:
    roots = tuple(str(tmp_path / str(index)) for index in range(4))
    for root in roots:
        Path(root).mkdir()
        (Path(root) / ".hidden").write_bytes(b"sample")
    sparse = Path(roots[3]) / "sparse"
    with sparse.open("wb") as handle:
        handle.truncate(12345)
    (Path(roots[0]) / "outside").symlink_to(tmp_path / "not-present")
    counts = guest_usage.measure(roots)
    assert len(counts["usage"]) == len(counts["free"]) == 4
    assert counts["usage"][3] >= 12345
    assert all(value > 0 for value in counts["free"])


def test_probe_does_not_follow_root_symlink_or_hide_missing_root(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="not a directory"):
        guest_usage.measure((str(link),))
    with pytest.raises(FileNotFoundError):
        guest_usage.measure((str(tmp_path / "missing"),))


def test_probe_entry_bound_refuses_instead_of_reporting_partial_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "file").write_bytes(b"sample")
    monkeypatch.setattr(guest_usage, "MAX_ENTRIES", 0)
    with pytest.raises(TimeoutError, match="bound exceeded"):
        guest_usage.measure((str(tmp_path),))


def test_probe_bounds_pending_descriptors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "directory").mkdir()
    monkeypatch.setattr(guest_usage, "MAX_OPEN_DIRECTORIES", 0)
    with pytest.raises(ValueError, match="descriptor bound exceeded"):
        guest_usage.measure((str(tmp_path),))


def test_probe_does_not_hide_unreadable_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(_path: object) -> Any:
        raise PermissionError("synthetic unreadable directory")

    monkeypatch.setattr(guest_usage.os, "scandir", denied)
    with pytest.raises(PermissionError):
        guest_usage.measure((str(tmp_path),))
