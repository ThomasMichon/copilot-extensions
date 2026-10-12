"""``clear_exclude`` for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap
(same pattern as :mod:`agent_dispatch.client_suspend`) rather than grow an
already-baselined file.
"""

from __future__ import annotations


class ClearExcludeClientMixin:
    """``clear_exclude``, mixed into ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    def clear_exclude(
        self,
        task_id: str,
        *,
        exclude: str | None = None,
        actor: str | None = None,
        expected_status: str | None = None,
    ) -> dict:
        """Remove a self-exclusion appended by ``yield --exclude``/
        ``--exclude-self`` (:meth:`agent_dispatch.queue.TaskQueue.clear_exclude`).
        Omit ``exclude`` to clear every exclusion on the task."""
        return self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/unexclude",
                json={
                    "exclude": exclude,
                    "actor": actor,
                    "expected_status": expected_status,
                },
            )
        )
