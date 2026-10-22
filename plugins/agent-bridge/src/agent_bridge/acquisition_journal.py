"""Durable acquisition attempt fencing; pending ambiguity never expires into retry."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass

from .acquisition_contract import (
    AcquisitionRefused, AcquisitionRequest, AcquisitionResult, validate_result,
)
from .db import Database
from .acquisition_policy import AcquisitionDecision, ConversationEvidence

_SCHEMA = """
CREATE TABLE IF NOT EXISTS acquisition_attempts (
    delivery_id TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    token TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'refused')),
    result_json TEXT,
    refusal_code TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""


@dataclass(frozen=True)
class AcquisitionClaim:
    token: str | None
    result: AcquisitionResult | None = None


def _digest(request: AcquisitionRequest) -> str:
    facts = request.model_dump(mode="json")
    if request.version == 1 and request.worktree_path is None:
        facts.pop("worktree_path")
    payload = json.dumps(
        facts, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AcquisitionJournal:
    """One durable delivery fence and one pending operation per target/worktree.

    This journal does not authorize launch. The lifecycle adapter must still
    acquire the existing holder fence and validate native identity. An adapter
    interruption leaves ``pending`` until explicit reconciliation proves what
    happened; a timeout alone is never permission to spawn or deliver again.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        db.execute_write(_SCHEMA)
        db.execute_write(
            "CREATE TABLE IF NOT EXISTS acquisition_effects ("
            "delivery_id TEXT PRIMARY KEY, stage TEXT NOT NULL, data_json TEXT NOT NULL)"
        )
        db.execute_write(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_acquisition_pending "
            "ON acquisition_attempts(target, worktree_id) WHERE state='pending'"
        )
        db.execute_write(
            "CREATE INDEX IF NOT EXISTS idx_acquisition_history "
            "ON acquisition_attempts(worktree_id, state)"
        )

    def claim(self, request: AcquisitionRequest) -> AcquisitionClaim:
        token = uuid.uuid4().hex
        digest = _digest(request)
        now = time.time()
        # Admission and the complete prepared recovery facts commit together.
        # No await or independently committed write can strand a digest-only claim.
        with self.db._write_lock:
            conn = self.db._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                inserted = conn.execute(
                    "INSERT INTO acquisition_attempts "
                    "(delivery_id, target, worktree_id, request_digest, token, state, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?) ON CONFLICT DO NOTHING",
                    (request.delivery_id, request.target, request.worktree_id, digest, token, now, now),
                ).rowcount == 1
                if inserted:
                    conn.execute(
                        "INSERT INTO acquisition_effects VALUES (?, 'prepared', ?)",
                        (request.delivery_id, json.dumps({"_request": request.model_dump(mode="json")})),
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        if inserted:
            return AcquisitionClaim(token)
        rows = self.db.execute_read(
            "SELECT * FROM acquisition_attempts WHERE delivery_id=?",
            (request.delivery_id,),
        )
        if not rows:
            raise AcquisitionRefused("acquisition_pending", "another operation holds the target")
        row = rows[0]
        if row["request_digest"] != digest:
            raise AcquisitionRefused("delivery_conflict", "delivery identity was reused for other facts")
        if row["state"] == "completed":
            receipt = json.loads(row["result_json"])
            result = AcquisitionResult.model_validate(receipt.get("result", receipt))
            decision = AcquisitionDecision(
                result.acquired, creation_authorized=receipt.get("creation_authorized", False),
                retirement_recovered=receipt.get("retirement_recovered", False),
                conversation=ConversationEvidence(**receipt["conversation"])
                if receipt.get("conversation") else None,
            )
            validate_result(request, result, decision=decision)
            return AcquisitionClaim(None, result)
        if row["state"] == "refused":
            raise AcquisitionRefused(row["refusal_code"], "this acquisition was refused")
        raise AcquisitionRefused(
            "acquisition_pending", "prior acquisition outcome is pending reconciliation",
        )

    def complete(
        self, request: AcquisitionRequest, token: str, result: AcquisitionResult,
        *, decision: AcquisitionDecision | None = None,
    ) -> None:
        validate_result(request, result, decision=decision)
        receipt = json.dumps({
            "result": result.model_dump(mode="json"),
            "creation_authorized": bool(decision and decision.creation_authorized),
            "retirement_recovered": bool(decision and decision.retirement_recovered),
            "conversation": asdict(decision.conversation)
            if decision and decision.retirement_recovered and decision.conversation else None,
        })
        updated = self.db.execute_write(
            "UPDATE acquisition_attempts SET state='completed', result_json=?, updated_at=? "
            "WHERE delivery_id=? AND request_digest=? AND token=? AND state='pending'",
            (receipt, time.time(), request.delivery_id, _digest(request), token),
        )
        if updated.rowcount != 1:
            raise AcquisitionRefused("fence_lost", "acquisition completion did not own the fence")

    def refuse(self, request: AcquisitionRequest, token: str, code: str) -> None:
        """Record a proven pre-effect refusal; not an ambiguous launch failure."""
        if not code:
            raise ValueError("refusal code must be non-empty")
        updated = self.db.execute_write(
            "UPDATE acquisition_attempts SET state='refused', refusal_code=?, updated_at=? "
            "WHERE delivery_id=? AND request_digest=? AND token=? AND state='pending'",
            (code, time.time(), request.delivery_id, _digest(request), token),
        )
        if updated.rowcount != 1:
            raise AcquisitionRefused("fence_lost", "acquisition refusal did not own the fence")

    def refused_before_effect(self, request: AcquisitionRequest) -> bool:
        """Only an exact refused/prepared receipt proves this request never launched."""
        if request.prior_session_id or request.prior_native_session_id:
            return False
        rows = self.db.execute_read(
            "SELECT a.*, e.stage, e.data_json FROM acquisition_attempts a "
            "JOIN acquisition_effects e USING(delivery_id) WHERE a.delivery_id=?",
            (request.delivery_id,),
        )
        if not rows:
            return False
        row = rows[0]
        if (
            row["state"] != "refused" or row["stage"] != "prepared"
            or row["request_digest"] != _digest(request) or row["result_json"]
            or row["refusal_code"] not in {
                "worktree_unknown", "workspace_identity_conflict", "worktree_terminal",
                "cli_capability_missing", "cli_options_unsupported",
                "target_unavailable", "venue_unsupported",
                "plugin_preparation_failed",
            }
        ):
            return False
        if self.db.execute_read(
            "SELECT 1 FROM acquisition_attempts a LEFT JOIN acquisition_effects e USING(delivery_id) "
            "WHERE a.worktree_id=? AND a.delivery_id<>? "
            "AND (a.state<>'refused' OR COALESCE(e.stage, '')<>'prepared') LIMIT 1",
            (request.worktree_id, request.delivery_id),
        ):
            return False
        try:
            recorded = AcquisitionRequest.model_validate(json.loads(row["data_json"])["_request"])
        except (ValueError, TypeError, KeyError):
            return False
        return _digest(recorded) == _digest(request)
