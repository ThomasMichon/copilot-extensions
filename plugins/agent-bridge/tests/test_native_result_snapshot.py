"""Native observations never relabel unscoped managed history."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge import native_result_snapshot as native
from agent_bridge.models import DelegatedResultSnapshot
from agent_bridge.runtime_custody import RuntimeAdmission
from agent_bridge.turn_custody import NativeTurnConsumer, new_handoff


@pytest.mark.parametrize("origin", [None, "native-before", "native-current"])
def test_native_result_keeps_only_content_with_matching_original_origin(
    monkeypatch: pytest.MonkeyPatch,
    origin: str | None,
) -> None:
    retained = DelegatedResultSnapshot.model_validate(
        {
            "identity": {
                "logical_delegate_kind": "session",
                "logical_delegate_id": "managed",
                "requested_ref": "managed",
                "snapshot_session_id": "managed",
                "current_session_id": "managed",
                "native_session_id": "native-current",
            },
            "fidelity": {"level": "full", "event_retention": "durable"},
            "state": {
                "session_status": "idle",
                "at_rest": True,
                "attention": {"availability": "available", "value": "turn_complete"},
                "active_work": {"availability": "available", "value": "unscoped old work"},
                "pending_input": {"availability": "available", "value": "unscoped old input"},
            },
            "latest_result": {
                "availability": "available",
                "value": {"turn_index": 3, "text": "completed answer", "stop_reason": "end_turn"},
            },
            "incremental": {
                "availability": "available",
                "items": [
                    {
                        "event_id": 1,
                        "kind": "assistant_message",
                        "summary": "unscoped old event",
                        "timestamp": 1.0,
                        "detail_ref": "unscoped-event-reference",
                    }
                ],
            },
            "limits": {"max_items": 1, "max_text_chars": 256, "used_text_chars": 100},
        }
    )
    admission = RuntimeAdmission("fixture-installation", 1, "fixture-producer")
    row: dict[str, Any] = {
        "session_id": "managed",
        "turn_index": 3,
        "runtime_custody_json": admission.document(),
        "native_handoff_json": (
            new_handoff(
                "managed",
                3,
                admission,
                NativeTurnConsumer("managed", origin, "fixture-incarnation", 1, "fixture-start"),
            )
            if origin is not None
            else None
        ),
        "native_handoff_started_at": None,
        "native_accepted_at": None,
        "native_consumed_at": None,
        "native_outcome": None,
    }
    reads: list[tuple[str, int]] = []

    def get_turn(session_id: str, index: int) -> dict[str, Any]:
        reads.append((session_id, index))
        return row

    def legacy(**_kwargs: Any) -> DelegatedResultSnapshot:
        return retained.model_copy(deep=True)

    monkeypatch.setattr(native, "legacy_snapshot", legacy)
    db: Any = SimpleNamespace(get_turn=get_turn)
    session: Any = SimpleNamespace(session_id="managed")
    result = native.build_owned_result_snapshot(
        db=db,
        session=session,
        requested_ref="managed",
        position=None,
        native_only=True,
    )
    assert reads == [("managed", 3)]
    if origin == "native-current":
        assert isinstance(result.latest_result.value, dict)
        assert result.latest_result.value["text"] == "completed answer"
        assert result.limits.used_text_chars == len("completed answerend_turn")
    else:
        assert result.latest_result.availability == "unsupported_for_target"
        assert "completed answer" not in result.model_dump_json()
        assert result.limits.used_text_chars == 0
    assert "unscoped old" not in result.model_dump_json()
    assert isinstance(retained.latest_result.value, dict)
    assert retained.latest_result.value["text"] == "completed answer"
    reads.clear()
    assert (
        native.build_owned_result_snapshot(
            db=db,
            session=session,
            requested_ref="managed",
            position=None,
        )
        == retained
    )
    assert not reads
