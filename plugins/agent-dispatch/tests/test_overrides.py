"""Tests for the operator override store (the supervised-unit kill-switch)."""

from __future__ import annotations

import threading
import time

from agent_dispatch import overrides as ov


def test_load_missing_returns_empty(tmp_path):
    assert ov.load_overrides(tmp_path / "nope.json") == {}


def test_set_load_clear_roundtrip(tmp_path):
    p = tmp_path / "overrides.json"
    rec = ov.set_override(p, "declared:x:y", reason="misbehaving", now=123.0)
    assert rec == {"disabled": True, "reason": "misbehaving", "at": 123.0}
    loaded = ov.load_overrides(p)
    assert loaded == {"declared:x:y": {"disabled": True, "reason": "misbehaving", "at": 123.0}}
    assert ov.overridden_off_ids(loaded) == {"declared:x:y"}
    assert ov.clear_override(p, "declared:x:y") is True
    assert ov.load_overrides(p) == {}
    # clearing a missing id is a no-op False (not an error)
    assert ov.clear_override(p, "declared:x:y") is False


def test_set_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "dir" / "overrides.json"
    ov.set_override(p, "a")
    assert p.is_file()
    assert ov.overridden_off_ids(ov.load_overrides(p)) == {"a"}


def test_disabled_false_is_not_off():
    overrides = {"a": {"disabled": False}, "b": {"disabled": True}, "c": {}}
    assert ov.overridden_off_ids(overrides) == {"b"}


def test_load_tolerates_malformed(tmp_path):
    p = tmp_path / "overrides.json"
    p.write_text("not json at all", encoding="utf-8")
    assert ov.load_overrides(p) == {}
    # a JSON non-object is indeterminate -> empty
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert ov.load_overrides(p) == {}
    # a well-formed object with a non-dict entry drops that entry
    p.write_text('{"a": {"disabled": true}, "b": "nope"}', encoding="utf-8")
    assert ov.load_overrides(p) == {"a": {"disabled": True}}


def test_set_override_replaces_existing(tmp_path):
    p = tmp_path / "overrides.json"
    ov.set_override(p, "a", reason="first", now=1.0)
    rec = ov.set_override(p, "a", reason="second", now=2.0)
    assert rec["reason"] == "second" and rec["at"] == 2.0
    assert ov.load_overrides(p) == {"a": {"disabled": True, "reason": "second", "at": 2.0}}


def test_save_is_atomic_leaves_no_temp(tmp_path):
    p = tmp_path / "overrides.json"
    ov.set_override(p, "a")
    # the temp file used for the atomic replace is cleaned up
    leftovers = [q.name for q in tmp_path.iterdir() if q.name.startswith(".overrides-")]
    assert leftovers == []


def test_mutations_are_serialized_without_losing_unrelated_entries(tmp_path):
    path = tmp_path / "overrides.json"
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first(overrides):
        overrides["first"] = {"disabled": True}
        first_entered.set()
        assert release_first.wait(2)

    def second(overrides):
        second_entered.set()
        overrides["second"] = {"disabled": True}

    thread_one = threading.Thread(target=lambda: ov.mutate_overrides(path, first))
    thread_two = threading.Thread(target=lambda: ov.mutate_overrides(path, second))
    thread_one.start()
    assert first_entered.wait(2)
    thread_two.start()
    time.sleep(0.1)
    assert not second_entered.is_set()
    release_first.set()
    thread_one.join(2)
    thread_two.join(2)

    assert not thread_one.is_alive()
    assert not thread_two.is_alive()
    assert ov.load_overrides(path) == {
        "first": {"disabled": True},
        "second": {"disabled": True},
    }
