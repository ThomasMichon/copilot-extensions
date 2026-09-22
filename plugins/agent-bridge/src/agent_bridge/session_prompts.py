"""Prompt admission, queueing, and usage tracking helpers."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import time
from typing import Any

from .models import SessionStatus
from .session_manager import DaemonDrainingError, Session, log
from .transport import SpawnTarget


class _SessionPromptMixin:
    """Prompt admission, queueing, and usage tracking helpers."""

    async def submit_prompt(self, session_id: str, prompt: str) -> int:
        """Atomically start a turn against conditional idle teardown."""
        if self._draining:
            raise DaemonDrainingError("turn")
        resolved = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(resolved)
        if not session:
            raise KeyError(f"Session {resolved} not found")
        async with session._turn_start_lock:
            return await self._submit_prompt_locked(resolved, prompt)

    async def _submit_prompt_locked(self, session_id: str, prompt: str) -> int:
        """Submit a prompt to a session, returning the turn index.

        The prompt is sent to the ACP subprocess. Streaming events
        (agent_message, tool_call_start, etc.) flow to the EventLog in
        real time. The prompt runs as a background task so the HTTP
        request can return immediately -- callers consume output via SSE.

        If the session process has died (e.g. after a server restart)
        but the ACP session ID is available, the process is
        automatically re-spawned and the session resumed before
        delivering the prompt.
        """
        if self._draining:
            raise DaemonDrainingError("turn")
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.status not in (SessionStatus.IDLE, SessionStatus.STOPPED):
            raise ValueError(
                f"Session {session_id} is {session.status.value}, not idle"
            )

        # Auto-resume if the process is dead but session is recoverable
        if not session.client or not session.client.is_running:
            log.info(
                "Session %s (%s) process is dead -- auto-%s",
                session_id,
                session.name,
                (
                    "resuming"
                    if session.acp_session_id
                    else "creating a fresh ACP session"
                ),
            )
            # Mark as STOPPED so resume_session accepts it
            session.status = SessionStatus.STOPPED
            # A `send`/prompt must land a working session, so opt into the
            # end+create last resort: if the stop->resume ladder is exhausted,
            # recreate a fresh ACP session in place rather than failing the send
            # (#1468).
            await self.resume_session(
                session_id, drain=False, allow_recreate=True
            )
            # resume_session sets status to IDLE and attaches a new client

        turn_index = session.turn_count
        session.turn_count += 1
        now = time.time()

        # Persist turn skeleton
        self._db.create_turn(session_id, turn_index, prompt, now)

        # Cancel any pending out-of-turn content bracket (#2835) synchronously,
        # BEFORE this new turn's `running` is written and the prompt task is
        # created. A due settle timer would otherwise fire between here and the
        # background task starting, injecting a spurious `idle` into the new
        # turn. Doing it here (on the loop, with no await before the task is
        # scheduled) guarantees the cancel wins that race.
        cancel_ooo = getattr(session.client, "_cancel_out_of_turn", None)
        if callable(cancel_ooo):
            # Real clients expose a sync canceller; tolerate a test double that
            # returns a coroutine by closing it (never a coroutine in prod).
            _maybe = cancel_ooo()
            if inspect.iscoroutine(_maybe):
                _maybe.close()

        # Update status
        session.status = SessionStatus.RUNNING
        # Reset the live-stall clock at turn start. `last_output_at` only
        # advances on an ACP frame, so after a long idle gap it still points at
        # the *previous* turn's last frame. Without this reset, the live-stall
        # watchdog (reconcile_wedged_running) sees the brand-new turn as
        # "silent for <entire idle gap>", judges it stalled, and interrupts it
        # (ACP session/cancel) before it can emit its first frame -- surfacing
        # as a phantom "Operation cancelled by user" on the first resend after
        # an idle gap > live_stall_interrupt_after_s (#4122). Silence must be
        # measured within the current turn; a genuine stall is still caught,
        # measured from turn-start. Synchronous (no await before the prompt task
        # is scheduled), so it wins the race against the watchdog.
        session.last_output_at = now
        self._db.update_session_status(session_id, SessionStatus.RUNNING.value, now)

        if session.event_log:
            # Persist the user's prompt as a durable, replayable event -- not
            # just a row in the turns table -- so every consumer (other tabs,
            # other relay instances, and history replayed on resume/open) sees
            # the prompt bubble, not only the agent's response. This mirrors
            # what the agent's load-time replay emits during a resync, keeping
            # live and replayed histories consistent.
            session.event_log.append("user_message", {"content": prompt})
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.RUNNING.value,
                "turn_index": turn_index,
            })

        # Run the prompt as a background task
        session._prompt_task = asyncio.create_task(
            self._run_prompt(session, turn_index, prompt)
        )

        # The child is now busy: tell its session host it is NOT reapable so a
        # front lost mid-turn never self-reaps a running turn (#51).
        await self._notify_host_reapable(session)

        session.touch()
        return turn_index

    async def submit_or_queue_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a prompt now, or durably queue it if the session is busy.

        The durable counterpart to :meth:`submit_prompt`. Where ``submit_prompt``
        rejects a busy session (409), this persists the follow-up to the
        ``pending_prompts`` table so it survives a caller remount, an NF crash,
        and a bridge/host restart, then drains it -- FIFO, exactly once -- on the
        next turn-settle (or on resume). This is the send-or-queue seam host CLI
        agents and Neuron Forge both submit through.

        Returns a dict describing the outcome:
          - ran now:   ``{"queued": False, "turn_index": int, "status": str}``
          - enqueued:  ``{"queued": True, "queue_id": int, "position": int,
                          "status": str}``

        A prompt is enqueued (rather than run) when the session is actively
        running a turn, when the daemon is draining for redeploy, or when a
        queue already exists (a new submit joins the back of the line -- never
        jumps ahead of already-queued follow-ups). Otherwise it runs immediately
        via ``submit_prompt`` (which auto-resumes a recoverable STOPPED session).
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        async with session._turn_start_lock:
            if self._sessions.get(session_id) is not session:
                raise KeyError(f"Session {session_id} not found")
            result, kick_session = await self._submit_or_queue_prompt_locked(
                session,
                prompt,
                caller_id=caller_id,
            )
        if kick_session is not None:
            await self._kick_pending_drain(kick_session)
        return result

    async def _submit_or_queue_prompt_locked(
        self,
        session: Session,
        prompt: str,
        *,
        caller_id: str | None = None,
    ) -> tuple[dict[str, Any], Session | None]:
        """Admit or queue one prompt while conditional teardown is excluded."""
        session_id = session.session_id

        # Context-pressure handoff (opt-in, prompt-triggered): a prompt into an
        # already-saturated session that is idle rolls the worktree to a fresh
        # successor FIRST, then delivers this prompt to it. This is the only way
        # forward for a minimal consumer -- a phone -- that can *only* send the
        # next message and has no manual session-creation affordance. Unlike the
        # proactive path this ignores `unwatched_only`: the sender is explicitly
        # asking for the next turn. A live turn is left to the proactive
        # turn-settle path (it would be handed off mid-turn otherwise); the
        # successor is fresh, so this never recurses.
        if (session.status in (SessionStatus.IDLE, SessionStatus.STOPPED)
                and self._is_over_critical(session)
                and self._auto_handoff_eligible(session)):
            session._handoff_pending = False
            successor = await self.handoff_session(
                session_id, reason="context-pressure-prompt"
            )
            return (
                await self.submit_or_queue_prompt(
                    successor.session_id, prompt, caller_id=caller_id
                ),
                None,
            )

        turn_live = (
            session.status == SessionStatus.RUNNING
            or (session._prompt_task is not None
                and not session._prompt_task.done())
        )
        queue_nonempty = self._db.count_pending_prompts(session_id) > 0
        # During a redeploy drain we must not start a new turn, but we can still
        # persist the follow-up -- it delivers after the restart resumes.
        must_queue = turn_live or queue_nonempty or self._draining

        if not must_queue:
            turn_index = await self._submit_prompt_locked(session_id, prompt)
            return (
                {
                    "queued": False,
                    "turn_index": turn_index,
                    "status": session.status.value,
                },
                None,
            )

        now = time.time()
        queue_id = self._db.enqueue_prompt(
            session_id, prompt, now, caller_id=caller_id
        )
        position = self._db.count_pending_prompts(session_id)
        if session.event_log:
            session.event_log.append("prompt_enqueued", {
                "queue_id": queue_id,
                "caller_id": caller_id,
                "position": position,
            })
        log.info(
            "Queued prompt %d for session %s (position %d, status=%s)",
            queue_id, session_id, position, session.status.value,
        )
        # If no turn is live to drain the queue on settle -- and we are not
        # mid-drain -- kick delivery now, resuming a recoverable STOPPED session
        # if needed. A live turn's settle tail (or the post-restart resume) will
        # drain otherwise. Arm recovery HERE, at admission -- not later inside
        # the kick's resume_session() call -- so an explicit stop racing this
        # admission cannot leave the session dormant despite a queued prompt
        # already committed to running it (review of #3058).
        kick_session = session if not turn_live and not self._draining else None
        if kick_session is not None and session.status == SessionStatus.STOPPED:
            self._set_background_recovery_enabled(session, True)
        return (
            {
                "queued": True,
                "queue_id": queue_id,
                "position": position,
                "status": session.status.value,
            },
            kick_session,
        )

    async def _kick_pending_drain(self, session: Session) -> None:
        """Start draining a queue when no turn-settle will do it for us.

        For an IDLE session with a live client, drain directly. For a
        recoverable STOPPED session (queue outlived a restart, or a fresh submit
        landed on a dormant session), resume it -- ``resume_session`` drains as
        it lands IDLE. Best-effort: a failure here leaves the rows durably
        queued for the next resume/settle, never lost.
        """
        try:
            if (session.status == SessionStatus.IDLE
                    and session.client and session.client.is_running):
                await self._drain_pending_prompts(session)
            elif (session.status == SessionStatus.STOPPED
                    and session.acp_session_id):
                await self.resume_session(session.session_id)
        except Exception as exc:
            log.warning(
                "Kick-drain for session %s failed (queue preserved): %s",
                session.session_id, exc,
            )

    async def _drain_pending_prompts(self, session: Session) -> None:
        """Deliver the next durable queued prompt, if the session can run it.

        Called from the turn-settle tail and the resume idle-tail. Atomically
        pops one row and submits it; the follow-up's own settle re-invokes this,
        so the queue drains one-per-turn in FIFO order. Only drains an IDLE
        session with a live client -- if the process is dead, a later resume
        drains instead, so a queued message is never lost to a dead process, and
        pop-then-submit stays exactly-once (no loss, no dup).
        """
        async with session._turn_start_lock:
            if self._sessions.get(session.session_id) is not session:
                return
            await self._drain_pending_prompts_locked(session)

    async def _drain_pending_prompts_locked(self, session: Session) -> None:
        """Pop and submit one queued prompt while teardown is excluded."""
        if session.status != SessionStatus.IDLE:
            return
        if not (session.client and session.client.is_running):
            return
        row = self._db.pop_pending_prompt(session.session_id)
        if row is None:
            return
        prompt = row["prompt"]
        if session.event_log:
            session.event_log.append("prompt_dequeued", {
                "queue_id": row["id"],
                "caller_id": row.get("caller_id"),
            })
        try:
            await self._submit_prompt_locked(session.session_id, prompt)
        except Exception as exc:
            # Defensive: an unexpected submit failure must not drop the message.
            # Re-enqueue (at the tail -- a rare reorder is better than a loss);
            # a later settle/resume retries delivery.
            log.error(
                "Dequeued prompt for session %s failed to submit: %s "
                "-- re-enqueuing",
                session.session_id, exc,
            )
            self._db.enqueue_prompt(
                session.session_id, prompt, time.time(),
                caller_id=row.get("caller_id"),
            )

    def _clear_pending_queue(self, session: Session, *, reason: str) -> int:
        """Drop a session's whole durable queue; emit ``queue_cleared`` if any.

        The shared teardown path for interrupt/end (and any future rollback):
        mirrors NF's "queue cleared if cancelled, ended, or rolled" so queued
        follow-ups never resurface against a session the operator tore down.
        Returns how many rows were removed.
        """
        removed = self._db.clear_pending_prompts(session.session_id)
        if removed and session.event_log:
            session.event_log.append("queue_cleared", {
                "removed": removed,
                "reason": reason,
            })
        return removed

    def list_pending_queue(self, session_id: str) -> list[dict[str, Any]]:
        """Snapshot a session's durable queue in FIFO order (route/CLI read)."""
        session_id = self._resolve_ref(session_id) or session_id
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        return self._db.list_pending_prompts(session_id)

    def remove_pending_prompt(self, session_id: str, queue_id: int) -> bool:
        """Drop one queued prompt by id (operator drops a chip). True if hit."""
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        removed = self._db.remove_pending_prompt(session_id, queue_id)
        if removed and session.event_log:
            session.event_log.append("prompt_removed", {"queue_id": queue_id})
        return removed

    def clear_pending_queue(self, session_id: str) -> int:
        """Clear a session's whole durable queue on operator request."""
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        return self._clear_pending_queue(session, reason="cleared")

    async def _run_prompt(
        self, session: Session, turn_index: int, prompt: str
    ) -> None:
        """Background task: send prompt via ACP and persist the result."""
        try:
            result = await session.client.send_prompt(prompt)

            # Persist completed turn
            self._db.update_turn(
                session.session_id,
                turn_index,
                response_text=result.get("response_text", ""),
                thought_text=result.get("thought_text", ""),
                stop_reason=result.get("stop_reason"),
                tool_calls_json=json.dumps(result.get("tool_calls", [])),
                completed_at=time.time(),
            )

            session.status = SessionStatus.IDLE
            self._db.update_session_status(
                session.session_id, SessionStatus.IDLE.value, time.time()
            )

        except Exception as exc:
            log.error(
                "Prompt failed for session %s turn %d: %s",
                session.session_id, turn_index, exc,
            )
            self._db.update_turn(
                session.session_id,
                turn_index,
                stop_reason=f"error: {exc}",
                completed_at=time.time(),
            )
            session.status = SessionStatus.IDLE
            self._db.update_session_status(
                session.session_id, SessionStatus.IDLE.value, time.time()
            )

        # Always drive the event log to a terminal state so no consumer is left
        # mirroring a turn that never ends. On the happy path this trails the
        # client's turn_complete; on failure it is paired with the client's
        # (now non-hanging) error -- either way the stream reaches idle, matching
        # the synthetic idle a resync would emit (issue #22).
        if session.event_log:
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.IDLE.value,
            })

        # Turn done: refresh the session host's reapable state (idle + no active
        # background tasks), so a subsequently-lost front can self-reap the idle
        # child (#51).
        await self._notify_host_reapable(session)

        session.touch()

        # Deliver the next durable queued follow-up now that the turn settled.
        # One-per-turn FIFO: this drains a single row and submits it; that turn's
        # own settle re-enters here for the next. Exactly-once (atomic pop), and
        # a no-op when the queue is empty or the process has since died.
        await self._drain_pending_prompts(session)

        # If a context-pressure handoff came due mid-turn and the queue is now
        # drained (session still idle), roll the worktree to a fresh successor.
        # A pending drained-prompt above leaves the session RUNNING, so this
        # no-ops until the queue empties -- the handoff then carries no work.
        self._schedule_auto_handoff_if_pending(session)

    def _handle_usage_update(
        self, session: Session, data: dict[str, Any]
    ) -> None:
        """Persist context usage and emit threshold warnings.

        Merge semantics: only the fields actually present in ``data`` are
        advanced. In particular ``usage_model`` is preserved unless a *real*
        model is reported -- copilot's ACP ``UsageUpdate`` carries ``model=None``
        every turn, so a naive overwrite kept wiping the model the client applied
        via ``session/set_config_option`` (dotfiles#790), leaving ``usage_model``
        perpetually NULL so ``status`` could never show the dispatched agent's
        model. The client re-emits the *applied* model through this same path
        (a model-only ``usage_update``), so the last-known model sticks.
        """
        now = time.time()
        if "context_size" in data:
            session.context_size = data.get("context_size")
        if "context_used" in data:
            session.context_used = data.get("context_used")
        model = data.get("model")
        if model:
            session.usage_model = model
        session.last_usage_at = now

        ctx_size = session.context_size
        ctx_used = session.context_used
        self._db.update_session_usage(
            session.session_id,
            context_size=ctx_size,
            context_used=ctx_used,
            usage_model=session.usage_model,
            now=now,
        )

        # Check thresholds and emit warnings
        if ctx_size and ctx_used is not None and ctx_size > 0:
            pct = ctx_used / ctx_size * 100
            thresholds = self._thresholds

            if pct >= thresholds.critical and "critical" not in session._crossed_thresholds:
                session._crossed_thresholds.add("critical")
                if session.event_log:
                    session.event_log.append("context_critical", {
                        "context_size": ctx_size,
                        "context_used": ctx_used,
                        "context_pct": round(pct, 1),
                        "threshold": thresholds.critical,
                        "message": "Context window usage critical -- consider handoff",
                    })
                # Context-pressure handoff (opt-in): mark a handoff owed, then
                # fire it once the session is idle. Usage typically crosses
                # critical mid-turn, so the turn-settle path (_run_prompt) drives
                # the deferred cutover; if we are already idle (usage arrived out
                # of turn), _schedule_auto_handoff_if_pending fires it now.
                if self._auto_handoff_eligible(session):
                    session._handoff_pending = True
                    self._schedule_auto_handoff_if_pending(session)

            elif pct >= thresholds.warning and "warning" not in session._crossed_thresholds:
                session._crossed_thresholds.add("warning")
                if session.event_log:
                    session.event_log.append("context_warning", {
                        "context_size": ctx_size,
                        "context_used": ctx_used,
                        "context_pct": round(pct, 1),
                        "threshold": thresholds.warning,
                        "message": "Context window usage elevated -- prepare for handoff",
                    })

    def _host_reapable(self, session: Session) -> bool:
        """Is the child safe to free? True only when its turn has completed and
        no background sub-agents are still running (#51)."""
        return (session.status == SessionStatus.IDLE
                and not session.has_active_background_tasks)

    def _is_codespace_target(self, target: "SpawnTarget") -> bool:
        """True if this target is a CodeSpace boundary agent -- structured
        ``codespace`` metadata, or a codespace-shaped ``spawn_command``. Such a
        target must run under a Session Host (never the process-owned path), so
        ``connect`` refuses to fall through to that path for one. A CodeSpace
        target that cannot be resolved to a spawner is a misconfiguration to
        surface, not to silently honor."""
        cs = getattr(target, "codespace", None)
        if isinstance(cs, dict) and cs.get("name"):
            return True
        sc = getattr(target, "spawn_command", None)
        if sc:
            try:
                from .session_host.codespace_transport import parse_codespace_target
                if parse_codespace_target(sc) is not None:
                    return True
            except Exception:
                pass
        return False

    def _session_host_client(self, session: Session) -> Any:
        """The session's host control channel, or None if not host-backed."""
        if session.client is None:
            return None
        return getattr(session.client, "session_host_client", None)

    async def _notify_host_reapable(self, session: Session) -> None:
        """Push the child's current reapable state to its session host, so the
        host can self-reap it if the front is later lost (#51). No-op for a
        non-host-backed session; best-effort so it never disturbs a turn."""
        hc = self._session_host_client(session)
        if hc is None:
            return
        with contextlib.suppress(Exception):
            await hc.send_status(self._host_reapable(session))

    async def _detach_host(self, session: Session) -> None:
        """Signal a GRACEFUL detach (+ current reapable state) to the session
        host before teardown, so an idle host reaps promptly instead of after
        the unexpected-grace window (#51). No-op / best-effort."""
        hc = self._session_host_client(session)
        if hc is None:
            return
        with contextlib.suppress(Exception):
            await hc.detach(self._host_reapable(session))

    async def refresh_host_reapable(self) -> None:
        """Reconcile every host-backed session's reapable state to its host.

        A periodic backstop (called from the heartbeat) beneath the precise
        turn-boundary STATUS pushes: it catches an initial idle session that has
        run no turn yet, and background-sub-agent transitions that occur outside
        a turn boundary -- so the host's ``_last_reapable`` never drifts stale
        enough to reap a session that is actually busy, or to miss reaping a
        genuinely idle one (#51)."""
        for session in list(self._sessions.values()):
            await self._notify_host_reapable(session)

