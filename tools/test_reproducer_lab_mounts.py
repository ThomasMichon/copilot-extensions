"""Synthetic mount posture and finite reader controls; never invoke Docker."""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_reproducer_lab_monitoring import LAB, FakeDocker, experiment, resources

import guest_mounts
import mounts
import reader

TABLE = (
    "1 9 0:1 / / ro,relatime - overlay overlay ro\n"
    "2 1 0:2 / /tmp rw,nosuid,nodev - tmpfs tmpfs rw\n"
    "3 1 0:3 / /run rw,nosuid,nodev - tmpfs tmpfs rw\n"
)


def observation() -> dict[str, Any]:
    return {
        "uid": 1000, "gid": 1000, "mountinfo": TABLE,
        "roots": [
            {"path": path, "uid": 1000, "gid": 1000, "mode": 0o700,
             "device": device, "bytes": size, "readonly": False}
            for path, device, size in (
                ("/tmp", "0:2", experiment().budgets.tmp_bytes),
                ("/run", "0:3", experiment().budgets.run_bytes),
            )
        ],
    }


def test_effective_mounts_are_evidence_not_experiment_admission() -> None:
    result = mounts.inspect_volatile_mounts(experiment(), observation())
    assert result == mounts.VolatileMounts(
        1, 2, 3, experiment().budgets.tmp_bytes, experiment().budgets.run_bytes,
    )
    assert not hasattr(result, "ready")


@pytest.mark.parametrize("old,new", [
    ("/ ro,relatime", "/ rw,relatime"),
    ("/ ro,relatime", "/ ro,rw"),
    ("/tmp rw,nosuid,nodev", "/tmp rw,nodev"),
    ("/tmp rw,nosuid,nodev", "/tmp rw,nosuid"),
    ("/tmp rw,nosuid,nodev", "/tmp ro,nosuid,nodev"),
    ("- tmpfs tmpfs", "- ext4 tmpfs"),
    ("0:2 / /tmp", "0:2 /subpath /tmp"),
    ("0:2 / /tmp", "0:3 / /tmp"),
    ("0:2 / /tmp", "0:1 / /tmp"),
    ("2 1 0:2", "1 1 0:2"),
    ("/tmp rw,nosuid,nodev", "/tmp rw,nosuid,nodev,nodev"),
    ("/tmp rw,nosuid,nodev", "/tmp rw,nosuid,nodev,suid"),
    ("/tmp rw,nosuid,nodev", "/tmp rw,nosuid,nodev,dev"),
    ("/tmp rw,nosuid,nodev", "/tmp rw,,nosuid,nodev"),
    ("/tmp", "/tmp/../tmp"),
    ("/tmp", "/./tmp"),
    ("/tmp", "//tmp"),
    ("/tmp", r"/t\777mp"),
    ("2 1 0:2", "2 x 0:2"),
    ("2 1 0:2", "2 1 bad"),
    (" - tmpfs", " tmpfs"),
])
def test_unsafe_missing_or_ambiguous_mount_posture_refuses(old: str, new: str) -> None:
    value = observation()
    value["mountinfo"] = TABLE.replace(old, new)
    with pytest.raises(ValueError):
        mounts.inspect_volatile_mounts(experiment(), value)


@pytest.mark.parametrize("suffix", [
    TABLE.splitlines(keepends=True)[1],
    "4 2 0:4 / /tmp/nested rw - tmpfs tmpfs rw\n",
    "4 3 0:4 / /run/nested rw - tmpfs tmpfs rw\n",
])
def test_shadowed_or_nested_mounts_refuse(suffix: str) -> None:
    value = observation()
    value["mountinfo"] += suffix
    with pytest.raises(ValueError):
        mounts.inspect_volatile_mounts(experiment(), value)


@pytest.mark.parametrize("table", ["", TABLE[:-1], "x" * (32 * 1024 + 1), "\u00e9" * (32 * 1024)])
def test_mount_table_bytes_and_truncation_are_bounded(table: str) -> None:
    value = observation()
    value["mountinfo"] = table
    with pytest.raises(ValueError):
        mounts.inspect_volatile_mounts(experiment(), value)


