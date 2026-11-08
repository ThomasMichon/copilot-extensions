from __future__ import annotations

import types

import pytest

from worktree_manager.production_picker.picker_tui import derive
from worktree_manager.production_picker.picker_tui.engine import PickerScreen


def _raw(**values):
    row = {
        "id": "child",
        "repo": "example",
        "status": "active",
        "started_at": "2026-01-01T00:00:00",
    }
    row.update(values)
    return row


@pytest.mark.parametrize("ownership", [
    {"owner_ref": "host/example/controller"},
    {"controllers": [{"kind": "session", "state": "active", "controller_session_id": "origin"}]},
    {"owner_ref": "host/example/missing", "controller_findings": [{"status": "missing"}]},
])
def test_sessionless_owned_workspace_is_not_an_ordinary_resume_target(ownership) -> None:
    row = derive.norm(_raw(session_count=0, **ownership), "host", "windows")
    assert row["managed_child"]
    assert PickerScreen._session_action_verbs(row) == ["Refresh"]
    screen = types.SimpleNamespace(
        src=types.SimpleNamespace(bucket=derive.bucket),
        is_all=lambda: False,
        _visible_scope_data=lambda: [row],
    )
    _cols, sections = PickerScreen.current_list(screen)
    assert all(row not in rows for label, rows in sections if label in ("Active", "Recent", "Completed"))
    assert next(rows for label, rows in sections if label.startswith("Owned")) == [row]


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("mux_attached", [False, True])
def test_picker_status_counts_owned_workspaces_in_full_and_compact_headers(compact, mux_attached):
    row = derive.norm(_raw(
        session_count=0, owner_ref="host/example/controller", mux_attached=mux_attached,
    ), "host", "windows")
    screen = types.SimpleNamespace(
        src=types.SimpleNamespace(bucket=derive.bucket),
        is_all=lambda: False,
        _visible_scope_data=lambda: [row],
        _kind=lambda: "worktrees",
        pulse=0, live=False, loader=None, _orphans=[],
    )
    screen.current_list = lambda: PickerScreen.current_list(screen)
    text = PickerScreen.status_text(screen, compact=compact).plain
    assert ("own:1" if compact else "1 owned") in text


def _bulk_screen(rows, calls):
    return types.SimpleNamespace(
        list_records=lambda: rows,
        _row_key=PickerScreen._row_key,
        _record_supports=PickerScreen._record_supports,
        _open_sync=lambda **kwargs: calls.append(("Sync", kwargs["ids"])),
        _open_cleanup=lambda **kwargs: calls.append(("Cleanup", kwargs["ids"])),
        _start_finalize=lambda selected: calls.append(("Finalize", {row["id"] for row in selected})),
        _start_stop=lambda selected: calls.append(("Stop", {row["id"] for row in selected})),
        _start_reclaim=lambda selected: calls.append(("Reclaim", {row["id"] for row in selected})),
    )


@pytest.mark.parametrize("action", ["Sync", "Cleanup", "Finalize"])
def test_bulk_mutations_exclude_owned_children_and_do_not_expand_empty_scope(action):
    owned = [
        derive.norm(_raw(
            id=f"owned-{index}", session_count=0, owner_ref="host/example/controller",
            state="unused", cleanup_bucket="unused", ff_eligible=True,
        ), "host", "windows")
        for index in range(2)
    ]
    calls = []
    screen = _bulk_screen(owned, calls)
    ids = {PickerScreen._row_key(row) for row in owned}
    PickerScreen._run_maint_action(screen, action, ids)
    assert not calls
    assert "no eligible worktrees" in screen.debug
    unowned = derive.norm(_raw(id="unowned", session_count=0), "host", "windows")
    screen.list_records = lambda: [*owned, unowned]
    PickerScreen._run_maint_action(screen, action, ids | {PickerScreen._row_key(unowned)})
    expected = {unowned["id"]} if action == "Finalize" else {PickerScreen._row_key(unowned)}
    assert calls == [(action, expected)]


@pytest.mark.parametrize("action", ["Stop", "Reclaim"])
def test_bulk_owned_child_recovery_is_still_available(action):
    row = derive.norm(_raw(
        session_count=0, owner_ref="host/example/controller",
    ), "host", "windows")
    calls = []
    screen = _bulk_screen([row], calls)
    PickerScreen._run_maint_action(screen, action, {PickerScreen._row_key(row)})
    assert calls == [(action, {row["id"]})]


def test_finalize_execution_boundary_rejects_owned_children():
    owned = derive.norm(_raw(
        session_count=0, owner_ref="host/example/controller",
        state="unused", cleanup_bucket="unused",
    ), "host", "windows")
    calls = []
    screen = types.SimpleNamespace(
        _record_supports=PickerScreen._record_supports,
        _run_op_progress=lambda *_args, **_kwargs: calls.append("unexpected mutation"),
    )
    PickerScreen._start_finalize(screen, [owned])
    assert not calls
    assert "no conversation-only" in screen.debug


