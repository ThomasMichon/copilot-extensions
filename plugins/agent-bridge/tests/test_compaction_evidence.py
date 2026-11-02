"""Start-event preservation and honest unqualified pre-capacity construction."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any

import pytest

from agent_bridge.compaction_evidence import (
    MAX_RECORD_BYTES,
    START_FIELDS,
    CompactionEvidenceError,
    CompactionSource,
    from_start_event,
)
from agent_bridge.events import SseEvent
from agent_bridge.live_representation import translate_sdk_event


def start(raw: dict[str, Any]) -> SseEvent:
    event, data = translate_sdk_event("session.compaction_start", raw)[0]
    return SseEvent(id=7, event=event, data=data, timestamp=123.0)


def source() -> CompactionSource:
    return CompactionSource(
        kind="represented-sdk",
        bridge_session_id="bridge-session",
        durable_session_id="native-session",
        sdk_session_id="native-session",
        sdk_event_id="sdk-start",
        continuity_id="continuity",
        context_epoch="epoch",
        source_timestamp="2026-11-02T00:00:00Z",
    )


def test_actual_translated_total_is_preserved_but_target_denominator_never_qualifies() -> None:
    event = start(
        {
            "currentTokens": 900,
            "tokenLimit": 2000,
            "compactionAttemptId": "attempt",
            "model": "source-model",
            "targetPromptTokenLimit": 1200,
            "execution": "direct",
            "trigger": "manual",
            "conversationTokens": 600,
            "systemTokens": 100,
            "toolDefinitionsTokens": 200,
        }
    )
    evidence = from_start_event(event, source=source()).as_dict()
    assert evidence["schema"] == "bridge-compaction-evidence-v1" and evidence["version"] == 1
    pre = evidence["pre_usage"]
    assert pre["used_tokens"] == 900
    assert pre["coverage"] == "total-system-conversation-tools-at-start"
    assert pre["relation"] == "same-start-event"
    assert pre["active_capacity_tokens"] is None
    assert pre["target_capacity_tokens"] == 2000 and pre["target_prompt_budget_tokens"] == 1200
    assert pre["qualification"] == "unknown"
    assert "target-capacity:is-not-active-pre-capacity" in pre["reasons"]
    assert evidence["compaction"]["compaction_attempt_id"] == "attempt"
    assert (
        evidence["source"]["bridge_event_id"] == 7
        and evidence["source"]["sdk_event_id"] == "sdk-start"
    )


def test_legacy_start_and_unknown_source_remain_unknown_without_manufactured_ids() -> None:
    evidence = from_start_event(start({"conversationTokens": 600, "systemTokens": 100})).as_dict()
    assert evidence["pre_usage"]["used_tokens"] is None
    assert evidence["pre_usage"]["qualification"] == "unknown"
    assert evidence["source"]["kind"] == "unknown"
    for key in (
        "bridge_session_id",
        "durable_session_id",
        "sdk_session_id",
        "sdk_event_id",
        "context_epoch",
    ):
        assert evidence["source"][key] is None
    assert evidence["diagnostics"]["start_fields"]["conversation_tokens"] == 600
    assert "current_tokens:missing" in evidence["pre_usage"]["reasons"]
    assert evidence["source"]["bridge_observed_timestamp"] == 123.0
    assert evidence["source"]["source_timestamp"] is None


@pytest.mark.parametrize("raw", [{}, {"currentTokens": None, "tokenLimit": None}])
def test_no_field_or_null_field_is_an_explicit_unknown_record(raw: dict[str, Any]) -> None:
    evidence = from_start_event(start(raw), source=source()).as_dict()
    assert evidence["pre_usage"]["used_tokens"] is None
    assert evidence["pre_usage"]["target_capacity_tokens"] is None
    assert evidence["pre_usage"]["qualification"] == "unknown"
    reason = "current_tokens:invalid-integer-type" if raw else "current_tokens:missing"
    assert reason in evidence["pre_usage"]["reasons"]


@pytest.mark.parametrize(
    "value", [True, False, 1.0, 1.5, -1, "900", [], {}, math.nan, math.inf, 1 << 100]
)
def test_invalid_numerator_type_is_diagnosed_without_success_defaults(value: Any) -> None:
    evidence = from_start_event(
        start({"currentTokens": value, "tokenLimit": 2000}), source=source()
    ).as_dict()
    assert evidence["pre_usage"]["used_tokens"] is None
    assert evidence["pre_usage"]["qualification"] == "unknown"
    assert any(reason.startswith("current_tokens:") for reason in evidence["pre_usage"]["reasons"])
    json.dumps(evidence, allow_nan=False)
    assert "current_tokens" in evidence["diagnostics"]["start_fields"]


@pytest.mark.parametrize("value", [True, None, 0, -1, 2000.0, "2000", math.nan, math.inf])
def test_invalid_target_capacity_does_not_become_active_capacity(value: Any) -> None:
    evidence = from_start_event(
        start({"currentTokens": 900, "tokenLimit": value}), source=source()
    ).as_dict()
    assert evidence["pre_usage"]["used_tokens"] == 900
    assert evidence["pre_usage"]["target_capacity_tokens"] is None
    assert evidence["pre_usage"]["active_capacity_tokens"] is None
    assert evidence["pre_usage"]["qualification"] == "unknown"


@pytest.mark.parametrize("switch", [None, "target-model"])
def test_absent_switch_metadata_does_not_promote_target_window(switch: str | None) -> None:
    raw: dict[str, Any] = {"currentTokens": 900, "tokenLimit": 2000, "model": "source-model"}
    if switch is not None:
        raw["switchTargetModel"] = switch
    evidence = from_start_event(start(raw), source=source()).as_dict()
    assert evidence["pre_usage"]["active_capacity_tokens"] is None
    assert evidence["pre_usage"]["qualification"] == "unknown"
    if switch is not None:
        assert (
            "model-switch:target-and-source-capacities-may-differ"
            in evidence["pre_usage"]["reasons"]
        )


def test_last_sample_metadata_sums_and_post_metrics_never_supply_pre_capacity() -> None:
    event = start({"conversationTokens": 600, "systemTokens": 100})
    event.data.update(
        {
            "cached_context_used": 900,
            "cached_context_size": 2000,
            "last_usage_at": 123.0,
            "post_compaction_tokens": 200,
            "pre_compaction_tokens": 900,
            "active_capacity_tokens": 2000,
            "owner_verified": True,
        }
    )
    evidence = from_start_event(event, source=source()).as_dict()
    assert evidence["pre_usage"]["used_tokens"] is None
    assert evidence["pre_usage"]["active_capacity_tokens"] is None
    assert evidence["pre_usage"]["qualification"] == "unknown"
    assert "active_capacity_tokens" not in evidence["diagnostics"]["start_fields"]


def test_new_record_is_bounded_and_nonfinite_diagnostics_are_json_safe() -> None:
    event = start(
        {
            "currentTokens": math.nan,
            "tokenLimit": 2000,
            "model": "x" * 10000,
            "trigger": {"many": [{"deeper": "x" * 10000}] * 20},
        }
    )
    evidence = from_start_event(event, source=source()).as_dict()
    assert evidence["diagnostics"]["start_fields"]["current_tokens"] == {
        "type": "nonfinite-number",
        "value": "nan",
    }
    assert evidence["compaction"]["model"] is None
    assert (
        len(json.dumps(evidence, allow_nan=False, ensure_ascii=False).encode()) <= MAX_RECORD_BYTES
    )


def test_wrong_event_is_a_visible_error_not_a_compaction_record() -> None:
    with pytest.raises(CompactionEvidenceError, match="actual compaction_start"):
        from_start_event(SseEvent(id=1, event="usage_update", data={"context_used": 900}))


def test_unconfirmed_source_contract_keeps_total_coverage_unknown() -> None:
    evidence = from_start_event(start({"currentTokens": 900, "tokenLimit": 2000})).as_dict()
    assert evidence["pre_usage"]["used_tokens"] == 900
    assert evidence["pre_usage"]["coverage"] == "unknown"
    assert "total-coverage:source-contract-unconfirmed" in evidence["pre_usage"]["reasons"]


def test_zero_total_and_complete_source_metadata_still_do_not_qualify_capacity() -> None:
    evidence = from_start_event(
        start({"currentTokens": 0, "tokenLimit": 2000}), source=source()
    ).as_dict()
    assert evidence["pre_usage"]["used_tokens"] == 0
    assert evidence["pre_usage"]["qualification"] == "unknown"
    assert evidence["pre_usage"]["active_capacity_tokens"] is None


def test_source_identity_mismatch_and_invalid_values_remain_diagnostic() -> None:
    context = CompactionSource(
        kind="represented-sdk",
        durable_session_id="session-a",
        sdk_session_id="session-b",
        sdk_event_id="sdk-event",
        context_epoch="epoch",
    )
    event = SseEvent(
        id=True,
        event="compaction_start",
        data={"current_tokens": 900, "token_limit": 2000},
        timestamp=math.nan,
    )
    evidence = from_start_event(event, source=context).as_dict()
    assert evidence["source"]["bridge_event_id"] is None
    assert evidence["source"]["bridge_observed_timestamp"] is None
    assert evidence["diagnostics"]["bridge_event_id"] is True
    assert "source-session:identity-mismatch" in evidence["pre_usage"]["reasons"]
    assert evidence["pre_usage"]["qualification"] == "unknown"


@pytest.mark.parametrize("invalid", ["\ud800", "\udfff", "prefix\ud800suffix"])
def test_surrogate_model_and_source_ids_produce_json_safe_unknown_diagnostics(
    invalid: str,
) -> None:
    raw = {
        "currentTokens": 900,
        "tokenLimit": 2000,
        "model": invalid,
        "compactionAttemptId": invalid,
        "trigger": {"nested": [invalid]},
    }
    event = start(raw)
    assert event.data["model"] == invalid
    context = replace(source(), sdk_session_id=invalid, sdk_event_id=invalid)
    evidence = from_start_event(event, source=context).as_dict()
    assert evidence["compaction"]["model"] is None
    assert evidence["source"]["sdk_session_id"] is None
    assert evidence["source"]["sdk_event_id"] is None
    assert evidence["diagnostics"]["start_fields"]["model"]["type"] == "invalid-unicode-text"
    assert (
        evidence["diagnostics"]["source_fields"]["sdk_event_id"]["type"] == "invalid-unicode-text"
    )
    encoded = json.dumps(evidence, allow_nan=False, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= MAX_RECORD_BYTES
    assert evidence["pre_usage"]["qualification"] == "unknown"
    assert evidence["pre_usage"]["active_capacity_tokens"] is None


def test_direct_as_dict_surrogate_is_an_explicit_evidence_error_not_encoding_exception() -> None:
    evidence = from_start_event(start({"currentTokens": 900, "tokenLimit": 2000}), source=source())
    malformed = replace(evidence, source={**evidence.source, "sdk_event_id": "\ud800"})
    with pytest.raises(CompactionEvidenceError, match="finite UTF-8 JSON"):
        malformed.as_dict()


@pytest.mark.parametrize("payload", ["\ud800" * 10000, "\x01" * 10000, "😀" * 10000])
def test_worst_nested_diagnostics_are_bounded_or_fail_explicitly(payload: str) -> None:
    nested = {f"{payload}{index}": [payload] * 20 for index in range(20)}
    event = SseEvent(
        id=7,
        event="compaction_start",
        data={field: nested for field in START_FIELDS},
        timestamp=123.0,
    )
    context = CompactionSource(
        kind="represented-sdk",
        bridge_session_id=payload,
        durable_session_id=payload,
        sdk_session_id=payload,
        sdk_event_id=payload,
        continuity_id=payload,
        context_epoch=payload,
        source_timestamp=payload,
    )
    try:
        result = from_start_event(event, source=context).as_dict()
    except CompactionEvidenceError as exc:
        assert "record bound" in str(exc)
    else:
        encoded = json.dumps(result, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        assert len(encoded.encode("utf-8")) <= MAX_RECORD_BYTES
        assert result["pre_usage"]["qualification"] == "unknown"
        assert result["pre_usage"]["active_capacity_tokens"] is None
