"""Use the same installation-bound Windows scheduling owner from native startup."""
from __future__ import annotations

import json
import os
from argparse import Namespace
from pathlib import Path
import re
from typing import Any


def ensure_coordinator_command(args: Namespace) -> int:
    """Share local user-mode ensure without a data command's repository effects.

    Explicit lifecycle startup retains disabled start intent and action controls,
    but automatic-only maintenance must not suppress a manual request. Scheduled
    activation performs its automatic admission before calling this entrypoint.
    """
    from .client_authority import local_service_args
    from .config import has_live_local_coordinator
    from .loop_commands import _resolve_cli_module

    _resolve_cli_module()._ensure_local_coordinator(local_service_args(args), automatic=False)
    return 0 if has_live_local_coordinator() else 1


def windows_policy(
    root: Path, *, reconcile: bool = False, observe_supervisor: bool = True,
    supervisor_profile: str | None = None, include_primary: bool = False,
) -> dict[str, Any]:
    default = {"windows_scheduled_task": "preserve", "start_coordinator": True, "start_supervisor": True}
    if include_primary:
        default["primary_start_supervisor"] = True
    if os.name != "nt":
        return default
    selector = os.environ.get("AGENT_DISPATCH_INSTALLATION_CONFIG")
    if selector and not Path(selector).is_absolute():
        raise ValueError("native installation configuration selector must be absolute")
    from agent_procutil.owned_process import run_caller_owned
    from .install_paths import installation_suffix
    from .procutil import powershell_host
    from .source_start import _path_name

    def quote(value: str | Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    suffix = installation_suffix(root)
    suffix = "-" + suffix if suffix else ""
    coordinator, supervisor = "agent-dispatch" + suffix, "agent-dispatch-supervisor" + suffix
    observed_supervisor = supervisor
    if supervisor_profile is not None:
        if re.fullmatch(r"[A-Za-z0-9_-]+", supervisor_profile) is None:
            raise ValueError("invalid native supervisor profile name")
        observed_supervisor += "-" + supervisor_profile
    installed = Path(__file__).with_name("windows-scheduling.ps1")
    helper = _path_name(installed)
    if not helper.is_absolute() or not helper.is_file() or not helper.samefile(installed):
        raise ValueError("Dispatch scheduling helper is not the installed file")
    if _path_name(helper.resolve(strict=True)) != helper:
        raise ValueError("Dispatch scheduling helper must not be redirected")
    command = (
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2; "
        f". {quote(helper)}; $root={quote(root)}; "
        "$config=$env:AGENT_DISPATCH_INSTALLATION_CONFIG; "
        "$policy=Read-DispatchInstallationPolicy -Root $root -Config $config; "
        f"$coordinator={quote(coordinator)}; $supervisor={quote(supervisor)}; "
    )
    if reconcile:
        command += """
if($policy.windows_scheduled_task -eq 'absent') {
 $names=@($coordinator,$supervisor)
 if(Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
  $names+=@(Get-ScheduledTask -ErrorAction Stop | Where-Object {
   $_.TaskName -like "$supervisor-*"
  } | ForEach-Object {$_.TaskName})
 }
 Remove-DispatchScheduledTaskOnly $root $coordinator $supervisor $names $policy
}
if($policy.windows_scheduled_task -eq 'enabled') {
 Register-DispatchExistingLauncherTasks $root $coordinator $supervisor $policy
}
"""
    command += """
if($policy.start_coordinator){
 $task=Get-DispatchNativeTask $coordinator -Root $root
 if($task -and $task.State -eq 'Disabled'){$policy.start_coordinator=$false}
}
"""
    if include_primary:
        command += f"$primary=$policy.start_supervisor; if($primary){{$task=Get-DispatchNativeTask {quote(supervisor)} -Root $root; "
        command += "if($task -and $task.State -eq 'Disabled'){$primary=$false}}; "
        command += "$policy['primary_start_supervisor']=$primary; "
    if observe_supervisor:
        command += f"if($policy.start_supervisor){{$task=Get-DispatchNativeTask {quote(observed_supervisor)} -Root $root; "
        command += "if($task -and $task.State -eq 'Disabled'){$policy.start_supervisor=$false}}; "
    command += "$policy|ConvertTo-Json -Compress"
    cwd = next((path for path in (root, *root.parents) if path.is_dir()), Path.cwd())
    result = run_caller_owned(
        [powershell_host(), "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=cwd, timeout=20, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    if result.returncode:
        raise RuntimeError("Dispatch scheduling intent is unavailable: " + result.stderr)
    policy: dict[str, Any] = json.loads(result.stdout)
    if (
        not isinstance(policy, dict) or set(policy) != set(default)
        or policy["windows_scheduled_task"] not in {"preserve", "enabled", "absent"}
        or type(policy["start_coordinator"]) is not bool
        or type(policy["start_supervisor"]) is not bool
        or (include_primary and type(policy["primary_start_supervisor"]) is not bool)
    ):
        raise RuntimeError("Dispatch scheduling owner returned an invalid policy")
    return policy
