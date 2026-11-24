"""Native-custody recovery for legacy physical-Python supervisors."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest

from agent_dispatch import supervisor_processes
from agent_dispatch.procutil import no_window_kwargs, powershell_host
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


def test_registered_supervisor_expands_addressed_short_path_before_receipt_comparison(
    registered_base_supervisor: RegisteredSupervisor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import _daemon_instance_store as store

    root, master, _, _, _ = registered_base_supervisor
    alias = Path(r"C:\PROGRA~1\Dispatch")
    expanded: list[Path] = []

    def expand(path: Path) -> Path:
        expanded.append(path)
        return root if path == alias else path

    monkeypatch.setattr(store, "_expanded_path", expand)
    assert supervisor_processes._registered_supervisor_roots([master], alias) == {master.pid}
    assert expanded == [alias]


@pytest.mark.parametrize("change", [
    "absent", "foreign-root", "root-identity", "birth", "protected", "purpose",
    "service", "state", "lifecycle", "dead", "reused", "coordinator", "client", "no-census-birth",
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
    elif change in {"dead", "reused"}:
        monkeypatch.setattr(identity, "identity_state", lambda *_: change)
    elif change in {"coordinator", "client"}:
        master = replace(master, command_line='"python.exe" -m agent_dispatch ' + (
            "serve" if change == "coordinator" else "supervise list"
        ))
    else:
        master = replace(master, start_time="")
    assert supervisor_processes._registered_supervisor_roots([master], root) == set()


@pytest.mark.parametrize("failure", ["registry", "path-identity", "identity-observation"])
def test_unavailable_legacy_custody_refuses_retirement_before_any_signal_or_native_fallback(
    registered_base_supervisor: RegisteredSupervisor, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from agent_procutil import _daemon_instance_identity as identity, daemon_instances

    root, master, child, _, _ = registered_base_supervisor
    if failure == "registry":
        monkeypatch.setattr(daemon_instances, "inspect_instances", lambda: (
            (_ for _ in ()).throw(PermissionError("native registry unavailable"))
        ))
    elif failure == "path-identity":
        monkeypatch.setattr(identity, "path_identity", lambda _: (
            (_ for _ in ()).throw(PermissionError("native path identity unavailable"))
        ))
    else:
        monkeypatch.setattr(identity, "identity_state", lambda *_: "unknown")
    result = retire_windows_supervisor_generations(
        root, platform_name="nt", list_processes=lambda: [master, child],
        terminate=lambda _: pytest.fail("unavailable custody must not authorize retirement"),
    )
    assert not result.ok
    assert result.selected == result.retired == []
    assert result.errors[0].startswith("native supervisor custody unavailable:")
    if os.name != "nt":
        return
    plugin = Path(__file__).resolve().parents[1]
    source = (plugin / "scripts/install.ps1").read_text(encoding="utf-8")
    start = source.index("function Retire-SupervisorProcesses {")
    function = source[start:source.index("\nfunction ", start + 1)]
    script = root / "caller.ps1"
    payload = json.dumps({
        "ok": result.ok, "selected": result.selected, "retired": result.retired, "errors": result.errors,
    }).replace("'", "''")
    script.write_text("\n".join([
        "$ErrorActionPreference='Stop'; $env:OS='Windows_NT'",
        f"$InstallDir='{str(root).replace(chr(39), chr(39) * 2)}'",
        "$VenvPython='Invoke-FixtureRetirement'; $LinkPython='unused'",
        f". '{plugin / 'scripts/supervisor-lifecycle.ps1'}'",
        "function Test-Path { param($LiteralPath) $LiteralPath -eq 'Invoke-FixtureRetirement' }",
        "function Invoke-SupervisorAudit { }",
        "function Write-Warn { }",
        f"function Invoke-FixtureRetirement {{ $global:LASTEXITCODE=1; '{payload}' }}",
        "function Retire-SupervisorProcessesFallback { throw 'native fallback must not retire refused work' }",
        function,
        "if((Retire-SupervisorProcesses) -ne 0){throw 'unexpected retirement'}",
        "Write-Output 'CUSTODY_FAILURE_PRESERVED'",
    ]), encoding="utf-8")
    observed = subprocess.run(
        [powershell_host(), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    assert "CUSTODY_FAILURE_PRESERVED" in observed.stdout
