"""Companion PR association selection and serialized owner-backed point reads.

Mixed into the Mux Companion app. Associations load only for an engine that
declared ``companion-pr-observations-v1``; every explicit read first
revalidates the association through that same pinned runtime, and at most one
read thread/process is in flight. Any invalidation (refresh, deselection,
switch, close) advances the generation so a late reply is never displayed.
"""

from __future__ import annotations

import asyncio
import time
from threading import Event

from rich.text import Text
from textual import work
from textual.widgets import Button, Select

from . import companion_pr_state as cpr
from . import engine_client as ec


class CompanionPrMixin:
    """PR state for :class:`MuxCompanionApp`; requires its read-runner and topology hooks."""

    def _init_pr_state(self) -> None:
        self._pr_cancel = Event()
        self._pr_read_cancel = Event()
        self._pr_thread: asyncio.Future | None = None
        self._pr_options: tuple[tuple[str, str], ...] | None = None
        self._pr_generation = 0
        self._pr_owner = None
        self._pr_fact: dict | None = None
        self._pr_error: str | None = "not-loaded"
        self._pr_selected: cpr.Association | None = None
        self._pr_read: cpr.PointRead | None = None
        self._pr_busy = False

    def _pr_topology_state(self) -> dict:
        return {
            "declared": cpr.supported(self._data.bundle), "fact": self._pr_fact,
            "error": None if self._pr_error == "not-loaded" else self._pr_error,
            "selected": self._pr_selected, "read": self._pr_read, "busy": self._pr_busy,
        }

    def _reset_pr_state(self, error: str | None = "not-loaded") -> None:
        """Invalidate associations, selection and any in-flight point read."""
        self._pr_cancel.set()
        self._pr_read_cancel.set()
        self._pr_generation += 1
        self._pr_owner = None
        self._pr_fact = None
        self._pr_error = error
        self._pr_selected = None
        self._pr_read = None
        self._pr_busy = False

    @work(exclusive=True, group="pr-associations")
    async def _load_pr_associations(self) -> None:
        data = self._data
        self._pr_owner = data
        worktree_id = data.worktree.get("id")
        if not cpr.supported(data.bundle) or not isinstance(worktree_id, str) or not worktree_id:
            self._update_pr_controls()
            return
        cancelled = Event()
        self._pr_cancel = cancelled
        generation = self._pr_generation
        self._pr_error = "loading"
        self._update_topology()
        try:
            if not data.engine_command:
                raise ec.EngineError("declaring engine runtime unknown")
            fact = await asyncio.to_thread(
                cpr.read_associations, worktree_id, runner=self._owned_read_runner(cancelled),
                engine_command=data.engine_command, project=data.bundle.get("project")
                if isinstance(data.bundle.get("project"), str) else None,
            )
        except ec.EngineError as exc:
            fact, error = None, str(exc)
        else:
            error = None
        finally:
            cancelled.set()
        if generation != self._pr_generation or self._data is not data:
            return
        self._pr_fact, self._pr_error = fact, error
        self._update_pr_controls()
        self._update_topology()

    def _update_pr_controls(self) -> None:
        associations, _ = cpr.admitted(self._pr_fact)
        select = self.query_one("#pr-association", Select)
        options = tuple((item.label, item.pr_id) for item in associations)
        selected = self._pr_selected
        with self.prevent(Select.Changed):
            if options != self._pr_options:
                self._pr_options = options
                # Text keeps repository/provider names literal, never markup.
                select.set_options([(Text(label), pr_id) for label, pr_id in options])
            # set_options() blanks the value; keep the admitted selection visible.
            value = selected.pr_id if selected is not None and any(
                pr_id == selected.pr_id for _label, pr_id in options) else None
            if value is None:
                if not select.is_blank():
                    select.clear()
            elif select.value != value:
                select.value = value
        select.disabled = not associations
        self.query_one("#pr-read-btn", Button).disabled = (
            self._pr_selected is None or self._pr_busy
        )

    def _deselect_pr(self) -> None:
        """Invalidate and cancel any read for the current choice."""
        self._pr_read_cancel.set()
        self._pr_generation += 1
        self._pr_selected, self._pr_read, self._pr_busy = None, None, False
        self._update_pr_controls()
        self._update_topology()

    def _select_pr(self, pr_id: str | None) -> None:
        if pr_id is None:
            if self._pr_selected is not None:
                self._deselect_pr()
            return
        associations, _ = cpr.admitted(self._pr_fact)
        selected = next((item for item in associations if item.pr_id == pr_id), None)
        if selected is None or (self._pr_selected is not None and selected.key == self._pr_selected.key):
            return
        self._pr_selected, self._pr_read, self._pr_busy = selected, None, False
        self._start_pr_read()

    def _start_pr_read(self) -> None:
        """One explicit finite read of the selected, still-admitted association."""
        selected = self._pr_selected
        associations, _ = cpr.admitted(self._pr_fact)
        if selected is None or self._pr_busy or selected.key not in {item.key for item in associations}:
            return
        self._pr_read_cancel.set()
        self._pr_generation += 1
        self._pr_busy = True
        self._update_pr_controls()
        self._update_topology()
        self._read_pr(selected, self._pr_generation)

    @work(exclusive=True, group="pr-point-read")
    async def _read_pr(self, selected: cpr.Association, generation: int) -> None:
        # Cancelling a worker does not stop its thread: retire the previous
        # read's thread and owned process before starting another (one in flight).
        await self._retire_pr_read()
        if generation != self._pr_generation:
            return
        fact = None
        if self._engine_cleanup_failures:
            result = cpr.PointRead(
                selected, "unavailable", time.time(),
                reason="engine resource retirement unresolved; no read started")
        else:
            cancelled = Event()
            self._pr_read_cancel = cancelled
            thread = asyncio.ensure_future(asyncio.to_thread(
                cpr.revalidated_read, selected, self._data.worktree.get("id"),
                runner=self._owned_read_runner(cancelled), engine_command=self._data.engine_command,
            ))
            self._pr_thread = thread
            try:
                await asyncio.wait({thread})
            finally:
                cancelled.set()
            try:
                fact, result = thread.result()
            except Exception as exc:  # noqa: BLE001 - surfaced as unavailability
                result = cpr.PointRead(selected, "unavailable", time.time(), reason=str(exc)[:240])
        if generation != self._pr_generation or self._pr_selected is None \
                or self._pr_selected.key != selected.key:
            return
        if fact is None and result is None:
            # Another project or worktree incarnation: nothing shown remains valid.
            self._pr_fact, self._pr_error = None, "worktree source changed; refresh to reload"
            self._pr_selected, self._pr_read, self._pr_busy = None, None, False
        elif fact is not None:
            self._pr_fact = fact
        if result is None and self._pr_selected is not None:
            # Removed or reassigned since load: show the refreshed associations instead.
            self._pr_selected, self._pr_read, self._pr_busy = None, None, False
        else:
            self._pr_read, self._pr_busy = result, False
        self._update_pr_controls()
        self._update_topology()

    async def _retire_pr_read(self) -> None:
        self._pr_read_cancel.set()
        thread = self._pr_thread
        if thread is not None and not thread.done():
            await asyncio.wait({thread})
