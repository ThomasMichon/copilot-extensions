"""Native child custody used only by the opt-in auth-wait receiver."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys

from agent_procutil import spawn_sync_in_kill_on_close_job
from agent_procutil.owned_process import OwnedCleanupError


class AuthPaneChild:
    """Retain a Job or unreaped private group through whole-scope settlement."""

    def __init__(self) -> None:
        if os.name != "nt" and not sys.platform.startswith("linux"):
            raise RuntimeError("Headed auth wait requires Windows Job or Linux group custody")
        if os.name != "nt":
            from agent_procutil._caller_owned import _require_posix_support

            _require_posix_support()
            if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
                raise RuntimeError("Headed auth wait requires exclusive child wait ownership")
        self.proc: subprocess.Popen | None = None
        self.job = None
        self.started = asyncio.Event()
        self._settled = False
        self._cleanup_task: asyncio.Task | None = None

    def launch(self, argv: list[str], **kwargs) -> None:
        if self.proc is not None:
            raise RuntimeError("A native incarnation cannot be launched twice")
        if os.name == "nt":
            self.proc, self.job = spawn_sync_in_kill_on_close_job(
                argv, require_job=True, allow_breakaway=False, **kwargs,
            )
        else:
            # Keep the caller's pane descriptors, but never its shared process group.
            self.proc = subprocess.Popen(argv, start_new_session=True, **kwargs)
        self.started.set()

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    @property
    def returncode(self) -> int | None:
        if self.proc is None:
            return None
        if os.name == "nt" or self._settled or self.proc.returncode is not None:
            return self.proc.poll()
        from agent_procutil.owned_process import _observe_unreaped

        status = _observe_unreaped(self.proc)
        if status is None:
            return None
        return status.si_status if status.si_code == os.CLD_EXITED else -status.si_status

    async def wait(self) -> int:
        await self.started.wait()
        while (result := self.returncode) is None:
            await asyncio.sleep(.025)
        return result

    def _retire(self) -> None:
        if self.proc is None or self._settled:
            return
        try:
            if os.name == "nt":
                self.job.terminate_and_wait(2)
            else:
                from agent_procutil._caller_owned import _confirm_group
                from agent_procutil.owned_process import _retire_posix_group

                _retire_posix_group(self.proc)
                _confirm_group(self.proc)
            self.proc.wait(timeout=2)
            self._settled = True
            if self.job is not None:
                self.job.close()
        except BaseException as error:
            raise OwnedCleanupError(self.proc, self.job) from error

    async def terminate(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(asyncio.to_thread(self._retire))
        await asyncio.shield(self._cleanup_task)
