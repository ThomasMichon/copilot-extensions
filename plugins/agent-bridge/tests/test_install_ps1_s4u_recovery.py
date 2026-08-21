"""Regression guards for Windows S4U scheduled-task recovery (#876)."""

from __future__ import annotations

from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"


def _text() -> str:
    return _INSTALL_PS1.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    text = _text()
    start = text.index(f"function {name} ")
    end = text.find("\nfunction ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_scheduler_has_not_run_result_is_read_through_guarded_helper():
    text = _text()
    assert "$ScheduledTaskHasNotRunResult = 267011" in text

    body = _function_body("Get-ScheduledTaskLastResult")
    assert "Get-ScheduledTaskInfo -TaskName $Name -ErrorAction Stop" in body
    assert "return [int64]$info.LastTaskResult" in body
    assert "catch" in body and "return $null" in body


def test_explicit_noninteractive_selection_precedes_failed_task_recovery():
    body = _function_body("Resolve-DaemonLogonMode")
    explicit = body.index("if ($NonInteractive")
    task_result = body.index("$lastTaskResult = Get-ScheduledTaskLastResult")
    assert explicit < task_result, (
        "An explicit flag/env opt-in must keep intentional non-interactive installations"
    )


def test_never_run_s4u_task_does_not_override_interactive_default():
    body = _function_body("Resolve-DaemonLogonMode")
    failure = body.index("($existing.Principal.LogonType -eq 'S4U')")
    preservation = body.index("$Script:UseNonInteractive = $true", failure)
    recovery = body[failure:preservation]

    assert "$lastTaskResult -eq $ScheduledTaskHasNotRunResult" in recovery
    assert "selecting the default interactive AtLogOn mode" in recovery
    assert "return" in recovery


def test_requested_s4u_start_diagnoses_token_failure_and_recovery():
    body = _function_body("Invoke-Start")
    requested_start = body.index("Start-ScheduledTask -TaskName $TaskName")
    task_result = body.index(
        "$lastTaskResult = Get-ScheduledTaskLastResult", requested_start
    )
    diagnosis = body.index("SCHED_S_TASK_HAS_NOT_RUN", task_result)

    assert requested_start < task_result < diagnosis
    assert "could not acquire the S4U logon token" in body[diagnosis:]
    assert "default interactive AtLogOn task" in body[diagnosis:]
