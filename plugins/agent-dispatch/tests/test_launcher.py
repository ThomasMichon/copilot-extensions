"""Regression guard for the generated Windows coordinator launcher.

`install.ps1` writes `serve-service.ps1`, which the `-AtLogOn` Scheduled Task
runs headless via `conhost --headless`. The launcher used to run

    $ErrorActionPreference = 'Stop'
    ...
    & <python> -m agent_dispatch serve *>> $logFile

Uvicorn logs to **stderr**; PowerShell wraps a native command's stderr as a
terminating `NativeCommandError`, so under `Stop` the coordinator was killed on
its very first log line -- the task "launched" but never bound a listener
(observed fleet-wide on dev58/dev59 after the #2889 log tee was added). The
serve invocation must drop to `Continue` so stderr is captured, not fatal.

These tests read `install.ps1` as text and assert the safe shape, so the
regression cannot silently return.
"""

from __future__ import annotations

import re
from pathlib import Path

INSTALL_PS1 = (
    Path(__file__).resolve().parent.parent / "scripts" / "install.ps1"
)


def _launcher_serve_region() -> str:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    # The serve invocation is the `& '$VenvPython' -m agent_dispatch serve ...`
    # line inside the here-string that builds $launcherBody.
    match = re.search(
        r"-m agent_dispatch serve[^\r\n]*", text
    )
    assert match, "could not locate the serve invocation in install.ps1"
    # Return the ~5 lines preceding + the serve line for context assertions.
    start = text.rfind("\n", 0, match.start() - 400)
    return text[start : match.end()]


def test_install_ps1_exists():
    assert INSTALL_PS1.is_file(), f"missing {INSTALL_PS1}"


def test_serve_invocation_not_fatal_stream_redirect():
    """The serve line must not use `*>>` -- that merges stderr and, under a
    `Stop` preference, turns uvicorn's stderr into a process-killing error."""
    region = _launcher_serve_region()
    assert "-m agent_dispatch serve *>>" not in region, (
        "serve invocation uses `*>> $logFile`; uvicorn stderr becomes a "
        "terminating NativeCommandError and kills the headless coordinator"
    )


def test_serve_invocation_drops_to_continue():
    """The serve line must be immediately preceded by an ErrorActionPreference
    of 'Continue' so native stderr is non-terminating."""
    region = _launcher_serve_region()
    serve_idx = region.index("-m agent_dispatch serve")
    preceding = region[:serve_idx]
    assert "$ErrorActionPreference = 'Continue'" in preceding, (
        "the serve invocation must be preceded by "
        "$ErrorActionPreference = 'Continue' so uvicorn stderr is not fatal"
    )


def _install_coordinator_task_body() -> str:
    """The body of the `Install-CoordinatorTask` function -- the coordinator's
    scheduled-task registration -- isolated from the separate supervisor task
    (which deliberately keeps a different principal)."""
    text = INSTALL_PS1.read_text(encoding="utf-8")
    m = re.search(r"function\s+Install-CoordinatorTask\s*\{", text)
    assert m, "could not locate Install-CoordinatorTask in install.ps1"
    # Body runs until the next column-0 `function ` declaration.
    rest = text[m.end() :]
    nxt = re.search(r"\n function |\nfunction ", rest)
    return rest[: nxt.start()] if nxt else rest


def test_coordinator_task_runs_headless_via_s4u():
    """The coordinator task must use LogonType S4U so it runs on a headless box
    with no interactive login (Borealis is SSH-only). Interactive registered but
    never fired there."""
    body = _install_coordinator_task_body()
    assert "-LogonType S4U" in body, (
        "coordinator principal must be -LogonType S4U (runs headless, no login); "
        "Interactive only runs with a console session"
    )
    assert "-LogonType Interactive" not in body, (
        "coordinator principal must not be Interactive -- it won't fire on a "
        "headless host"
    )


def test_coordinator_task_starts_at_boot():
    """The coordinator task must have an -AtStartup trigger so it is a true
    always-on service surviving a headless reboot."""
    body = _install_coordinator_task_body()
    assert "-AtStartup" in body, (
        "coordinator task must include a New-ScheduledTaskTrigger -AtStartup so "
        "it comes up at boot without a login"
    )


def test_serve_output_still_captured_to_log():
    """The launcher must still capture serve output to the log for headless
    diagnosability (#2889)."""
    region = _launcher_serve_region()
    serve_line = next(
        line for line in region.splitlines() if "-m agent_dispatch serve" in line
    )
    assert "$logFile" in serve_line and (
        "Out-File" in serve_line or ">>" in serve_line
    ), f"serve output is not captured to the log: {serve_line!r}"
