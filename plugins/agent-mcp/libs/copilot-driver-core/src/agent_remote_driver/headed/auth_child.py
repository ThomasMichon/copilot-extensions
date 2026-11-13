"""Windows Job custody used only by the opt-in auth-wait receiver."""

from __future__ import annotations

import asyncio
import os
import subprocess

from agent_procutil import JobHandle, spawn_sync_in_kill_on_close_job
from agent_procutil.owned_process import OwnedCleanupError


def require_auth_custody() -> None:
    if os.name != "nt":
        raise RuntimeError("POSIX auth wait requires independent owner-death custody")
    if not callable(getattr(JobHandle, "try_terminate_and_confirm_empty", None)):
        raise RuntimeError("Auth wait requires independently callable Job retirement custody")


class AuthPaneChild:
    """Retain the native Job through whole-scope settlement."""

    def __init__(self) -> None:
        require_auth_custody()
        self.proc: subprocess.Popen | None = None
        self.job = None
        self.started = asyncio.Event()
        self._settled = False
        self._job_empty = False
        self.retirement_started = False
        self._cleanup_task: asyncio.Task | None = None
        self._native_scope: object | None = None
        self._cleanup_completed = False

    def launch(self, argv: list[str], **kwargs) -> None:
        if self.proc is not None:
            raise RuntimeError("A native incarnation cannot be launched twice")
        self.proc, self.job = spawn_sync_in_kill_on_close_job(
            argv, require_job=True, allow_breakaway=False, **kwargs,
        )
        self._native_scope = object()
        self.started.set()

    @property
    def native_scope(self) -> object | None:
        """Opaque identity for this launch's retained Popen handle and Job.

        It is local to this retained owner, not a serialized descriptor or an
        environment/boot/conversation mapping that can be reacquired after loss.
        """
        return self._native_scope

    def scope_settled(self, native_scope: object) -> bool | None:
        """Read successful owned cleanup only; never poll, wait or retire here."""
        if native_scope is not None and native_scope is self._native_scope:
            return True if self._cleanup_completed else None
        return None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    @property
    def returncode(self) -> int | None:
        if self.proc is None:
            return None
        return self.proc.poll()

    async def wait(self) -> int:
        await self.started.wait()
        while (result := self.returncode) is None:
            await asyncio.sleep(.025)
        return result

    def _retire(self) -> None:
        if self.proc is None or self._settled:
            return
        try:
            if not self._job_empty:
                self.job.terminate_and_wait(2)
                self._job_empty = True
            self.proc.wait(timeout=2)
            self._settled = True
            if self.job is not None:
                self.job.close()
            self._cleanup_completed = True
        except BaseException as error:
            raise OwnedCleanupError(self.proc, self.job) from error

    async def terminate(self) -> None:
        if self._cleanup_task is None and self.proc is not None:
            try:
                self._job_empty = self.job.try_terminate_and_confirm_empty()
            except BaseException as error:
                raise OwnedCleanupError(self.proc, self.job) from error
            self.retirement_started = True
            self._cleanup_task = asyncio.create_task(asyncio.to_thread(self._retire))
        if self._cleanup_task is not None:
            await asyncio.shield(self._cleanup_task)
