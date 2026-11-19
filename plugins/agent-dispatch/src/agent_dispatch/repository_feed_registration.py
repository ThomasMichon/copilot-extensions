"""Explicit finite registration recovery under the existing native waiter owner."""

from __future__ import annotations

import math
import time
from typing import Any

from .queue_feed_attempts import ATTEMPT_PROTOCOL
from .queue_feed_preparation import FeedWaiterAdmission, REGISTRATION_PROTOCOL
from .queue_feed_receipts import RECEIPT_PROTOCOL
from .queue_records import TaskError
from .repository_feed_preparation import RepositoryFeedPreparer
from .repository_feed_waiter_contract import validate_feed_delegation

PROTOCOL = REGISTRATION_PROTOCOL
_RECOVERY_PROTOCOL = "agent-pull-requests.repository-request-recovery/v1"


class RepositoryFeedRegistrar(RepositoryFeedPreparer):
    """Prepare and register explicitly; no arming, cycle, delivery, wake or ACK.

    The embedding owns the same trusted SDK client and native checkpoint as
    preparation. Every possible transmission is retained before IO; a later
    invocation inspects the original request rather than attempting creation.
    """

    protocol = PROTOCOL

    async def prepare(
        self, task_id: str, *, delegation: dict[str, Any], worker_id: str,
        resume_worktree: str, reason: str, host: str | None = None,
    ) -> dict[str, Any]:
        """Refuse unsendable original intent before the native suspension transaction."""
        selected = validate_feed_delegation(delegation)
        intent = self._intent({"delegation": selected})
        if time.time() >= intent["not_after"]:
            raise TaskError("original repository-feed creation cutoff elapsed")
        return await super().prepare(
            task_id, delegation=selected, worker_id=worker_id, resume_worktree=resume_worktree,
            reason=reason, host=host,
        )

    async def _negotiate(self) -> dict[str, Any]:
        if getattr(self.queue, "repository_feed_attempt_protocol", None) != ATTEMPT_PROTOCOL:
            raise TaskError("native repository-feed attempt unsupported")
        if getattr(self.queue, "repository_feed_registration_protocol", None) != PROTOCOL:
            raise TaskError("native repository-feed creation cutoff unsupported")
        if getattr(self.queue, "repository_feed_receipt_protocol", None) != RECEIPT_PROTOCOL:
            raise TaskError("native repository-feed receipt expiry unsupported")
        capabilities = await super()._negotiate()
        feature = capabilities.get("request_recovery")
        if not isinstance(feature, dict) or any(
            feature.get(name) != expected for name, expected in {
                "protocol": _RECOVERY_PROTOCOL, "inspection": "inspect-request",
                "cutoff": "not_after", "clock": "unix-seconds", "expiry": "min-ttl-cutoff",
            }.items()
        ):
            raise ValueError("original repository request recovery unsupported")
        return capabilities

    def _prepare_native(
        self, task_id: str, *, admission: FeedWaiterAdmission, delegation: dict[str, Any],
        worker_id: str, resume_worktree: str, reason: str, host: str | None,
    ) -> dict[str, Any]:
        return self.queue.prepare_repository_feed_waiter(
            task_id, admission=admission, delegation=delegation, worker_id=worker_id,
            resume_worktree=resume_worktree, reason=reason, host=host,
            creation_cutoff=self._intent({"delegation": delegation})["not_after"],
        )

    @staticmethod
    def _intent(waiter: dict[str, Any]) -> dict[str, Any]:
        feed = waiter["delegation"]["feed"]
        baseline = feed["baseline"]
        ttl = math.ceil(waiter["delegation"]["timeout"])
        cutoff = math.floor(feed["deadline_at"])
        if (
            baseline is None or not 1 <= ttl <= 86400 or not 0 < cutoff < 2**53
            or baseline["event_cursor"] >= 2**53
        ):
            raise ValueError("original repository-feed baseline or lifetime unsupported")
        return {"after": baseline["event_cursor"], "ttl": ttl, "not_after": cutoff}

    def _receipt(
        self, waiter: dict[str, Any], result: dict[str, Any],
    ) -> tuple[str, str, int]:
        from agent_pull_requests.repository_contract import RepositoryIdentity

        feed = waiter["delegation"]["feed"]
        row = result.get("subscription")
        status = result.get("status")
        if (
            not isinstance(row, dict) or not isinstance(status, str)
            or status not in {"active", "expired", "cancelled"}
        ):
            raise ValueError("original repository-feed receipt unavailable")
        registration = row.get("registration_id")
        expiry = row.get("expires_at")
        if (
            not isinstance(registration, str) or not 1 <= len(registration) <= 128
            or any(ord(char) < 32 for char in registration)
            or row.get("request_id") != feed["request_id"]
            or row.get("consumer") != self._binding["consumer"]
            or row.get("target") != RepositoryIdentity(**self._binding["identity"]).key
            or row.get("request") != self._intent(waiter)
            or type(expiry) is not int or not 0 < expiry <= self._intent(waiter)["not_after"]
            or type(row.get("cancelled")) is not bool
            or (status == "cancelled") != row["cancelled"]
        ):
            raise ValueError("original repository-feed receipt differs")
        return status, registration, expiry

    async def reconcile_registration(
        self, task_id: str, preparation_id: str,
    ) -> dict[str, Any]:
        """One original registration/inspection exchange, bounded to thirty seconds.

        Missing receipts, cancellation and original expiry remain reconciliation;
        this operation neither arms/completes a native waiter nor settles delivery.
        """
        if self._running:
            raise TaskError("repository-feed operation already in flight")
        self._checkpoint()
        waiter = self.queue.find_delegated_preparation(task_id, preparation_id)
        if waiter is None or waiter["delegation"].get("feed", {}).get("binding") != self._binding:
            raise TaskError("original repository-feed preparation unavailable")
        intent = self._intent(waiter)
        self._checkpoint()
        waiter = self.queue.replay_waiter_progress(waiter)
        if waiter["replay_settled"]:
            return {"status": "settled"}
        if not waiter["replay_attempted"] and waiter["state"] != "preparing":
            return {"status": "retired"}
        prior_deadline = self.client.deadline
        deadline = time.monotonic() + 30
        self.client.deadline = min(prior_deadline, deadline) if prior_deadline is not None else deadline
        self._running = True
        try:
            await self._negotiate()
            self._checkpoint()
            if waiter["replay_attempted"]:
                result = await self.client.request("inspect-request", {
                    "request_id": waiter["delegation"]["feed"]["request_id"],
                })
                now = result.get("now")
                if (
                    result.get("request_id") != waiter["delegation"]["feed"]["request_id"]
                    or type(now) is not int
                    or now < math.floor(waiter["delegation"]["feed"]["issued_at"])
                ):
                    raise ValueError("original repository request inspection differs")
                if result.get("status") == "not_found":
                    if result.get("subscription") is not None:
                        raise ValueError("original repository request inspection differs")
                    return {"status": "unresolved", "provider_now": now}
            else:
                if time.time() >= intent["not_after"]:
                    raise TaskError("original repository-feed creation cutoff elapsed")
                waiter = self.queue.replay_waiter_progress(waiter, attempt=True)
                self._checkpoint()
                result = await self.client.request("subscribe", {
                    "request_id": waiter["delegation"]["feed"]["request_id"], **intent,
                })
            status, registration, expiry = self._receipt(waiter, result)
            self._checkpoint()
            waiter = self.queue.replay_waiter_progress(
                waiter, registration_id=registration, registration_expires_at=expiry,
            )
            return {
                "status": status, "registration_id": registration,
                "waiter_state": waiter["state"],
            }
        finally:
            self.client.deadline = prior_deadline
            self._running = False
