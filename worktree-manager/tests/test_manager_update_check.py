"""Tests for `manager_update_check` -- the Manager's own (as opposed to the
engine/marketplace payload's) cached update-availability check.

See the module's own docstring for why this exists as a SEPARATE signal from
`agent_worktrees.update_stage.indicator_state()`: that function reflects the
engine plugin's staged marketplace payload, not whether a newer Worktree
Manager release exists -- conflating the two in the picker topbar read as
"the checkmark says I'm current" when it never checked the Manager's own
version at all (confirmed live: an operator running a stale build saw a
plain checkmark next to their outdated version string).
"""

from __future__ import annotations

import time

from worktree_manager import manager_update_check as muc
from worktree_manager import self_install


def test_read_status_is_empty_dict_when_nothing_persisted(tmp_path):
    assert muc.read_status(tmp_path) == {}


def test_should_check_is_true_when_nothing_persisted_yet(tmp_path):
    assert muc.should_check(tmp_path) is True


def test_should_check_is_false_within_the_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(self_install, "current_version", lambda root=None: "1.0.0")
    monkeypatch.setattr(self_install, "fetch_remote_version", lambda root=None: "1.0.0")
    muc.check_now(tmp_path)
    assert muc.should_check(tmp_path) is False


def test_should_check_is_true_once_the_interval_has_elapsed(tmp_path, monkeypatch):
    monkeypatch.setattr(self_install, "current_version", lambda root=None: "1.0.0")
    monkeypatch.setattr(self_install, "fetch_remote_version", lambda root=None: "1.0.0")
    muc.check_now(tmp_path)
    status = muc.read_status(tmp_path)
    status["checked_at"] = time.time() - muc.CHECK_INTERVAL_SECS - 1
    muc._write_status(status, tmp_path)
    assert muc.should_check(tmp_path) is True


def test_check_now_marks_available_when_remote_is_newer(tmp_path, monkeypatch):
    monkeypatch.setattr(self_install, "current_version", lambda root=None: "0.1.0-dev54")
    monkeypatch.setattr(
        self_install, "fetch_remote_version", lambda root=None: "0.1.0-dev55")
    data = muc.check_now(tmp_path)
    assert data["available"] is True
    assert data["local_version"] == "0.1.0-dev54"
    assert data["remote_version"] == "0.1.0-dev55"
    assert muc.indicator_state(tmp_path) == "available"


def test_check_now_marks_current_when_versions_match(tmp_path, monkeypatch):
    monkeypatch.setattr(self_install, "current_version", lambda root=None: "0.1.0-dev55")
    monkeypatch.setattr(
        self_install, "fetch_remote_version", lambda root=None: "0.1.0-dev55")
    muc.check_now(tmp_path)
    assert muc.indicator_state(tmp_path) == "current"


def test_check_now_never_raises_when_the_fetch_fails(tmp_path, monkeypatch):
    """A network failure (already degraded to ``None`` by
    ``fetch_remote_version``) must persist a harmless, non-crashing status,
    not propagate -- a background poll depends on this never raising."""
    monkeypatch.setattr(self_install, "current_version", lambda root=None: "0.1.0-dev54")
    monkeypatch.setattr(self_install, "fetch_remote_version", lambda root=None: None)
    data = muc.check_now(tmp_path)
    assert data["available"] is False
    assert data["remote_version"] is None
    assert muc.indicator_state(tmp_path) == "idle"


def test_indicator_state_is_idle_before_any_check(tmp_path):
    assert muc.indicator_state(tmp_path) == "idle"


def test_indicator_state_never_triggers_a_network_check(tmp_path, monkeypatch):
    """indicator_state() must be pure read-only -- never call
    fetch_remote_version itself. A caller drives check_now() separately,
    gated by should_check()."""
    def boom(root=None):
        raise AssertionError("indicator_state must not fetch the network itself")

    monkeypatch.setattr(self_install, "fetch_remote_version", boom)
    assert muc.indicator_state(tmp_path) == "idle"