def test_exact_record_ceiling_and_escaped_unrelated_path() -> None:
    value = observation()
    value["mountinfo"] += "4 1 0:4 / /other\\040path ro - tmpfs tmpfs ro\n"
    assert mounts.inspect_volatile_mounts(experiment(), value).tmp_id == 2
    value["mountinfo"] += "".join(
        f"{index} 1 1:{index} / /other-{index} ro - tmpfs tmpfs ro\n" for index in range(5, 257)
    )
    assert mounts.inspect_volatile_mounts(experiment(), value).run_id == 3
    value["mountinfo"] += "257 1 1:257 / /excess ro - tmpfs tmpfs ro\n"
    with pytest.raises(ValueError, match="record ceiling"):
        mounts.inspect_volatile_mounts(experiment(), value)


@pytest.mark.parametrize("field,value", [
    ("uid", 0), ("gid", 0), ("uid", True), ("roots", []), ("mountinfo", None),
])
def test_wire_observation_requires_complete_nonroot_fields(field: str, value: object) -> None:
    observed = observation()
    observed[field] = value
    with pytest.raises(ValueError):
        mounts.inspect_volatile_mounts(experiment(), observed)


@pytest.mark.parametrize("field,value", [
    ("path", "/foreign"), ("uid", 0), ("gid", 0), ("mode", 0o777),
    ("bytes", True), ("bytes", 0), ("device", "0:99"), ("readonly", True), ("readonly", 0),
])
def test_actual_volatile_root_fields_are_checked(field: str, value: object) -> None:
    observed = observation()
    observed["roots"][0][field] = value
    with pytest.raises(ValueError):
        mounts.inspect_volatile_mounts(experiment(), observed)


@pytest.mark.parametrize("change", [-1, 1])
def test_actual_capacity_must_exactly_match_not_round_past_declaration(change: int) -> None:
    observed = observation()
    observed["roots"][0]["bytes"] += change
    with pytest.raises(ValueError):
        mounts.inspect_volatile_mounts(experiment(), observed)


def test_duplicate_roots_and_extra_fields_refuse() -> None:
    observed = observation()
    observed["roots"][1] = copy.deepcopy(observed["roots"][0])
    with pytest.raises(ValueError):
        mounts.inspect_volatile_mounts(experiment(), observed)
    observed = observation()
    observed["extra"] = True
    with pytest.raises(ValueError):
        mounts.inspect_volatile_mounts(experiment(), observed)
    observed = observation()
    observed["roots"][0]["extra"] = True
    with pytest.raises(ValueError):
        mounts.inspect_volatile_mounts(experiment(), observed)


