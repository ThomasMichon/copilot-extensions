"""One finite replay cycle; the embedding owns scheduling, credentials and admission."""

from __future__ import annotations

import json

from .delegated_replay_contract import replay_scope, validate_replay
from .delegated_waiter_contract import validate_event
from .queue_records import TaskError


def create_replay_consumer(queue, caller, *, route_prefix: str, target: str,
                           repo: str, caller_scope: str):
    """Explicit optional composition, never imported by ordinary Dispatch startup.

    caller_scope is the embedding's stable authenticated principal/namespace
    reference, not a service authorization claim. The embedding must keep that
    credential scope stable and close admission on known credential loss.
    """
    from agent_pull_requests.watch_replay_client import ReplayClient, ReplayRequest

    binding = {"gateway": caller.gateway, "route_prefix": route_prefix,
               "target": target, "repo": repo, "caller_scope": caller_scope}
    client = ReplayClient(caller, route_prefix=route_prefix, target=target, repo=repo)
    return ReplayConsumer(
        queue, binding=binding, client=client, request_factory=ReplayRequest,
        binding_current=lambda: caller.gateway == binding["gateway"],
    )


class ReplayConsumer:
    """Trusted injected client for one immutable route and authenticated scope.

    No retries, processes, sleepers, credential lookup or fallback. A failed
    exchange stops this cycle, including work admitted later in the same batch.
    A transmitted mutation may still commit; the next cycle keeps its original
    identity. Queue effects commit before ACK, with terminal progress retained.
    """

    def __init__(self, queue, *, binding: dict, client, request_factory, binding_current=lambda: True):
        validated = validate_replay(
            {"binding": binding, "request_id": "validation", "issued_at": 1,
             "deadline_at": 2}, repo=binding["repo"], timeout=1,
        )
        self.queue, self.client, self.request_factory = queue, client, request_factory
        self._scope = replay_scope(validated["binding"])
        self._binding_current = binding_current
        self._after, self._running = 0, False

    @property
    def binding(self) -> dict:
        return json.loads(self._scope)

    def _check_binding(self):
        if not self._binding_current():
            raise ValueError("replay binding changed")

    def _request(self, waiter):
        self._check_binding()
        replay = waiter["delegation"]["replay"]
        if replay_scope(replay["binding"]) != self._scope:
            raise ValueError("replay binding changed")
        return self.request_factory(replay["request_id"], replay["issued_at"])

    def _current(self, waiter, **progress):
        return self.queue.replay_waiter_progress(waiter, **progress)

    async def prepare(self, task_id: str, *, delegation: dict, **options):
        """Negotiate before suspension, then durably prepare without remote acceptance."""
        from .delegated_waiter_contract import validate_delegation
        delegation = json.loads(json.dumps(validate_delegation(delegation), allow_nan=False))
        if replay_scope(delegation["replay"]["binding"]) != self._scope:
            raise ValueError("replay binding changed")
        self._check_binding()
        await self.client.capabilities()
        return self.queue.prepare_run_waiter(
            task_id, kind="delegated", delegation=delegation, command=[], **options,
        )

    async def _settle(self, waiter, receipt):
        if receipt.status not in ("active", "acknowledged", "cancelled"):
            raise ValueError("replay receipt unresolved")
        expected = waiter["delegation"]["replay"]["deadline_at"]
        if receipt.deadline_at != expected:
            raise ValueError("replay deadline mismatch")
        waiter = self._current(waiter, registration_id=receipt.registration_id)
        live = waiter["state"] in ("preparing", "active")
        if receipt.status == "acknowledged":
            if live or receipt.event_id != waiter["event_id"] or not receipt.event_id:
                raise ValueError("replay receipt has no committed local completion")
        elif receipt.status == "cancelled":
            if live:
                recover = (self.queue.recover_preparing_run_waiter
                           if waiter["state"] == "preparing"
                           else self.queue.recover_dead_run_waiter)
                result = recover(
                    waiter["task_id"], generation=waiter["generation"],
                    reason="service watch cancelled", message="Service watch cancelled; re-check external state.",
                    sender="agent-dispatch-replay-recovery",
                )
                if result is None:
                    return False
                waiter = self._current(waiter)
        else:
            if live:
                if waiter["state"] == "preparing":
                    spec = {**waiter["delegation"], "registration_id": receipt.registration_id}
                    self.queue.arm_run_waiter(
                        waiter["task_id"], generation=waiter["generation"],
                        kind="delegated", delegation=spec,
                    )
                return False
            request = self._request(waiter)
            if waiter["event_id"]:
                result = await self.client.acknowledge(
                    request, registration_id=receipt.registration_id, event_id=waiter["event_id"],
                )
            else:
                result = await self.client.cancel(request, registration_id=receipt.registration_id)
            if result.status not in ("acknowledged", "cancelled"):
                raise ValueError("replay receipt unresolved")
            return await self._settle(waiter, result)
        self._current(waiter, settled=True)
        return True

    async def _reconcile(self, waiter):
        waiter = self._current(waiter)
        if waiter["replay_settled"]:
            return False
        if waiter["state"] not in ("preparing", "active") and not waiter["replay_attempted"]:
            return self._current(waiter, settle_unattempted=True)["replay_settled"]
        request = self._request(waiter)
        if waiter["state"] == "preparing":
            waiter = self._current(waiter, attempt=True)
            if waiter["state"] != "preparing" or not waiter["replay_attempted"]:
                return False
            replay = waiter["delegation"]["replay"]
            receipt = await self.client.register(
                request, number=waiter["delegation"]["number"],
                until=tuple(waiter["delegation"]["until"]), deadline_at=replay["deadline_at"],
            )
        else:
            receipt = await self.client.inspect(request)
        return await self._settle(waiter, receipt)

    async def _complete(self, event):
        waiter = self.queue.find_replay_waiter(self.binding, event.registration_id)
        if waiter is None or waiter["replay_settled"]:
            return False
        spec = waiter["delegation"]
        if (event.repo, event.number) != (spec["repo"], spec["number"]):
            raise ValueError("replay event target mismatch")
        if waiter["state"] == "preparing":
            self.queue.arm_run_waiter(
                waiter["task_id"], generation=waiter["generation"], kind="delegated",
                delegation={**spec, "registration_id": event.registration_id},
            )
            waiter = self._current(waiter)
        if waiter["state"] == "active":
            payload = event.payload()
            if (payload["subscriber_id"] != event.registration_id
                    or payload["event_id"] != event.event_id
                    or payload["registration_id"] != event.registration_id):
                raise ValueError("replay event identity mismatch")
            fields = {"event_id", "registration_id", "notification_protocol", "repo", "number",
                      "transitions", "timed_out", "pr_state", "merged",
                      "review_decision", "mergeable", "checks_state"}
            projected = {k: v for k, v in payload.items() if k in fields}
            projected["subscriber_id"] = spec["subscriber_id"]
            base = {k: v for k, v in spec.items() if k != "registration_id"}
            validate_event(projected, base)
            result = self.queue.retire_run_waiter_with_wake(
                waiter["task_id"], generation=waiter["generation"], delegation=base,
                event=projected, reason="waiter completed",
                message=("PR watch timed out; re-check external state." if projected["timed_out"]
                         else "PR watch completed: " + ", ".join(projected["transitions"])),
                sender="agent-dispatch-hibernate",
            )
            if result is None:
                return False
            waiter = self._current(waiter)
        if waiter["state"] in ("preparing", "active"):
            return False
        if waiter["event_id"] and waiter["event_id"] != event.event_id:
            raise ValueError("replay event changed")
        request = self._request(waiter)
        if waiter["event_id"]:
            receipt = await self.client.acknowledge(
                request, registration_id=event.registration_id, event_id=event.event_id,
            )
        else:
            receipt = await self.client.cancel(request, registration_id=event.registration_id)
        return await self._settle(waiter, receipt)

    async def cycle(self) -> dict:
        """At most 32 row reconciliations and one 16-event pending batch."""
        if self._running:
            raise RuntimeError("replay cycle already running")
        self._running = True
        counts = {"checked": 0, "events": 0, "settled": 0, "blocked": False}
        try:
            self._check_binding()
            await self.client.capabilities()
            rows = self.queue.list_replay_waiters(self.binding, after=self._after, limit=32)
            if not rows:
                self._after = 0
            for waiter in rows:
                self._after = waiter["id"]
                counts["checked"] += 1
                counts["settled"] += int(await self._reconcile(waiter))
            self._check_binding()
            batch = await self.client.pending(limit=16)
            if not hasattr(batch, "events"):
                counts["blocked"] = True
                return counts
            if not isinstance(batch.events, tuple) or len(batch.events) > 16:
                raise ValueError("replay batch exceeds cycle bound")
            for event in batch.events:
                counts["events"] += 1
                counts["settled"] += int(await self._complete(event))
            return counts
        except (ValueError, OSError, TaskError):
            counts["blocked"] = True
            return counts
        finally:
            self._running = False
