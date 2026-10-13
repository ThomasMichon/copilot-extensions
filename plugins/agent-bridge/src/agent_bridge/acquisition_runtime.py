"""Local acquisition composed over existing ACP and worktree CLI lifecycle owners."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, replace
from typing import Any
from types import SimpleNamespace

from .acquisition_authority import verify_dispatch, verify_worktree, worktree_command, bind_dispatch_receipt, mark_dispatch_acquisition
from .acquisition_contract import (
    DEFAULT_CLI_WORKER_TARGET,
    AcquisitionRefused,
    AcquisitionRequest,
    AcquisitionResult,
    validate_result,
    cli_driver,
)
from .acquisition_policy import (
    AcquisitionDecision,
    AcquisitionEvidence,
    ConversationEvidence,
    select_acquisition,
)
from .acquisition_store import AcquisitionStore
from .acquisition_executor import executor
from .acquisition_initial_creation import initial_creation_proven
from .db_core import live_session_is_fresh, local_pid_alive
from .models import SessionStatus, StartSessionRequest
from .preference_requests import apply_request_preferences
from .routes.worktrees import _fresh_start_locks, _apply_bound_charter
from .transport import SpawnTarget


class AcquisitionRuntime:
    """One operation owns selection, launch, identity verification and delivery."""

    def __init__(self, manager: Any, resolver: Any, db: Any, state: Any = None) -> None:
        self.manager, self.resolver, self.db = manager, resolver, db
        self.state = state
        self.store = AcquisitionStore(db)

    async def acquire(self, req: AcquisitionRequest) -> AcquisitionResult:
        if self.manager.is_draining:
            raise AcquisitionRefused("acquisition_draining", "bridge is draining")
        if req.mode == "cli" and any(
            item is not None for item in (req.charter, req.model, req.effort)
        ):
            raise AcquisitionRefused(
                "cli_options_unsupported", "CLI acquisition cannot yet prove execution overrides",
                effect="not_started",
            )
        async with _fresh_start_locks.setdefault(req.worktree_id, asyncio.Lock()):
            with executor(self.db, req.worktree_id):
                if self.manager.is_draining:
                    raise AcquisitionRefused("acquisition_draining", "bridge is draining")
                self.manager._active_acquisitions = getattr(self.manager, "_active_acquisitions", 0) + 1
                try:
                    return await self._acquire_locked(req)
                except AcquisitionRefused as exc:
                    if self.store.journal.refused_before_effect(req):
                        exc.effect = "not_started"
                    raise
                finally:
                    self.manager._active_acquisitions -= 1

    async def _acquire_locked(self, req: AcquisitionRequest) -> AcquisitionResult:
        completed = self.db.execute_read(
            "SELECT state FROM acquisition_attempts WHERE delivery_id=?",
            (req.delivery_id,),
        )
        if completed and completed[0]["state"] == "completed":
            replay = self.store.journal.claim(req)
            assert replay.result is not None
            self.store.release_gate(req)
            return replay.result
        venue = await verify_dispatch(req, self.resolver)
        preflight_claim = None
        if not completed and not self.store.pending(req.worktree_id):
            preflight_claim = self.store.journal.claim(req)
        try:
            row, head = await verify_worktree(req)
        except AcquisitionRefused as exc:
            if preflight_claim and preflight_claim.token:
                self.store.journal.refuse(req, preflight_claim.token, exc.code)
            raise
        if venue["acquisition_reservation"]["state"] == "reserving":
            await mark_dispatch_acquisition(req, self.resolver)
        await self._reconcile_prior(req, row, head)
        venue = await verify_dispatch(req, self.resolver)
        row, head = await verify_worktree(req)
        try:
            claim = preflight_claim or self.store.journal.claim(req)
        except AcquisitionRefused as exc:
            if exc.code != "acquisition_pending":
                raise
            token, stage, data = self.store.recovery(req)
            return await self._recover(req, token, stage, data, row, head)
        if claim.result is not None:
            self.store.release_gate(req)
            return claim.result
        assert claim.token is not None
        token = claim.token
        try:
            evidence = self._evidence(req, venue, head)
            selected_req = req
            initial_creation = False
            if (
                not evidence.conversations and head is None
                and initial_creation_proven(req, venue, self.store)
            ):
                selected_req = req.model_copy(update={"intent": "fresh"})
                initial_creation = True
            if req.intent == "fresh" and not (
                req.conversation_retired is True
                or venue["acquisition_reservation"].get("attempt") == 1
                and not venue["task"].get("owner_session_id")
                and not venue.get("attachments")
            ):
                raise AcquisitionRefused("fresh_authority_missing", "task owner did not authorize a fresh transcript")
            if (
                req.allocation == "targeted"
                and req.intent == "automatic"
                and venue["acquisition_reservation"].get("attempt") == 1
                and not venue["acquisition_reservation"].get("session_handle")
                and not venue["task"].get("owner_session_id")
                and not venue.get("attachments")
                and not req.prior_session_id
                and not req.prior_native_session_id
            ):
                selected_req = req.model_copy(update={"intent": "fresh"})
            decision = select_acquisition(selected_req, evidence)
            target, request_env = await self._prepare_target(req, row)
            if req.mode == "cli" and decision.acquired != "reused":
                intent = (
                    decision.conversation.native_session_id if decision.conversation else "fresh"
                )
                assert intent is not None
                preflight = await worktree_command(
                    [
                        "embody",
                        "--worktree-id",
                        req.worktree_id,
                        "--json",
                        "--bridge-acquisition",
                        intent,
                        "--dry-run",
                    ],
                    req.project,
                )
                if (
                    preflight.get("dry_run") is not True
                    or preflight.get("worktree_id") != req.worktree_id
                    or preflight.get("resume_session") != (None if intent == "fresh" else intent)
                ):
                    raise AcquisitionRefused(
                        "cli_capability_missing",
                        "worktree owner cannot prove exact CLI launch intent",
                    )
        except AcquisitionRefused as exc:
            self.store.journal.refuse(req, token, exc.code)
            raise
        if initial_creation or req.conversation_retired is True:
            try:
                venue = await verify_dispatch(req, self.resolver)
                row, head = await verify_worktree(req)
                evidence = self._evidence(req, venue, head)
                if initial_creation and (
                    head is not None or evidence.conversations
                    or not initial_creation_proven(req, venue, self.store)
                ):
                    raise AcquisitionRefused(
                        "initial_creation_fence_changed", "first-conversation authority changed before launch",
                    )
                select_acquisition(selected_req, evidence)
            except AcquisitionRefused as exc:
                self.store.journal.refuse(req, token, exc.code)
                raise
        if req.mode == "cli" and decision.acquired != "reused":
            existing = self.db.get_cli_mode_reservation(req.worktree_id)
            if existing and existing["expires_at"] > time.time():
                self.store.journal.refuse(req, token, "cli_launch_pending")
                raise AcquisitionRefused("cli_launch_pending", "existing CLI launch reservation remains active")
        retired = (
            frozenset(
                item.native_session_id for item in evidence.conversations if item.native_session_id
            )
            if not decision.retirement_recovered
            and (selected_req.conversation_retired is True or selected_req.intent == "fresh")
            else frozenset()
        )
        if not decision.retirement_recovered and (
            selected_req.conversation_retired is True or selected_req.intent == "fresh"
        ):
            retired = (
                retired
                | frozenset(
                    binding["native_session_id"]
                    for binding in self.store.bindings(req.worktree_id)
                )
                | (frozenset({head}) if head else frozenset())
            )
        if retired:
            self.store.retire(req.worktree_id)
        self.store.gate(
            req,
            decision.conversation.session_id if decision.conversation else None,
            decision.conversation.native_session_id if decision.conversation else None,
        )
        self.store.stage(
            req,
            "launching",
            acquired=decision.acquired,
            session_id=decision.conversation.session_id if decision.conversation else None,
            native_session_id=decision.conversation.native_session_id
            if decision.conversation
            else None,
            retired_native_ids=sorted(retired),
            creation_authorized=decision.creation_authorized,
            retirement_recovered=decision.retirement_recovered,
            retirement_conversation=asdict(decision.conversation)
            if decision.retirement_recovered and decision.conversation else None,
        )
        try:
            session_id, native = await self._launch(req, decision, row, target, request_env)
        except AcquisitionRefused as exc:
            if exc.code == "acquisition_draining":
                self.store.journal.refuse(req, token, exc.code)
                self.store.release_gate(req)
            raise
        output = self._result(req, decision.acquired, session_id, native)
        validate_result(req, output, decision=decision, retired_native_ids=retired)
        self.store.bind(req, session_id, native)
        self.store.stage(req, "launched", result=output.model_dump())
        await self._deliver(req, output)
        self.store.journal.complete(req, token, output, decision=decision)
        self.store.release_gate(req)
        return output

    def _evidence(
        self,
        req: AcquisitionRequest,
        venue: dict[str, Any],
        head: str | None,
    ) -> AcquisitionEvidence:
        bindings = {item["session_id"]: item for item in self.store.bindings(req.worktree_id)}
        task = venue["task"]
        proven = {task.get("owner_session_id")}
        proven.update(
            item.get("session_id")
            for item in venue.get("attachments", [])
            if isinstance(item, dict) and item.get("worktree") in {None, req.worktree_id}
        )
        conversations: list[ConversationEvidence] = []
        for session in self.manager.list_sessions():
            if session.target.worktree_id != req.worktree_id:
                continue
            native = session.acp_session_id
            running = bool(session.client and session.client.is_running)
            current = native == head
            # Historical stopped records are not current holders. Retain their
            # native identities only for fresh/retired exclusion checks.
            if not current and not running:
                continue
            binding = bindings.get(session.session_id)
            ours = (
                bool(binding["task_id"] == req.task_id and not binding["retired"])
                if binding
                else session.session_id in proven or native in proven
            )
            conversations.append(
                ConversationEvidence(
                    binding["target"] if binding else session.agent_name or req.target,
                    "acp",
                    req.task_id if ours else "foreign",
                    int(binding["task_generation"]) if binding else req.task_generation,
                    req.worktree_id,
                    session.session_id,
                    native,
                    "live"
                    if running
                    else "stopped"
                    if session.status == SessionStatus.STOPPED
                    else "unknown",
                    False if ours else None,
                    busy=session.status == SessionStatus.RUNNING,
                    charter=self._charter(session.target.copilot_args),
                    model=getattr(session, "model_override", None),
                    effort=getattr(session, "effort_override", None),
                    generation_bound=bool(
                        binding and ours and native
                        and self.store.retirement_recovery_proven(req, session.session_id, native)
                    ),
                )
            )
        for live in self.db.list_live_sessions(req.worktree_id, include_dead=True):
            native = live["session_id"]
            fresh = live_session_is_fresh(live, time.time())
            alive = local_pid_alive(live.get("pid"))
            if native != head and not fresh and alive is not True:
                continue
            binding = bindings.get(native)
            ours = (
                bool(binding["task_id"] == req.task_id and not binding["retired"])
                if binding
                else native in proven
            )
            conversations.append(
                ConversationEvidence(
                    binding["target"] if binding else req.target,
                    "cli",
                    req.task_id if ours else "foreign",
                    int(binding["task_generation"]) if binding else req.task_generation,
                    req.worktree_id,
                    native,
                    native,
                    "live" if fresh else "stopped" if alive is False else "unknown",
                    False if ours else None,
                    busy=fresh and (
                        live.get("turn_state") not in {"idle", "completed"}
                        or bool(self.db.execute_read(
                            "SELECT id FROM live_messages WHERE session_id=? AND delivered_at IS NULL LIMIT 1",
                            (native,),
                        ))
                    ),
                    charter=req.charter if ours else None,
                    model=req.model if ours else None,
                    effort=req.effort if ours else None,
                    generation_bound=bool(
                        binding and ours
                        and self.store.retirement_recovery_proven(req, native, native)
                    ),
                )
            )
        # A native head with no authoritative bridge record is unknown, never
        # an invitation to recover a filesystem-discovered transcript.
        known_head = head is None or any(item.native_session_id == head for item in conversations)
        return AcquisitionEvidence(known_head, head, tuple(conversations))

    async def _launch(
        self,
        req: AcquisitionRequest,
        decision: AcquisitionDecision,
        row: dict[str, Any],
        target: Any,
        request_env: dict[str, str],
    ) -> tuple[str, str]:
        if self.manager.is_draining:
            raise AcquisitionRefused("acquisition_draining", "bridge drained before launch")
        if req.mode == "acp":
            if decision.conversation:
                session = self.manager.get_session(decision.conversation.session_id)
                if decision.acquired == "resumed":
                    session = await self.manager.resume_session(
                        decision.conversation.session_id,
                        drain=False,
                        allow_recreate=False,
                    )
            else:
                target = replace(
                    target, cwd=row["path"], worktree_id=req.worktree_id, explicit_cwd=True
                )
                session = await self.manager.start_session(
                    target,
                    agent_name=req.target,
                    caller_id=f"acquisition:{req.delivery_id}",
                    copilot_args=["--agent", req.charter] if req.charter else None,
                    model=req.model,
                    effort=req.effort,
                    env_overrides=request_env or None,
                )
            if not session or not session.acp_session_id or session.status != SessionStatus.IDLE:
                raise AcquisitionRefused(
                    "native_start_unproven", "ACP launch did not prove an idle native conversation"
                )
            if not self.db.reserve_worktree_ownership(
                req.worktree_id,
                session.session_id,
                now=time.time(),
            ):
                raise AcquisitionRefused("live_holder", "ACP ownership fence was not acquired")
            await worktree_command(
                [
                    "bind-session",
                    "--session-id",
                    session.acp_session_id,
                    "--worktree-id",
                    req.worktree_id,
                    "--worktree-dir",
                    row["path"],
                ],
                req.project,
            )
            return session.session_id, session.acp_session_id
        if decision.acquired == "reused":
            assert decision.conversation and decision.conversation.native_session_id
            return decision.conversation.session_id, decision.conversation.native_session_id
        driver = cli_driver(req)
        reservation = self.db.create_cli_mode_reservation(
            req.worktree_id,
            now=time.time(),
            ttl_seconds=300,
        )
        if not reservation:
            raise AcquisitionRefused(
                "cli_launch_pending", "another CLI reservation holds the target"
            )
        resume_id = decision.conversation.native_session_id if decision.conversation else "fresh"
        assert resume_id is not None
        await worktree_command(
            [
                "embody",
                "--worktree-id",
                req.worktree_id,
                "--json",
                "--bridge-acquisition",
                resume_id,
                "--driver",
                driver,
                "--verify-timeout",
                str(req.verify_timeout),
            ],
            req.project,
        )
        return await self._cli_native(req, driver, decision)

    async def _prepare_target(
        self,
        req: AcquisitionRequest,
        row: dict[str, Any],
    ) -> tuple[Any, dict[str, str]]:
        if req.mode == "cli" and req.target == DEFAULT_CLI_WORKER_TARGET:
            # The worktree owner, not an ACP profile, owns CLI launch defaults.
            return SpawnTarget(
                type="local", cwd=row["path"], worktree_id=req.worktree_id,
                explicit_cwd=True,
            ), {}
        try:
            target = await self.resolver.resolve_async(req.target)
        except KeyError as exc:
            raise AcquisitionRefused(
                "target_unavailable", f"Acquisition target '{req.target}' is not registered",
            ) from exc
        if target.type != "local":
            raise AcquisitionRefused("venue_unsupported", "this acquisition adapter is local only")
        bound = row.get("bound_agent")
        if bound and bound != req.target:
            target = _apply_bound_charter(
                target,
                self.resolver,
                SimpleNamespace(bound_agent=bound),
                req.worktree_id,
            )
        request_env, _source = apply_request_preferences(
            StartSessionRequest(model=req.model, effort=req.effort),
            self.state,
            target,
        )
        return target, request_env

    async def _cli_native(
        self,
        req: AcquisitionRequest,
        driver: str,
        decision: AcquisitionDecision | None = None,
    ) -> tuple[str, str]:
        deadline = time.monotonic() + (req.verify_timeout or 180)
        while True:
            expected = (
                decision.conversation.native_session_id
                if decision and decision.conversation
                else None
            )
            rows = [
                row
                for row in self.db.list_fresh_live_sessions(req.worktree_id, now=time.time())
                if (row.get("driven_by") == driver or (
                    decision and decision.retirement_recovered and decision.acquired == "reused"
                ))
                and (expected is None or row["session_id"] == expected)
            ]
            if len(rows) == 1:
                native = rows[0]["session_id"]
                _, head = await verify_worktree(req)
                if head != native:
                    if time.monotonic() >= deadline:
                        raise AcquisitionRefused(
                            "native_head_pending", "CLI registration has not become the native worktree head",
                        )
                    await asyncio.sleep(0.2)
                    continue
                if (
                    decision
                    and decision.conversation
                    and native != decision.conversation.native_session_id
                ):
                    raise AcquisitionRefused(
                        "native_identity_changed", "CLI registered another native conversation"
                    )
                reservation = self.db.get_cli_mode_reservation(req.worktree_id)
                if reservation and reservation.get("claimed_by_session_id") == native:
                    self.db.release_cli_mode_reservation(
                        req.worktree_id,
                        reservation_id=reservation["reservation_id"],
                    )
                return native, native
            if len(rows) > 1:
                raise AcquisitionRefused(
                    "multiple_live_holders", "CLI launch registered multiple holders"
                )
            if time.monotonic() >= deadline:
                raise AcquisitionRefused(
                    "native_registration_pending", "CLI native registration remains unproven"
                )
            await asyncio.sleep(0.2)

    async def _deliver(self, req: AcquisitionRequest, result: AcquisitionResult) -> None:
        prompt = self._delivery_prompt(req, result)
        await verify_dispatch(req, self.resolver)
        _, head = await verify_worktree(req)
        if head != result.native_session_id:
            raise AcquisitionRefused("head_changed", "native head changed before delivery")
        if req.mode == "cli":
            await self._verify_delivery_admission(req, result)
            await bind_dispatch_receipt(req, result, self.resolver)
            await self._verify_delivery_admission(req, result)
            self.store.stage(req, "delivering", result=result.model_dump())
            message, reason = self.db.enqueue_live_message_if_fresh(
                result.session_id,
                sender=req.driver,
                body=prompt,
                now=time.time(),
                expected_session_id=result.native_session_id,
                idempotency_key=f"acquisition:{req.delivery_id}",
                require_idle=True,
            )
            if message is None:
                raise AcquisitionRefused(
                    reason or "delivery_refused", "CLI inbox refused exact native delivery"
                )
        else:
            session = self.manager.get_session(result.session_id)
            if not session or session.acp_session_id != result.native_session_id:
                raise AcquisitionRefused(
                    "native_identity_changed", "ACP identity changed before delivery"
                )
            if session.status == SessionStatus.STOPPED:
                session = await self.manager.resume_session(
                    result.session_id, drain=False, allow_recreate=False,
                )
                if session.acp_session_id != result.native_session_id:
                    raise AcquisitionRefused(
                        "native_identity_changed", "strict recovery changed native identity",
                    )
                await self._verify_delivery_admission(req, result)
            async with session._turn_start_lock:
                await self._verify_delivery_admission(req, result)
                await bind_dispatch_receipt(req, result, self.resolver)
                await self._verify_delivery_admission(req, result)
                current = self.manager.get_session(result.session_id)
                if current is not session or current.acp_session_id != result.native_session_id:
                    raise AcquisitionRefused(
                        "native_identity_changed", "native holder changed while awaiting turn admission",
                    )
                self.store.stage(
                    req, "delivering", result=result.model_dump(), turn_index=session.turn_count
                )
                await self.manager._submit_prompt_locked(
                    result.session_id,
                    prompt,
                    no_resume=True,
                    delivery_id=req.delivery_id,
                )
        self.store.stage(req, "delivered", result=result.model_dump())

    async def _verify_delivery_admission(
        self, req: AcquisitionRequest, result: AcquisitionResult,
    ) -> None:
        _, head = await verify_worktree(req)
        if head != result.native_session_id:
            raise AcquisitionRefused("head_changed", "native head changed before turn admission")
        # Dispatch is checked last: worktree resolution itself awaits owner I/O.
        await verify_dispatch(req, self.resolver)
        if self.manager.is_draining:
            raise AcquisitionRefused("acquisition_draining", "bridge drained before delivery")

    async def _recover(
        self,
        req: AcquisitionRequest,
        token: str,
        stage: str,
        data: dict[str, Any],
        row: dict[str, Any],
        head: str | None,
    ) -> AcquisitionResult:
        recovery_decision = None
        if data.get("retirement_recovered"):
            conversation = ConversationEvidence(**data["retirement_conversation"])
            if not conversation.native_session_id or not self.store.retirement_recovery_proven(
                req, conversation.session_id, conversation.native_session_id,
            ):
                raise AcquisitionRefused(
                    "retirement_recovery_changed", "current retirement recovery is no longer proven"
                )
            recovery_decision = AcquisitionDecision(
                data["result"]["acquired"] if data.get("result") else data["acquired"],
                conversation=conversation, retirement_recovered=True,
            )
        if stage in {"delivered", "delivering", "launched"}:
            output = AcquisitionResult.model_validate(data["result"])
            decision = recovery_decision or AcquisitionDecision(
                output.acquired,
                creation_authorized=data.get("creation_authorized", False),
            )
            validate_result(req, output, decision=decision)
            if stage == "delivering":
                if req.mode == "cli":
                    await self._deliver(req, output)
                    self.store.journal.complete(req, token, output, decision=decision)
                    self.store.release_gate(req)
                    return output
                turns = self.db.execute_read(
                    "SELECT prompt, acquisition_delivery_id FROM turns WHERE session_id=? AND turn_index=?",
                    (output.session_id, data["turn_index"]),
                )
                if not turns:
                    session = self.manager.get_session(output.session_id)
                    if session and session.turn_count == data["turn_index"]:
                        await self._deliver(req, output)
                    else:
                        raise AcquisitionRefused(
                            "delivery_ambiguous", "ACP turn admission needs reconciliation"
                        )
                elif (
                    turns[0]["prompt"] != self._delivery_prompt(req, output)
                    or turns[0]["acquisition_delivery_id"] != req.delivery_id
                ):
                    raise AcquisitionRefused(
                        "delivery_ambiguous", "ACP turn admission needs reconciliation"
                    )
                # A durable turn admission proves no second submission is
                # permitted; it does not claim the native turn completed.
            elif stage == "launched":
                await self._deliver(req, output)
            self.store.journal.complete(req, token, output, decision=decision)
            self.store.release_gate(req)
            return output
        if stage == "launching":
            if req.mode == "acp":
                sessions = [
                    item
                    for item in self.manager.list_sessions()
                    if item.caller_id == f"acquisition:{req.delivery_id}"
                    or item.session_id == data.get("session_id")
                ]
                if len(sessions) != 1 or not sessions[0].acp_session_id:
                    # A Session Host can survive a failed attach without a
                    # client/native ID here. Missing records are not absence proof.
                    raise AcquisitionRefused(
                        "launch_ambiguous", "ACP native launch identity is not proven"
                    )
                session = sessions[0]
                if session.status == SessionStatus.STOPPED:
                    session = await self.manager.resume_session(
                        session.session_id, drain=False, allow_recreate=False
                    )
                session_id, native = session.session_id, session.acp_session_id
            else:
                session_id, native = await self._cli_native(req, cli_driver(req), recovery_decision)
            if data.get("native_session_id") and data["native_session_id"] != native:
                raise AcquisitionRefused(
                    "native_identity_changed", "recovered launch changed native identity"
                )
            if req.mode == "acp":
                await self._restore_recovered_head(req, session_id, native)
            _, head = await verify_worktree(req)
            if head != native:
                raise AcquisitionRefused(
                    "head_changed", "recovered native launch is not the current head"
                )
            output = self._result(req, data["acquired"], session_id, native)
            validate_result(
                req,
                output,
                decision=recovery_decision or AcquisitionDecision(
                    data["acquired"],
                    creation_authorized=data.get("creation_authorized", False),
                ),
                retired_native_ids=frozenset(data["retired_native_ids"]),
            )
            self.store.bind(req, session_id, native)
            self.store.stage(req, "launched", result=output.model_dump())
            await self._deliver(req, output)
            self.store.journal.complete(
                req,
                token,
                output,
                decision=recovery_decision or AcquisitionDecision(
                    output.acquired,
                    creation_authorized=data.get("creation_authorized", False),
                ),
            )
            self.store.release_gate(req)
            return output
        if stage == "prepared":
            self.store.release_gate(req)
            self.store.journal.refuse(req, token, "interrupted_before_launch")
            raise AcquisitionRefused(
                "interrupted_before_launch", "no launch effect was begun; reserve a new attempt"
            )
        raise AcquisitionRefused(
            "acquisition_pending", "prepared attempt requires owner reconciliation"
        )

    async def inspect_pending(self, delivery_id: str) -> dict[str, Any]:
        """Report recovery facts without consuming, launching or delivering."""
        rows = self.db.execute_read(
            "SELECT a.worktree_id,a.state,e.stage,e.data_json FROM acquisition_attempts a "
            "LEFT JOIN acquisition_effects e USING(delivery_id) WHERE a.delivery_id=?",
            (delivery_id,),
        )
        if not rows:
            raise AcquisitionRefused("acquisition_not_found", "acquisition delivery was not found")
        row = rows[0]
        data = json.loads(row["data_json"] or "{}")
        delivery_admitted = False
        if data.get("result") and data.get("_request"):
            from .acquisition_delivery import admitted
            recorded_request = AcquisitionRequest.model_validate(data["_request"])
            recorded_result = AcquisitionResult.model_validate(data["result"])
            delivery_admitted = admitted(
                self.db, recorded_request, recorded_result,
                self._delivery_prompt(recorded_request, recorded_result),
            )
        sessions = self.db.execute_read(
            "SELECT id,status,acp_session_id FROM sessions WHERE caller_id=?",
            (f"acquisition:{delivery_id}",),
        )
        return {
            "delivery_id": delivery_id, "state": row["state"],
            "stage": row["stage"] or "prepared", "worktree_id": row["worktree_id"],
            "native_evidence": [dict(item) for item in sessions],
            "recorded_result": data.get("result"),
            "delivery_admitted": delivery_admitted,
            "recovery": "retry the same request, or allow a current same-task reservation to reconcile",
        }

    async def _reconcile_prior(
        self,
        req: AcquisitionRequest,
        row: dict[str, Any],
        head: str | None,
    ) -> None:
        """Reconcile a superseded attempt from durable admission/launch facts."""
        for pending in self.store.pending(req.worktree_id):
            if pending["delivery_id"] == req.delivery_id:
                continue
            data = json.loads(pending["data_json"] or "{}")
            if "_request" not in data:
                raise AcquisitionRefused(
                    "prior_attempt_unknown", "prior acquisition lacks recovery facts"
                )
            previous = AcquisitionRequest.model_validate(data["_request"])
            if (
                previous.task_id != req.task_id
                or previous.mode != req.mode
                or previous.target != req.target
            ):
                raise AcquisitionRefused(
                    "foreign_pending_acquisition", "another task owns an interrupted acquisition"
                )
            token, stage, data = self.store.recovery(previous)
            if stage == "prepared":
                self.store.journal.refuse(previous, token, "interrupted_before_launch")
                self.store.release_gate(previous)
                continue
            if stage in {"delivered", "delivering"}:
                output = AcquisitionResult.model_validate(data["result"])
                if stage == "delivering" and previous.mode == "cli":
                    admitted = self.db.execute_read(
                        "SELECT session_id,body FROM live_messages WHERE idempotency_key=?",
                        (f"acquisition:{previous.delivery_id}",),
                    )
                    if not admitted or admitted[0]["session_id"] != output.session_id or admitted[0]["body"] != self._delivery_prompt(previous, output):
                        raise AcquisitionRefused("prior_delivery_ambiguous", "prior CLI admission is not proven")
                elif stage == "delivering":
                    admitted = self.db.execute_read(
                        "SELECT session_id, prompt FROM turns WHERE acquisition_delivery_id=?",
                        (previous.delivery_id,),
                    )
                    if (
                        not admitted
                        or admitted[0]["session_id"] != output.session_id
                        or admitted[0]["prompt"] != self._delivery_prompt(previous, output)
                    ):
                        raise AcquisitionRefused(
                            "prior_delivery_ambiguous", "prior ACP admission is not proven"
                        )
                self.store.journal.complete(
                    previous,
                    token,
                    output,
                    decision=AcquisitionDecision(
                        output.acquired,
                        creation_authorized=data.get("creation_authorized", False),
                        retirement_recovered=data.get("retirement_recovered", False),
                        conversation=ConversationEvidence(**data["retirement_conversation"])
                        if data.get("retirement_recovered") else None,
                    ),
                )
                self.store.release_gate(previous)
                continue
            if stage == "launching":
                if previous.mode == "acp":
                    found = [
                        item
                        for item in self.manager.list_sessions()
                        if item.caller_id == f"acquisition:{previous.delivery_id}"
                        or item.session_id == data.get("session_id")
                    ]
                    if len(found) != 1 or not found[0].acp_session_id:
                        raise AcquisitionRefused(
                            "prior_launch_ambiguous", "prior ACP launch remains unproven"
                        )
                    session, native = found[0].session_id, found[0].acp_session_id
                else:
                    recovery_decision = AcquisitionDecision(
                        data["acquired"], retirement_recovered=data.get("retirement_recovered", False),
                        conversation=ConversationEvidence(**data["retirement_conversation"])
                        if data.get("retirement_recovered") else None,
                    )
                    session, native = await self._cli_native(previous, cli_driver(previous), recovery_decision)
                if previous.mode == "acp":
                    await self._restore_recovered_head(req, session, native)
                    _, head = await verify_worktree(req)
                if native != head or (
                    data.get("native_session_id") and data["native_session_id"] != native
                ):
                    raise AcquisitionRefused(
                        "prior_native_mismatch", "interrupted native launch changed identity"
                    )
                output = self._result(previous, data["acquired"], session, native)
                if data.get("retirement_recovered") and not self.store.retirement_recovery_proven(
                    previous, session, native,
                ):
                    raise AcquisitionRefused(
                        "retirement_recovery_changed", "prior recovery receipt is no longer proven"
                    )
                validate_result(
                    previous,
                    output,
                    decision=AcquisitionDecision(
                        data["acquired"],
                        creation_authorized=data.get("creation_authorized", False),
                        retirement_recovered=data.get("retirement_recovered", False),
                        conversation=ConversationEvidence(**data["retirement_conversation"])
                        if data.get("retirement_recovered") else None,
                    ),
                    retired_native_ids=frozenset(data["retired_native_ids"]),
                )
                self.store.bind(previous, session, native)
                self.store.stage(previous, "launched", result=output.model_dump())
            elif stage == "launched":
                output = AcquisitionResult.model_validate(data["result"])
                if output.native_session_id != head:
                    raise AcquisitionRefused(
                        "prior_native_mismatch", "prior launched conversation is no longer head"
                    )
            else:
                raise AcquisitionRefused(
                    "prior_attempt_unknown", "prior acquisition stage cannot be reconciled"
                )
            # Launch succeeded but durable admission was not begun. The old
            # reservation no longer authorizes its prompt. Preserve the native
            # binding, settle its fence, and let the current request reuse it.
            self.store.journal.refuse(previous, token, "superseded_before_delivery")
            self.store.release_gate(previous)

    @staticmethod
    def _charter(args: list[str]) -> str | None:
        for index, arg in enumerate(args):
            if arg.startswith("--agent="):
                return arg.split("=", 1)[1]
            if arg == "--agent" and index + 1 < len(args):
                return args[index + 1]
        return None

    @staticmethod
    def _delivery_prompt(req: AcquisitionRequest, result: AcquisitionResult) -> str:
        return (
            req.prompt
            + "\n\nBridge acquisition identity: when issuing this task's start command, "
            + f"add --owner-session-id {result.native_session_id}. "
            + "This is the exact native conversation being delivered this task, "
            + "not the launch worker ID or a prior session's identity."
        )

    async def _restore_recovered_head(
        self, req: AcquisitionRequest, session_id: str, native: str,
    ) -> None:
        await verify_dispatch(req, self.resolver)
        row, head = await verify_worktree(req)
        if head == native:
            return
        session = self.manager.get_session(session_id)
        if not session or session.acp_session_id != native or not (
            session.caller_id and session.caller_id.startswith("acquisition:")
        ) or session.target.worktree_id != req.worktree_id:
            raise AcquisitionRefused("recovery_owner_unknown", "native acquisition owner cannot be verified")
        if self.db.list_fresh_live_sessions(req.worktree_id, now=time.time()) or any(
            item.session_id != session_id and item.target.worktree_id == req.worktree_id
            and item.client and item.client.is_running
            for item in self.manager.list_sessions()
        ):
            raise AcquisitionRefused("recovery_holder_conflict", "another holder prevents ledger restoration")
        if not self.db.reserve_worktree_ownership(req.worktree_id, session_id, now=time.time()):
            raise AcquisitionRefused("recovery_holder_conflict", "native ownership could not be restored")
        await worktree_command([
            "bind-session", "--session-id", native,
            "--worktree-id", req.worktree_id, "--worktree-dir", row["path"],
        ], req.project)

    @staticmethod
    def _result(
        req: AcquisitionRequest, acquired: Any, session: str, native: str
    ) -> AcquisitionResult:
        return AcquisitionResult(
            target=req.target,
            mode=req.mode,
            task_id=req.task_id,
            task_generation=req.task_generation,
            worktree_id=req.worktree_id,
            session_id=session,
            native_session_id=native,
            acquired=acquired,
            delivery_id=req.delivery_id,
        )
