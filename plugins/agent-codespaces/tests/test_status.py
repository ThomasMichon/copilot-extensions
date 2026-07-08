"""Tests for the CodeSpace eligibility status store (state redirected to tmp)."""

from __future__ import annotations

import pytest

from agent_codespaces import status as status_mod


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Redirect status state to a tmp dir so tests never touch real state."""
    monkeypatch.setattr(status_mod, "STATUS_FILE", tmp_path / "codespace-status.json")
    monkeypatch.setattr(status_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(status_mod, "_LOCK_FILE", tmp_path / "codespace-status.lock")
    monkeypatch.setattr(status_mod, "ensure_runtime_dir", lambda: None)
    return tmp_path


def test_unmarked_is_none(store):
    assert status_mod.get_status("cs-one") is None
    assert status_mod.is_eligible("cs-one") is False


def test_set_and_get_recovered(store):
    rec = status_mod.set_status("cs-one", status_mod.STATE_RECOVERED, "finalized")
    assert rec.state == status_mod.STATE_RECOVERED
    assert rec.reason == "finalized"
    got = status_mod.get_status("cs-one")
    assert got is not None
    assert got.state == status_mod.STATE_RECOVERED
    assert status_mod.is_eligible("cs-one") is True


def test_set_prunable(store):
    status_mod.set_status("cs-one", status_mod.STATE_PRUNABLE, "pr merged")
    assert status_mod.is_eligible("cs-one") is True
    prunable = status_mod.list_by_state(status_mod.STATE_PRUNABLE)
    assert [s.codespace for s in prunable] == ["cs-one"]


def test_active_drops_the_marker(store):
    status_mod.set_status("cs-one", status_mod.STATE_RECOVERED)
    status_mod.set_status("cs-one", status_mod.STATE_ACTIVE)
    assert status_mod.get_status("cs-one") is None
    assert status_mod.is_eligible("cs-one") is False


def test_clear_status(store):
    status_mod.set_status("cs-one", status_mod.STATE_PRUNABLE)
    assert status_mod.clear_status("cs-one") is True
    assert status_mod.get_status("cs-one") is None
    # clearing an unmarked box is a no-op
    assert status_mod.clear_status("cs-one") is False


def test_promotion_recovered_to_prunable(store):
    status_mod.set_status("cs-one", status_mod.STATE_RECOVERED)
    status_mod.set_status("cs-one", status_mod.STATE_PRUNABLE, "promoted")
    got = status_mod.get_status("cs-one")
    assert got.state == status_mod.STATE_PRUNABLE
    assert got.reason == "promoted"
    # only one record per codespace
    assert len(status_mod.list_status()) == 1


def test_multiple_codespaces_independent(store):
    status_mod.set_status("cs-one", status_mod.STATE_RECOVERED)
    status_mod.set_status("cs-two", status_mod.STATE_PRUNABLE)
    by_name = {s.codespace: s.state for s in status_mod.list_status()}
    assert by_name == {
        "cs-one": status_mod.STATE_RECOVERED,
        "cs-two": status_mod.STATE_PRUNABLE,
    }


def test_unknown_state_raises(store):
    with pytest.raises(RuntimeError, match="unknown codespace state"):
        status_mod.set_status("cs-one", "bogus")


def test_set_requires_name(store):
    with pytest.raises(RuntimeError, match="requires a CodeSpace name"):
        status_mod.set_status("", status_mod.STATE_RECOVERED)


def test_read_tolerates_unknown_keys(store):
    # A record written by a newer version with an extra field must still load.
    STATUS_FILE = status_mod.STATUS_FILE
    STATUS_FILE.write_text(
        '{"cs-one": {"codespace": "cs-one", "state": "prunable", '
        '"state_at": 1.0, "reason": "x", "future_field": 42}}',
        encoding="utf-8",
    )
    got = status_mod.get_status("cs-one")
    assert got is not None
    assert got.state == status_mod.STATE_PRUNABLE


def test_read_corrupt_is_empty(store):
    status_mod.STATUS_FILE.write_text("{not json", encoding="utf-8")
    assert status_mod.list_status() == []
