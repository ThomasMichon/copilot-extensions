"""Opt-in in-process publication at the actual headed client's creation."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from agent_procutil import spawn_sync_in_kill_on_close_job


@dataclass(frozen=True)
class WindowsNativeHandoff:
    """Borrow explicit read handles and publish from the original suspended Popen.

    The callback must finish before resume and return None. It may inspect the
    retained creation handle, not resume, replace or launch another child.
    This is an in-process API, not an environment or serialized-code hook.
    """

    on_suspended: Callable[[subprocess.Popen], None]
    inherited_handles: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not callable(self.on_suspended):
            raise ValueError("Native handoff requires an in-process callback")
        handles = self.inherited_handles
        if (
            type(handles) is not tuple
            or any(type(handle) is not int or handle <= 0 for handle in handles)
            or len(set(handles)) != len(handles)
        ):
            raise ValueError("Native handoff requires unique positive inherited handles")

    def launch(self, argv: list[str], **kwargs):
        """Create only the selected native client, without a coordinator substitute."""
        if kwargs.get("shell", False) is not False or kwargs.get("executable") is not None:
            raise ValueError("Native handoff cannot override its direct executable")
        if (
            os.name != "nt" or not argv or not Path(argv[0]).is_absolute()
            or Path(argv[0]).name.casefold() != "copilot.exe"
        ):
            raise ValueError("Native handoff requires the selected direct Windows Copilot image")
        with open(argv[0], "rb") as image:
            if image.read(2) != b"MZ":
                raise ValueError("Native handoff cannot attribute a script or forwarding launcher")
        if any(not os.get_handle_inheritable(handle) for handle in self.inherited_handles):
            raise ValueError("Native handoff handles must already be inheritable")
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": list(self.inherited_handles)}
        return spawn_sync_in_kill_on_close_job(
            argv, require_job=True, allow_breakaway=False, on_suspended=self.on_suspended,
            startupinfo=startup, close_fds=True, **kwargs,
        )
