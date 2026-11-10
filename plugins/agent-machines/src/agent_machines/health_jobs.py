"""Read-only native job preparation; registration has a separate effect owner."""

from __future__ import annotations

import re
import subprocess
from pathlib import PurePosixPath, PureWindowsPath
from xml.etree import ElementTree as ET

SCHEMA = "agent-machines.health.job-preparation"
NAME = "agent-machines-health"
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


class JobError(ValueError):
    """Bounded public preparation refusal, without private path details."""


def _text(value: str) -> str:
    if not value or any(ord(char) < 32 for char in value):
        raise JobError("invalid-job-path")
    return value


def _systemd(value: str) -> str:
    value = _text(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"' + value.replace("$", "$$") + '"'


def _systemd_directory(value: str) -> str:
    if _text(value) != value.strip() or value.endswith("\\"):
        raise JobError("invalid-job-path")
    return value.replace("%", "%%")


def prepare(
    *, platform: str, payload: str, store: str, policy_token: str,
    user_id: str = "", windows_root: str = "",
) -> dict:
    """Render inert definitions for explicit review, never touch a scheduler."""
    if re.fullmatch(r"[a-f0-9]{64}", policy_token) is None:
        raise JobError("policy-token-required")
    if platform not in {"win32", "linux"}:
        raise JobError("native-health-job-unsupported")
    path_type = PureWindowsPath if platform == "win32" else PurePosixPath
    if not all(path_type(_text(value)).is_absolute() for value in (payload, store)):
        raise JobError("absolute-job-path-required")
    if any(".." in path_type(value).parts for value in (payload, store)):
        raise JobError("invalid-job-path")
    arguments = ["health", "run", "--store-dir", store, "--apply", "--expect-policy", policy_token]
    if platform == "win32":
        if (
            re.fullmatch(r"S-1-(?:5-21|12-1)(?:-[0-9]+){4}", user_id) is None
            or not PureWindowsPath(_text(windows_root)).is_absolute()
        ):
            raise JobError("interactive-user-and-windows-root-required")
        system = PureWindowsPath(windows_root) / "System32"
        host = str(system / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        launcher = str(PureWindowsPath(payload) / "bin" / "agent-machines.ps1")
        ET.register_namespace("", NS)
        task = ET.Element(f"{{{NS}}}Task", version="1.4")

        def element(parent, name, text=None, **attributes):
            node = ET.SubElement(parent, f"{{{NS}}}{name}", attributes)
            node.text = text
            return node

        trigger = element(element(task, "Triggers"), "TimeTrigger")
        repetition = element(trigger, "Repetition")
        element(repetition, "Interval", "PT1H")
        element(repetition, "StopAtDurationEnd", "false")
        element(trigger, "StartBoundary", "2000-01-01T00:00:15Z")
        element(trigger, "Enabled", "false")
        principal = element(element(task, "Principals"), "Principal", id="Author")
        element(principal, "UserId", user_id)
        element(principal, "LogonType", "InteractiveToken")
        element(principal, "RunLevel", "LeastPrivilege")
        settings = element(task, "Settings")
        for key, value in (
            ("MultipleInstancesPolicy", "IgnoreNew"),
            ("DisallowStartIfOnBatteries", "false"), ("StopIfGoingOnBatteries", "false"),
            ("StartWhenAvailable", "true"), ("RunOnlyIfNetworkAvailable", "false"),
            ("Enabled", "false"), ("WakeToRun", "false"), ("ExecutionTimeLimit", "PT1M"),
        ):
            element(settings, key, value)
        action = element(element(task, "Actions", Context="Author"), "Exec")
        element(action, "Command", str(system / "conhost.exe"))
        element(action, "Arguments", subprocess.list2cmdline([
            "--headless", host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", launcher, *arguments,
        ]))
        element(action, "WorkingDirectory", payload)
        artifacts = {f"{NAME}.xml": ET.tostring(task, encoding="unicode")}
    else:
        if user_id or windows_root:
            raise JobError("platform-job-options-mismatch")
        launcher = str(PurePosixPath(payload) / "bin" / "agent-machines")
        command = " ".join(_systemd(value) for value in ["/bin/bash", launcher, *arguments])
        artifacts = {
            f"{NAME}.service": (
                "[Unit]\nDescription=Bounded local machine health collection\n\n"
                "[Service]\nType=oneshot\n"
                f"ExecStart={command}\nWorkingDirectory={_systemd_directory(payload)}\n"
                "TimeoutStartSec=55s\nTimeoutStopSec=5s\nKillMode=control-group\n"
                "Restart=no\n"
            ),
            f"{NAME}.timer": (
                "[Unit]\nDescription=Hourly local machine health collection\n\n"
                "[Timer]\nOnCalendar=*-*-* *:00:15 UTC\nPersistent=true\n"
                "AccuracySec=1s\nRandomizedDelaySec=0\n"
                f"Unit={NAME}.service\n\n[Install]\nWantedBy=timers.target\n"
            ),
        }
    return {
        "schema": SCHEMA, "version": 1, "status": "prepared",
        "platform": platform, "artifacts": artifacts, "registered": False, "enabled": False,
        "qualification": "native-scheduler-not-qualified", "carrier_publication": "not-requested",
    }
