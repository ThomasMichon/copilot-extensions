"""Completion Review methods (``confirm``/``reopen_completed``) for
:class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap; a
pure mechanical extraction with no behavior change. Mixed into
``DispatchClient`` alongside its other method groups.
"""

from __future__ import annotations


class CompletionReviewMixin:
    """The Completion Review card's backend calls, mixed into
    ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    def confirm(
        self,
        task_id: str,
        *,
        actor: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
    ) -> dict:
        """Corroborate a completion claim and close the task for good --
        the true lifecycle terminal beyond a provisional ``completed``
        (:meth:`agent_dispatch.queue.TaskQueue.confirm`)."""
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/confirm",
                json={
                    "actor": actor,
                    "expected_status": expected_status,
                    "expected_generation": expected_generation,
                },
            )
        )

    def reopen_completed(
        self,
        task_id: str,
        *,
        reason: str | None = None,
        steer_fields: dict | None = None,
        sender: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
    ) -> dict:
        """The Completion Review card's "Re-queue with steering" action --
        return a completed-but-unconfirmed task to ``queued``, progress
        preserved, optionally recording new operator steer fields atomically
        with the reopen (:meth:`agent_dispatch.queue.TaskQueue
        .reopen_completed`)."""
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/reopen",
                json={
                    "reason": reason,
                    "steer_fields": steer_fields,
                    "sender": sender,
                    "expected_status": expected_status,
                    "expected_generation": expected_generation,
                },
            )
        )
