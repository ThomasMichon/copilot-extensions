"""Identity-bound Windows coordinator trampoline handling, local to dispatch."""

from __future__ import annotations

import logging
import os
import shlex

from zdd.diagnostics import process_start_time, terminate_pid_if_identity

from .supervisor_processes import iter_windows_processes

log = logging.getLogger(__name__)


def is_windows() -> bool:
    return os.name == "nt"


def _arguments(command: str) -> list[str]:
    from .reap import is_coordinator_cmdline

    if not is_coordinator_cmdline(command):
        return []
    try:
        # Only the interpreter path differs between a venv trampoline and base
        # interpreter. No arbitrary ancestor/descendant exemption is safe.
        return [s.strip('"') for s in shlex.split(command, posix=False)][1:]
    except ValueError:
        return []


def verified_pair(pid: int, expected: str | None) -> dict[int, str]:
    """Return just a live server and its matching direct Windows trampoline.

    Creation tokens are checked on both sides of the ancestry census. A reused
    parent PID (created after the child), incomplete census, or ambiguous pair
    refuses the whole reap rather than risking the promoted coordinator.
    """
    if not expected or process_start_time(pid) != expected:
        raise RuntimeError("coordinator identity unavailable or changed")
    rows = {p.pid: p for p in iter_windows_processes()}
    server = rows.get(pid)
    if server is None or not _arguments(server.command_line):
        raise RuntimeError("coordinator missing from process census")
    parent = rows.get(server.parent_pid)
    children = [p for p in rows.values() if p.parent_pid == pid
                and _arguments(p.command_line)]
    peers = []
    if parent is not None and _arguments(parent.command_line):
        peers.append(parent)
    peers.extend(children)
    if len(peers) > 1:
        raise RuntimeError("ambiguous coordinator trampoline family")
    result = {pid: expected}
    for peer in peers:
        token = process_start_time(peer.pid)
        if not token or _arguments(peer.command_line) != _arguments(server.command_line):
            raise RuntimeError("coordinator trampoline identity unverified")
        parent_token, child_token = (
            (token, expected) if peer is parent else (expected, token)
        )
        if int(parent_token) >= int(child_token):
            raise RuntimeError("coordinator ancestry points to a reused PID")
        result[peer.pid] = token
    if any(process_start_time(p) != token for p, token in result.items()):
        raise RuntimeError("coordinator identity changed during census")
    return result


def _identity_running(pid: int, token: str | None) -> bool | None:
    """Observe exit, not merely an extant process object retained by a handle."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        return False if ctypes.get_last_error() == 87 else None
    try:
        current = process_start_time(pid)
        if not current or not token:
            return None
        if current != token:
            return False
        status = api.WaitForSingleObject(handle, 0)
        return {0: False, 258: True}.get(status)
    finally:
        api.CloseHandle(handle)


class CoordinatorProcess:
    """Popen-compatible cutover handle whose promoted PID is the actual server.

    Before readiness the retained Popen handle owns the launcher. /health names
    the serving process; adopt it only after proving the exact direct pair.
    zdd reads pid again at publication, so no shared cutover redesign is needed.
    """

    def __init__(self, launcher):
        self.launcher = launcher
        self.pid = launcher.pid
        self.launcher_token = process_start_time(self.pid)
        self.server_token = self.launcher_token

    def accept_health(self, payload: dict) -> bool:
        slot = payload.get("slot")
        pid = slot.get("pid") if isinstance(slot, dict) else None
        try:
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise RuntimeError("health did not identify the serving process")
            if self.pid != self.launcher.pid:
                if pid != self.pid or process_start_time(pid) != self.server_token:
                    raise RuntimeError("healthy server identity changed")
                return True
            if process_start_time(self.launcher.pid) != self.launcher_token:
                raise RuntimeError("spawned launcher identity changed")
            pair = verified_pair(self.launcher.pid, self.launcher_token)
            if pid not in pair:
                raise RuntimeError("healthy server is not the spawned coordinator")
            self.pid, self.server_token = pid, pair[pid]
            return True
        except (OSError, RuntimeError, ValueError) as exc:
            log.warning("cutover server identity refused: %s", exc)
            return False

    def poll(self):
        if self.pid != self.launcher.pid and _identity_running(self.pid, self.server_token) is not False:
            return None
        return self.launcher.poll()

    def terminate(self):
        pair = (
            {self.pid: self.server_token} if self.pid != self.launcher.pid
            else verified_pair(self.launcher.pid, self.launcher_token)
        )
        for pid, token in pair.items():
            if pid == self.launcher.pid:
                continue
            outcome = terminate_pid_if_identity(pid, token)
            if not outcome.get("killed"):
                raise RuntimeError("could not terminate identity-verified passive server")
        if self.launcher.poll() is None:
            self.launcher.terminate()
