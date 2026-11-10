"""Tests for the operator override store (the supervised-unit kill-switch)."""

from __future__ import annotations

import json
import threading
import time

import pytest

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
    second_started = threading.Event()
    second_entered = threading.Event()

    def first(overrides):
        overrides["first"] = {"disabled": True}
        first_entered.set()
        assert release_first.wait(10)

    def second(overrides):
        second_entered.set()
        overrides["second"] = {"disabled": True}

    def run_second():
        second_started.set()
        ov.mutate_overrides(path, second)

    thread_one = threading.Thread(target=lambda: ov.mutate_overrides(path, first))
    thread_two = threading.Thread(target=run_second)
    thread_one.start()
    assert first_entered.wait(10)
    thread_two.start()
    assert second_started.wait(10)
    time.sleep(0.1)
    assert not second_entered.is_set()
    release_first.set()
    thread_one.join(10)
    thread_two.join(10)

    assert not thread_one.is_alive()
    assert not thread_two.is_alive()
    assert ov.load_overrides(path) == {
        "first": {"disabled": True},
        "second": {"disabled": True},
    }


def test_standing_deny_selectors_clear_and_read_only_admission(tmp_path):
    path = tmp_path / "overrides.json"
    lane = ov.StandingAttachmentSelector("owner", "charter", "first")
    other_lane = ov.StandingAttachmentSelector("owner", "charter", "second")
    all_lanes = ov.StandingAttachmentSelector("owner", "charter")
    with ov.standing_attachment_admission(path, lane) as policy:
        original_revision = policy["policy_revision"]
    assert not path.exists()

    unrelated = ov.set_override(path, "unrelated", reason="preserve")
    ov.set_standing_attachment_deny(path, lane, reason="maintenance", now=10)
    with pytest.raises(ValueError, match="denied locally: maintenance"):
        with ov.standing_attachment_admission(path, lane):
            pytest.fail("denied admission ran")
    with ov.standing_attachment_admission(path, other_lane) as policy:
        assert policy["policy_revision"] == original_revision
    ov.set_standing_attachment_deny(path, all_lanes, reason="all lanes", now=11)
    assert ov.clear_standing_attachment_deny(path, lane)
    with pytest.raises(ValueError, match="all lanes"):
        with ov.standing_attachment_admission(path, lane):
            pytest.fail("narrow clear bypassed inherited veto")
    assert ov.clear_standing_attachment_deny(path, all_lanes)
    assert not ov.clear_standing_attachment_deny(path, all_lanes)
    before = path.read_bytes()
    with ov.standing_attachment_admission(path, lane) as policy:
        assert policy["policy_revision"] == original_revision
    assert path.read_bytes() == before
    assert ov.load_overrides(path) == {"unrelated": unrelated}


@pytest.mark.parametrize("damage", [
    "json", "entry", "version", "selector", "flag", "unknown", "duplicate-veto", "duplicate-selector",
])
def test_standing_policy_corruption_holds_without_rewriting(tmp_path, damage):
    path = tmp_path / "overrides.json"
    selector = ov.StandingAttachmentSelector("owner", "charter", "lane")
    ov.set_standing_attachment_deny(path, selector, reason="stop")
    data = ov.load_overrides(path)
    record = data[selector.override_id]
    if damage == "json":
        path.write_text("{", encoding="utf-8")
    elif damage == "duplicate-veto":
        path.write_text(
            json.dumps(data).replace('"disabled": true', '"disabled": true, "disabled": false'),
            encoding="utf-8",
        )
    elif damage == "duplicate-selector":
        path.write_text(
            "{" + json.dumps(selector.override_id) + ":" + json.dumps(record) + ","
            + json.dumps(selector.override_id) + ":" + json.dumps({**record, "disabled": False}) + "}",
            encoding="utf-8",
        )
    else:
        if damage == "entry":
            data["unknown"] = None
        elif damage == "version":
            record["version"] = 2
        elif damage == "selector":
            record["selector"]["charter_key"] = "other"
        elif damage == "flag":
            record["disabled"] = 0
        else:
            record["allow"] = True
        ov.save_overrides(path, data)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        with ov.standing_attachment_admission(path, selector):
            pytest.fail("corrupt policy admitted work")
    with pytest.raises(ValueError):
        ov.clear_standing_attachment_deny(path, selector)
    assert path.read_bytes() == before


def test_standing_policy_unreadable_is_not_absent(tmp_path, monkeypatch):
    def unreadable(*args, **kwargs):
        raise PermissionError("test policy cannot be read")

    monkeypatch.setattr(ov.Path, "read_text", unreadable)
    with pytest.raises(PermissionError):
        with ov.standing_attachment_admission(
            tmp_path / "overrides.json", ov.StandingAttachmentSelector("owner", "charter", "lane"),
        ):
            pytest.fail("unreadable policy admitted work")


def test_standing_deny_waits_for_admission_and_preserves_occupant(tmp_path):
    path = tmp_path / "overrides.json"
    selector = ov.StandingAttachmentSelector("owner", "charter", "lane")
    entered = threading.Event()
    finished = threading.Event()
    failures = []
    occupant = {}

    def deny():
        entered.set()
        try:
            ov.set_standing_attachment_deny(path, selector, reason="stop future admissions")
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()

    with ov.standing_attachment_admission(path, selector) as policy:
        thread = threading.Thread(target=deny)
        thread.start()
        assert entered.wait(5)
        assert not finished.wait(0.1)
        occupant.update({"reservation": "original-native-reservation", "policy": policy})
    thread.join(10)
    assert not thread.is_alive() and not failures
    assert occupant["reservation"] == "original-native-reservation"
    assert occupant["policy"] == policy
    with pytest.raises(ValueError, match="denied locally"):
        with ov.standing_attachment_admission(path, selector):
            pytest.fail("later admission bypassed the deny")


def test_standing_admission_waits_for_in_progress_deny(tmp_path):
    path = tmp_path / "overrides.json"
    selector = ov.StandingAttachmentSelector("owner", "charter", "lane")
    entered = threading.Event()
    results = []

    def admit():
        entered.set()
        try:
            with ov.standing_attachment_admission(path, selector):
                results.append("admitted")
        except ValueError as exc:
            results.append(str(exc))

    with ov.locked_overrides(path) as overrides:
        thread = threading.Thread(target=admit)
        thread.start()
        assert entered.wait(5)
        overrides[selector.override_id] = {
            "version": 1, "selector": {"namespace": "owner", "charter_key": "charter", "lane_key": "lane"},
            "disabled": True, "reason": "deny ordered first", "at": 1,
        }
        ov.save_overrides(path, overrides)
    thread.join(10)
    assert not thread.is_alive()
    assert len(results) == 1 and "denied locally: deny ordered first" in results[0]
