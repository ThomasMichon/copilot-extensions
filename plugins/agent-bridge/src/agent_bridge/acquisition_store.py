"""Durable acquisition stages and conversation bindings over the bridge database."""

from __future__ import annotations

import json
import time
from typing import Any

from .acquisition_contract import AcquisitionRefused, AcquisitionRequest, cli_driver
from .acquisition_journal import AcquisitionJournal, _digest
from .db import Database


class AcquisitionStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.journal = AcquisitionJournal(db)
        db.execute_write(
            "CREATE TABLE IF NOT EXISTS acquisition_effects ("
            "delivery_id TEXT PRIMARY KEY, stage TEXT NOT NULL, data_json TEXT NOT NULL)"
        )
        db.execute_write(
            "CREATE TABLE IF NOT EXISTS acquisition_bindings ("
            "session_id TEXT PRIMARY KEY, native_session_id TEXT NOT NULL, "
            "task_id TEXT NOT NULL, task_generation INTEGER NOT NULL, "
            "worktree_id TEXT NOT NULL, mode TEXT NOT NULL, target TEXT NOT NULL, "
            "retired INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)"
        )
        db.execute_write(
            "CREATE TABLE IF NOT EXISTS acquisition_launch_gates ("
            "worktree_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL, mode TEXT NOT NULL, "
            "session_id TEXT, native_session_id TEXT, driver TEXT NOT NULL)"
        )
        db.execute_write(
            "CREATE TRIGGER IF NOT EXISTS acquisition_owned_start_guard BEFORE INSERT ON sessions "
            "WHEN EXISTS (SELECT 1 FROM acquisition_launch_gates g "
            "WHERE g.worktree_id=json_extract(NEW.target_json, '$.worktree_id') "
            "AND (g.mode != 'acp' OR NEW.caller_id != 'acquisition:' || g.delivery_id "
            "OR NEW.caller_id IS NULL OR (g.session_id IS NOT NULL AND NEW.id != g.session_id) "
            "OR EXISTS (SELECT 1 FROM sessions s "
            "WHERE json_extract(s.target_json, '$.worktree_id')=g.worktree_id "
            "AND s.status IN ('created','starting','running','idle') "
            "AND s.id != COALESCE(g.session_id,'')))) "
            "BEGIN SELECT RAISE(ABORT, 'acquisition launch fence held'); END"
        )
        db.execute_write(
            "CREATE TRIGGER IF NOT EXISTS acquisition_cli_start_guard BEFORE INSERT ON live_sessions "
            "WHEN EXISTS (SELECT 1 FROM acquisition_launch_gates g WHERE g.worktree_id=NEW.worktree_id "
            "AND (g.mode != 'cli' OR NEW.session_id != COALESCE(g.native_session_id,'') "
            "AND (NEW.driven_by IS NULL "
            "OR NEW.driven_by != g.driver) "
            "OR EXISTS (SELECT 1 FROM live_sessions ls "
            "WHERE ls.worktree_id=g.worktree_id AND ls.status='live' "
            "AND ls.updated_at >= NEW.updated_at - 120 AND ls.session_id != NEW.session_id "
            "AND (NEW.pid IS NULL OR ls.pid IS NULL OR ls.pid != NEW.pid)))) "
            "BEGIN SELECT RAISE(ABORT, 'acquisition launch fence held'); END"
        )
        db.execute_write(
            "CREATE TRIGGER IF NOT EXISTS acquisition_cli_update_guard BEFORE UPDATE ON live_sessions "
            "WHEN NEW.status='live' AND EXISTS (SELECT 1 FROM acquisition_launch_gates g "
            "WHERE g.worktree_id=NEW.worktree_id AND (g.mode != 'cli' "
            "OR NEW.session_id != COALESCE(g.native_session_id,'') AND "
            "(NEW.driven_by IS NULL OR NEW.driven_by != g.driver))) "
            "BEGIN SELECT RAISE(ABORT, 'acquisition launch fence held'); END"
        )

    def gate(
        self, request: AcquisitionRequest, session: str | None, native: str | None,
    ) -> None:
        inserted = self.db.execute_write(
            "INSERT INTO acquisition_launch_gates VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(worktree_id) DO NOTHING",
            (request.worktree_id, request.delivery_id, request.mode, session, native, cli_driver(request)),
        )
        if inserted.rowcount != 1:
            raise AcquisitionRefused("launch_gate_held", "another acquisition holds the launch gate")
        # Existing legacy starts already past their insert must also be fenced.
        rows = self.db.execute_read(
            "SELECT id FROM sessions WHERE json_extract(target_json, '$.worktree_id')=? "
            "AND status IN ('created','starting','running','idle') AND id != COALESCE(?, '')",
            (request.worktree_id, session),
        )
        if rows:
            self.release_gate(request)
            raise AcquisitionRefused("live_holder", "an owned start raced acquisition")
        live = self.db.list_fresh_live_sessions(request.worktree_id, now=time.time())
        if any(row["session_id"] != native for row in live):
            self.release_gate(request)
            raise AcquisitionRefused("live_holder", "a represented start raced acquisition")

    def release_gate(self, request: AcquisitionRequest) -> None:
        self.db.execute_write(
            "DELETE FROM acquisition_launch_gates WHERE worktree_id=? AND delivery_id=?",
            (request.worktree_id, request.delivery_id),
        )

    def stage(self, request: AcquisitionRequest, stage: str, **data: Any) -> None:
        previous = self.db.execute_read(
            "SELECT data_json FROM acquisition_effects WHERE delivery_id=?", (request.delivery_id,),
        )
        if previous:
            authority = json.loads(previous[0]["data_json"]).get("creation_authorized", False)
            data.setdefault("creation_authorized", authority)
        self.db.execute_write(
            "INSERT INTO acquisition_effects VALUES (?, ?, ?) "
            "ON CONFLICT(delivery_id) DO UPDATE SET stage=excluded.stage, data_json=excluded.data_json",
            (request.delivery_id, stage, json.dumps(
                {**data, "_request": request.model_dump(mode="json")}, sort_keys=True,
            )),
        )

    def recovery(self, request: AcquisitionRequest) -> tuple[str, str, dict[str, Any]]:
        rows = self.db.execute_read(
            "SELECT a.*, e.stage, e.data_json FROM acquisition_attempts a "
            "LEFT JOIN acquisition_effects e USING(delivery_id) WHERE delivery_id=?",
            (request.delivery_id,),
        )
        if not rows or rows[0]["request_digest"] != _digest(request):
            raise AcquisitionRefused("delivery_conflict", "pending delivery facts differ")
        row = rows[0]
        if row["state"] != "pending":
            raise AcquisitionRefused("fence_lost", "attempt is not pending")
        return row["token"], row["stage"] or "prepared", json.loads(row["data_json"] or "{}")

    def bindings(self, worktree_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute_read(
            "SELECT * FROM acquisition_bindings WHERE worktree_id=?", (worktree_id,),
        )]

    def pending(self, worktree_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute_read(
            "SELECT a.delivery_id,e.stage,e.data_json FROM acquisition_attempts a "
            "LEFT JOIN acquisition_effects e USING(delivery_id) "
            "WHERE a.worktree_id=? AND a.state='pending'", (worktree_id,),
        )]

    def bind(self, request: AcquisitionRequest, session_id: str, native_id: str) -> None:
        rows = self.db.execute_read(
            "SELECT * FROM acquisition_bindings WHERE session_id=?", (session_id,),
        )
        if rows and (
            rows[0]["task_id"] != request.task_id
            or rows[0]["native_session_id"] != native_id
            or rows[0]["mode"] != request.mode
            or rows[0]["worktree_id"] != request.worktree_id
            or rows[0]["target"] != request.target
            or rows[0]["task_generation"] > request.task_generation
            or rows[0]["retired"]
        ):
            raise AcquisitionRefused("binding_conflict", "conversation binding changed")
        self.db.execute_write(
            "INSERT INTO acquisition_bindings VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "task_generation=excluded.task_generation, updated_at=excluded.updated_at",
            (session_id, native_id, request.task_id, request.task_generation,
             request.worktree_id, request.mode, request.target, time.time()),
        )

    def retire(self, worktree_id: str) -> frozenset[str]:
        ids = frozenset(row["native_session_id"] for row in self.bindings(worktree_id))
        self.db.execute_write(
            "UPDATE acquisition_bindings SET retired=1 WHERE worktree_id=?", (worktree_id,),
        )
        return ids
