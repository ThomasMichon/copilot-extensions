"""Historical direct-Manager trigger definitions retained beside the AM wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path, PureWindowsPath
from typing import Any

from .automatic_update_ownership import LEGACY_FLEET_TRIGGER

FLEET_DESCRIPTION = "agent-machines unattended fleet-update sweep (daily worktree-manager update)"


def historical_manager_parts(
    tasks: Any,
    *,
    home: Path | None,
    marked: bool,
) -> list[str]:
    base: Path | PureWindowsPath = home or Path.home()
    command = "worktree-manager"
    if tasks.sys.platform == "win32":
        base = PureWindowsPath(str(base))
        command += ".cmd"
    parts = [str(base / ".local" / "bin" / command), "update"]
    if marked:
        parts.extend(["--automatic-trigger", LEGACY_FLEET_TRIGGER])
    return parts


def historical_windows_arguments(tasks: Any, *, home: Path | None) -> tuple[str, ...]:
    return tuple(
        "--headless cmd.exe /c "
        + subprocess.list2cmdline(historical_manager_parts(tasks, home=home, marked=marked))
        for marked in (False, True)
    )


def historical_linux_bodies(tasks: Any, *, home: Path | None) -> set[str]:
    base = home or Path.home()
    path = (
        f"PATH={base / '.local' / 'bin'}:/usr/local/sbin:/usr/local/bin:"
        "/usr/sbin:/usr/bin:/sbin:/bin"
    )
    bodies: set[str] = set()
    for marked in (False, True):
        command = " ".join(
            tasks._systemd_quote(part)
            for part in historical_manager_parts(tasks, home=home, marked=marked)
        )
        for with_path in (False, True):
            environment = f"Environment={tasks._systemd_quote(path)}\n" if with_path else ""
            bodies.add(
                f"[Unit]\nDescription={FLEET_DESCRIPTION}\n\n[Service]\nType=oneshot\n"
                f"WorkingDirectory={base / '.agent-machines'}\n"
                f"{environment}ExecStart={command}\n"
            )
    return bodies


def classify_fleet_trigger(
    tasks: Any,
    content: str,
    *,
    home: Path | None,
    windows: bool,
) -> str | None:
    if windows:
        actual = content.casefold()
        if actual == tasks.task_action_arguments("sweep", home=home).casefold():
            return "am-wrapper"
        for marked, arguments in zip(
            (False, True),
            historical_windows_arguments(tasks, home=home),
            strict=True,
        ):
            if actual == arguments.casefold():
                return "direct-manager-marked" if marked else "direct-manager-preflag"
        return None
    starts = [
        line.removeprefix("ExecStart=")
        for line in content.splitlines()
        if line.startswith("ExecStart=")
    ]
    if len(starts) != 1:
        return None
    if starts[0] == tasks._linux_exec_start("sweep", home=home):
        return "am-wrapper"
    for marked in (False, True):
        command = " ".join(
            tasks._systemd_quote(part)
            for part in historical_manager_parts(tasks, home=home, marked=marked)
        )
        if starts[0] == command:
            return "direct-manager-marked" if marked else "direct-manager-preflag"
    return None
