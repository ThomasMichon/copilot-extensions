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
    user_id: str = "", windows_root: str = "", gui_python: str = "", runtime_site: str = "",
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
        if not all(
            PureWindowsPath(_text(value)).is_absolute()
            and ".." not in PureWindowsPath(value).parts
            for value in (gui_python, runtime_site)
        ):
            raise JobError("windowless-runtime-required")
        system = PureWindowsPath(windows_root) / "System32"
        host = str(system / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        entry = str(PureWindowsPath(payload) / "scripts" / "health-job-entry.py")
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
        element(action, "Command", gui_python)
        element(action, "Arguments", subprocess.list2cmdline([
            "-I", "-B", "-X", "utf8", entry, "--site", runtime_site,
            "--powershell", host, "--payload", payload, "--store", store,
            "--expect-policy", policy_token,
        ]))
        element(action, "WorkingDirectory", payload)
        artifacts = {f"{NAME}.xml": ET.tostring(task, encoding="unicode")}
    else:
        if user_id or windows_root or gui_python or runtime_site:
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


def installed_definition(
    *, slot: str, python: str, gui_python: str, identity: str, store: str,
    policy_token: str, user_id: str, windows_root: str, owner: str, enabled: bool,
) -> str:
    """Render only an installation-selected closure, never a configurable action."""
    if re.fullmatch(r"[a-f0-9]{32}:[a-f0-9]{64}", identity) is None:
        raise JobError("installed-build-identity-required")
    if re.fullmatch(r"urn:copilot-extensions:health-job:[a-f0-9]{64}", owner) is None:
        raise JobError("installed-job-owner-required")
    directory = PureWindowsPath(slot)
    if not directory.is_absolute() or directory.parent.name != "versions" or directory.name == "dev":
        raise JobError("immutable-job-runtime-required")
    prepared = prepare(
        platform="win32", payload=slot, store=store, policy_token=policy_token,
        user_id=user_id, windows_root=windows_root, gui_python=gui_python,
        runtime_site=str(PureWindowsPath(python).parent.parent / "Lib" / "site-packages"),
    )
    root = ET.fromstring(prepared["artifacts"][f"{NAME}.xml"])
    info = ET.Element(f"{{{NS}}}RegistrationInfo")
    ET.SubElement(info, f"{{{NS}}}Description").text = owner
    root.insert(0, info)
    ET.SubElement(root.find(f"{{{NS}}}Settings"), f"{{{NS}}}UseUnifiedSchedulingEngine").text = "true"
    action = root.find(f"{{{NS}}}Actions/{{{NS}}}Exec")
    action.find(f"{{{NS}}}Arguments").text = subprocess.list2cmdline([
        "-I", "-S", "-B", "-X", "utf8", str(directory / ".runtime-bootstrap" / "runtime_launcher.py"),
        "--gui-runtime", "--runtime-identity", identity, "-m", "agent_machines.health_job",
        "--store", store, "--expect-policy", policy_token,
    ])
    root.find(f"{{{NS}}}Triggers/{{{NS}}}TimeTrigger/{{{NS}}}Enabled").text = "true"
    root.find(f"{{{NS}}}Settings/{{{NS}}}Enabled").text = "true" if enabled else "false"
    return ET.tostring(root, encoding="unicode")
