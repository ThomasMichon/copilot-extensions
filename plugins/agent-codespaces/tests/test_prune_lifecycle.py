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


@pytest.fixture
def quota_creation(store, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from agent_codespaces import __main__ as cli, account_binding, allocation, coordination, lifecycle
    from agent_codespaces.config import CodespacesConfig

    state = SimpleNamespace(rows=[], after_failure=[], attempts=0)
    monkeypatch.setattr(cli, "load_merged_config", CodespacesConfig)
    monkeypatch.setattr(cli, "validate_context", lambda: None)
    monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
    monkeypatch.setattr(cli, "_require_codespace_scope", lambda *a, **kw: None)
    monkeypatch.setattr(allocation, "allocation_context", lambda repo: ("selected-owner", {"GH_TOKEN": "fixture-token"}))
    monkeypatch.setattr(lifecycle, "_list_codespaces_under", lambda *a, **kw: list(state.rows))
    monkeypatch.setattr(cli.pool_mod, "codespaces_in_use", lambda names: {})
    monkeypatch.setattr(coordination, "list_leases", lambda: {})
    monkeypatch.setattr(coordination, "list_cleanliness", lambda: {})
    monkeypatch.setattr("agent_codespaces.driving_worktrees.resolve_current_workstream_box", lambda *a: None)
    monkeypatch.setattr(account_binding, "BINDINGS_FILE", store / "account-bindings.json")
    monkeypatch.setattr(account_binding, "_LOCK_FILE", store / "account-bindings.lock")
    monkeypatch.setattr(account_binding, "ensure_runtime_dir", lambda: None)
    account_binding.bind("cs-old", "selected-owner", "owner/repo")

    def create(*args, **kwargs):
        state.attempts += 1
        if state.attempts == 1:
            state.rows = list(state.after_failure)
            raise RuntimeError("maximum number of codespaces")
        return _info("new-cs", "Available")

    def delete(name, **kwargs):
        state.rows = [row for row in state.rows if row.name != name]

    state.create = Mock(side_effect=create)
    state.rescue = Mock(return_value={"ok": True})
    state.delete = Mock(side_effect=delete)
    monkeypatch.setattr(cli, "create_codespace", state.create)
    monkeypatch.setattr(cli, "sync_codespace_sessions", state.rescue)
    monkeypatch.setattr(cli, "delete_codespace", state.delete)
    return state


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

@pytest.mark.parametrize("force", [False, True])
def test_quota_rescue_wake_obeys_local_headroom_unless_explicitly_forced(quota_creation, force):
    old, busy = _info("cs-old", "Shutdown"), _info("busy", "Available")
    old.machine, busy.machine = "premiumLinux", "custom64core"
    old.account = busy.account = "selected-owner"
    quota_creation.after_failure = [old, busy]
    status_mod.set_status("cs-old", status_mod.STATE_PRUNABLE, "merged")
    extra = ["--force-create"] if force else []
    assert main(["create", "owner/repo", "--no-wait", *extra]) == (0 if force else 1)
    assert quota_creation.attempts == (2 if force else 1)
    assert quota_creation.rescue.call_count == int(force)
    assert quota_creation.delete.call_count == int(force)
    if not force:
        assert status_mod.get_status("cs-old") is not None


def test_quota_retry_rechecks_capacity_after_reclaim(quota_creation, monkeypatch):
    from agent_codespaces import __main__ as cli

    def reclaim(*args, **kwargs):
        busy = _info("concurrent", "Available")
        busy.machine, busy.account = "custom64core", "selected-owner"
        quota_creation.rows = [busy]
        return "freed an eligible slot"

    monkeypatch.setattr(cli, "_reclaim_for_quota", reclaim)
    assert main(["create", "owner/repo", "--no-wait"]) == 4
    assert quota_creation.attempts == 1


def test_quota_recovery_retries_when_wake_and_creation_fit(quota_creation):
    old = _info("cs-old", "Shutdown")
    old.machine, old.account = "premiumLinux", "selected-owner"
    quota_creation.after_failure = [old]
    status_mod.set_status("cs-old", status_mod.STATE_PRUNABLE, "merged")
    assert main(["create", "owner/repo", "--no-wait"]) == 0
    assert quota_creation.attempts == 2
    quota_creation.rescue.assert_called_once()
    quota_creation.delete.assert_called_once()


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


@pytest.mark.parametrize("running", [False, True])
def test_quota_reclaim_only_mutates_selected_account(store, monkeypatch, running):
    from agent_codespaces import __main__ as cli, allocation

    status_mod.set_status("foreign", status_mod.STATE_PRUNABLE)
    status_mod.set_status("selected", status_mod.STATE_PRUNABLE)
    monkeypatch.setattr(
        allocation, "list_account_codespaces",
        lambda account, **kwargs: [_info("selected", "Available" if running else "Shutdown")],
    )
    monkeypatch.setattr("agent_codespaces.account_binding.bound_account_or_raise", lambda name: "selected-owner")
    monkeypatch.setattr("agent_codespaces.owner_inventory.credential", lambda account: "selected-fixture-token")
    with patch.object(cli, "sync_codespace_sessions", return_value={"ok": True}) as rescue, \
         patch.object(cli, "delete_codespace") as delete, \
         patch.object(cli, "stop_codespace") as stop:
        note = cli._reclaim_for_quota(
            "too many codespaces running" if running else "maximum number of codespaces",
            account="selected-owner",
        )
    assert note is not None and "selected" in note
    if running:
        stop.assert_called_once_with(
            "selected", account="selected-owner", token="selected-fixture-token",
        )
        delete.assert_not_called()
    else:
        delete.assert_called_once_with(
            "selected", force=False, account="selected-owner", token="selected-fixture-token",
        )
        assert rescue.call_args.kwargs["account"] == "selected-owner"
        assert rescue.call_args.kwargs["token"] == "selected-fixture-token"
        stop.assert_not_called()
    assert status_mod.get_status("foreign") is not None


@pytest.mark.parametrize("bound", [None, "foreign-owner"])
@pytest.mark.parametrize("running", [False, True])
def test_quota_reclaim_refuses_missing_or_foreign_same_name_binding(
    store, monkeypatch, bound, running,
):
    from agent_codespaces import __main__ as cli, allocation

    status_mod.set_status("same-name", status_mod.STATE_PRUNABLE)
    monkeypatch.setattr(
        allocation, "list_account_codespaces",
        lambda account, **kwargs: [_info("same-name", "Available" if running else "Shutdown")],
    )
    monkeypatch.setattr("agent_codespaces.account_binding.bound_account_or_raise", lambda name: bound)
    monkeypatch.setattr(
        "agent_codespaces.owner_inventory.credential",
        lambda account: pytest.fail("ineligible identity read a credential"),
    )
    with patch.object(cli, "sync_codespace_sessions") as rescue, \
         patch.object(cli, "delete_codespace") as delete, \
         patch.object(cli, "stop_codespace") as stop:
        assert cli._reclaim_for_quota(
            "too many codespaces running" if running else "maximum number of codespaces",
            account="selected-owner",
        ) is None
    rescue.assert_not_called()
    delete.assert_not_called()
    stop.assert_not_called()
    assert status_mod.get_status("same-name") is not None


def test_quota_reclaim_unknown_account_inventory_preserves_venues(store, monkeypatch):
    from agent_codespaces import __main__ as cli, allocation

    status_mod.set_status("retained", status_mod.STATE_PRUNABLE)
    monkeypatch.setattr(
        allocation, "list_account_codespaces",
        lambda account, **kwargs: (_ for _ in ()).throw(RuntimeError("inventory unavailable")),
    )
    with patch.object(cli, "sync_codespace_sessions") as rescue, \
         patch.object(cli, "delete_codespace") as delete:
        assert cli._reclaim_for_quota(
            "maximum number of codespaces", account="selected-owner",
        ) is None
    rescue.assert_not_called()
    delete.assert_not_called()
    assert status_mod.get_status("retained") is not None


def test_create_retries_after_reclaim(store):
    info = _info("new-cs", "Available")
    with patch("agent_codespaces.__main__._ambient_codespace_scope", return_value=(True, "")), \
         patch("agent_codespaces.allocation.allocation_context", return_value=(
             "selected-owner", {"GH_TOKEN": "selected-fixture-token"},
         )), \
         patch("agent_codespaces.__main__.load_merged_config", return_value=object()), \
         patch("agent_codespaces.__main__._reclaim_for_quota", return_value="pruned x"), \
         patch(
             "agent_codespaces.__main__.create_codespace",
             side_effect=[RuntimeError("maximum number of codespaces"), info],
         ) as cc:
        rc = main(["create", "owner/repo", "--no-wait", "--force-create"])
    assert rc == 0
    assert cc.call_count == 2


def test_create_quota_no_reclaim_fails(store):
    with patch("agent_codespaces.__main__._ambient_codespace_scope", return_value=(True, "")), \
         patch("agent_codespaces.allocation.allocation_context", return_value=(
             "selected-owner", {"GH_TOKEN": "selected-fixture-token"},
         )), \
         patch("agent_codespaces.__main__.load_merged_config", return_value=object()), \
         patch("agent_codespaces.__main__._reclaim_for_quota", return_value=None), \
         patch(
             "agent_codespaces.__main__.create_codespace",
             side_effect=RuntimeError("maximum number of codespaces"),
         ):
        rc = main(["create", "owner/repo", "--no-wait", "--force-create"])
    assert rc == 1
