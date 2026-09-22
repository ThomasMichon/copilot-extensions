"""Parity-only relay interruption and container replacement helpers."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from .models import SessionStatus
from .transport import SpawnTarget


class _SessionParityMixin:
    """Parity-only relay interruption and container replacement helpers."""

    async def interrupt_relays_for_parity(
        self,
        session_id: str,
        *,
        timeout: float = 90.0,
    ) -> dict[str, Any]:
        """Interrupt one parity session's relay processes and await recovery."""
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        if not str(session.caller_id or "").startswith("venue-parity:"):
            raise PermissionError(
                "relay interruption is restricted to venue-parity sessions"
            )
        if session.status is not SessionStatus.IDLE:
            raise RuntimeError("relay interruption requires an idle session")
        active_states = {
            SessionStatus.CREATED,
            SessionStatus.STARTING,
            SessionStatus.RUNNING,
            SessionStatus.IDLE,
            SessionStatus.STOPPING,
        }
        active_others = [
            item.session_id
            for item in self._sessions.values()
            if item.session_id != session.session_id
            and item.status in active_states
        ]
        if active_others:
            raise RuntimeError(
                "relay interruption refuses another active managed session: "
                + ", ".join(active_others[:5])
            )

        relays = list(self._relays.get(session.session_id) or [])
        if not relays:
            raise RuntimeError("session has no supervised credential relay")
        handles_before = tuple(id(relay) for relay in relays)
        processes_before = [getattr(relay, "_proc", None) for relay in relays]
        pids_before = [
            int(getattr(process, "pid", 0) or 0)
            for process in processes_before
        ]
        if (
            not all(getattr(relay, "is_alive", False) for relay in relays)
            or not all(processes_before)
            or not all(pids_before)
        ):
            raise RuntimeError("session relay is not healthy before interruption")

        interrupted = 0
        for process in processes_before:
            try:
                process.kill()
            except ProcessLookupError as exc:
                raise RuntimeError(
                    "session relay exited before interruption"
                ) from exc
            interrupted += 1
        for process in processes_before:
            with contextlib.suppress(
                ProcessLookupError,
                TimeoutError,
                asyncio.TimeoutError,
            ):
                await asyncio.wait_for(process.wait(), timeout=5.0)

        deadline = time.monotonic() + max(1.0, timeout)
        current: list[Any] = []
        pids_after: list[int] = []
        while time.monotonic() < deadline:
            current = list(self._relays.get(session.session_id) or [])
            pids_after = [
                int(getattr(getattr(relay, "_proc", None), "pid", 0) or 0)
                for relay in current
            ]
            if (
                tuple(id(relay) for relay in current) == handles_before
                and len(current) == len(relays)
                and all(getattr(relay, "is_alive", False) for relay in current)
                and all(pids_after)
                and all(
                    before != after
                    for before, after in zip(pids_before, pids_after, strict=True)
                )
            ):
                return {
                    "owner_count_before": len(relays),
                    "owner_count_after": len(current),
                    "interrupted_count": interrupted,
                    "all_recovered": True,
                    "owner_identity_preserved": True,
                    "processes_replaced": True,
                }
            await asyncio.sleep(0.25)
        raise TimeoutError(
            f"session relay did not recover within {timeout:.0f}s"
        )

    async def recreate_container_for_parity(
        self,
        session_id: str,
        *,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Recreate one parity container and replace its bridge session."""
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        if not str(session.caller_id or "").startswith("venue-parity:"):
            raise PermissionError(
                "container recreation is restricted to venue-parity sessions"
            )
        if session.status is not SessionStatus.IDLE:
            raise RuntimeError("container recreation requires an idle session")
        container_target = (
            session.target.container
            if isinstance(session.target.container, dict)
            else None
        )
        if (
            not container_target
            or container_target.get("security_profile") != "trusted"
        ):
            raise RuntimeError(
                "container recreation requires a trusted container target"
            )
        active_states = {
            SessionStatus.CREATED,
            SessionStatus.STARTING,
            SessionStatus.RUNNING,
            SessionStatus.IDLE,
            SessionStatus.STOPPING,
        }
        active_others = [
            item.session_id
            for item in self._sessions.values()
            if item.session_id != session.session_id
            and item.status in active_states
        ]
        if active_others:
            raise RuntimeError(
                "container recreation refuses another active managed session: "
                + ", ".join(active_others[:5])
            )
        if self._host_index is None:
            raise RuntimeError("container session has no HostIndex")
        old_host = self._host_index.get(session.session_id)
        if old_host is None or old_host.boundary != "container":
            raise RuntimeError(
                "container session has no authoritative container Host record"
            )
        replacement_target = SpawnTarget.from_json(session.target.to_json())
        old_session_id = session.session_id
        old_acp_session_id = session.acp_session_id
        old_child_pid = session.pid
        old_host_pid = old_host.host_pid

        from .session_host.container_transport import (
            ContainerRecreateAfterRemovalError,
            container_state,
            recreate_container_for_parity,
        )

        before = await container_state(container_target)
        old_container_id = str(before.get("container_id") or "")
        if (
            before.get("name") != container_target.get("name")
            or before.get("running") is not True
            or not old_container_id
        ):
            raise RuntimeError(
                "container lifecycle state is not authoritative before recreation"
            )
        try:
            replacement = await recreate_container_for_parity(
                container_target,
                expected_container_id=old_container_id,
                timeout=timeout,
            )
        except ContainerRecreateAfterRemovalError:
            container_target["authoritative_identity_removed"] = True
            self._db.update_session_target(
                old_session_id,
                session.target.to_json(),
                session.target.cwd,
            )
            self._mark_session_failed(
                session, trigger="container_recreate_failed"
            )
            if session.event_log:
                session.event_log.append("container_recreate_failed", {
                    "message": (
                        "The original container identity was removed before "
                        "replacement failed; target ownership is retained."
                    ),
                })
            raise
        container_target["authoritative_identity_removed"] = True
        self._db.update_session_target(
            old_session_id,
            session.target.to_json(),
            session.target.cwd,
        )
        self._mark_session_failed(session, trigger="container_recreated")
        if session.event_log:
            session.event_log.append("container_recreated", {
                "message": "The original container identity was replaced.",
            })

        new_session = await self.start_session(
            replacement_target,
            agent_name=session.agent_name,
            caller_id=session.caller_id,
            mcp_servers=[
                dict(server) for server in session.mcp_servers
            ],
            model=session.model_override,
            effort=session.effort_override,
            replace_session_id=old_session_id,
            retain_container_lock_on_failure=True,
        )
        if new_session.status is not SessionStatus.IDLE:
            new_container = (
                new_session.target.container
                if isinstance(new_session.target.container, dict)
                else {}
            )
            if (
                self._host_index.get(new_session.session_id) is None
                and new_container.get("launch_pending_session_id")
                != new_session.session_id
            ):
                new_container["recreate_failed_without_host"] = True
                self._db.update_session_target(
                    new_session.session_id,
                    new_session.target.to_json(),
                    new_session.target.cwd,
                )
            raise RuntimeError(
                f"replacement container session {new_session.session_id} "
                "failed to reach idle and retains target ownership"
            )
        new_host = self._host_index.get(new_session.session_id)
        if new_host is None or new_host.boundary != "container":
            raise RuntimeError(
                "replacement container session has no Host record"
            )

        await self._stop_relays(old_session_id)
        forward = self._forwards.pop(old_session_id, None)
        if forward is not None:
            with contextlib.suppress(Exception):
                await forward.cancel()
        self._host_index.remove(old_session_id)
        await self.end_session(old_session_id, force=True)

        name = str(container_target["name"])
        return {
            "old_session_id": old_session_id,
            "replacement_session_id": new_session.session_id,
            "old_acp_session_id": old_acp_session_id,
            "replacement_acp_session_id": new_session.acp_session_id,
            "old_container_id": old_container_id,
            "replacement_container_id": replacement["new_container_id"],
            "old_host_pid": old_host_pid,
            "replacement_host_pid": new_host.host_pid,
            "old_child_pid": old_child_pid,
            "replacement_child_pid": new_session.pid,
            "container_identity_changed": (
                replacement["new_container_id"] != old_container_id
            ),
            "old_session_removed": (
                old_session_id not in self._sessions
                and self._db.get_session(old_session_id) is None
            ),
            "old_host_index_removed": (
                self._host_index.get(old_session_id) is None
            ),
            "old_forward_removed": old_session_id not in self._forwards,
            "old_relay_removed": old_session_id not in self._relays,
            "target_lock_transferred": (
                self._container_lock_sessions.get(new_session.session_id)
                == name
                and old_session_id not in self._container_lock_sessions
            ),
        }

    def _acquire_container_lock(self, session_id: str, name: str) -> None:
        if session_id in self._container_lock_sessions:
            return
        existing = self._container_locks.get(name)
        if existing is not None:
            raise RuntimeError(
                f"Container '{name}' is already owned by bridge session "
                f"{existing[1]}"
            )
        from ssh_manager import TargetLock

        lock = TargetLock(f"container:{name}", op="session-host")
        lock.acquire()
        self._container_locks[name] = (lock, session_id)
        self._container_lock_sessions[session_id] = name

    def _transfer_container_lock(
        self,
        old_session_id: str,
        new_session_id: str,
        name: str,
    ) -> None:
        """Move one held container target lock without an unlocked window."""
        entry = self._container_locks.get(name)
        if (
            self._container_lock_sessions.get(old_session_id) != name
            or entry is None
            or entry[1] != old_session_id
        ):
            raise RuntimeError(
                f"Container '{name}' is not owned by bridge session "
                f"{old_session_id}"
            )
        lock, _owner = entry
        self._container_lock_sessions.pop(old_session_id, None)
        self._container_lock_sessions[new_session_id] = name
        self._container_locks[name] = (lock, new_session_id)

    def _release_container_lock(self, session_id: str) -> None:
        name = self._container_lock_sessions.pop(session_id, None)
        if name is None:
            return
        entry = self._container_locks.get(name)
        if entry is None:
            return
        lock, owner_session = entry
        if owner_session != session_id:
            return
        self._container_locks.pop(name, None)
        with contextlib.suppress(Exception):
            lock.release()

    def _acquire_codespace_lock(self, session_id: str, name: str) -> None:
        """Same-machine mirror of :meth:`_acquire_container_lock` for a
        CodeSpace target -- see ``session_core``'s ``_codespace_locks``
        docstring for why this exists alongside the cross-machine
        ``_claim_codespace`` claim. Keyed by the bare CodeSpace name (no
        ``container:`` prefix), matching exactly how ``agent-codespaces
        ssh``/``copilot`` key their own ``TargetLock`` acquisitions, so a
        local CLI invocation and this daemon's own Session-Host dispatch
        contend for the SAME lock.
        """
        if session_id in self._codespace_lock_sessions:
            return
        existing = self._codespace_locks.get(name)
        if existing is not None:
            raise RuntimeError(
                f"CodeSpace '{name}' is already owned by bridge session "
                f"{existing[1]}"
            )
        from ssh_manager import TargetLock

        lock = TargetLock(name, op="session-host")
        lock.acquire()
        self._codespace_locks[name] = (lock, session_id)
        self._codespace_lock_sessions[session_id] = name

    def _transfer_codespace_lock(
        self,
        old_session_id: str,
        new_session_id: str,
        name: str,
    ) -> None:
        """Move one held CodeSpace target lock without an unlocked window."""
        entry = self._codespace_locks.get(name)
        if (
            self._codespace_lock_sessions.get(old_session_id) != name
            or entry is None
            or entry[1] != old_session_id
        ):
            raise RuntimeError(
                f"CodeSpace '{name}' is not owned by bridge session "
                f"{old_session_id}"
            )
        lock, _owner = entry
        self._codespace_lock_sessions.pop(old_session_id, None)
        self._codespace_lock_sessions[new_session_id] = name
        self._codespace_locks[name] = (lock, new_session_id)

    def _release_codespace_lock(self, session_id: str) -> None:
        name = self._codespace_lock_sessions.pop(session_id, None)
        if name is None:
            return
        entry = self._codespace_locks.get(name)
        if entry is None:
            return
        lock, owner_session = entry
        if owner_session != session_id:
            return
        self._codespace_locks.pop(name, None)
        with contextlib.suppress(Exception):
            lock.release()

    def _set_container_launch_pending(
        self,
        session_id: str,
        pending: bool,
    ) -> None:
        """Persist whether a partially launched container Host needs reaping."""
        session = self._sessions.get(session_id)
        if session is None or not isinstance(session.target.container, dict):
            return
        target = session.target.container
        if pending:
            target["launch_pending_session_id"] = session_id
        elif target.get("launch_pending_session_id") == session_id:
            target.pop("launch_pending_session_id", None)
        else:
            return
        self._db.update_session_target(
            session_id,
            session.target.to_json(),
            session.target.cwd,
        )

