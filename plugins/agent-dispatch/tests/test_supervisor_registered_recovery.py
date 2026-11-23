"""Native-custody recovery for legacy physical-Python supervisors."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent_dispatch import supervisor_processes
from agent_dispatch.supervisor_processes import WindowsProcess, retire_windows_supervisor_generations

RegisteredSupervisor = tuple[
    Path, WindowsProcess, WindowsProcess, dict[str, str], list[dict[str, str]],
]


@pytest.fixture
def registered_base_supervisor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> RegisteredSupervisor:
    from agent_procutil import _daemon_instance_identity as identity, daemon_instances

    base = r"C:\Python311\python.exe"
    master = WindowsProcess(71001, 1, base, f'"{base}" -m agent_dispatch supervise serve', "12345")
    child = WindowsProcess(71003, master.pid, r"C:\Tools\child.exe", "owned-child")
    record = {
        "pid": str(master.pid), "birth": "windows:12345", "runtime_root": str(tmp_path),
        "runtime_device": "device", "runtime_inode": "inode", "protected": "0",
        "service": "agent-dispatch", "purpose": "production",
        "lifecycle": "resident", "state": "running",
    }
    records = [record]
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda: records)
    monkeypatch.setattr(identity, "path_identity", lambda _: ("device", "inode"))
    monkeypatch.setattr(identity, "identity_state", lambda *_: "matching")
    return tmp_path, master, child, record, records


def test_registered_base_supervisor_recovery_uses_existing_tree_and_retirement_owner(
    registered_base_supervisor: RegisteredSupervisor,
) -> None:
    root, master, child, _, _ = registered_base_supervisor
    coordinator = replace(master, pid=71005, command_line='"python.exe" -m agent_dispatch serve')
    stopped: list[int] = []

    def terminate(pid: int) -> bool:
        stopped.append(pid)
        return True

    result = retire_windows_supervisor_generations(
        root, platform_name="nt", list_processes=lambda: [master, child, coordinator],
        terminate=terminate,
    )
    assert result.ok
    assert result.selected == result.retired == stopped == [master.pid, child.pid]


@pytest.mark.parametrize("change", [
    "absent", "foreign-root", "root-identity", "birth", "protected", "purpose",
    "service", "state", "lifecycle", "reused", "coordinator", "client", "no-census-birth",
])
def test_registered_base_supervisor_refuses_unconfirmed_or_unrelated_work(
    registered_base_supervisor: RegisteredSupervisor, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_procutil import _daemon_instance_identity as identity

    root, master, _, record, records = registered_base_supervisor
    if change == "absent":
        records.clear()
    elif change == "foreign-root":
        record["runtime_root"] += "-foreign"
    elif change == "root-identity":
        record["runtime_inode"] = "replaced"
    elif change == "birth":
        record["birth"] = "windows:54321"
    elif change in {"protected", "purpose", "service", "state", "lifecycle"}:
        record[change] = {
            "protected": "1", "purpose": "unattended-test", "service": "foreign-service",
            "state": "reserved", "lifecycle": "finite-worker",
        }[change]
    elif change == "reused":
        monkeypatch.setattr(identity, "identity_state", lambda *_: "reused")
    elif change in {"coordinator", "client"}:
        master = replace(master, command_line='"python.exe" -m agent_dispatch ' + (
            "serve" if change == "coordinator" else "supervise list"
        ))
    else:
        master = replace(master, start_time="")
    assert supervisor_processes._registered_supervisor_roots([master], root) == set()


def test_unavailable_legacy_custody_refuses_retirement_before_any_signal(
    registered_base_supervisor: RegisteredSupervisor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import daemon_instances

    root, master, child, _, _ = registered_base_supervisor
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda: (
        (_ for _ in ()).throw(PermissionError("native registry unavailable"))
    ))
    result = retire_windows_supervisor_generations(
        root, platform_name="nt", list_processes=lambda: [master, child],
        terminate=lambda _: pytest.fail("unavailable custody must not authorize retirement"),
    )
    assert not result.ok
    assert result.selected == result.retired == []
    assert "native registry unavailable" in result.errors[0]
