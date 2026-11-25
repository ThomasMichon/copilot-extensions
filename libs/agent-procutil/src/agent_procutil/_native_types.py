"""Structural types for platform-dependent native process boundaries."""

import os
from typing import Callable, Protocol, TypedDict

from agent_procutil.owned_budget import OwnedExecutionBudget


class DescriptorStream(Protocol):
    def fileno(self) -> int: ...


class StdioSpawnOptions(TypedDict, total=False):
    stdin: int | DescriptorStream | None
    stdout: int | DescriptorStream | None
    stderr: int | DescriptorStream | None
    text: bool
    bufsize: int
    encoding: str
    errors: str
    creationflags: int
    startupinfo: object
    start_new_session: bool
    pass_fds: tuple[int, ...]
    cwd: str | os.PathLike[str]
    env: dict[str, str]


class SpawnOptions(StdioSpawnOptions, total=False):
    budget: OwnedExecutionBudget
    command_deadline: float | None
    on_settled: Callable[[], None] | None


class WindowsErrors(Protocol):
    def WinError(self, code: int) -> OSError: ...
    def get_last_error(self) -> int: ...


class WindowsProcessAPI(Protocol):
    O_BINARY: int

    def set_handle_inheritable(self, handle: int, inheritable: bool) -> None: ...


class MsvcrtAPI(Protocol):
    def get_osfhandle(self, fd: int) -> int: ...


class WindowsSpawnOptions(StdioSpawnOptions, total=False):
    require_job: bool
    allow_breakaway: bool
    inherit_job_scopes: bool


class PollHandle(Protocol):
    def register(self, fd: int, eventmask: int) -> None: ...
    def poll(self, timeout: int) -> list[tuple[int, int]]: ...


class PollAPI(Protocol):
    POLLIN: int
    POLLERR: int
    POLLNVAL: int

    def poll(self) -> PollHandle: ...


class WaitStatus(Protocol):
    @property
    def si_pid(self) -> int: ...


class ProcessAPI(Protocol):
    P_PID: int
    WEXITED: int
    WNOHANG: int
    WNOWAIT: int

    def getpgid(self, pid: int) -> int: ...
    def getpgrp(self) -> int: ...
    def getsid(self, pid: int) -> int: ...
    def killpg(self, pgid: int, sig: int) -> None: ...
    def set_blocking(self, fd: int, blocking: bool) -> None: ...
    def waitid(self, idtype: int, id: int, options: int) -> WaitStatus | None: ...


class SignalAPI(Protocol):
    SIGKILL: int
    SIGCHLD: int


class FcntlAPI(Protocol):
    F_DUPFD_CLOEXEC: int

    def fcntl(self, fd: int, cmd: int, arg: int) -> int: ...
