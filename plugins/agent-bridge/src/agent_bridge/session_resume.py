"""Session lifecycle operations; transport factories remain manager injection seams."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .session_ownership import (
    cleanup_failed_resume,
    finish_owned,
    settle_cancelled_resume,
    spawn_owned,
)

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager


async def resume_session_admitted(
    self: SessionManager,
    session: Session,
    permission_callback: Any | None = None,
    *,
    allow_recreate: bool = False,
) -> Session:
    """Resume while the caller owns turn admission; acquire lifecycle once."""

    from .session_manager import (
        AcpClient,
        SessionStatus,
        _MAX_RESUME_ROUNDS,
        _default_cwd,
        _venue_workspace_cwd,
        asyncio,
        log,
        spawn,
        time,
    )

    session_id = session.session_id
    async with session._lifecycle_lock:
        if self._sessions.get(session_id) is not session:
            raise KeyError(f"Session {session_id} not found")
        if session.status != SessionStatus.STOPPED:
            raise ValueError(
                f"Session {session_id} is {session.status.value}, not stopped"
            )
        # The caller now owns recovery; a failed explicit attempt must not
        # leave stale restart intent rearming background provider probes.
        session._lifecycle_generation += 1
        session.restart_status = None
        self._db.update_session_status(
            session_id, SessionStatus.STOPPED.value, time.time(),
        )
        if self._host_index is not None:
            self._host_index.set_resume_flag(session_id, False)
        if not session.acp_session_id and not allow_recreate:
            raise RuntimeError(
                f"Session {session_id} has no ACP session ID -- cannot resume"
            )

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
        if await self._try_reattach_live_host(session):
            log.info(
                "Session %s (%s) resumed by reattaching to its live "
                "Session Host (no respawn)", session_id, session.name,
            )
            session.touch()
            return session

        await self._refresh_provider_target(session)

        session.status = SessionStatus.STARTING
        self._db.update_session_status(
            session_id, SessionStatus.STARTING.value, time.time()
        )

        def on_acp_event(event_type: str, data: dict[str, Any]) -> None:
            if session.event_log:
                session.event_log.append(event_type, data)
            self._capture_progress(session, event_type, data)
            if event_type == "usage_update":
                self._handle_usage_update(session, data)

        client: AcpClient | None = None
        from .session_host.spawner import RemoteSpawnCleanupPendingError

        for attempt in range(1, _MAX_RESUME_ROUNDS + 1):
            client = None
            try:
                load_existing = bool(session.acp_session_id)
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
                    agent_proc = await spawn_owned(session, spawn(session.target))
                    client = AcpClient(
                        on_event=on_acp_event,
                        on_permission=permission_callback,
                        model_override=session.model_override,
                        effort_override=session.effort_override,
                    )
                    if permission_callback:
                        client.auto_approve = False
                    # Bound each round so a stalled Copilot ACP launch (the
                    # "Resuming…"/extension-reload race, #1468) fails fast and
                    # we re-roll, rather than hanging in STARTING.
                    await asyncio.wait_for(
                        client.start(agent_proc.proc),
                        timeout=self._timeouts.session_start,
                    )
                    if session.acp_session_id:
                        await asyncio.wait_for(
                            client.load_session(
                                cwd=(
                                    session.target.cwd
                                    or _default_cwd(session.target)
                                ),
                                session_id=session.acp_session_id,
                            ),
                            timeout=self._timeouts.session_new,
                        )
                        resumed_acp_id = session.acp_session_id
                    else:
                        resumed_acp_id = await asyncio.wait_for(
                            client.new_session(
                                cwd=(
                                    session.target.cwd
                                    or _default_cwd(session.target)
                                ),
                                mcp_servers=session.mcp_servers,
                            ),
                            timeout=self._timeouts.session_new,
                        )

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
            except asyncio.CancelledError:
                await finish_owned(
                    settle_cancelled_resume(self, session, client),
                    propagate_cancel=False,
                )
                raise
            except Exception as exc:
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
                await cleanup_failed_resume(self, session, client)
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
                    agent_proc = await spawn_owned(session, spawn(session.target))
                    recreate_client = AcpClient(
                        on_event=on_acp_event,
                        on_permission=permission_callback,
                        model_override=session.model_override,
                        effort_override=session.effort_override,
                    )
                    if permission_callback:
                        recreate_client.auto_approve = False
                    await asyncio.wait_for(
                        recreate_client.start(agent_proc.proc),
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
            except asyncio.CancelledError:
                await finish_owned(
                    settle_cancelled_resume(self, session, recreate_client),
                    propagate_cancel=False,
                )
                raise
            except Exception as exc:
                await cleanup_failed_resume(self, session, recreate_client)
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(
                    session_id, SessionStatus.STOPPED.value, time.time()
                )
                exc_desc = f"{type(exc).__name__}: {exc}".rstrip(": ")
                if session.event_log:
                    session.event_log.append("error", {
                        "message": f"Resume + recreate failed: {exc_desc}",
                    })
                log.error(
                    "Resume + recreate failed for session %s: %s",
                    session_id, exc_desc,
                )
                raise

    session.touch()
    return session


async def resync_session(self: SessionManager, session_id: str, *, background: bool = False) -> int:
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

    from .session_manager import (
        AcpClient,
        SessionStatus,
        _default_cwd,
        asyncio,
        contextlib,
        log,
        spawn,
        time,
    )

    session_id = self._resolve_ref(session_id) or session_id
    session = self._sessions.get(session_id)
    if not session:
        raise KeyError(f"Session {session_id} not found")
    if not session.acp_session_id:
        raise RuntimeError(
            f"Session {session_id} has no ACP session ID -- cannot resync"
        )

    async with session._turn_start_lock, session._lifecycle_lock:
        if self._sessions.get(session_id) is not session:
            raise KeyError(f"Session {session_id} not found")
        if background and session.status != SessionStatus.RUNNING:
            raise ValueError(
                f"Session {session_id} is no longer running; skipping background resync"
            )
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

        session._lifecycle_generation += 1
        session.restart_status = None
        if self._host_index is not None:
            self._host_index.set_resume_flag(session_id, False)

        # Tear down any live client so we can reattach cleanly.
        await cleanup_failed_resume(self, session, session.client)

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
            agent_proc = await spawn_owned(session, spawn(session.target))
            client = AcpClient(
                on_event=on_capture,
                model_override=session.model_override,
                effort_override=session.effort_override,
            )
            await client.start(agent_proc.proc)
            # suppress_replay=False -> the replayed history is captured.
            await client.load_session(
                cwd=session.target.cwd or _default_cwd(session.target),
                session_id=session.acp_session_id,
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
        except asyncio.CancelledError:
            await finish_owned(
                settle_cancelled_resume(self, session, client, trigger="resync_cancelled"),
                propagate_cancel=False,
            )
            raise
        except Exception as exc:
            await cleanup_failed_resume(self, session, client)
            session.status = SessionStatus.STOPPED
            self._db.update_session_status(
                session_id, SessionStatus.STOPPED.value, time.time()
            )
            log.error("Failed to resync session %s: %s", session_id, exc)
            raise

    session.touch()
    return count
