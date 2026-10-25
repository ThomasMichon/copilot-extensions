"""Same-machine Session Host preparation and its final frontend launch owner."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..acquisition_launch_owner import AcquisitionLaunchOwner
    from .spawner import SpawnedHost


class LocalSpawner:
    """Launch a survivable local Host through the existing launcher seam."""

    boundary = "local"

    def __init__(
        self, *, unexpected_reap_seconds: float = 60.0,
        active_reap_seconds: float = 0.0, ready_timeout: float = 90.0,
    ) -> None:
        self._unexpected_reap_seconds = unexpected_reap_seconds
        self._active_reap_seconds = active_reap_seconds
        self._ready_timeout = ready_timeout

    async def spawn(
        self, child_argv: list[str], *, cwd: str | None = None,
        env: dict[str, str] | None = None, session_id: str = "",
        acquisition_launch: AcquisitionLaunchOwner | None = None,
    ) -> SpawnedHost:
        from .. import __version__
        from . import spawner as core

        nonce = core.new_nonce()
        handle = await asyncio.to_thread(
            core.launch_session_host, child_argv, cwd=cwd, env=env, nonce=nonce,
            session_id=session_id, host_version=__version__,
            ready_timeout=self._ready_timeout,
            unexpected_reap_seconds=self._unexpected_reap_seconds,
            active_reap_seconds=self._active_reap_seconds,
            **({"acquisition_launch": acquisition_launch} if acquisition_launch is not None else {}),
        )
        return core.SpawnedHost(
            local_port=handle.port, host_pid=handle.host_pid, child_pid=handle.child_pid,
            protocol_version=handle.protocol_version, boundary=self.boundary,
            nonce=nonce, state_file=handle.state_file, proc=handle.proc, _refresh=None,
        )
