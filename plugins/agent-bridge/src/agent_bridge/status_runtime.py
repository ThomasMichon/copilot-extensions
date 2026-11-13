"""One existing-daemon task for bounded notice recovery and host fanout."""

from __future__ import annotations

import asyncio
import sqlite3
import time

from .runtime_custody import RuntimeCustodyError
from .status_notices import deliver_notice
from .status_store import Selection, StatusStore


class StatusRuntime:
    def __init__(self, app):
        self.app = app
        self.wake = asyncio.Event()
        self.task = None
        self.remote = None
        self.error = None
        self.streams = 0

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.remote is not None:
            await self.remote.close()

    async def prepare(self, selection: Selection) -> Selection:
        if any(a.host != "local" for a in selection.agents):
            from .status_remote import HostFanout
            if self.remote is None:
                self.remote = HostFanout(self.app)
            return await self.remote.prepare(selection)
        return selection

    def dispatch(self):
        state = self.app.state
        if (
            not getattr(state, "ready", False) or getattr(state, "shutting_down", False)
            or state.db.read_only or getattr(state.session_manager, "is_draining", False)
        ):
            return
        selected = StatusStore(state.db)
        pending = selected.pending()
        if pending:
            for sub in pending:
                deliver_notice(state.db, sub["id"], admission=None, refusal=None)
        # Silence is an observation, never an instruction to cancel/recreate.
        from .routes.live_sessions import _live_liveness
        from .db_core import live_session_is_fresh
        agents = {
            a.session_id
            for sub in state.db.execute_read("SELECT selection FROM bridge_status_subscriptions")
            for a in Selection.model_validate_json(sub["selection"]).agents if a.host == "local"
        }
        now = time.time()
        for session_id in agents:
            row = state.db.get_live_session(session_id)
            if row and live_session_is_fresh(row, now) and _live_liveness(row, now=now) == "stalled":
                selected.publish(
                    session_id, "stalled", f"stalled:{session_id}:{row.get('last_activity_at')}",
                    evidence="silence_observation",
                )

    async def run(self):
        while True:
            self.wake.clear()
            if getattr(self.app.state, "ready", False):
                try:
                    if getattr(self.app.state.session_manager, "is_draining", False):
                        if self.remote is not None:
                            await self.remote.close()
                            self.remote = None
                        await asyncio.sleep(5)
                        continue
                    self.dispatch()
                    if self.remote is None:
                        remote_selected = any(
                            agent.host != "local"
                            for row in self.app.state.db.execute_read("SELECT selection FROM bridge_status_subscriptions")
                            for agent in Selection.model_validate_json(row["selection"]).agents
                        )
                        if remote_selected:
                            from .status_remote import HostFanout
                            self.remote = HostFanout(self.app)
                    if self.remote is not None:
                        await self.remote.reconcile()
                    self.error = None
                except (sqlite3.Error, RuntimeCustodyError, ValueError):
                    self.error = "status_runtime_unavailable"
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except (TimeoutError, asyncio.TimeoutError):
                pass
