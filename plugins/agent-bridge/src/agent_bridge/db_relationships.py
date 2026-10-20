"""Bridge relationship journal, retained independently of session garbage collection."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any


class _RelationshipsMixin:
    def create_relationship(
        self, session_id: str, owner: str, binding: dict[str, Any],
        requested_native_id: str | None, *, name: str, agent_name: str,
        caller_id: str | None, caller_session_id: str | None, target_json: str,
    ) -> dict[str, Any]:
        relationship_id = uuid.uuid4().hex
        authority = self.execute_read("SELECT id FROM remote_acp_relationship_authority WHERE singleton=1")
        if len(authority) != 1:
            raise RuntimeError("Bridge relationship database has no unique authority")
        recovery = {
            "relationship_id": relationship_id, "binding": binding,
            "provider": {"namespace": "bridge-relationship", "authority": authority[0]["id"]},
        }
        conn = self._get_conn()
        with self._write_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO sessions "
                    "(id, name, agent_name, caller_id, caller_session_id, target_dir, target_type, "
                    "status, target_json, created_at, updated_at, background_recovery_enabled) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'remote-acp', 'starting', ?, ?, ?, 0)",
                    (session_id, name, agent_name, caller_id, caller_session_id,
                     binding["cwd"], target_json, time.time(), time.time()),
                )
                conn.execute(
                    "INSERT INTO remote_acp_relationships "
                    "(id, session_id, owner, recovery_json, requested_native_id, phase) VALUES (?, ?, ?, ?, ?, 'pending')",
                    (relationship_id, session_id, owner, json.dumps(recovery, sort_keys=True), requested_native_id),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        row = self.get_relationship(session_id)
        if row is None:
            raise RuntimeError("Relationship journal was not persisted")
        return row

    def get_relationship(self, session_id: str) -> dict[str, Any] | None:
        rows = self.execute_read(
            "SELECT * FROM remote_acp_relationships WHERE session_id=?", (session_id,),
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["recovery"] = json.loads(row.pop("recovery_json"))
        raw = row.pop("claim_json")
        receipt = json.loads(raw) if raw is not None else None
        row["absence"] = receipt if isinstance(receipt, dict) and receipt.get("absent") is True else None
        row["claim"] = None if row["absence"] is not None else receipt
        row["uncertain"] = bool(row["uncertain"])
        return row

    def save_relationship_claim(
        self, session_id: str, claim: dict[str, Any], phase: str,
    ) -> None:
        self.execute_write(
            "UPDATE remote_acp_relationships SET claim_json=?, phase=?, "
            "uncertain=CASE WHEN ?='bound' THEN 0 ELSE uncertain END WHERE session_id=?",
            (json.dumps(claim, sort_keys=True), phase, phase, session_id),
        )

    def enter_relationship_attempt(self, session_id: str) -> None:
        self.execute_write(
            "UPDATE remote_acp_relationships SET phase='attempting', uncertain=1 "
            "WHERE session_id=? AND phase='reserved'", (session_id,),
        )

    def save_relationship_absence(self, session_id: str, receipt: dict[str, Any]) -> None:
        """Retain the no-reservation authority receipt without inventing a claim."""
        cursor = self.execute_write(
            "UPDATE remote_acp_relationships SET claim_json=?, phase='closed' "
            "WHERE session_id=? AND phase='retired' AND claim_json IS NULL "
            "AND native_id IS NULL AND uncertain=0",
            (json.dumps(receipt, sort_keys=True), session_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Unreserved relationship changed before confirmed local close")

    def save_relationship_native_id(self, session_id: str, native_id: str) -> None:
        self.execute_write(
            "UPDATE remote_acp_relationships SET native_id=?, phase='binding' WHERE session_id=?",
            (native_id, session_id),
        )

    def retire_relationship(self, session_id: str) -> None:
        self.execute_write(
            "UPDATE remote_acp_relationships SET phase='retired' WHERE session_id=?", (session_id,),
        )

    def relationship_subject(self, subject: str) -> dict[str, Any] | None:
        # Match the exact provider-owned claim, not caller-decoded envelope fields.
        rows = self.execute_read(
            "SELECT session_id FROM remote_acp_relationships WHERE json_extract(claim_json, '$.ref')=?",
            (subject,),
        )
        if len(rows) != 1:
            return None
        return self.get_relationship(rows[0]["session_id"])
