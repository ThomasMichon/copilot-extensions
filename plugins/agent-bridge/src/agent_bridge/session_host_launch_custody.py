"""Host launch registration and confirmed rollback custody."""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any


class _SessionHostLaunchCustodyMixin:
    def _register_host_launch(self, spawned: Any, session_id: str) -> Any:
        from . import __version__
        from .session_host.host_index import HostRecord
        from zdd.diagnostics import process_start_time

        if self._host_index is not None:
            record = HostRecord(
                session_id=session_id,
                port=spawned.local_port,
                host_pid=spawned.host_pid,
                child_pid=spawned.child_pid,
                host_version=__version__,
                protocol_version=spawned.protocol_version,
                state_file=spawned.state_file,
                created_at=time.time(),
                nonce=spawned.nonce,
                boundary=spawned.boundary,
                endpoint=getattr(spawned, "endpoint", {}) or {},
                owner_generation=getattr(self, "_generation_id", "") or "",
                owner_pid=os.getpid(),
                extra={
                    "remote_authority_v2": spawned.boundary != "local",
                    **({
                        "host_process_start": process_start_time(spawned.host_pid),
                        "child_process_start": process_start_time(spawned.child_pid),
                    } if spawned.boundary == "local" else {}),
                },
            )
            self._host_index.register(record)
            return record
        return None

    async def _rollback_failed_host_launch(
        self,
        spawner: Any,
        spawned: Any,
        sock: Any,
        streams: Any,
        session_id: str,
        result: dict[str, Any] | None,
    ) -> bool:
        """Reap a failed Host launch and remove every local holder."""
        with contextlib.suppress(Exception):
            await sock.terminate()
        with contextlib.suppress(Exception):
            await streams.aclose()
        with contextlib.suppress(Exception):
            await sock.close()

        remote = getattr(spawned, "boundary", "local") != "local"
        confirmed = not remote
        if remote:
            abort = getattr(spawner, "abort_spawned", None)
            if callable(abort):
                try:
                    confirmed = bool(await abort(spawned, session_id))
                except Exception:
                    confirmed = False
        with contextlib.suppress(Exception):
            await spawned.aclose()
        self._forwards.pop(session_id, None)
        self._relays.pop(session_id, None)
        if result is not None:
            result.update({
                "host_process_removed": confirmed,
                "child_process_removed": confirmed,
                "remote_authority_removed": confirmed,
                "forward_removed": session_id not in self._forwards,
                "relay_removed": session_id not in self._relays,
            })
        return confirmed
