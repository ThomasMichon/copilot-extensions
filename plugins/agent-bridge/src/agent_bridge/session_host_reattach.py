"""Reattach to an existing Host without starting another native conversation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import time
from typing import TYPE_CHECKING, Any, cast

from agent_remote_driver.session_preferences import client_preferences

from .session_status import SessionStatus

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager


class _SessionHostReattachMixin:
    async def _reattach_one(
        self, rec: Any, session: Session, *, new_status: SessionStatus,
        send_resume: bool = False, prune_on_fail: bool = False,
        refresh_relays: bool = False, require_relay_ready: bool = False,
        retained_check: Callable[[], None] | None = None,
    ) -> bool:
        """Adopt the existing Host transport; retained completion never prunes or nudges."""
        self = cast("SessionManager", self)
        from . import session_manager as core
        from .session_manager import log
        from .session_host.acp_adapter import open_acp_streams
        from agent_remote_driver.session_host.client import SessionHostClient
        from .session_host.endpoints import CredentialRelayReadinessError
        from .acquisition_launch_owner import require_unassociated_session
        from .retained_session_recovery import _refuse, permission_held

        require_unassociated_session(self, session, operation="Host reattachment")
        native = session.acp_session_id
        if native is None:
            return False
        if retained_check is not None:
            if (
                session.client is not None or new_status != SessionStatus.STOPPED
                or send_resume or prune_on_fail or refresh_relays or require_relay_ready
                or rec.boundary != "local" or rec.endpoint
            ):
                raise _refuse("Retained reattachment cannot replace transport or release custody")
            retained_check()

        # Host-mode shutdown detaches its frontend; the child survives.
        old = session.client
        if old is not None:
            with contextlib.suppress(Exception):
                await old.shutdown()

        sock = None
        streams = None
        client = None

        async def _close_partial_reattach() -> None:
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.shutdown()
            if streams is not None:
                with contextlib.suppress(Exception):
                    await streams.aclose()
            if sock is not None:
                with contextlib.suppress(Exception):
                    await sock.close()

        def _settle_dead_child(exit_code: int) -> None:
            client_obj = session.client
            if client_obj is not None:
                client_obj.mark_host_child_exited(exit_code)
            self._reap_host_record(rec, f"Session Host child exited (code={exit_code})")
            session.client = None
            session.status = SessionStatus.STOPPED
            self._db.update_session_status(rec.session_id, SessionStatus.STOPPED.value, time.time())
            if session.event_log:
                session.event_log.append("session_state_changed", {
                    "status": SessionStatus.STOPPED.value,
                    "host_child_exited": True, "exit_code": exit_code,
                })

        try:
            await self._ensure_forward(
                rec, refresh_relays=refresh_relays, require_relay_ready=require_relay_ready,
            )
            sock = await SessionHostClient.connect(port=rec.port)
            hello = await sock.attach(0, nonce=getattr(rec, "nonce", "").encode())
            if retained_check is not None:
                retained_check()
                if hello.child_pid != rec.child_pid:
                    raise _refuse("Retained Host acknowledged another child")
            streams = await open_acp_streams(sock)

            async def _closer(_streams: Any = streams, _sock: Any = sock) -> None:
                await _streams.aclose()
                await _sock.close()

            def _on_acp_event(event_type: str, data: dict[str, Any]) -> None:
                self._route_acp_event(session, event_type, data)

            client = core.AcpClient(
                on_event=_on_acp_event,
                model_override=session.model_override,
                effort_override=session.effort_override,
                **client_preferences(session.target, self._db, rec.session_id),
            )
            if permission_held(self._db, session.target):
                client.auto_approve = False
            streams.on_transport_lost = client.mark_transport_lost
            streams.on_child_exit = client.mark_host_child_exited
            setattr(client, "session_host_client", sock)
            await asyncio.wait_for(
                client.start_streams(
                    streams.reader, streams.writer, child_pid=rec.child_pid, closer=_closer,
                ), timeout=self._timeouts.session_start,
            )
            if streams.child_exit_code is not None:
                client.mark_host_child_exited(streams.child_exit_code)
                await _close_partial_reattach()
                if retained_check is None:
                    _settle_dead_child(streams.child_exit_code)
                return False
            if retained_check is not None:
                retained_check()
            client.adopt_session(native)
            from .privileged_plugins import restore_host_launch

            restore_host_launch(session, rec, client)
            session.client = client
            session.status = new_status
            self._db.update_session_status(
                rec.session_id, new_status.value, time.time(), pid=session.pid,
            )
            log.info(
                "Reattached session %s to live Session Host (pid=%s, port=%s)",
                rec.session_id, rec.host_pid, rec.port,
            )
        except asyncio.CancelledError:
            await _close_partial_reattach()
            raise
        except Exception as exc:
            from .privileged_plugins import PrivilegedLaunchRefused

            child_exit_code = streams.child_exit_code if streams is not None else None
            await _close_partial_reattach()
            if isinstance(exc, (CredentialRelayReadinessError, PrivilegedLaunchRefused)):
                raise
            if child_exit_code is not None and retained_check is None:
                session.client = client
                _settle_dead_child(child_exit_code)
                return False
            log.warning(
                "Failed to reattach session %s to host pid=%s%s", rec.session_id, rec.host_pid,
                "; pruning" if prune_on_fail else "", exc_info=True,
            )
            if prune_on_fail and self._host_index is not None:
                with contextlib.suppress(Exception):
                    await self._drop_forward(rec.session_id)
                with contextlib.suppress(Exception):
                    self._host_index.remove(rec.session_id)
            return False

        if send_resume and self._host_index is not None:
            self._host_index.set_resume_flag(rec.session_id, False)
            try:
                await self.submit_prompt(rec.session_id, "Resume")
                log.info(
                    "Sent 'Resume' to reattached session %s "
                    "(turn was graceful-cancelled for redeploy)", rec.session_id,
                )
            except Exception:
                log.warning("Failed to send 'Resume' to reattached session %s", rec.session_id, exc_info=True)
        return True
