"""Regression: Sync-ConnectionOwnerService must not rewrite an already-
correct scheduled task on every routine run.

`Register-ScheduledTask -Force` requires the same Task Scheduler ACL write
permission regardless of whether the new definition actually differs from
what is already registered. Calling it unconditionally on every install/
update run means a routine, nothing-changed run fails with Access Denied
on any non-elevated host whose task ACL was ever touched by an elevated
run -- forever, since there is no way to make the values "different enough"
to succeed. The task's action already points at a stable binstub + a
stable resolved host binary (pwsh/powershell.exe), so it is meant to be
byte-identical across routine updates; the fix is to compare before writing
and skip the elevation-requiring call entirely when nothing changed
(mirrors agent-vault's Register-AgentVaultTask and agent-index's
Test-TaskCurrent / Register-UserModeTask, both established exemplars of
this pattern -- see docs/patterns/service-lifecycle-supervision.md).
"""

from __future__ import annotations

from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
INSTALL_PS1 = PLUGIN / "scripts" / "install.ps1"


def _sync_connection_owner_service_body() -> str:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    rest = text.split("function Sync-ConnectionOwnerService", 1)[1]
    # Closed by a `}` at column 0, the file's top-level function-closing
    # convention (matches agent-logger's test_install_binstub.py style).
    return rest.split("\n}\n", 1)[0]


def test_connection_owner_service_skips_reregister_when_action_unchanged() -> None:
    body = _sync_connection_owner_service_body()

    assert "Get-ScheduledTask -TaskName $OwnerTaskName -ErrorAction SilentlyContinue" in body
    assert "$existingAction.Execute -eq $action.Execute" in body
    assert "already correct" in body
    # The unconditional-write call must now be reached only on the genuine-
    # deviation branch, not called bare at the top of the function.
    assert body.count("Register-ScheduledTask -TaskName $OwnerTaskName") == 1


def test_connection_owner_service_still_registers_on_real_deviation() -> None:
    # The actual write path must still exist (not deleted outright) -- this
    # guards against a fix that silently no-ops registration forever instead
    # of correctly gating it.
    body = _sync_connection_owner_service_body()
    assert "Register-ScheduledTask -TaskName $OwnerTaskName -Action $action -Trigger $trigger" in body
    assert "-Force -ErrorAction Stop" in body