@pytest.mark.parametrize("values", [
    {"mux_attached": True, "state": "unused", "ff_eligible": True},
    {"state": "gone"},
])
def test_owned_child_menu_excludes_maintenance_even_when_mux_is_openable_or_directory_gone(values):
    row = derive.norm(_raw(
        session_count=0, owner_ref="host/example/controller", **values,
    ), "host", "windows")
    assert row["managed_child"]
    assert not {"Sync", "Cleanup", "Finalize"} & set(PickerScreen._session_action_verbs(row))


@pytest.mark.parametrize("values", [
    {"session_count": 1},
    {"session_count": 0, "last_session_id": "own-session"},
    {},
])
def test_controller_does_not_hide_an_own_or_unknown_conversation(values) -> None:
    row = derive.norm(_raw(owner_ref="host/example/controller", **values), "host", "windows")
    assert not row["managed_child"]
    assert "Resume" in PickerScreen._session_action_verbs(row)


def test_unowned_empty_workspace_and_released_controller_remain_openable() -> None:
    for ownership in ({}, {"controllers": [{"state": "released"}]}):
        row = derive.norm(_raw(session_count=0, **ownership), "host", "windows")
        assert not row["managed_child"]
        assert "Open" in PickerScreen._session_action_verbs(row)


def test_explicit_controller_release_overrides_historical_owner_backlink():
    row = derive.norm(_raw(
        session_count=0,
        owner_ref="host/example/former-controller",
        reciprocal_relation={
            "version": 1, "state": "unbound",
            "binding": {"state": "unbound"},
            "control": {"state": "released"}, "actions": [],
        },
    ), "host", "windows")
    assert not row["managed_child"]
    assert "Open" in PickerScreen._session_action_verbs(row)


@pytest.mark.parametrize("residue,expected", [
    ({"mux_attached": True}, ["Open", "Stop"]),
    ({"session_lock_stale": True}, ["Reclaim"]),
])
def test_owned_workspace_retains_existing_mux_and_orphan_recovery(residue, expected) -> None:
    row = derive.norm(
        _raw(session_count=0, owner_ref="host/example/controller", **residue),
        "host", "windows",
    )
    assert row["managed_child"]
    actions = PickerScreen._session_action_verbs(row)
    assert actions[:len(expected)] == expected
    assert "Resume" not in actions


@pytest.mark.parametrize("embodied", [False, True])
def test_successful_embodiment_can_attach_despite_stale_sessionless_snapshot(embodied):
    row = derive.norm(
        _raw(session_count=0, owner_ref="host/example/controller"),
        "host", "windows",
    )
    decisions = []
    screen = types.SimpleNamespace(
        _find_internal_worktree=lambda *_args: (row, ""),
        _resume_decision=lambda value: {"action": "resume", "worktree_id": value["id"]},
        _decide=decisions.append,
    )
    ok, message = PickerScreen._resume_embodied_worktree_cli(
        screen, row["id"], embodied=embodied,
    )
    assert ok is embodied
    if embodied:
        assert decisions == [{"action": "resume", "worktree_id": row["id"]}]
    else:
        assert not decisions
        assert "no session here" in message
    assert "Resume" not in PickerScreen._session_action_verbs(row)


def test_current_reciprocal_relation_is_preserved() -> None:
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "bound-here", "session_id": "bound"},
        "control": {"state": "controlled-remote"},
        "actions": [],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"] == relation
    assert row["relation"] == "CONTROL"


# ---------------------------------------------------------------------------
# #3307 Phase 5: the retired "R" column's values fold into STATE/LIVE instead
# ---------------------------------------------------------------------------

