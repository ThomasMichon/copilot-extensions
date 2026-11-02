"""Strict source-qualified frames; no host or service fixtures."""

from __future__ import annotations

from copy import deepcopy

import pytest

from agent_worktrees import owner_observation_contract as c


def source(project="project", journal="a" * 32, resident="b" * 32, head="head"):
    return c.Source(project, "full-worktree", "record:one", journal, resident, head)


def entry(kind="claim-added", evidence="persisted-mutation", fence=None):
    return {
        "kind": kind,
        "evidence_kind": evidence,
        "native_kind": "claim_added",
        "occurred_at": None,
        "observed_at": "2026-11-02T00:00:00Z",
        "actor": {
            "kind": "worktree",
            "id": "full-worktree",
            "qualification": "authoritative",
            "session_id": "head",
        },
        "correlation": dict.fromkeys(c.CORRELATION_FIELDS),
        "relations": [{"kind": "resource", "ref": "resource:child", "qualification": "reported"}],
        "record_fence": {"head_revision": 1} if fence is None else fence,
        "source_ref": {
            "source": "canonical-record",
            "generation": "record:one",
            "record_id": "save:one",
            "payload_ref": None,
        },
        "summary": "[bold]literal source prose",
        "details": None,
        "reason": None,
        "phase": None,
        "payload": {"claim": "resource:child"},
    }


def stored(item, current, seq):
    return {
        **deepcopy(item),
        "event_id": f"event:{seq}",
        "cursor": c.position(current, seq),
        "head_session_id": current.head_session_id,
    }


def snapshot(current, tip=0, items=None, base=None):
    return c.frame(
        "snapshot",
        current,
        c.position(current, tip),
        {
            "availability": "unknown" if current.head_session_id is None else "observed",
            "completeness": "unknown",
            "durability": "committed",
            "history_scope": "retained-range",
            "history_floor": c.position(current, 0),
            "replay_barrier": c.position(current, tip),
            "base_cursor": base,
            "entries": [] if items is None else items,
            "replay_complete": True,
            "facts": None if current.head_session_id is None else {"claims": {"held_count": 1}},
            "observed_at": "2026-11-02T00:00:00Z",
        },
    )


def test_opaque_position_matches_namespace_and_incarnation_not_file_offset():
    current = source()
    token = c.position(current, 19)
    assert c.sequence(token, current) == 19
    assert c.position(source(resident="c" * 32), 19) == token
    with pytest.raises(c.ObservationError):
        c.sequence(token, source(project="another"))
    with pytest.raises(c.ObservationError) as error:
        c.sequence(token, source(journal="d" * 32))
    assert error.value.code == "source-replaced"
    for value in ("line:19", "19", token + "!", token + "="):
        with pytest.raises(c.ObservationError):
            c.sequence(value, current)


@pytest.mark.parametrize("value", [True, -1, c.MAX_POSITION + 1, 1.0])
def test_position_rejects_coercion_and_overflow(value):
    with pytest.raises(c.ObservationError):
        c.position(source(), value)


@pytest.mark.parametrize("bad", [[], {}, None, False, "invented"])
def test_nonstring_or_unknown_frame_kind_raises_domain_error(bad):
    with pytest.raises(c.ObservationError):
        c.validate_frame({"type": bad, "contract_version": 1})


def test_persisted_entry_requires_explicit_post_save_record_fence():
    value = entry()
    with pytest.raises(c.ObservationError):
        c.entry(value)
    c.entry(value, post_save=True)
    value["record_fence"] = {}
    with pytest.raises(c.ObservationError):
        c.entry(value, post_save=True)


def test_count_change_is_not_claim_added_or_canonical_execution():
    value = entry(evidence="observed-change")
    value["payload"] = {"held_count_before": 1, "held_count_after": 2}
    with pytest.raises(c.ObservationError):
        c.entry(value, post_save=True)
    value["kind"] = "observed-change"
    c.entry(value)
    reported = entry("contribution-reported", "reported-lifecycle")
    with pytest.raises(c.ObservationError):
        c.entry(reported)
    reported["actor"]["qualification"] = "reported"
    c.entry(reported)


def test_retained_entries_need_identified_record_and_literal_refs():
    value = entry(evidence="retained-event")
    value["source_ref"]["record_id"] = None
    with pytest.raises(c.ObservationError):
        c.entry(value)
    value["source_ref"]["record_id"] = "original-event:17"
    c.entry(value)
    assert value["summary"] == "[bold]literal source prose"
    value["relations"][0]["ref"] = "[child](relative/path)"
    with pytest.raises(c.ObservationError):
        c.entry(value)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"kind":1,"kind":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b"\xff",
        b'{"missing":',
        b" " * (c.FRAME_BYTES + 1),
    ],
)
def test_decode_rejects_malformed_nonfinite_duplicate_and_oversized(raw):
    with pytest.raises(c.ObservationError):
        c.decode(raw)


@pytest.mark.parametrize("value", ["{}", None, 1, [], {}, bytearray(b"{}"), memoryview(b"{}")])
def test_wire_decoder_rejects_nonbytes_with_domain_error(value):
    with pytest.raises(c.ObservationError, match="must be bytes"):
        c.decode(value)


