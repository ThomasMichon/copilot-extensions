"""Regression guard for coordinator stop/restart termination (#3602).

`install.ps1` launches the coordinator (and the embody supervisor) under
`conhost.exe --headless`, which DETACHES the `python -m agent_dispatch serve`
process from the Scheduled Task's tracked process tree. `Stop-ScheduledTask`
therefore does NOT terminate the running coordinator: a `stop` reported success
while the process kept serving, and an `update` rebuilt the venv but left the
OLD build serving the rendezvous endpoint (version drift).

These tests read `install.ps1` as text and assert the lifecycle actions
actively terminate the detached process, so the regression cannot silently
return.
"""

from __future__ import annotations

import re
from pathlib import Path

INSTALL_PS1 = Path(__file__).resolve().parent.parent / "scripts" / "install.ps1"


def _function_body(name: str) -> str:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\{", text)
    assert m, f"could not locate {name} in install.ps1"
    rest = text[m.end():]
    nxt = re.search(r"\nfunction ", rest)
    return rest[: nxt.start()] if nxt else rest


def test_stop_dispatch_process_helper_exists():
    """A helper must terminate the detached process (not just Stop-ScheduledTask)."""
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert re.search(r"function\s+Stop-DispatchProcess\b", text), (
        "install.ps1 must define Stop-DispatchProcess to kill the detached "
        "coordinator/supervisor process (#3602)"
    )


def test_stop_dispatch_process_uses_rendezvous_pid_and_cmdline():
    """It resolves the live PID from the rendezvous endpoint AND matches the
    `-m agent_dispatch <subcommand>` command line, then kills the process."""
    body = _function_body("Stop-DispatchProcess")
    assert "endpoint.json" in body, (
        "Stop-DispatchProcess must read the rendezvous endpoint.json for the "
        "live coordinator PID"
    )
    assert "Win32_Process" in body and "CommandLine" in body, (
        "Stop-DispatchProcess must match the running process by its command line"
    )
    assert "Stop-Process" in body, "Stop-DispatchProcess must actually kill the process"


def test_stop_dispatch_process_clears_stale_endpoint():
    """After killing the coordinator it removes the stale rendezvous file so a
    client does not chase a dead endpoint."""
    body = _function_body("Stop-DispatchProcess")
    assert "Remove-Item" in body and "endpoint" in body.lower(), (
        "Stop-DispatchProcess must clear the stale endpoint.json"
    )


def test_invoke_stop_terminates_process_not_just_task():
    """`stop` must terminate the coordinator process, not only the Scheduled Task."""
    body = _function_body("Invoke-Stop")
    assert "Stop-DispatchProcess -Subcommand serve" in body, (
        "Invoke-Stop must call Stop-DispatchProcess for the coordinator so the "
        "detached process is actually terminated (#3602)"
    )


def test_invoke_update_cycles_the_coordinator_before_reinstall():
    """`update` must terminate the OLD coordinator before re-registering/starting,
    so the freshly-rebuilt build takes over instead of the survivor."""
    body = _function_body("Invoke-Update")
    stop_idx = body.find("Stop-DispatchProcess -Subcommand serve")
    reinstall_idx = body.find("Install-CoordinatorTask")
    assert stop_idx != -1, "Invoke-Update must stop the stale coordinator process"
    assert reinstall_idx != -1, "Invoke-Update must (re)install the coordinator task"
    assert stop_idx < reinstall_idx, (
        "Invoke-Update must terminate the old coordinator BEFORE Install-"
        "CoordinatorTask restarts it, or the old build keeps the endpoint"
    )


def test_update_and_start_verify_running_version():
    """After (re)start, the installer verifies the coordinator actually answers
    and runs the installed build (catches the version-drift symptom)."""
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert re.search(r"function\s+Confirm-CoordinatorRunning\b", text), (
        "install.ps1 must define Confirm-CoordinatorRunning"
    )
    assert "Confirm-CoordinatorRunning" in _function_body("Invoke-Update")
    assert "Confirm-CoordinatorRunning" in _function_body("Invoke-Start")