def test_handed_off_relation_becomes_handoff_state_when_not_live() -> None:
    relation = {
        "version": 1,
        "state": "handed-off",
        "binding": {"state": "handed-off"},
        "control": {"state": "none"},
        "actions": [],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["relation"] == "HANDOFF"
    assert row["state"] == "HANDOFF"
    # Not a FINAL/MERGED descriptor state, so no closure-style override.
    assert row["state_style"] is None


def test_live_session_beats_handoff_relation() -> None:
    """A live process still wins ACTIVE over a stale HANDOFF relation -- a
    successor already resumed, so the handoff itself is no longer the most
    useful thing to show."""
    relation = {
        "version": 1,
        "state": "handed-off",
        "binding": {"state": "handed-off"},
        "control": {"state": "none"},
        "actions": [],
    }

    row = derive.norm(
        _raw(reciprocal_relation=relation, mux_attached=True), "host", "windows")

    assert row["state"] == "ACTIVE"


def test_bucket_places_handoff_state_in_recent() -> None:
    relation = {
        "version": 1,
        "state": "handed-off",
        "binding": {"state": "handed-off"},
        "control": {"state": "none"},
        "actions": [],
    }
    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")
    _active, recent, _completed = derive.bucket([row])
    assert [r["id"] for r in recent] == ["child"]


def test_live_worktree_shows_acp_mode_when_bridge_interface() -> None:
    """BOUND vs CONTROL folds into LIVE as a CLI/ACP interface-mode marker
    (not the reciprocal_relation binding/control axis -- a worktree owned by
    an orchestrating CLI session still reads as CONTROL despite its own live
    session being ordinary CLI, so the mode marker uses ``interface``
    directly)."""
    row = derive.norm(
        _raw(session_bound_live=True, interface="acp"), "host", "windows")
    assert row["sess"] == "ACP"
    assert row["state"] == "ACTIVE"


def test_live_worktree_shows_proc_for_cli_interface() -> None:
    row = derive.norm(
        _raw(session_bound_live=True, interface="cli"), "host", "windows")
    assert row["sess"] == "PROC"


def test_controlled_elsewhere_cli_worktree_still_shows_proc() -> None:
    """The common agent-orchestrated case (this very effort's own worktrees):
    CONTROL no longer implies ACP -- a CLI-interface worktree owned by
    another session still shows PROC, not ACP."""
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "bound-here"},
        "control": {"state": "controlled-remote"},
        "actions": [],
    }
    row = derive.norm(
        _raw(reciprocal_relation=relation, session_bound_live=True,
             interface="cli"),
        "host", "windows")
    assert row["relation"] == "CONTROL"
    assert row["sess"] == "PROC"


def test_legacy_controller_row_fails_closed() -> None:
    row = derive.norm(
        _raw(controllers=[{"relation_revision": 1}]),
        "host",
        "windows",
    )

    assert row["reciprocal_relation"]["state"] == "ambiguous"
    assert row["reciprocal_relation"]["actions"] == []
    assert row["relation"] == "AMBIG"


def test_ambiguous_payload_cannot_enable_navigation() -> None:
    relation = {
        "version": 1,
        "state": "ambiguous",
        "binding": {"state": "unknown"},
        "control": {"state": "ambiguous"},
        "actions": [{
            "kind": "navigate-worktree",
            "scope": "remote",
            "target": {
                "project": "example",
                "worktree_id": "parent",
            },
        }],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"]["compatibility"] == "invalid"
    assert row["reciprocal_relation"]["actions"] == []


def test_remote_navigation_requires_exact_machine() -> None:
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "unbound"},
        "control": {"state": "controlled-remote"},
        "actions": [{
            "kind": "navigate-worktree",
            "scope": "remote",
            "target": {
                "project": "example",
                "worktree_id": "parent",
            },
        }],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"]["compatibility"] == "invalid"
    assert row["reciprocal_relation"]["actions"] == []


def test_summary_must_match_binding_and_control_axes() -> None:
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "unbound"},
        "control": {"state": "none"},
        "actions": [],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"]["compatibility"] == "invalid"


def test_navigation_target_must_match_control_target() -> None:
    relation = {
        "version": 1,
        "state": "controlled-elsewhere",
        "binding": {"state": "unbound"},
        "control": {
            "state": "controlled-remote",
            "target": {
                "project": "example",
                "worktree_id": "declared",
                "machine": "remote-host",
            },
        },
        "actions": [{
            "kind": "navigate-worktree",
            "scope": "remote",
            "target": {
                "project": "example",
                "worktree_id": "other",
                "machine": "remote-host",
            },
        }],
    }

    row = derive.norm(_raw(reciprocal_relation=relation), "host", "windows")

    assert row["reciprocal_relation"]["compatibility"] == "invalid"
    assert row["reciprocal_relation"]["actions"] == []


def test_controller_navigation_requires_exact_loaded_target() -> None:
    target = {
        "raw": {"id": "parent", "repo": "example"},
        "machine": "remote-host",
        "source_id": "machine-ssh:remote-host:windows",
    }
    child = {
        "reciprocal_relation": {
            "actions": [{
                "kind": "navigate-worktree",
                "target": {
                    "project": "example",
                    "worktree_id": "parent",
                    "machine": "remote-host",
                },
            }],
        },
    }
    picker = object.__new__(PickerScreen)
    picker.data = [target]

    assert picker._reciprocal_target_row(child) is target

    picker.data.append({
        **target,
        "source_id": "machine-ssh:remote-host:wsl",
    })
    assert picker._reciprocal_target_row(child) is None
