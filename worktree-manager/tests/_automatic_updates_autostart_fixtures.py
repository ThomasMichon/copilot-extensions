"""Synthetic native-owner records for automatic-update startup contracts."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from worktree_manager import automatic_updates_autostart as autostart
from worktree_manager import automatic_updates_startup as startup

LinuxCalls = list[tuple[str, ...]]
WindowsEntries = dict[str, str]
NativeSnapshot = tuple[int, int, tuple[str, ...], tuple[str, ...]]
NativeOwner = tuple[dict[str, str], dict[int, NativeSnapshot]]


@pytest.fixture
def native_owner(
    installation: Path, linux: LinuxCalls, monkeypatch: pytest.MonkeyPatch,
) -> NativeOwner:
    registered = autostart.register_autostart(installation)
    unit = str(registered["unit"])
    name = str(registered["name"])
    group = f"/user.slice/user-1000.slice/user@1000.service/app.slice/{unit}"
    fields = {
        "Id": unit, "ActiveState": "active", "UnitFileState": "enabled",
        "MainPID": str(os.getpid() + 100000), "ControlGroup": group,
        "FragmentPath": str(autostart._unit_path(name)), "NeedDaemonReload": "no",
        "KillMode": "process", "SendSIGKILL": "no", "TimeoutStopUSec": "infinity",
        "DropInPaths": "",
    }
    snapshots: dict[int, NativeSnapshot] = {
        int(fields["MainPID"]): (
            100, 1, (sys.executable, "-I", "-S",
                     str(installation / "automatic-updates/startup.py"),
                     "--root", str(installation)), (group,),
        ),
        os.getpid(): (
            200, int(fields["MainPID"]),
            (sys.executable, "-m", "worktree_manager", "automatic-updates",
             "run", "--root", str(installation)), (group,),
        ),
        1: (1, 0, ("init",), ("/",)),
    }
    monkeypatch.setattr(
        autostart, "_systemctl",
        lambda *args: subprocess.CompletedProcess(
            args, 0, "\n".join(f"{key}={value}" for key, value in fields.items()), "",
        ),
    )
    monkeypatch.setattr(startup, "process_snapshot", lambda pid: snapshots[pid])
    monkeypatch.delenv(autostart.UNIT_ENV, raising=False)
    return fields, snapshots
