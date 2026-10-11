"""First-conversation authority from a complete, positively refused attempt chain."""

from __future__ import annotations

import json
from typing import Any

from .acquisition_contract import AcquisitionRefused, AcquisitionRequest
from .acquisition_journal import _digest


def initial_creation_proven(request: AcquisitionRequest, venue: dict[str, Any], store: Any) -> bool:
    """No missing receipt, directory absence, or caller assertion grants creation."""
    if (
        request.allocation != "reused" or request.intent != "automatic"
        or request.conversation_retired is not False
        or request.prior_session_id or request.prior_native_session_id
        or venue["task"].get("owner_session_id") or venue.get("attachments")
    ):
        return False
    current = venue["acquisition_reservation"]
    history = venue.get("acquisition_reservation_history")
    count = current.get("attempt")
    if (
        type(count) is not int or not 2 <= count <= 256
        or current.get("state") != "reserving"
        or not isinstance(history, dict) or type(history.get("version")) is not int
        or history["version"] != 1
        or not isinstance(history.get("reservations"), list)
        or len(history["reservations"]) != count
    ):
        return False
    reservations = history["reservations"]
    by_attempt = {}
    for row in reservations:
        if not isinstance(row, dict) or type(row.get("attempt")) is not int:
            return False
        if row["attempt"] in by_attempt:
            return False
        by_attempt[row["attempt"]] = row
    if set(by_attempt) != set(range(1, count + 1)) or by_attempt[count] != current:
        return False
    for number, row in by_attempt.items():
        if (
            not isinstance(row.get("key"), str) or not row["key"]
            or row.get("task_id") != request.task_id or row.get("worktree") != request.worktree_id
            or row.get("worktree_ownership") != ("created" if number == 1 else "reused")
            or row.get("conversation_retired") is not False or row.get("session_handle")
            or row.get("release_requested")
            or row.get("state") not in (
                {"reserving"} if number == count else {"deferred", "failed"}
            )
        ):
            return False
    records = store.db.execute_read(
        "SELECT a.*,e.stage,e.data_json FROM acquisition_attempts a "
        "LEFT JOIN acquisition_effects e USING(delivery_id) "
        "WHERE a.worktree_id=? AND a.delivery_id!=?",
        (request.worktree_id, request.delivery_id),
    )
    if len(records) != count - 1 or store.bindings(request.worktree_id):
        return False
    expected = {row["key"]: row for number, row in by_attempt.items() if number != count}
    for record in records:
        if (
            record["state"] != "refused" or record["stage"] != "prepared"
            or record["result_json"] or not record["refusal_code"]
        ):
            return False
        try:
            data = json.loads(record["data_json"])
            if not isinstance(data, dict) or set(data) != {"_request"}:
                return False
            previous = AcquisitionRequest.model_validate(data["_request"])
        except (ValueError, TypeError, KeyError) as exc:
            raise AcquisitionRefused("initial_creation_evidence_invalid", "pre-launch receipt is invalid") from exc
        row = expected.pop(previous.reservation_key, None)
        if row is None or record["request_digest"] != _digest(previous) or (
            previous.task_id != request.task_id or previous.target != request.target
            or previous.worktree_id != request.worktree_id or previous.mode != request.mode
            or previous.project != request.project or previous.task_generation > request.task_generation
            or previous.allocation != row["worktree_ownership"]
            or previous.conversation_retired is not False
            or previous.intent != "automatic"
            or previous.prior_session_id or previous.prior_native_session_id
        ):
            return False
    return not expected
