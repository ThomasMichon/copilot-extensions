"""Small, deterministic v1 acquisition-hint contract controls."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_index.indexing.change_hints import ChangeHints, MetadataRevision, RefRange


def hints(**kwargs):
    return ChangeHints(
        **{"source": "git:sample", "canonical_family": "git", "account_id": "account-a", **kwargs}
    )


def test_lossless_deterministic_union_and_roundtrip():
    first = hints(
        item_ids=("b", "a", "a"), ref_ranges=(RefRange("base", "target-1"),),
        event_ids=("event-1",), metadata_revisions=(MetadataRevision("a", "rev-1"),),
    )
    second = hints(
        item_ids=("c", "a"), ref_ranges=(RefRange("base", "target-2"),),
        event_ids=("event-2",), metadata_revisions=(MetadataRevision("a", "rev-2"),),
    )
    merged = first.union(second)
    assert merged.item_ids == ("a", "b", "c")
    assert merged.ref_ranges == (RefRange("base", "target-1"), RefRange("base", "target-2"))
    assert merged.metadata_revisions == (MetadataRevision("a", "rev-1"), MetadataRevision("a", "rev-2"))
    assert merged.event_ids == ("event-1", "event-2")
    assert merged == second.union(first) == merged.union(first)
    assert ChangeHints.from_json(merged.to_json()) == merged
    assert merged.to_json() == second.union(first).to_json()
    with pytest.raises(FrozenInstanceError):
        first.source = "another"
    with pytest.raises(FrozenInstanceError):
        first.ref_ranges[0].base = "another"


@pytest.mark.parametrize("field,value", [
    ("source", "git:alias"), ("canonical_family", "custom"), ("account_id", "account-b"),
])
def test_union_never_crosses_scope(field, value):
    with pytest.raises(ValueError, match="different scopes"):
        hints().union(hints(**{field: value}))


@pytest.mark.parametrize("kwargs", [
    {"version": 2}, {"version": True}, {"source": "all"}, {"source": ""},
    {"account_id": None}, {"canonical_family": " "}, {"item_ids": ["a"]},
    {"item_ids": (42,)}, {"event_ids": ("",)}, {"ref_ranges": ({"base": "a"},)},
    {"metadata_revisions": ("rev",)},
])
def test_constructor_rejects_untyped_or_unsupported_values(kwargs):
    with pytest.raises(ValueError):
        hints(**kwargs)


@pytest.mark.parametrize("value", [
    None, [], {}, {"snapshot_complete": True},
])
def test_decoder_does_not_infer_or_drop_fields(value):
    with pytest.raises(ValueError):
        ChangeHints.from_dict(value)


@pytest.mark.parametrize("field,value", [
    ("version", 0), ("item_ids", "item"), ("item_ids", [None]),
    ("ref_ranges", [{"base": "a", "target": "b", "unknown": True}]),
    ("ref_ranges", [{"base": "a"}]),
    ("metadata_revisions", [{"item_id": "a", "revision": 1}]),
])
def test_decoder_is_strict(field, value):
    payload = hints().to_dict()
    payload[field] = value
    with pytest.raises(ValueError):
        ChangeHints.from_dict(payload)


def test_no_snapshot_or_delete_authority_and_no_duplicate_json_keys():
    payload = hints(event_ids=("notification-only",)).to_dict()
    payload["snapshot_complete"] = True
    with pytest.raises(ValueError):
        ChangeHints.from_dict(payload)
    with pytest.raises(ValueError, match="Duplicate"):
        ChangeHints.from_json('{"version":1,"version":2}')
    assert hints().item_ids == ()