def test_internal_encoded_text_requires_explicit_utf8_handoff():
    value = {"label": "\u6f22\U0001f680"}
    text = c.encode(value)
    assert isinstance(text, str)
    with pytest.raises(c.ObservationError):
        c.decode(text)
    assert c.decode(c.utf8(text)) == value


@pytest.mark.parametrize(
    "value",
    [
        {"label": "\ud800"},
        {"\udfff": "value"},
        {"nested": [{"label": "\udc00"}]},
    ],
)
def test_surrogate_json_keys_and_values_fail_at_contract_boundary(value):
    for validate in (c.bounded, c.encode):
        with pytest.raises(c.ObservationError, match="malformed Unicode"):
            validate(value)


@pytest.mark.parametrize("value", ["\ud800", "record:\udfff"])
def test_surrogate_references_raise_observation_error_not_unicode_error(value):
    with pytest.raises(c.ObservationError, match="malformed Unicode"):
        c.reference(value, "source reference")


@pytest.mark.parametrize("raw", [b'{"\\ud800":"value"}', b'{"value":"\\udfff"}'])
def test_escaped_wire_surrogates_are_not_accepted_as_valid_utf8_json_values(raw):
    with pytest.raises(c.ObservationError, match="malformed Unicode"):
        c.decode(raw)


def test_limits_measure_utf8_bytes_not_python_character_count(monkeypatch):
    assert c.reference("\u00e9" * 256, "reference") == "\u00e9" * 256
    with pytest.raises(c.ObservationError):
        c.reference("\u00e9" * 257, "reference")
    c.bounded("\U0001f680" * 1024)
    with pytest.raises(c.ObservationError):
        c.bounded("\U0001f680" * 1025)
    text = "\u6f22" * 1300
    assert len(text) < 2000 < len(c.utf8(text))
    with pytest.raises(c.ObservationError) as error:
        c.encode({"label": text}, limit=2000)
    assert error.value.code == "oversized"
    monkeypatch.setattr(
        c.json, "loads", lambda *a, **kw: pytest.fail("parsed oversized wire bytes")
    )
    with pytest.raises(c.ObservationError) as error:
        c.decode(b" " * (c.FRAME_BYTES + 1))
    assert error.value.code == "oversized"


def test_utf8_text_member_depth_and_body_bounds_are_independent():
    value = entry("observed-change", "observed-change")
    value["summary"] = "\U0001f680" * 1025
    with pytest.raises(c.ObservationError):
        c.entry(value)
    value = entry("observed-change", "observed-change")
    value["payload"] = {str(i): None for i in range(129)}
    with pytest.raises(c.ObservationError):
        c.entry(value)
    value["payload"] = {}
    nested = []
    for _ in range(17):
        nested = [nested]
    value["payload"]["nested"] = nested
    with pytest.raises(c.ObservationError):
        c.entry(value)
    with pytest.raises(c.ObservationError):
        c.encode(["x" * 4000] * 9, c.BODY_BYTES)


def test_snapshot_barrier_and_delta_replay_are_atomic_not_coalesced_history():
    current = source()
    events = [stored(entry(), current, seq) for seq in (1, 2)]
    first = snapshot(current, 2, events, c.position(current, 0))
    c.validate_frame(first)
    assert c.decode(c.encode(first).encode("utf-8")) == first
    missing = deepcopy(first)
    missing["entries"] = events[1:]
    with pytest.raises(c.ObservationError):
        c.validate_frame(missing)
    for field, value in (("replay_barrier", c.position(current, 1)), ("contract_version", 2)):
        changed = {**first, field: value}
        with pytest.raises(c.ObservationError):
            c.validate_frame(changed)


def test_page_count_and_unknown_head_do_not_claim_complete_lifetime():
    current = source(head=None)
    value = snapshot(current)
    value.update(availability="unknown", facts=None)
    c.validate_frame(value)
    value["history_scope"] = "lifetime"
    with pytest.raises(c.ObservationError):
        c.validate_frame(value)
    value = snapshot(source())
    value["entries"] = [stored(entry(), source(), i) for i in range(1, 66)]
    with pytest.raises(c.ObservationError):
        c.validate_frame(value)


def test_requests_are_exact_distinct_snapshot_and_history_operations():
    request = {
        "contract_version": 1,
        "project": "project",
        "worktree_id": "full-worktree",
        "expected_head_session_id": None,
        "after_cursor": None,
    }
    selected, _ = c.request(request)
    assert selected.expected_head_session_id is None
    with pytest.raises(c.ObservationError):
        c.request({**request, "limit": 4})
    page = {key: value for key, value in request.items() if key != "after_cursor"}
    page.update(source_generation="a" * 32, before_cursor=None, limit=64)
    c.request(page, history=True)
    with pytest.raises(c.ObservationError):
        c.request({**page, "limit": True}, history=True)
    with pytest.raises(c.ObservationError):
        c.request({**request, "worktree_id": "../full-worktree"})
