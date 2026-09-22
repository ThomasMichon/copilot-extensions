"""Context-pressure auto-handoff helpers."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from .models import SessionStatus
from .session_manager import DaemonDrainingError, Session, _workspace_key, log


class _SessionHandoffMixin:
    """Context-pressure auto-handoff helpers."""

    def _auto_handoff_eligible(self, session: Session) -> bool:
        """Is this session a candidate for policy-driven in-place handoff?

        False unless the operator opted in (`auto_handoff.enabled`), and never
        for a single-checkout (command/CodeSpace) agent -- those cannot host
        predecessor and successor at once, so the spawn-then-retire cutover does
        not apply (their retire-before-spawn variant is deferred, mirroring the
        guard in ``handoff_session``)."""
        if not self._auto_handoff.enabled:
            return False
        if _workspace_key(session.agent_name, session.target, session.caller_id):
            return False
        return True

    @staticmethod
    def _is_over_critical(session: Session) -> bool:
        """True once the session has crossed the critical context threshold."""
        return "critical" in session._crossed_thresholds

    def _schedule_auto_handoff_if_pending(self, session: Session) -> None:
        """Fire a deferred context-pressure handoff if one is owed and the
        session has settled to idle. Called at turn-settle (and after an
        out-of-turn usage update). No-op unless a handoff is pending, the
        session is idle, and the policy still permits it.

        The ``unwatched_only`` preference is honored here (proactive path): a
        session a human is streaming is left pending -- not rolled out from
        under -- and re-evaluated on its next settle. The prompt-triggered path
        (`submit_or_queue_prompt`) bypasses this and hands off directly, because
        the sender is explicitly asking for the next turn."""
        if not session._handoff_pending:
            return
        if session.status != SessionStatus.IDLE:
            return
        if not self._auto_handoff_eligible(session):
            session._handoff_pending = False
            return
        if self._auto_handoff.unwatched_only and session.subscriber_count > 0:
            return  # a human is attached; defer until unwatched
        session._handoff_pending = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._run_auto_handoff(session.session_id))
        self._auto_handoff_tasks.add(task)
        task.add_done_callback(self._auto_handoff_tasks.discard)

    async def _run_auto_handoff(self, session_id: str) -> None:
        """Perform a policy-driven handoff and migrate any durably-queued
        follow-ups onto the successor, so no prompt is stranded on the retired
        predecessor. Best-effort: a failed handoff logs and leaves the
        predecessor current (``handoff_session`` never orphans the worktree)."""
        try:
            pending = self._db.list_pending_prompts(session_id)
        except Exception:
            pending = []
        try:
            successor = await self.handoff_session(
                session_id, reason="context-pressure"
            )
        except Exception as exc:
            log.warning("Auto-handoff of %s failed: %s", session_id, exc)
            return
        for row in pending:
            try:
                await self.submit_or_queue_prompt(
                    successor.session_id,
                    row["prompt"],
                    caller_id=row.get("caller_id"),
                )
            except Exception as exc:
                log.warning(
                    "Auto-handoff could not transfer queued prompt %s from %s to %s: %s",
                    row.get("id"),
                    session_id,
                    successor.session_id,
                    exc,
                )
                continue
            with contextlib.suppress(Exception):
                self._db.remove_pending_prompt(session_id, row["id"])

    @staticmethod
    def _build_handoff_prompt(session: Session) -> str:
        """The self-contained instruction that asks a retiring child to author
        its own continuation brief. Deliberately does NOT depend on any handoff
        skill being installed in the child -- the structure is inlined here so a
        plain ``copilot --acp`` child produces a usable brief."""
        return (
            "SYSTEM: Your context window is nearly full and this session is about "
            "to be handed off to a fresh successor session in the SAME worktree. "
            "Author a CONTINUATION BRIEF that lets the successor resume your work "
            "with no access to this conversation. Respond with the brief ONLY -- "
            "no preamble, no questions, no offer to continue.\n\n"
            "Write the brief in Markdown with these sections:\n"
            "## Objective -- the goal you are pursuing (1-3 sentences).\n"
            "## State -- what is done, what is in progress, and key decisions or "
            "findings so far.\n"
            "## Files -- files created or modified and their purpose.\n"
            "## Next steps -- the concrete next actions, in order.\n"
            "## Gotchas -- pitfalls, constraints, or context the successor must "
            "know.\n\n"
            "Be specific and self-contained: name paths, commands, ids, issue and "
            "PR numbers, and branch names explicitly rather than referring to "
            "'the above'."
        )

    @staticmethod
    def _build_seed_prompt(brief: str) -> str:
        """Wrap a continuation brief as the successor's opening turn."""
        return (
            "You are the successor session continuing work in this worktree after "
            "an automatic context handoff. The previous session has been retired "
            "because its context window filled. Below is its continuation brief -- "
            "pick the work up from it and keep going.\n\n"
            "----- CONTINUATION BRIEF -----\n"
            f"{brief}\n"
            "----- END BRIEF -----"
        )

    def _synthesize_handoff_brief(self, session: Session) -> str:
        """Fallback brief built from session-side state, used when the child
        cannot author one (dead client, errored turn, empty reply)."""
        rows = self._db.execute_read(
            "SELECT prompt FROM turns WHERE session_id=? ORDER BY turn_index ASC "
            "LIMIT 1",
            (session.session_id,),
        )
        first_prompt = rows[0]["prompt"] if rows else "(original request unknown)"
        return (
            "## Objective\n"
            f"Continue the work started in the predecessor session for worktree "
            f"`{session.caller_id or 'unknown'}` (agent "
            f"`{session.agent_name or 'unknown'}`).\n\n"
            "## State\n"
            f"The predecessor ran {session.turn_count} turn(s) before its context "
            "window filled. An agent-authored brief could not be produced, so this "
            "is a minimal synthesized handoff.\n\n"
            "## Original request\n"
            f"{first_prompt}\n\n"
            "## Next steps\n"
            "Re-establish context from the worktree itself -- recent git log, "
            "modified files, and any effort/plan/README docs -- then resume the "
            "original request above."
        )

    async def _run_turn_sync(self, session: Session, prompt: str) -> str:
        """Run one prompt to completion synchronously and return the agent's
        reply text. Unlike :meth:`submit_prompt` (which backgrounds the turn and
        returns only a turn index), this awaits ``turn_complete`` so the caller
        gets the response text -- exactly what handoff-brief authoring needs.

        The session must be IDLE with a live client. Status is driven RUNNING for
        the duration and restored to IDLE afterward so watchdogs and the
        concurrency guard see a coherent state.
        """
        turn_index = session.turn_count
        session.turn_count += 1
        now = time.time()
        self._db.create_turn(session.session_id, turn_index, prompt, now)
        session.status = SessionStatus.RUNNING
        session.last_output_at = now
        self._db.update_session_status(
            session.session_id, SessionStatus.RUNNING.value, now
        )
        result: dict[str, Any] | None = None
        try:
            result = await session.client.send_prompt(prompt)
        except Exception as exc:
            self._db.update_turn(
                session.session_id,
                turn_index,
                stop_reason=f"error: {exc}",
                completed_at=time.time(),
            )
            raise
        finally:
            session.status = SessionStatus.IDLE
            self._db.update_session_status(
                session.session_id, SessionStatus.IDLE.value, time.time()
            )
        text = ""
        if isinstance(result, dict):
            text = result.get("response_text", "") or ""
        self._db.update_turn(
            session.session_id,
            turn_index,
            response_text=text,
            stop_reason=(result or {}).get("stop_reason")
            if isinstance(result, dict) else None,
            completed_at=time.time(),
        )
        return text

    async def handoff_session(
        self,
        session_id: str,
        *,
        reason: str | None = None,
        seed: bool = True,
        seed_text: str | None = None,
        handoff_token: str | None = None,
    ) -> Session:
        """Hand a hosted session off to a fresh successor in the same worktree.

        The in-place, bridge-native analogue of the interactive context handoff:

        1. Build the successor's opening prompt: either use an externally
           supplied seed verbatim, or ask the retiring child to author a
           continuation brief (synthesize one from session state if it cannot)
           and wrap it as the warm-start prompt.
        2. Spawn a successor with the SAME target (same worktree, agent, caller).
        3. Record a durable two-way predecessor/successor link.
        4. Announce the changeover with a ``session_handoff`` event on BOTH the
           predecessor's and the successor's event streams, so every caller --
           a UI following the worktree, a bridge-as-agent host, a CLI reader --
           follows the baton in place rather than staring at a retired session.
        5. Seed the successor's opening turn with the brief so it resumes warm.
        6. Retire the predecessor (``stop_session`` -> STOPPED/resumable, its
           transcript and succession link preserved).

        Ordering is spawn-then-retire so a failed successor spawn leaves the
        predecessor untouched and current -- a handoff never orphans the
        worktree. Returns the successor session.

        The self-authored-brief path is the normal and currently only
        self-contained route for agent-bridge's own ACP-hosted sessions,
        including usage-driven auto-handoff. ``seed_text`` is optional
        enrichment for an external caller that already composed the exact
        successor opening turn; it does not change the default ACP behavior or
        create any dependency on Copilot CLI extensions being active in the
        hosted child.
        """
        if self._draining:
            raise DaemonDrainingError("handoff")
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        # Single-checkout (CodeSpace/command) agents cannot host predecessor and
        # successor at once, so the spawn-then-retire ordering below does not
        # apply; that path needs retire-before-spawn and is deferred.
        if _workspace_key(session.agent_name, session.target, session.caller_id):
            raise ValueError(
                f"Session {session_id} is a single-checkout (command) agent; "
                "in-place handoff for those is not yet supported"
            )
        if session.status not in (SessionStatus.IDLE, SessionStatus.STOPPED):
            raise ValueError(
                f"Session {session_id} is {session.status.value}, not idle -- "
                "cannot hand off mid-turn"
            )

        # A live child is needed to author the brief; auto-resume a recoverable
        # STOPPED session, mirroring submit_prompt.
        if not session.client or not session.client.is_running:
            if not session.acp_session_id:
                raise RuntimeError(
                    f"Session {session_id} has no live child and no ACP id -- "
                    "cannot hand off"
                )
            session.status = SessionStatus.STOPPED
            await self.resume_session(session_id, drain=False)

        # 1. Resolve the successor's opening turn. External requestors may
        #    provide the exact prompt text; otherwise the predecessor authors a
        #    brief and agent-bridge wraps it as the successor seed.
        external_seed = (seed_text or "").strip()
        brief = ""
        seed_prompt = ""
        seed_source = "external" if external_seed else "agent-authored"
        if external_seed:
            brief = external_seed
            seed_prompt = external_seed
        else:
            try:
                brief = (
                    await self._run_turn_sync(
                        session, self._build_handoff_prompt(session)
                    )
                ).strip()
            except Exception as exc:
                log.warning(
                    "Handoff brief authoring failed for %s: %s", session_id, exc
                )
            if not brief:
                brief = self._synthesize_handoff_brief(session)
                seed_source = "synthesized"
            seed_prompt = self._build_seed_prompt(brief)
        if session.event_log:
            session.event_log.append(
                "handoff_brief",
                {
                    "brief": brief,
                    "reason": reason,
                    "source": seed_source,
                    "handoff_token": handoff_token,
                },
            )

        # 2. Spawn the successor in the SAME worktree. Local worktree agents are
        #    unguarded, so predecessor + successor briefly coexist; the
        #    predecessor is retired only after the successor is confirmed up.
        successor = await self.start_session(
            session.target,
            agent_name=session.agent_name,
            caller_id=session.caller_id,
        )
        if successor.status != SessionStatus.IDLE:
            # Spawn failed -- retain the predecessor and surface the failure so
            # the worktree is never left headless.
            if session.event_log:
                session.event_log.append("handoff_failed", {
                    "reason": reason,
                    "successor_id": successor.session_id,
                    "successor_status": successor.status.value,
                    "message": "successor failed to start; predecessor retained",
                })
            raise RuntimeError(
                f"Handoff of {session_id} aborted: successor "
                f"{successor.session_id} failed to start "
                f"({successor.status.value}); predecessor retained"
            )

        # 3. Persist the two-way succession link.
        now = time.time()
        self._db.link_succession(session_id, successor.session_id, now)

        # 3b. Phase 4b (worktree-self-knowledge): write the succession into the
        #     agent-worktrees GROUND LAYER -- the authoritative, single-owner
        #     lineage the facility derives the head from. The `_db` link above is
        #     the bridge's *private* event bookkeeping; the source of truth is the
        #     ground layer (the vision's *derive-dont-duplicate*). One atomic
        #     write marks the predecessor `handed-off` + makes the successor the
        #     derived head; a companion note mirrors the handoff into the worktree
        #     record's history. LOCAL worktrees only (a remote worktree's ground
        #     layer lives on its own machine); best-effort / fail-open.
        gl_worktree = getattr(session.target, "worktree_id", None)
        gl_local = getattr(session.target, "type", None) == "local"
        gl_dir = getattr(session.target, "cwd", None)
        pred_acp = session.acp_session_id
        succ_acp = successor.acp_session_id
        if gl_local and gl_worktree and pred_acp and succ_acp:
            from . import worktree_lineage
            # The successor's own start_session already registered it into the
            # ground layer (4a, synchronous), so link-succession finds it tracked.
            with contextlib.suppress(Exception):
                worktree_lineage.link_succession(
                    gl_worktree, pred_acp, succ_acp, worktree_dir=gl_dir,
                )
            with contextlib.suppress(Exception):
                worktree_lineage.note_handoff(
                    gl_worktree, pred_acp, title=(reason or "context-pressure"),
                    worktree_dir=gl_dir,
                )

        # 4. Announce the changeover on BOTH event streams.
        payload = {
            "rolled_from": session_id,
            "rolled_to": successor.session_id,
            "predecessor_acp": session.acp_session_id,
            "successor_acp": successor.acp_session_id,
            "worktree_id": session.caller_id,
            "reason": reason or "context-pressure",
            "summary": brief[:1000],
            "seed_source": seed_source,
        }
        if handoff_token:
            payload["handoff_token"] = handoff_token
        if session.event_log:
            session.event_log.append("session_handoff", payload)
        if successor.event_log:
            successor.event_log.append("session_handoff", payload)

        # 5. Seed the successor's opening turn with the brief (fire-and-forget;
        #    the successor processes it as its first turn). Phase 4c: prepend the
        #    successor's ground-layer role + the worktree history digest, because
        #    the `sessionStart` role/digest hook CANNOT fire under `copilot --acp`
        #    -- so the ACP successor learns its lineage from its opening turn
        #    instead. Fail-open: a missing digest never blocks the warm seed.
        if seed:
            if gl_local and gl_worktree and succ_acp:
                from . import worktree_lineage
                with contextlib.suppress(Exception):
                    role = worktree_lineage.session_role(
                        gl_worktree, succ_acp, worktree_dir=gl_dir,
                    )
                    digest = worktree_lineage.history_digest(
                        gl_worktree, succ_acp, worktree_dir=gl_dir,
                    )
                    header = worktree_lineage.build_succession_seed_header(
                        role, digest, predecessor=pred_acp,
                    )
                    if header:
                        seed_prompt = header + seed_prompt
            with contextlib.suppress(Exception):
                await self.submit_prompt(successor.session_id, seed_prompt)

        # 6. Retire the predecessor: STOPPED keeps it resumable and preserves its
        #    transcript + succession link (end_session would delete both).
        with contextlib.suppress(Exception):
            await self.stop_session(session_id, force=True)

        log.info(
            "Handoff: session %s -> %s (worktree %s)",
            session_id, successor.session_id, session.caller_id,
        )
        return successor

