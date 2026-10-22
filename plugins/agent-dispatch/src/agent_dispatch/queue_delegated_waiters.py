"""Delegation identity checks for the existing prepare/arm/finish transactions."""

from __future__ import annotations

from .delegated_waiter_contract import validate_delegation, validate_event
from .queue_records import TaskError


class DelegatedWaiterBindingMixin:
    def run_waiter_coordinator_id(self) -> str:
        with self._connect() as conn:
            return str(conn.execute("SELECT id FROM run_waiter_identity").fetchone()["id"])

    def find_delegated_preparation(self, task_id: str, preparation_id: str) -> dict | None:
        with self._connect() as conn:
            return self._find_delegated_preparation(conn, task_id, preparation_id)

    def _find_delegated_preparation(self, conn, task_id: str, preparation_id: str) -> dict | None:
        for row in conn.execute("SELECT * FROM run_waiters WHERE task_id = ?", (task_id,)):
            value = self._run_waiter_from_row(row)
            if value["delegation"] and value["delegation"]["preparation_id"] == preparation_id:
                return value
        return None

    def _validate_waiter_preparation(self, kind: str, delegation: dict | None) -> dict | None:
        if kind not in ("process", "delegated") or (kind == "process" and delegation is not None):
            raise TaskError("invalid run waiter kind")
        if kind == "delegated":
            delegation = validate_delegation(delegation)
            if delegation["coordinator_id"] != self.run_waiter_coordinator_id():
                raise TaskError("delegated waiter belongs to another coordinator")
        return delegation

    @staticmethod
    def _validate_waiter_arm(waiter, *, kind, delegation, pid, host, start_token):
        if waiter["kind"] != kind:
            raise TaskError("run waiter kind mismatch")
        if kind == "delegated":
            value = validate_delegation(delegation, armed=True)
            if {k: v for k, v in value.items() if k != "registration_id"} != waiter["delegation"]:
                raise TaskError("delegated waiter identity mismatch")
        elif kind != "process" or not pid or not host or not start_token:
            raise TaskError("invalid process waiter identity")

    @staticmethod
    def _delegated_finish_is_replay(waiter, delegation, event) -> bool:
        if delegation is None:
            if waiter["kind"] != "process":
                raise TaskError("run waiter kind mismatch")
            return False
        validate_delegation(delegation)
        validate_event(event, delegation)
        stored = waiter["delegation"]
        if waiter["kind"] != "delegated" or not stored:
            raise TaskError("run waiter kind mismatch")
        if {k: v for k, v in stored.items() if k != "registration_id"} != delegation:
            raise TaskError("delegated waiter identity mismatch")
        if stored.get("registration_id") not in (None, event["registration_id"]):
            raise TaskError("watch registration mismatch")
        if waiter["state"] != "active":
            if waiter.get("event_id") not in (None, event["event_id"]):
                raise TaskError("watch event mismatch")
            return True
        if stored.get("registration_id") is None:
            raise TaskError("delegated waiter has no registration")
        return False