class MountDocker(FakeDocker):
    def __call__(self, argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        if argv[1] != "exec":
            return super().__call__(argv, timeout=timeout)
        self.calls.append((argv, timeout))
        assert argv[1:8] == [
            "exec", "--user", "1000:1000", "c" * 64, "/usr/bin/python3", "-I", "-c",
        ]
        assert argv[8] == (LAB / "guest_mounts.py").read_text()
        return subprocess.CompletedProcess(argv, 0, json.dumps(observation()), "")


def test_finite_mount_reader_uses_same_selection_and_fixed_nonroot_probe() -> None:
    fake = MountDocker()
    host = reader.DockerReader(Path("/selected/docker"), run=fake, clock=lambda: 0)
    assert host.volatile_mounts(experiment(), resources()).tmp_id == 2
    assert [argv[1] for argv, _timeout in fake.calls] == ["container", "volume", "exec", "container", "volume"]
    assert all(0 < timeout <= 10 for _argv, timeout in fake.calls)


@pytest.mark.parametrize("failure_at", [1, 2, 3, 4, 5])
def test_mount_read_failures_never_retry_or_report_success(failure_at: int) -> None:
    fake = MountDocker()

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        if len(fake.calls) + 1 == failure_at:
            fake.calls.append((argv, timeout))
            raise subprocess.TimeoutExpired(argv, timeout)
        return fake(argv, timeout=timeout)

    with pytest.raises(reader.ReadError):
        reader.DockerReader(Path("/selected/docker"), run=run).volatile_mounts(experiment(), resources())
    assert len(fake.calls) == failure_at


def test_mount_reader_checks_final_settlement_and_resource_replacement() -> None:
    fake = MountDocker()
    now = 0.0

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal now
        result = fake(argv, timeout=timeout)
        if len(fake.calls) == 5:
            now = 30
        return result

    with pytest.raises(reader.ReadError, match="deadline"):
        reader.DockerReader(Path("/selected/docker"), run=run, clock=lambda: now).volatile_mounts(
            experiment(), resources(),
        )
    fake = MountDocker()
    fake.volumes[0]["CreatedAt"] = "changed"
    with pytest.raises(reader.ReadError, match="retained resources"):
        reader.DockerReader(Path("/selected/docker"), run=fake).volatile_mounts(experiment(), resources())
    assert len(fake.calls) == 2


@pytest.fixture
def guest_probe(
    tmp_path: Path, monkeypatch: Any,
) -> list[int]:
    if os.name != "posix":
        pytest.skip("local directory-descriptor fixtures require POSIX")
    selected = {name: tmp_path / name[1:] for name in ("/tmp", "/run")}
    for path in selected.values():
        path.mkdir(mode=0o700)
    opened: list[int] = []
    original_open, original_lstat = os.open, os.lstat

    def open_root(path: str, flags: int) -> int:
        assert flags == os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = original_open(selected[path], flags)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(guest_mounts, "os", SimpleNamespace(
        open=open_root, lstat=lambda path: original_lstat(selected[path]),
        fstat=os.fstat, fstatvfs=os.fstatvfs, close=os.close,
        major=os.major, minor=os.minor, geteuid=lambda: 1000, getegid=lambda: 1000,
        O_RDONLY=os.O_RDONLY, O_DIRECTORY=os.O_DIRECTORY, O_NOFOLLOW=os.O_NOFOLLOW,
        ST_RDONLY=os.ST_RDONLY, path=os.path,
    ))
    monkeypatch.setattr(guest_mounts, "_table", lambda: TABLE)
    return opened


def test_fixed_guest_probe_reads_only_selected_roots_and_closes_descriptors(
    guest_probe: list[int],
) -> None:
    result = guest_mounts.observe()
    assert [row["path"] for row in result["roots"]] == ["/tmp", "/run"]
    assert result["mountinfo"] == TABLE
    for descriptor in guest_probe:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_guest_mount_table_change_and_elapsed_deadline_refuse(
    guest_probe: list[int], monkeypatch: Any,
) -> None:
    tables = iter([TABLE, TABLE + "changed"])
    monkeypatch.setattr(guest_mounts, "_table", lambda: next(tables))
    with pytest.raises(ValueError, match="changed or expired"):
        guest_mounts.observe()
    monkeypatch.setattr(guest_mounts, "_table", lambda: TABLE)
    clock = iter([0, guest_mounts.MAX_SECONDS])
    monkeypatch.setattr(guest_mounts, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(ValueError, match="changed or expired"):
        guest_mounts.observe()
    for descriptor in guest_probe:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    with pytest.raises(TimeoutError):
        guest_mounts._expired(0, None)


def test_guest_table_reader_stops_at_its_byte_ceiling(tmp_path: Path, monkeypatch: Any) -> None:
    table = tmp_path / "mountinfo"
    table.write_bytes(b"x" * (guest_mounts.MAX_TABLE_BYTES + 1))
    monkeypatch.setattr(guest_mounts, "open", lambda *_args: table.open("rb"), raising=False)
    with pytest.raises(ValueError, match="ceiling"):
        guest_mounts._table()


def test_physical_storage_replacement_after_mount_probe_refuses() -> None:
    fake = MountDocker()

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        result = fake(argv, timeout=timeout)
        if len(fake.calls) == 3:
            fake.volumes[0]["CreatedAt"] = "replaced-after-probe"
        return result

    with pytest.raises(reader.ReadError, match="changed during"):
        reader.DockerReader(Path("/selected/docker"), run=run).volatile_mounts(experiment(), resources())


@pytest.mark.parametrize("cause", ["changed-root", "stat-failed"])
def test_guest_probe_closes_owned_descriptor_on_failed_observation(
    guest_probe: list[int], monkeypatch: Any, cause: str,
) -> None:
    if cause == "changed-root":
        monkeypatch.setattr(
            guest_mounts.os, "lstat", lambda _path: SimpleNamespace(st_dev=0, st_ino=0),
        )
        error = ValueError
    else:
        def fail(_descriptor: int) -> object:
            raise OSError("synthetic-stat-failure")
        monkeypatch.setattr(guest_mounts.os, "fstat", fail)
        error = OSError
    with pytest.raises(error):
        guest_mounts.observe()
    assert len(guest_probe) == 1
    with pytest.raises(OSError):
        os.fstat(guest_probe[0])
