"""Session resume and resync helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .session_preferences import client_preferences
from .models import SessionStatus
from .startup_progress import lifecycle_lock, observed, phase
from .transport import SpawnTarget
from .privileged_plugins import CONTEXT_KEY, PrivilegedLaunchRefused, relay_agent, start_client
from .session_manager import (
    _MAX_RESUME_ROUNDS,
    _default_cwd,
    _venue_workspace_cwd,
    Session,
    log,
)

if TYPE_CHECKING:
    from .acp_client import AcpClient

_NATIVE_OWNER_CALLER_PREFIX = "native-owner-resume:"


def _core() -> Any:
    from . import session_manager as core

    return core


def _record_launch_refused(session: Any, exc: Any) -> None:
    """The typed event for a host launch-policy refusal of a respawn, and the
    durable stop it causes (``session_state_changed``, the authority attention
    waits and other subscribers settle ``stopped`` on)."""
    if session.event_log:
        session.event_log.append("launch_refused", {"codespace": exc.codespace, "reason": exc.reason})
        session.event_log.append("session_state_changed", {
            "status": SessionStatus.STOPPED.value, "reason": "launch_refused",
        })


class _SessionResumeMixin:
    """Session resume and resync helpers."""

    def import_native_session(
        self, target: SpawnTarget, native_session_id: str, *, agent_name: str,
    ) -> Session:
        """Persist a dormant owner-verified conversation without creating one."""
        if self.is_draining:
            from .session_manager import DaemonDrainingError

            raise DaemonDrainingError("session")
        worktree_id = target.worktree_id
        if not worktree_id or not target.cwd or not native_session_id:
            raise ValueError("native import requires an exact workspace and conversation")
        matches = [
            session for session in self._sessions.values()
            if session.acp_session_id == native_session_id
            and session.target.worktree_id == target.worktree_id
        ]
        if len(matches) > 1:
            raise ValueError("multiple bridge records claim the native worktree head")
        if matches:
            session = matches[0]
            if session.target.cwd != target.cwd or session.status != SessionStatus.STOPPED:
                raise ValueError("native conversation already has a different or live owner")
            return session
        from .events import EventLog

        session_id = str(uuid.uuid4())[:12]
        session = Session(
            session_id, f"resumed-{native_session_id[:8]}", target, agent_name,
            caller_id=_NATIVE_OWNER_CALLER_PREFIX + worktree_id,
        )
        session.acp_session_id = native_session_id
        session.status = SessionStatus.STOPPED
        session.background_recovery_enabled = False
        session.mcp_servers = [dict(server) for server in target.mcp_servers]
        self._db.create_session(
            session_id=session_id,
            name=session.name,
            agent_name=agent_name,
            caller_id=session.caller_id,
            target_dir=target.cwd,
            target_type=target.type,
            status=SessionStatus.STOPPED.value,
            now=time.time(),
            target_json=target.to_json(),
            config_json=json.dumps({"mcp_servers": session.mcp_servers}),
            acp_session_id=native_session_id,
            background_recovery_enabled=False,
        )
        session.event_log = EventLog(
            db=self._db, session_id=session_id, worktree_id=target.worktree_id,
        )
        self._sessions[session_id] = session
        return session

    @observed
    async def resume_session(
        self,
        session_id: str,
        permission_callback: Any | None = None,
        *,
        drain: bool = True,
        allow_recreate: bool = False,
        owner_native_resume: bool = False,
    ) -> Session:
        """Resume a stopped session by spawning a new process.

        Uses AcpClient.load_session() to reattach to the persisted ACP
        session. The session is ready to receive prompts when this returns.

        ``drain`` (default True): once the session lands IDLE, deliver any
        durable ``pending_prompts`` queued for it -- this is how a queue that
        outlived a bridge/host restart is delivered when the session comes back.
        ``submit_prompt``'s auto-resume path passes ``drain=False`` because it
        runs its own prompt next and that turn's settle drains the rest, so the
        resume never starts a second concurrent turn.

        ``allow_recreate`` (default False): the **end+create last resort** of the
        resume recovery ladder (#1468). When the ``_MAX_RESUME_ROUNDS`` stop->
        resume rounds all fail (they preserve context by re-``load_session``-ing
        the SAME ACP session), a truthy ``allow_recreate`` falls back to a FRESH
        ACP session (``new_session``) under the **same bridge session id** --
        recovering a working session at the cost of prior-turn context. The
        ``send``/auto-resume path opts in; an explicit ``resume`` leaves it False
        so a resume never silently drops context.

        ``owner_native_resume`` selects exact owner-fenced worktree recovery.
        Imported native series retain that policy in their existing caller
        affinity across frontend restarts and cannot opt into recreation.
        Other provider/singleton sessions keep their existing transport.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if session.target.type == "remote-acp":
            from .remote_acp_sessions import connect_remote_session
            from .remote_relationships import owned
            if owned(session):
                resumed = await connect_remote_session(
                    self, session.agent_name.removeprefix("remote-acp:"),
                    cwd=session.target.cwd or "/", remote_session_id=session.acp_session_id,
                    session=session, project=session.target.project, reuse=True,
                )
                if drain:
                    await self._drain_pending_prompts(resumed)
                return resumed
            async with lifecycle_lock(session._lifecycle_lock):
                if session.status != SessionStatus.STOPPED or not session.acp_session_id:
                    raise ValueError("Remote resume requires a stopped durable session")
                resumed = await connect_remote_session(
                    self, session.agent_name.removeprefix("remote-acp:"),
                    cwd=session.target.cwd or "/",
                    remote_session_id=session.acp_session_id, session=session,
                )
            if drain:
                await self._drain_pending_prompts(resumed)
            return resumed

        async with lifecycle_lock(session._lifecycle_lock):
            from .acquisition_gates import assert_resume_allowed
            assert_resume_allowed(self._db, session)
            if relay_agent(session.target) is not None or CONTEXT_KEY in (session.target.venue or {}):
                allow_recreate = False
            worktree_id = session.target.worktree_id
            owner_native_resume = owner_native_resume or (
                worktree_id is not None
                and session.caller_id == _NATIVE_OWNER_CALLER_PREFIX + worktree_id
            )
            if owner_native_resume:
                allow_recreate = False
                if not session.target.project or not session.target.worktree_id or not session.acp_session_id:
                    raise ValueError("Native owner resume requires a complete workspace binding")
            if self._db.execute_read(
                "SELECT 1 FROM provider_embodiments WHERE session_id=? LIMIT 1",
                (session_id,),
            ):
                # Creation identity survives an interrupted acknowledgement too.
                allow_recreate = False
            core = _core()
            if session.status != SessionStatus.STOPPED:
                raise ValueError(
                    f"Session {session_id} is {session.status.value}, not stopped"
                )
            if not session.acp_session_id and not allow_recreate:
                raise RuntimeError(
                    f"Session {session_id} has no ACP session ID -- cannot resume"
                )
            self._set_background_recovery_enabled(session, True)
            self._clear_disconnected_reattach_retry_state(session_id)

            # Prefer reattaching to a surviving Session Host (adopt the running
            # child + its in-flight turn) over a fresh child + load_session, so a
            # resume after a transport drop (laptop sleep / tunnel flap / SSH
            # sever) recovers the SAME work instead of abandoning a mid-turn tool
            # call (#145). Falls through to the fresh-child path below when no
            # live host survives (a genuinely dead child).
            #
            # The provider target refresh is deferred until AFTER this reattach
            # attempt. `_try_reattach_live_host` (and the `_recover_remote_host_
            # records` it drives) inspects and dials the far side using the
            # target already persisted on this session, so it needs no
            # refreshed provider data -- refreshing here provides no benefit to
            # reattach. Refreshing eagerly used to abort resume outright (and so
            # permanently block reattach) whenever the resolver could not yet
            # resolve the agent -- e.g. immediately after a daemon restart before
            # providers finish loading, or for a legacy session that predates
            # request-override provenance -- even though a perfectly
            # reattachable Session Host was waiting on the other end.
            with phase("session-host-reattach"):
                reattached = await self._try_reattach_live_host(session)
            if reattached:
                log.info(
                    "Session %s (%s) resumed by reattaching to its live "
                    "Session Host (no respawn)", session_id, session.name,
                )
                session.touch()
                return session

            with phase("provider-target"):
                await self._refresh_provider_target(session)
            original_target = session.target
            resume_target = original_target
            if owner_native_resume:
                resume_target = replace(
                    session.target, native_resume_id=session.acp_session_id,
                )

            session.status = SessionStatus.STARTING
            self._db.update_session_status(
                session_id, SessionStatus.STARTING.value, time.time()
            )

            def on_acp_event(event_type: str, data: dict[str, Any]) -> None:
                self._route_acp_event(session, event_type, data)

            client: AcpClient | None = None
            from .session_host.spawner import RemoteSpawnCleanupPendingError
            from .venue_launch_policy import LaunchRefusedError

            for attempt in range(1, _MAX_RESUME_ROUNDS + 1):
                client = None
                try:
                    session.target = resume_target
                    load_existing = bool(session.acp_session_id)
                    with phase("session-host-preparation"):
                        host_result = await self._resume_via_new_remote_host(
                            session,
                            on_acp_event=on_acp_event,
                            permission_callback=permission_callback,
                            load_existing=load_existing,
                        )
                    resumed_acp_id = (
                        host_result[1] if host_result is not None else None
                    )
                    client = host_result[0] if host_result is not None else None
                    if client is None:
                        with phase("process-preparation"):
                            agent_proc = await core.spawn(session.target)
                        client = core.AcpClient(
                            on_event=on_acp_event,
                            on_permission=permission_callback,
                            model_override=session.model_override,
                            effort_override=session.effort_override,
                            **client_preferences(session.target, self._db, session_id),
                        )
                        if permission_callback:
                            client.auto_approve = False
                        # Bound each round so a stalled Copilot ACP launch (the
                        # "Resuming…"/extension-reload race, #1468) fails fast and
                        # we re-roll, rather than hanging in STARTING.
                        with phase("acp-initialize"):
                            await asyncio.wait_for(
                                start_client(client, agent_proc.proc, session),
                                timeout=self._timeouts.session_start,
                            )
                        if session.acp_session_id:
                            with phase("native-session-load"):
                                await asyncio.wait_for(
                                    client.load_session(
                                        cwd=(
                                            _venue_workspace_cwd(session.target)
                                            or session.target.cwd
                                            or _default_cwd(session.target)
                                        ),
                                        session_id=session.acp_session_id,
                                        mcp_servers=session.mcp_servers,
                                    ),
                                    timeout=self._timeouts.session_new,
                                )
                            resumed_acp_id = session.acp_session_id
                        else:
                            resumed_acp_id = await asyncio.wait_for(
                                client.new_session(
                                    cwd=(
                                        _venue_workspace_cwd(session.target)
                                        or session.target.cwd
                                        or _default_cwd(session.target)
                                    ),
                                    mcp_servers=session.mcp_servers,
                                ),
                                timeout=self._timeouts.session_new,
                            )

                    if load_existing and resumed_acp_id != session.acp_session_id:
                        raise RuntimeError("native resume returned a different conversation identity")
                    if resumed_acp_id and (
                        resumed_acp_id != session.acp_session_id
                    ):
                        session.acp_session_id = resumed_acp_id
                        self._db.update_session_acp_id(
                            session_id,
                            resumed_acp_id,
                        )

                    session.client = client
                    session.status = SessionStatus.IDLE
                    self._db.update_session_status(
                        session_id, SessionStatus.IDLE.value, time.time(),
                        pid=session.pid,
                    )
                    if session.event_log:
                        session.event_log.append("session_state_changed", {
                            "status": SessionStatus.IDLE.value,
                            "resumed": True,
                            "acp_session_id": session.acp_session_id,
                            "resume_attempt": attempt,
                        })
                    log.info(
                        "Session %s (%s) resumed, pid=%s (attempt %d/%d)",
                        session_id, session.name, session.pid,
                        attempt, _MAX_RESUME_ROUNDS,
                    )
                    break  # success -- leave the ladder
                except Exception as exc:
                    if isinstance(exc, LaunchRefusedError):
                        # The host's launch policy refused: a deliberate,
                        # terminal answer -- never retried or recreated around.
                        session.status = SessionStatus.STOPPED
                        self._db.update_session_status(
                            session_id, SessionStatus.STOPPED.value, time.time(),
                        )
                        _record_launch_refused(session, exc)
                        raise
                    if isinstance(exc, RemoteSpawnCleanupPendingError):
                        session.status = SessionStatus.STOPPED
                        self._db.update_session_status(
                            session_id,
                            SessionStatus.STOPPED.value,
                            time.time(),
                        )
                        if session.event_log:
                            session.event_log.append(
                                "remote_launch_cleanup_pending",
                                {"message": str(exc)},
                            )
                        raise
                    # Capture the child's startup stderr tail BEFORE tearing the
                    # client down, so the retry marker records why it stalled.
                    stderr_tail = client.stderr_tail() if client else ""
                    # Many stall exceptions (notably ``asyncio.TimeoutError()``)
                    # have an empty ``str()`` -- keep the type so markers/logs are
                    # interpretable.
                    exc_desc = f"{type(exc).__name__}: {exc}".rstrip(": ")
                    # Stop the wedged child before the next round (the "stop" in
                    # stop->resume); re-rolls the launch against the SAME ACP
                    # session, preserving prior-turn context.
                    if client:
                        with contextlib.suppress(Exception):
                            await client.shutdown()
                    session.client = None
                    if isinstance(exc, PrivilegedLaunchRefused):
                        session.status = SessionStatus.STOPPED
                        self._db.update_session_status(
                            session_id, SessionStatus.STOPPED.value, time.time(),
                        )
                        if session.event_log:
                            session.event_log.append("error", {"message": str(exc)})
                            session.event_log.append("session_state_changed", {
                                "status": SessionStatus.STOPPED.value,
                                "reason": "privileged_preparation_refused",
                            })
                        raise
                    if session.event_log:
                        session.event_log.append("acp_resume_retry", {
                            "attempt": attempt,
                            "of": _MAX_RESUME_ROUNDS,
                            "error": exc_desc,
                            "stderr_tail": stderr_tail,
                            "will_retry": attempt < _MAX_RESUME_ROUNDS,
                        })
                    if attempt < _MAX_RESUME_ROUNDS:
                        log.warning(
                            "Resume attempt %d/%d for session %s failed (%s); "
                            "stopping the wedged child and re-rolling",
                            attempt, _MAX_RESUME_ROUNDS, session_id, exc_desc,
                        )
                        continue
                    # Ladder exhausted.
                    if not allow_recreate:
                        # No fresh-session fallback (e.g. an explicit `resume`):
                        # surface the failure rather than silently drop context.
                        session.status = SessionStatus.STOPPED
                        self._db.update_session_status(
                            session_id, SessionStatus.STOPPED.value, time.time()
                        )
                        if session.event_log:
                            session.event_log.append("error", {
                                "message": f"Resume failed after "
                                           f"{_MAX_RESUME_ROUNDS} attempts: "
                                           f"{exc_desc}",
                            })
                        log.error(
                            "Failed to resume session %s after %d attempts: %s",
                            session_id, _MAX_RESUME_ROUNDS, exc_desc,
                        )
                        raise
                    # allow_recreate: fall through to the in-place end+create
                    # (fresh ACP session, prior-turn context dropped) below.
                    log.warning(
                        "Resume ladder exhausted for session %s after %d "
                        "attempts (%s); falling back to a fresh ACP session "
                        "(end+create, prior-turn context dropped)",
                        session_id, _MAX_RESUME_ROUNDS, exc_desc,
                    )
                    break
                finally:
                    session.target = original_target

            # End+create last resort (opt-in, ladder exhausted). The stop->resume
            # rounds above all failed to reattach the persisted ACP session; as a
            # final recovery, end it and create a FRESH ACP session in place --
            # SAME bridge session id (delivery cursor / affinity intact), new
            # (empty) ACP session -- so a wedged CodeSpace resume still yields a
            # working session, trading prior-turn context for availability
            # (#1468). Only reached when allow_recreate and no round succeeded.
            if session.status != SessionStatus.IDLE:
                recreate_client: AcpClient | None = None
                try:
                    host_result = await self._resume_via_new_remote_host(
                        session,
                        on_acp_event=on_acp_event,
                        permission_callback=permission_callback,
                        load_existing=False,
                    )
                    if host_result is not None:
                        recreate_client, new_acp = host_result
                    else:
                        agent_proc = await core.spawn(session.target)
                        recreate_client = core.AcpClient(
                            on_event=on_acp_event,
                            on_permission=permission_callback,
                            model_override=session.model_override,
                            effort_override=session.effort_override,
                            **client_preferences(session.target, self._db, session_id),
                        )
                        if permission_callback:
                            recreate_client.auto_approve = False
                        await asyncio.wait_for(
                            start_client(recreate_client, agent_proc.proc, session),
                            timeout=self._timeouts.session_start,
                        )
                        new_acp = await asyncio.wait_for(
                            recreate_client.new_session(
                                cwd=(
                                    _venue_workspace_cwd(session.target)
                                    or session.target.cwd
                                    or _default_cwd(session.target)
                                ),
                                mcp_servers=session.mcp_servers,
                            ),
                            timeout=self._timeouts.session_new,
                        )
                    old_acp = session.acp_session_id
                    session.client = recreate_client
                    session.acp_session_id = new_acp
                    session.status = SessionStatus.IDLE
                    if session.event_log:
                        session.event_log.set_telemetry_identity(
                            acp_session_id=new_acp,
                            worktree_id=session.target.worktree_id,
                        )
                    # The fresh ACP session starts EMPTY -- reset context-usage /
                    # handoff state so stale "critical" usage or a pending
                    # context-pressure handoff from the dropped session can't
                    # misfire against the new (empty) session (review on #1468).
                    session.context_size = None
                    session.context_used = None
                    session._crossed_thresholds = set()
                    session._handoff_pending = False
                    self._db.update_session_acp_id(session_id, new_acp)
                    self._db.update_session_status(
                        session_id, SessionStatus.IDLE.value, time.time(),
                        pid=session.pid,
                    )
                    if session.event_log:
                        # Durable lifecycle transition for SSE/telemetry consumers
                        # (the recreate is still a resume-to-IDLE, just onto a
                        # fresh ACP session).
                        session.event_log.append("session_state_changed", {
                            "status": SessionStatus.IDLE.value,
                            "resumed": True,
                            "recreated": True,
                            "acp_session_id": new_acp,
                        })
                        session.event_log.append("acp_resume_recreated", {
                            "old_acp_session_id": old_acp,
                            "new_acp_session_id": new_acp,
                            "context_dropped": True,
                        })
                    log.warning(
                        "Session %s (%s) recreated with a fresh ACP session %s "
                        "(was %s) after the resume ladder was exhausted -- "
                        "prior-turn context dropped",
                        session_id, session.name, new_acp, old_acp,
                    )
                except Exception as exc:
                    if recreate_client:
                        with contextlib.suppress(Exception):
                            await recreate_client.shutdown()
                    session.client = None
                    session.status = SessionStatus.STOPPED
                    self._db.update_session_status(
                        session_id, SessionStatus.STOPPED.value, time.time()
                    )
                    exc_desc = f"{type(exc).__name__}: {exc}".rstrip(": ")
                    if isinstance(exc, LaunchRefusedError):
                        # A refusal at the recreate step is the same terminal
                        # answer as one in the ladder: the same typed event.
                        _record_launch_refused(session, exc)
                    elif session.event_log:
                        session.event_log.append("error", {
                            "message": f"Resume + recreate failed: {exc_desc}",
                        })
                    log.error(
                        "Resume + recreate failed for session %s: %s",
                        session_id, exc_desc,
                    )
                    raise

        session.touch()
        # Queue outlived the restart? Deliver it now that the session is IDLE.
        # Outside the lifecycle lock (drain submits a fresh turn). Skipped by the
        # auto-resume-from-submit path (drain=False) to avoid a double turn.
        if drain and session.status == SessionStatus.IDLE:
            await self._drain_pending_prompts(session)
        return session

    async def resync_session(self, session_id: str) -> int:
        """Rebuild a session's event log from the agent's authoritative replay.

        Reattaches to the persisted ACP session and captures the full
        conversation history the agent streams back during load (per the ACP
        spec), then replaces the event log with it. This heals logs that were
        truncated by a mid-session disconnect (e.g. an oversized ACP frame
        that crashed the read loop): the agent always holds the complete
        history, so its replay is the source of truth.

        Idempotent: resyncing an already-complete session rebuilds the same
        log. Leaves the session IDLE with a live client, ready for prompts.
        Returns the number of events in the rebuilt log.

        A ``RUNNING`` status only blocks resync while a turn is *actually* live
        in this daemon. A **wedged** session -- status left at ``RUNNING`` with
        no live prompt task (a turn whose runner already exited without a
        terminal event, or a session rehydrated after a daemon restart) -- is
        exactly what needs healing, so it is allowed through; only a genuinely
        live turn is refused (issue #22 / #2385).
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.target.type == "remote-acp":
            raise ValueError("Remote ACP history is loaded through explicit stop/resume, not process resync")
        if not session.acp_session_id:
            raise RuntimeError(
                f"Session {session_id} has no ACP session ID -- cannot resync"
            )

        async with session._lifecycle_lock:
            core = _core()
            if session.status == SessionStatus.RUNNING:
                turn_live = (
                    session._prompt_task is not None
                    and not session._prompt_task.done()
                )
                if turn_live:
                    raise ValueError(
                        f"Session {session_id} is running a live turn "
                        "-- cannot resync"
                    )
                # Wedged RUNNING: no live turn to protect. Cancel any lingering
                # (already-finished) task handle and heal the stuck state.
                log.warning(
                    "Resyncing wedged RUNNING session %s (no live turn)",
                    session_id,
                )
                if session._prompt_task is not None:
                    with contextlib.suppress(Exception):
                        session._prompt_task.cancel()

            # Tear down any live client so we can reattach cleanly.
            if session.client:
                with contextlib.suppress(Exception):
                    await session.client.shutdown()
                session.client = None

            session.status = SessionStatus.STARTING
            self._db.update_session_status(
                session_id, SessionStatus.STARTING.value, time.time()
            )

            captured: list[tuple[str, dict[str, Any]]] = []

            def on_capture(event_type: str, data: dict[str, Any]) -> None:
                captured.append((event_type, data))
                if event_type == "usage_update":
                    self._handle_usage_update(session, data)

            client: AcpClient | None = None
            try:
                core = _core()
                agent_proc = await core.spawn(session.target)
                client = core.AcpClient(
                    on_event=on_capture,
                    model_override=session.model_override,
                    effort_override=session.effort_override,
                    **client_preferences(session.target, self._db, session_id),
                )
                await start_client(client, agent_proc.proc, session)
                # suppress_replay=False -> the replayed history is captured.
                await client.load_session(
                    cwd=session.target.cwd or _default_cwd(session.target),
                    session_id=session.acp_session_id,
                    mcp_servers=session.mcp_servers,
                    suppress_replay=False,
                )

                count = 0
                if session.event_log:
                    count = session.event_log.rebuild(captured)
                    session.event_log.append("session_state_changed", {
                        "status": SessionStatus.IDLE.value,
                        "resynced": True,
                        "acp_session_id": session.acp_session_id,
                    })

                session.client = client
                session.status = SessionStatus.IDLE
                self._db.update_session_status(
                    session_id, SessionStatus.IDLE.value, time.time(),
                    pid=session.pid,
                )
                log.info(
                    "Session %s (%s) resynced: rebuilt %d events",
                    session_id, session.name, count,
                )
            except Exception as exc:
                if client:
                    with contextlib.suppress(Exception):
                        await client.shutdown()
                session.client = None
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(
                    session_id, SessionStatus.STOPPED.value, time.time()
                )
                from .venue_launch_policy import LaunchRefusedError

                if isinstance(exc, LaunchRefusedError):  # a resync refused like a resume
                    _record_launch_refused(session, exc)
                log.error("Failed to resync session %s: %s", session_id, exc)
                raise

        session.touch()
        return count
