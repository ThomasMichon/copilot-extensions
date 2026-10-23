"""Card/steer methods for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap
(same pattern as :mod:`agent_dispatch.client_suspend`) rather than grow an
already-baselined file.
"""

from __future__ import annotations

import time
import uuid

import httpx

from .client_transport import default_idempotent_retries
from .client_errors import DispatchError, DispatchUpgradeRequired
from .steering import steer_request_fingerprint, steer_wake_message


class SteeringClientMixin:
    """``card``/``steer`` methods, mixed into ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    def set_card(self, task_id: str, worker_id: str, *, card: dict) -> dict:
        """Attach a card to a held task (awaiting-steer if it carries a form)."""
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/card",
                json={"worker_id": worker_id, "card": card},
            )
        )

    def save_card_draft(
        self, task_id: str, *, fields: dict, expected_revision: str | None = None,
        expected_draft_revision: str | None = None,
    ) -> dict:
        """Persist an operator's not-yet-submitted draft answer. Never touches
        ``awaiting_steer``/status -- the task stays blocked exactly as before,
        durably visible from any surface/machine via ``get``/``card show``."""
        if expected_revision is not None or expected_draft_revision is not None:
            return self._guarded_card_draft(
                task_id, fields, expected_revision, expected_draft_revision, clear=False,
            )
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/card-draft",
                json={"fields": fields},
            )
        )

    def clear_card_draft(
        self, task_id: str, *, expected_revision: str | None = None,
        expected_draft_revision: str | None = None,
    ) -> dict:
        """Clear a task's saved draft. Never touches ``awaiting_steer``/status."""
        if expected_revision is not None or expected_draft_revision is not None:
            return self._guarded_card_draft(
                task_id, {}, expected_revision, expected_draft_revision, clear=True,
            )
        return self._unwrap(self._http.delete(f"/tasks/{task_id}/card-draft"))

    def _guarded_card_draft(
        self, task_id: str, fields: dict, expected_revision: str | None,
        expected_draft_revision: str | None, *, clear: bool,
    ) -> dict:
        if not expected_revision or not expected_draft_revision:
            raise ValueError("both expected card and draft revisions are required")
        version = self.health().get("card_draft_cas")
        if type(version) is not int or version < 1:
            raise DispatchUpgradeRequired("coordinator lacks guarded card drafts")
        operation = "guarded-clear" if clear else "guarded"
        try:
            result = self._unwrap(self._http.post(
                f"/tasks/{task_id}/card-draft/{operation}",
                json={
                    "fields": fields, "expected_revision": expected_revision,
                    "expected_draft_revision": expected_draft_revision,
                },
            ))
        except DispatchError as exc:
            if exc.status_code in (404, 405) and exc.detail in ("Not Found", "Method Not Allowed"):
                raise DispatchUpgradeRequired("coordinator lacks guarded card drafts") from exc
            raise
        if (
            not isinstance(result, dict) or result.get("id") != task_id
            or result.get("steering_revision") != expected_revision
            or not isinstance(result.get("card_draft_revision"), str)
            or not result["card_draft_revision"]
            or "card_draft" not in result
            or (clear and (
                result["card_draft"] is not None or result["card_draft_revision"] != "absent"
            ))
            or (not clear and (
                not isinstance(result.get("card_draft"), dict)
                or result["card_draft_revision"] in ("absent", expected_draft_revision)
                or result["card_draft"].get("fields") != fields
                or result["card_draft"].get("steering_revision") != expected_revision
            ))
        ):
            raise DispatchError(502, "no verifiable guarded draft acknowledgement; refresh before retrying")
        return result

    def _coordinator_supports_steer_idempotency_key(self) -> bool:
        """Whether this coordinator's ``/health`` advertises that it
        recognizes and dedups a repeated ``idempotency_key`` on
        ``/tasks/{id}/steer`` (see that route's ``SteerBody`` and
        ``coordinator_status.py``'s ``/health``). An older coordinator mid
        zero-downtime update simply ignores the unrecognized field and
        performs no dedup at all -- retrying an ambiguous timeout against one
        would append a second, duplicate answer, the exact failure this
        feature exists to prevent. A failed/unreachable health probe itself
        degrades to "unsupported" (never retry), the same conservative
        answer as a confirmed-old coordinator.
        """
        try:
            health = self.health()
        except Exception:  # noqa: BLE001 -- any failure here means "don't retry"
            return False
        return bool(health.get("steer_idempotency_key"))

    def steer(
        self,
        task_id: str,
        *,
        fields: dict,
        sender: str | None = None,
        wake: bool = True,
        message: str | None = None,
        expected_status: str | None = None,
        expected_revision: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Submit an answer and ask the coordinator to resume the task owner.

        Generates (or reuses a caller-supplied) ``idempotency_key`` and
        retries a *timeout* (never a non-timeout error) up to
        :func:`default_idempotent_retries` times reusing the same key --
        see that function's docstring for why this is the one call where a
        plain read-timeout is safe to retry: the coordinator's own write is
        a single fast local transaction, but the full round trip can still
        exceed ``timeout`` under load even though the write already landed;
        the shared key lets the coordinator recognize a retry and return the
        already-committed result instead of submitting a second, duplicate
        answer.

        The first timeout lazily checks
        :meth:`_coordinator_supports_steer_idempotency_key` (once, not on
        every attempt) before committing to any retry at all -- an older
        coordinator that doesn't yet dedup on this key must never be
        retried, or the "ambiguous timeout" becomes a guaranteed duplicate.
        """
        guarded = expected_revision is not None
        if guarded:
            self._require_steering_acceptance()
            message = steer_wake_message(task_id, message)
        key = idempotency_key or (
            "card:" + steer_request_fingerprint(
                fields=fields, sender=sender, wake_requested=wake, wake_message=message,
                expected_status=expected_status, expected_revision=expected_revision,
            ) if guarded else uuid.uuid4().hex
        )
        payload = {
            "fields": fields,
            "sender": sender,
            "wake": wake,
            "message": message,
            "expected_status": expected_status,
            "idempotency_key": key,
        }
        if guarded:
            payload["expected_revision"] = expected_revision
        path = f"/tasks/{task_id}/steer" + ("/guarded" if guarded else "")
        attempts = default_idempotent_retries() + 1
        delay = 0.1
        checked_capability = guarded
        for attempt in range(attempts):
            try:
                result = self._unwrap(self._http.post(path, json=payload))
                if guarded:
                    receipt = result.get("steer_receipt") if isinstance(result, dict) else None
                    accepted = self._steer_acceptance(receipt, task_id, key)
                    if (
                        accepted.get("steering_revision") != expected_revision
                        or accepted.get("request_fingerprint") != steer_request_fingerprint(
                            fields=fields, sender=sender, wake_requested=wake,
                            wake_message=message, expected_status=expected_status,
                            expected_revision=expected_revision,
                        )
                    ):
                        raise DispatchError(
                            502, f"no verifiable steering acceptance for request {key!r}; inspect its receipt",
                        )
                return result
            except DispatchError as exc:
                if guarded and exc.status_code in (404, 405) and exc.detail in (
                    "Not Found", "Method Not Allowed",
                ):
                    raise DispatchUpgradeRequired("coordinator lacks guarded steering acceptance") from exc
                raise
            except httpx.TimeoutException:
                if attempt == attempts - 1:
                    raise
                if not checked_capability:
                    checked_capability = True
                    if not self._coordinator_supports_steer_idempotency_key():
                        raise
                time.sleep(delay)
                delay = min(delay * 2, 1.0)
        raise AssertionError("unreachable")  # pragma: no cover

    def _require_steering_acceptance(self) -> None:
        version = self.health().get("steering_acceptance")
        if type(version) is not int or version < 1:
            raise DispatchUpgradeRequired("coordinator lacks guarded steering acceptance")

    @staticmethod
    def _steer_acceptance(receipt: object, task_id: str, request_id: str) -> dict:
        accepted = receipt.get("acceptance") if isinstance(receipt, dict) else None
        wake = receipt.get("wake") if isinstance(receipt, dict) else None
        if not isinstance(accepted, dict) or (
            accepted.get("request_id") != request_id or accepted.get("task_id") != task_id
            or "wake_operation_id" not in accepted
            or (wake is not None and not isinstance(wake, dict))
            or (
                (wake.get("id") if isinstance(wake, dict) else None)
                != accepted.get("wake_operation_id")
            )
        ):
            raise DispatchError(
                502, f"no verifiable steering acceptance for request {request_id!r}; inspect its receipt",
            )
        return accepted

    def steer_receipt(self, task_id: str, request_id: str) -> dict:
        """Inspect this exact request's acceptance, consumption and original wake."""
        self._require_steering_acceptance()
        receipt = self._unwrap(
            self._http.get(f"/tasks/{task_id}/steer-receipt", params={"request_id": request_id})
        )
        self._steer_acceptance(receipt, task_id, request_id)
        return receipt

    def steer_take(
        self, task_id: str, worker_id: str, *, all_pending: bool = False
    ) -> dict:
        """Consume the next pending steer (returns ``{task_id, steer}``; steer is
        the payload dict or ``None`` when the inbox is empty). With
        ``all_pending``, drains the inbox and returns ``{task_id, steers}``."""
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/steer/take",
                json={
                    "worker_id": worker_id,
                    "all_pending": all_pending,
                },
            )
        )

    def steer_log(self, task_id: str) -> list[dict]:
        """The full steer inbox for a task (oldest first)."""
        return self._unwrap(self._http.get(f"/tasks/{task_id}/steer-log"))
