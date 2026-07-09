"""Tests for the worktree-style finalize/prune lifecycle (status marker)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_codespaces import lease as lease_mod
from agent_codespaces import status as status_mod
from agent_codespaces.__main__ import main
from agent_codespaces.lifecycle import CodespaceInfo


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Redirect BOTH the status and lease stores to tmp so tests never touch
    real host state (finalize/prune touch both)."""
    monkeypatch.setattr(status_mod, "STATUS_FILE", tmp_path / "codespace-status.json")
    monkeypatch.setattr(status_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(status_mod, "_LOCK_FILE", tmp_path / "codespace-status.lock")
    monkeypatch.setattr(status_mod, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(lease_mod, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease_mod, "_LOCK_FILE", tmp_path / "leases.lock")
    monkeypatch.setattr(lease_mod, "ensure_runtime_dir", lambda: None)
    return tmp_path


def _info(name: str, state: str) -> CodespaceInfo:
    return CodespaceInfo(
        name=name, display_name=name, repository="r", branch="", state=state, machine="m",
    )


# --- finalize: recover -> stop -> mark recovered ---------------------------

def test_finalize_marks_recovered_and_stops(store):
    with patch(
        "agent_codespaces.__main__.sync_codespace_sessions",
        return_value={"ok": True, "session_count": 2, "detail": "ok"},
    ), patch("agent_codespaces.__main__.stop_codespace", return_value=True) as stop:
        rc = main(["finalize", "cs-one"])
    assert rc == 0
    stop.assert_called_once_with("cs-one")
    st = status_mod.get_status("cs-one")
    assert st is not None and st.state == status_mod.STATE_RECOVERED


def test_finalize_failed_recovery_does_not_mark(store):
    with patch(
        "agent_codespaces.__main__.sync_codespace_sessions",
        return_value={"ok": False, "detail": "could not connect"},
    ), patch("agent_codespaces.__main__.stop_codespace", return_value=True):
        rc = main(["finalize", "cs-one"])
    assert rc == 1
    # A failed recovery must not leave a misleading 'recovered' marker.
    assert status_mod.get_status("cs-one") is None


def test_finalize_delete_clears_marker(store):
    status_mod.set_status("cs-one", status_mod.STATE_RECOVERED)
    with patch(
        "agent_codespaces.__main__.sync_codespace_sessions",
        return_value={"ok": True, "session_count": 0, "detail": "x"},
    ), patch("agent_codespaces.__main__.delete_codespace") as dele:
        rc = main(["finalize", "cs-one", "--delete"])
    assert rc == 0
    dele.assert_called_once_with("cs-one", force=False)
    assert status_mod.get_status("cs-one") is None


# --- prune: only 'prunable', with a final recovery -------------------------

def test_prune_noop_when_none(store, capsys):
    rc = main(["prune"])
    assert rc == 0
    assert "No prune-eligible" in capsys.readouterr().out


def test_prune_dry_run_lists_but_keeps(store, capsys):
    status_mod.set_status("cs-one", status_mod.STATE_PRUNABLE, "pr merged")
    with patch(
        "agent_codespaces.__main__.list_codespaces",
        return_value=[_info("cs-one", "Shutdown")],
    ):
        rc = main(["prune", "--dry-run"])
    assert rc == 0
    assert "cs-one" in capsys.readouterr().out
    # dry-run must not delete or clear the marker
    assert status_mod.get_status("cs-one").state == status_mod.STATE_PRUNABLE


def test_prune_deletes_prunable_and_clears_marker(store):
    status_mod.set_status("cs-one", status_mod.STATE_PRUNABLE, "pr merged")
    with patch(
        "agent_codespaces.__main__.list_codespaces",
        return_value=[_info("cs-one", "Shutdown")],
    ), patch(
        "agent_codespaces.__main__.sync_codespace_sessions",
        return_value={"ok": True, "session_count": 0, "detail": "x"},
    ), patch("agent_codespaces.__main__.delete_codespace") as dele:
        rc = main(["prune"])
    assert rc == 0
    dele.assert_called_once_with("cs-one", force=False)
    assert status_mod.get_status("cs-one") is None


def test_prune_ignores_recovered_boxes(store):
    # A 'recovered' (not yet promoted) box is never a prune candidate.
    status_mod.set_status("cs-one", status_mod.STATE_RECOVERED)
    with patch("agent_codespaces.__main__.delete_codespace") as dele:
        rc = main(["prune"])
    assert rc == 0
    dele.assert_not_called()
    assert status_mod.get_status("cs-one").state == status_mod.STATE_RECOVERED


def test_prune_clears_stale_marker_for_missing_box(store):
    status_mod.set_status("gone", status_mod.STATE_PRUNABLE)
    with patch(
        "agent_codespaces.__main__.list_codespaces", return_value=[],
    ), patch("agent_codespaces.__main__.delete_codespace") as dele:
        rc = main(["prune"])
    assert rc == 0
    dele.assert_not_called()  # nothing to delete; just drop the stale marker
    assert status_mod.get_status("gone") is None


# --- idempotent recovery: skip booting a Shutdown box ----------------------

def test_recovery_skips_boot_when_shutdown():
    from agent_codespaces import sessions

    with patch(
        "agent_codespaces.lifecycle.list_codespaces",
        return_value=[_info("cs-one", "Shutdown")],
    ):
        res = sessions.sync_codespace_sessions("cs-one", skip_if_shutdown=True)
    assert res["ok"] is True
    assert res.get("skipped") is True
    assert "Shutdown" in res["detail"]


# --- reuse un-marks --------------------------------------------------------

def test_borrow_clears_eligibility_marker(store):
    status_mod.set_status("cs-one", status_mod.STATE_RECOVERED)
    rc = main(["borrow", "effort-x", "cs-one"])
    assert rc == 0
    assert status_mod.get_status("cs-one") is None


# --- mark: skill-side promotion lever --------------------------------------

def test_mark_promotes_recovered_to_prunable(store):
    status_mod.set_status("cs-one", status_mod.STATE_RECOVERED)
    rc = main(["mark", "cs-one", "prunable", "--reason", "PR merged"])
    assert rc == 0
    st = status_mod.get_status("cs-one")
    assert st.state == status_mod.STATE_PRUNABLE
    assert st.reason == "PR merged"


def test_mark_active_clears(store):
    status_mod.set_status("cs-one", status_mod.STATE_PRUNABLE)
    rc = main(["mark", "cs-one", "active"])
    assert rc == 0
    assert status_mod.get_status("cs-one") is None


# --- list surfaces the eligibility marker ----------------------------------

def test_list_json_includes_eligibility(store, capsys):
    import json

    status_mod.set_status("cs-one", status_mod.STATE_PRUNABLE)
    with patch(
        "agent_codespaces.__main__.list_codespaces",
        return_value=[_info("cs-one", "Shutdown"), _info("cs-two", "Available")],
    ):
        rc = main(["list", "--json"])
    assert rc == 0
    data = {d["name"]: d["eligibility"] for d in json.loads(capsys.readouterr().out)}
    assert data == {"cs-one": "prunable", "cs-two": "active"}


# --- create quota auto-retry (reclaim + retry once) ------------------------

def test_reclaim_total_limit_prunes_prunable(store):
    status_mod.set_status("cs-old", status_mod.STATE_PRUNABLE, "merged")
    with patch(
        "agent_codespaces.__main__.sync_codespace_sessions",
        return_value={"ok": True, "session_count": 0, "detail": "x"},
    ), patch("agent_codespaces.__main__.delete_codespace") as dele:
        from agent_codespaces.__main__ import _reclaim_for_quota

        note = _reclaim_for_quota("You have reached the maximum number of codespaces")
    assert note is not None and "cs-old" in note
    dele.assert_called_once_with("cs-old", force=False)
    assert status_mod.get_status("cs-old") is None


def test_reclaim_running_limit_stops_eligible_running(store):
    status_mod.set_status("cs-run", status_mod.STATE_RECOVERED)
    with patch(
        "agent_codespaces.__main__.list_codespaces",
        return_value=[_info("cs-run", "Available")],
    ), patch("agent_codespaces.__main__.stop_codespace") as stop:
        from agent_codespaces.__main__ import _reclaim_for_quota

        note = _reclaim_for_quota("You have too many codespaces running. Please stop some")
    assert note is not None and "cs-run" in note
    stop.assert_called_once_with("cs-run")


def test_reclaim_running_limit_ignores_unmarked_running(store):
    # An unmarked (in-use) running box must never be auto-stopped.
    with patch(
        "agent_codespaces.__main__.list_codespaces",
        return_value=[_info("busy", "Available")],
    ), patch("agent_codespaces.__main__.stop_codespace") as stop:
        from agent_codespaces.__main__ import _reclaim_for_quota

        note = _reclaim_for_quota("too many codespaces running")
    assert note is None
    stop.assert_not_called()


def test_reclaim_unknown_error_returns_none(store):
    from agent_codespaces.__main__ import _reclaim_for_quota

    assert _reclaim_for_quota("some unrelated failure") is None


def test_create_retries_after_reclaim(store):
    info = _info("new-cs", "Available")
    with patch("agent_codespaces.__main__.load_merged_config", return_value=object()), \
         patch("agent_codespaces.__main__._reclaim_for_quota", return_value="pruned x"), \
         patch(
             "agent_codespaces.__main__.create_codespace",
             side_effect=[RuntimeError("maximum number of codespaces"), info],
         ) as cc:
        rc = main(["create", "owner/repo", "--no-wait"])
    assert rc == 0
    assert cc.call_count == 2


def test_create_quota_no_reclaim_fails(store):
    with patch("agent_codespaces.__main__.load_merged_config", return_value=object()), \
         patch("agent_codespaces.__main__._reclaim_for_quota", return_value=None), \
         patch(
             "agent_codespaces.__main__.create_codespace",
             side_effect=RuntimeError("maximum number of codespaces"),
         ):
        rc = main(["create", "owner/repo", "--no-wait"])
    assert rc == 1
