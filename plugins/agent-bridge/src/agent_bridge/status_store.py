"""Bounded metadata feed and explicit, cursor-neutral subscription state."""

from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .status_schema import MAX_SUBSCRIPTIONS, RETENTION

StatusKind = Literal[
    "question_pending", "permission_pending", "idle", "message_consumed", "stalled", "error",
]
SafeId = r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$"


class SelectedAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(default="local", min_length=1, max_length=128, pattern=SafeId)
    session_id: str = Field(min_length=1, max_length=256, pattern=SafeId)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    watcher: str = Field(min_length=1, max_length=256, pattern=SafeId)
    agents: list[SelectedAgent] = Field(min_length=1, max_length=256)
    events: list[StatusKind] = Field(min_length=1, max_length=6)
    include_nested: bool = False


def matches(selection: Selection, event: dict) -> bool:
    return (
        event["kind"] in selection.events
        and (selection.include_nested or event["scope"] == "root")
        and any(a.host == event["source"] and a.session_id == event["session_id"]
                for a in selection.agents)
    )


class StatusGap(ValueError):
    def __init__(self, info: dict):
        self.info = {"code": "status_replay_gap", "action": "reconcile", **info}
        super().__init__("Host status continuity is unavailable; reconcile before acknowledging")


class StatusStore:
    def __init__(self, db):
        self.db = db

    def bounds(self, conn) -> dict:
        floor = conn.execute("SELECT id FROM bridge_status_events ORDER BY id LIMIT 1").fetchone()
        head = conn.execute("SELECT id FROM bridge_status_events ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "epoch": conn.execute("SELECT epoch FROM bridge_status_meta").fetchone()[0],
            "floor": floor[0] if floor else 1, "head": head[0] if head else 0,
        }

    def publish(
        self, session_id: str, kind: StatusKind, key: str, *,
        source: str = "local", scope: str = "root",
        observed_at: float | None = None, evidence: str = "observed",
    ) -> int | None:
        with self.db._runtime_transaction(write=True, full_sync=False) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO bridge_status_events "
                "(source,source_key,session_id,kind,scope,observed_at,evidence) VALUES (?,?,?,?,?,?,?)",
                (source, key, session_id, kind, scope,
                 time.time() if observed_at is None else observed_at, evidence),
            )
            if not cur.rowcount:
                return None
            event_id = cur.lastrowid
            event = {"source": source, "session_id": session_id, "kind": kind, "scope": scope}
            for sub in conn.execute("SELECT * FROM bridge_status_subscriptions").fetchall():
                if matches(Selection.model_validate_json(sub["selection"]), event):
                    conn.execute(
                        "UPDATE bridge_status_subscriptions SET unread=?, "
                        "notice_epoch=notice_epoch+CASE WHEN unread<=acked THEN 1 ELSE 0 END, "
                        "notice_id=CASE WHEN unread<=acked THEN NULL ELSE notice_id END, "
                        "notice_error=NULL WHERE id=?", (event_id, sub["id"]),
                    )
            conn.execute(
                "DELETE FROM bridge_status_events WHERE id<=?", (event_id - RETENTION,),
            )
        return event_id

    def subscribe(self, subscription_id: str, selection: Selection) -> dict:
        encoded = selection.model_dump_json()
        with self.db._runtime_transaction(write=True) as conn:
            old = conn.execute(
                "SELECT * FROM bridge_status_subscriptions WHERE id=?", (subscription_id,),
            ).fetchone()
            if old:
                if old["selection"] != encoded:
                    raise ValueError("Subscription identity already has a different selection")
                return dict(old)
            if conn.execute("SELECT COUNT(*) FROM bridge_status_subscriptions").fetchone()[0] >= MAX_SUBSCRIPTIONS:
                raise ValueError("Host status subscription capacity reached")
            count = sum(len(Selection.model_validate_json(row[0]).agents) for row in conn.execute(
                "SELECT selection FROM bridge_status_subscriptions",
            ))
            if count + len(selection.agents) > 1024:
                raise ValueError("Host status selected-agent capacity reached")
            head = self.bounds(conn)["head"]
            conn.execute(
                "INSERT INTO bridge_status_subscriptions (id,generation,watcher,selection,acked,unread) "
                "VALUES (?,?,?,?,?,?)", (subscription_id, uuid.uuid4().hex, selection.watcher, encoded, head, head),
            )
            return dict(conn.execute(
                "SELECT * FROM bridge_status_subscriptions WHERE id=?", (subscription_id,),
            ).fetchone())

    def page(self, *, after: int, epoch: str | None, limit: int = 100, subscription_id: str | None = None) -> dict:
        with self.db._runtime_transaction(write=False) as conn:
            info = self.bounds(conn)
            if epoch not in (None, info["epoch"]) or after < info["floor"] - 1 or after > info["head"]:
                raise StatusGap(info)
            selection = None
            subscription = None
            if subscription_id:
                row = conn.execute(
                    "SELECT * FROM bridge_status_subscriptions WHERE id=?", (subscription_id,),
                ).fetchone()
                if row is None:
                    raise LookupError("Status subscription not found")
                subscription = dict(row)
                selection = Selection.model_validate_json(row["selection"])
            # Scan a bounded retained range, even when selected events are sparse.
            rows = conn.execute(
                "SELECT * FROM bridge_status_events WHERE id>? ORDER BY id LIMIT ?",
                (after, limit),
            ).fetchall()
            events = [dict(row) for row in rows if selection is None or matches(selection, dict(row))]
            return {
                **info, "events": events, "through": rows[-1]["id"] if rows else after,
                "subscription": subscription,
                "availability": "retained_observations",
                "reason": "Historical transitions are not current attention or liveness",
            }

    def acknowledge(self, subscription_id: str, through: int, epoch: str) -> dict:
        with self.db._runtime_transaction(write=True) as conn:
            info = self.bounds(conn)
            if epoch != info["epoch"] or not info["floor"] - 1 <= through <= info["head"]:
                raise StatusGap(info)
            row = conn.execute(
                "SELECT * FROM bridge_status_subscriptions WHERE id=?", (subscription_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Status subscription not found")
            if through > row["acked"]:
                # Rearm only on explicit forward acknowledgement. A concurrently
                # inserted event above through remains unread with a fresh notice.
                conn.execute(
                    "UPDATE bridge_status_subscriptions SET acked=?,notice_epoch=notice_epoch+1,"
                    "notice_id=NULL,notice_error=NULL WHERE id=?", (through, subscription_id),
                )
            return dict(conn.execute(
                "SELECT * FROM bridge_status_subscriptions WHERE id=?", (subscription_id,),
            ).fetchone())

    def pending(self) -> list[dict]:
        return [dict(row) for row in self.db.execute_read(
            "SELECT * FROM bridge_status_subscriptions WHERE unread>acked AND notice_id IS NULL",
        )]

    def remove(self, subscription_id: str) -> None:
        self.db.execute_write("DELETE FROM bridge_status_subscriptions WHERE id=?", (subscription_id,))
