"""Headless render test for the ported Worktree Picker TUI (slice 1).

Hermetic: drives the engine over a fixture source (no real tracking/git/SSH),
asserting it boots and renders real-shaped records with the canonical state
vocabulary.
"""
from __future__ import annotations

import asyncio
import datetime
import threading
import time
import types

from agent_worktrees.picker_tui import derive, new_picker_enabled
from agent_worktrees.picker_tui.engine import PickerApp, PickerScreen


def _fixture_source():
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-20260627-aaaa", "title": "Fix the thing",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 4, "state": "wip", "ahead": 2, "behind": 1,
         "mux_session": True, "mux_attached": True, "mux_clients": 1,
         "pr": {"number": 42, "state": "open"}},
        {"id": "lambda-core-win-20260620-bbbb", "title": "Old idle wt",
         "status": "active", "started_at": "2026-06-20T10:00:00",
         "turn_count": 0, "state": "unused"},
        {"id": "lambda-core-win-20260626-cccc", "title": "Done work",
         "status": "finalized", "completed_at": "2026-06-26T10:00:00",
         "started_at": "2026-06-25T10:00:00", "turn_count": 9,
         "state": "completed", "pr": {"number": 40, "state": "merged"}},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def _bridge_source():
    """Two machines; a bridge-owned worktree lives on the non-local one, for
    the #1424 jump-to-host flow."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    raws_local = [
        {"id": "lambda-core-win-1111", "title": "Local wt", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 3},
    ]
    raws_bor = [
        {"id": "borealis-win-bridge-2222", "title": "Bridge wt",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "kind": "bridge", "turn_count": 1},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = ("lambda-core", "Win")
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [
        ("lambda-core Win", "lambda-core", "Win", True),
        ("borealis Win", "borealis", "Win", True),
    ]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: (
        [derive.norm(w, "lambda-core", "Win") for w in raws_local]
        + [derive.norm(w, "borealis", "Win") for w in raws_bor]
    )
    return src


def test_jump_to_host_offered_only_for_managed(tmp_path):
    """The submenu offers 'Jump to host' for a bridge/system worktree, not a
    plain session worktree (#1424)."""
    src = _bridge_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.t0 = 0                     # force all machine tabs ready
            scr.show_hidden = True
            scr.machine_idx = 0            # All
            await pilot.pause()
            recs = scr.list_records()
            bi = next(i for i, r in enumerate(recs) if r.get("kind") == "bridge")
            si = next(i for i, r in enumerate(recs) if r.get("kind") == "session")
            scr.sel = ("L", bi)
            scr._open_submenu()
            assert "Jump to host" in scr.submenu["actions"]
            scr.submenu = None
            scr.sel = ("L", si)
            scr._open_submenu()
            assert "Jump to host" not in scr.submenu["actions"]

    asyncio.run(run())


def test_jump_to_host_switches_machine_and_highlights(tmp_path):
    """Invoking 'Jump to host' switches to the host machine tab, reveals hidden,
    lands selection on the row by stable id, and never exits the picker
    (#1424)."""
    src = _bridge_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.t0 = 0
            scr.show_hidden = True
            scr.machine_idx = 0            # start on All
            await pilot.pause()
            recs = scr.list_records()
            bi = next(i for i, r in enumerate(recs) if r.get("kind") == "bridge")
            scr.sel = ("L", bi)
            scr._open_submenu()
            scr.submenu_idx = scr.submenu["actions"].index("Jump to host")
            scr._key_submenu("enter")
            await pilot.pause()
        assert scr.submenu is None
        assert scr.machine_idx == scr._machine_index_for("borealis", "Win")
        assert scr.show_hidden is True
        assert scr.sel[0] == "L"
        landed = scr.list_records()[scr.sel[1]]
        assert (landed.get("raw") or {}).get("id") == "borealis-win-bridge-2222"
        assert app.result is None          # internal nav -- never exited

    asyncio.run(run())


def test_jump_to_worktree_unknown_id_is_safe(tmp_path):
    """A jump to a worktree not in the loaded set is a reported no-op, not a
    crash (guards the #1425 registered-pivot internal action)."""
    src = _bridge_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            ok, msg = scr._jump_to_worktree("does-not-exist")
            assert ok is False
            assert "not found" in msg
            # And the internal-action dispatcher reports unknown verbs.
            ok2, msg2 = scr._internal_pivot_action("no-such-verb", {})
            assert ok2 is False
            assert "unknown internal action" in msg2

    asyncio.run(run())


def test_profiles_hosted_under_configuration():
    """Profiles is placed under ⚙ Configuration: off the left cycle, in the
    config set, and the ('CFG', 0) stop exists (#1426)."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            prof = next(i for i, p in enumerate(scr.pivots)
                        if p["kind"] == "profiles")
            assert scr.pivots[prof]["placement"] == "config"
            assert prof in scr._config_pivots()
            assert prof not in scr._left_pivots()
            assert ("CFG", 0) in scr._v_stops()

    asyncio.run(run())


def test_configuration_menu_opens_profiles():
    """Enter on the Configuration entry opens its menu; choosing Profiles
    switches to that pivot and focuses its body (#1426)."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            assert scr._kind() == "worktrees"
            scr.sel = ("CFG", 0)
            scr._activate()                 # opens the Configuration menu
            assert scr.cfgmenu is not None
            scr._key_cfgmenu("enter")       # selects Profiles (only item)
            assert scr.cfgmenu is None
            assert scr._kind() == "profiles"
            assert scr.sel[0] in ("PR", "BTN")   # focused the profiles body

    asyncio.run(run())


def _maint_source():
    """Fixture with one worktree per cleanup bucket + one FF-eligible."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")

    def raw(code, bucket, ff=False):
        return {"id": f"lambda-core-win-{code}", "title": code,
                "status": "active", "started_at": "2026-06-27T17:00:00",
                "cleanup_bucket": bucket, "ff_eligible": ff}

    raws = [
        raw("cl00", "clean"),
        raw("el00", "clean", ff=True),
        raw("un00", "unused"),
        raw("cv00", "conversation"),
        raw("dr00", "dirty"),
        raw("op00", "open-pr"),
        raw("ac00", "active"),
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_cleanup_dialog_buckets_and_sync_eligibility():
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:  # noqa: F841
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()

            scr._open_cleanup()
            opts = {o["label"]: o["ids"] for o in scr.cleanup["opts"]}
            assert len(opts["Merged & finalized"]) == 2   # cl00 + el00
            assert len(opts["Unused"]) == 1               # un00
            assert len(opts["Conversation-only"]) == 1    # cv00
            assert len(opts["All eligible"]) == 4         # clean(2)+unused+convo
            # Unsafe buckets are never offered.
            for unsafe in ("dr00", "op00", "ac00"):
                assert unsafe not in opts["All eligible"]

            scr.cleanup = None
            scr._open_sync()
            sopts = {o["label"]: o["ids"] for o in scr.cleanup["opts"]}
            assert sopts["Eligible"] == {"el00"}          # only the FF-eligible

            # Disposition chips reflect the buckets.
            rows = {w["id4"]: w for w in scr.cleanup_rows()}
            assert rows["cl00"]["dispo_level"] == "SAFE"
            assert rows["ac00"]["dispo_level"] == "UNSAFE"
            assert rows["op00"]["dispo_level"] == ""      # open PR: healthy

    asyncio.run(run())


def test_maintenance_multiselect_and_actions_menu():
    """Maintenance: Space toggles selection, group/select-all quick-pick, and
    Enter opens the actions menu scoped to the selection (#1345)."""
    src = _maint_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            scr.htab = 1
            scr.sel = scr.default_sel()
            await pilot.pause()

            # New maintenance stops exist: Select-all, group headers, rows.
            kinds = {z for z, _ in scr.stops()}
            assert {"SA", "GH", "C"} <= kinds

            recs = scr.maint_records()
            assert recs  # grouped, non-empty

            # Space on a focused row toggles just that row.
            crow = next(s for s in scr.stops() if s[0] == "C")
            scr.sel = crow
            scr._toggle_maint(crow[1])
            assert len(scr.maint_sel) == 1

            # Select-all selects every candidate; again clears.
            scr._toggle_maint_all()
            assert scr.maint_sel == scr._maint_ids()
            scr._toggle_maint_all()
            assert not scr.maint_sel

            # Enter with nothing selected selects the focused row, opens menu.
            scr.sel = crow
            scr._activate()
            assert scr.maint_menu is not None
            assert scr.maint_menu["count"] == 1
            assert "Diagnostics" in scr.maint_menu["actions"]
            # Enter must NOT have produced a launch/resume decision.
            assert app.result is None

            # The menu's Cleanup action opens a scope dialog over the selection.
            acts = scr.maint_menu["actions"]
            if "Cleanup" in acts:
                scr.maint_menu_idx = acts.index("Cleanup")
                scr._key_maint_menu("enter")
                assert scr.cleanup is not None
                assert "selected" in scr.cleanup["scope"]

    asyncio.run(run())


def _profiles_source():
    """Fixture source exposing config-bound axes + profile IO hooks (no SSH)."""
    src = _fixture_source()
    src.host_cols = lambda: [
        ("Lambda-Core·Win", "Lambda-Core", "Win"),
        ("Borealis·Win", "Borealis", "Win"),
    ]
    src.target_envs = lambda: [("Lambda-Core", "Win"), ("Borealis", "Win")]
    # In-memory column store keyed by (machine, env).
    store: dict = {}
    applied_calls: list = []

    def load_col(machine, env):
        from agent_worktrees.profiles import self_diagonal
        return set(store.get((machine, env), {self_diagonal(machine, env)}))

    def apply_col(machine, env, sels, *, mirror=True):
        store[(machine, env)] = set(sels)
        applied_calls.append((machine, env, mirror))
        return True, "saved"

    src.load_profile_column = load_col
    src.apply_profile_column = apply_col
    src._store = store
    src._applied_calls = applied_calls
    # The engine resolves the local host from the source LOCAL; align it with a
    # host column so the self-diagonal lock lands on Lambda-Core Win.
    src.LOCAL = ("Lambda-Core", "Win")
    return src


def test_profiles_apply_writes_changed_columns():
    """Toggling a grid cell and Applying runs the per-host progress dialog and
    persists that host's column on close."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            # Find a non-locked cell in the Borealis column (host index 1) and
            # toggle it on.
            hi = 1
            ti = next(t for t in range(len(scr.targets))
                      if not scr.cell_locked(t, hi))
            scr.sel = ("PR", ti)
            scr.pcol = hi
            scr._toggle_cell()
            assert scr.grid_dirty()
            # Apply via the button -> opens the per-host progress dialog.
            scr.btn_idx = 0
            scr.sel = ("BTN", 0)
            assert scr.active_button() == "PA"
            scr._activate()
            await pilot.pause()
            # Apply now opens a confirm dialog showing the add/remove diff.
            assert scr.prof_confirm is not None
            assert scr.prof_confirm["changed"]
            scr._key_prof_confirm("enter")     # confirm -> runs the progress
            await pilot.pause()
            assert scr.progress is not None
            assert scr.progress["op"] == "profiles"
            # Drive the executor to completion, then close (Enter) to commit.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not scr.executor.is_done():
                time.sleep(0.02)
            scr._advance_progress()
            assert scr.progress["done"]
            scr._key_progress("enter")
            await pilot.pause()
        # The Borealis column was written and is no longer dirty.
        assert any(m == "Borealis" for m, _e, _mir in src._applied_calls)
        assert not scr.grid_dirty()

    asyncio.run(run())


def test_profiles_apply_confirm_cancel_is_noop():
    """Esc on the Apply confirm dialog cancels without writing or running."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            hi = 1
            ti = next(t for t in range(len(scr.targets))
                      if not scr.cell_locked(t, hi))
            scr.sel = ("PR", ti)
            scr.pcol = hi
            scr._toggle_cell()
            scr.btn_idx = 0
            scr.sel = ("BTN", 0)
            scr._activate()
            await pilot.pause()
            assert scr.prof_confirm is not None
            # Confirm shows the concrete add/remove diff for the changed host.
            added, removed = scr.prof_confirm["diffs"][hi]
            assert added or removed
            scr._key_prof_confirm("escape")    # cancel
            await pilot.pause()
        assert scr.prof_confirm is None
        assert scr.progress is None
        assert src._applied_calls == []        # nothing written
        assert scr.grid_dirty()                # edit preserved, not applied

    asyncio.run(run())


def _profiles_source_unavailable():
    """Profiles fixture where the Borealis host column fails to load, as an
    old/unreachable remote does over SSH (#1370)."""
    from agent_worktrees.picker_tui import profiles_io
    src = _profiles_source()
    inner = src.load_profile_column

    def load_col(machine, env):
        if machine == "Borealis":
            return profiles_io.UNAVAILABLE
        return inner(machine, env)

    src.load_profile_column = load_col
    return src


def test_profiles_unavailable_column_is_readonly():
    """A host column that couldn't load is marked read-only: cells show '?',
    toggling is a no-op, and Apply excludes it (#1370)."""
    src = _profiles_source_unavailable()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not scr._prof_loaded:
                await pilot.pause()
            # Borealis (host col 1) is unavailable; the local col 0 is not.
            assert 1 in scr._prof_unavailable
            assert 0 not in scr._prof_unavailable
            # Cells in the unavailable column render the "unknown" marker.
            ti = next(t for t in range(len(scr.targets))
                      if not scr.cell_locked(t, 1))
            ch, _st = scr._cell_visual(ti, 1, scr.cell_locked(ti, 1))
            assert ch == "?"
            # Toggling that column is a read-only no-op.
            scr.sel = ("PR", ti)
            scr.pcol = 1
            scr._toggle_cell()
            assert not scr.grid_dirty()
            # Apply finds nothing to change (the column is excluded).
            scr._apply_profiles()
            assert scr.prof_confirm is None
            # The legend keys the agent/shell rows and flags the unavailable col.
            body = "\n".join(v.text.plain for v in scr.build_body(118))
            assert "plain SSH login shell" in body
            assert "remote unavailable" in body

    asyncio.run(run())


def test_profiles_apply_progress_carries_restart_summary():
    """After confirming an Apply, the progress dict carries the add/remove
    counts the done-state surfaces alongside the restart reminder (#1368)."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            hi = 1
            ti = next(t for t in range(len(scr.targets))
                      if not scr.cell_locked(t, hi))
            scr.sel = ("PR", ti)
            scr.pcol = hi
            scr._toggle_cell()
            scr._apply_profiles()
            assert scr.prof_confirm is not None
            scr._key_prof_confirm("enter")
            await pilot.pause()
            assert scr.progress["op"] == "profiles"
            assert scr.progress["n_add"] + scr.progress["n_rem"] >= 1

    asyncio.run(run())


def test_profiles_active_row_label_highlighted():
    """The focused target row label carries the same subtle 'on grey23' shading
    the active host column header uses, so both cursor axes read -- not just the
    cell inversion cursor (#1287)."""
    src = _profiles_source()

    def _label_shaded(vrow):
        # The label occupies the first ~30 cells; look for an 'on grey23' span
        # anchored there (distinct from the 'reverse' cell cursor and from a
        # locked cell's later 'grey50 on grey23').
        return any("on grey23" in str(sp.style) and sp.start <= 2
                   for sp in vrow.text.spans)

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            scr.sel = ("PR", 1)
            scr.pcol = 0
            rows = {getattr(v, "stop", None): v for v in scr.build_body(118)}
            assert _label_shaded(rows[("PR", 1)])       # focused row shaded
            assert not _label_shaded(rows[("PR", 0)])   # others are not

    asyncio.run(run())


def test_profiles_grid_cell_restored_on_tab():
    """Tabbing out of the Profiles grid and back restores the last-focused
    target row and host column, not the top-left default (#1288)."""
    src = _profiles_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 2
            await pilot.pause()
            scr.sel = ("PR", 1)
            scr.pcol = 1
            scr.handle_key("shift+tab")     # grid -> View region
            assert scr.sel[0] != "PR"
            scr.handle_key("tab")           # back into the grid
            assert scr.sel == ("PR", 1)     # target row restored
            assert scr.pcol == 1            # host column persisted

    asyncio.run(run())


def test_run_tui_picker_redirects_stdout_when_captured(monkeypatch):
    """When stdout is captured (launcher) but stderr is a TTY, Textual must
    render to stderr while the real stdout stays reserved for the plan."""
    import io
    import sys

    import agent_worktrees.picker_tui as pkg
    from agent_worktrees.picker_tui import engine as eng

    seen = {}

    class _FakeApp:
        def __init__(self, source, live=False):
            self.result = {"action": "cancel"}

        def run(self):
            seen["during"] = sys.__stdout__

    class _Pipe(io.StringIO):
        def isatty(self):
            return False

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    pipe, tty = _Pipe(), _Tty()
    monkeypatch.setattr(sys, "__stdout__", pipe)
    monkeypatch.setattr(sys, "stderr", tty)
    monkeypatch.setattr(eng, "PickerApp", _FakeApp)

    result = pkg.run_tui_picker(source=object(), live=False)
    assert result == {"action": "cancel"}
    assert seen["during"] is tty       # Textual rendered to the terminal
    assert sys.__stdout__ is pipe      # restored: fd1 free for the JSON plan


def test_run_tui_picker_no_redirect_in_real_terminal(monkeypatch):
    """In a normal terminal (stdout is a TTY) no redirect happens."""
    import io
    import sys

    import agent_worktrees.picker_tui as pkg
    from agent_worktrees.picker_tui import engine as eng

    seen = {}

    class _FakeApp:
        def __init__(self, source, live=False):
            self.result = None

        def run(self):
            seen["during"] = sys.__stdout__

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    tty_out, tty_err = _Tty(), _Tty()
    monkeypatch.setattr(sys, "__stdout__", tty_out)
    monkeypatch.setattr(sys, "stderr", tty_err)
    monkeypatch.setattr(eng, "PickerApp", _FakeApp)

    pkg.run_tui_picker(source=object(), live=False)
    assert seen["during"] is tty_out   # unchanged -- Textual uses stdout


def test_bucket_sections_key_off_state():
    """Active = in-session (ACTIVE); Completed = FINAL (any age); Recent = rest."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)

    def rec(raw_state, hours):
        ts = (derive.NOW - datetime.timedelta(hours=hours)).isoformat()
        return derive.norm(
            {"id": f"x-{raw_state}-{hours}", "status": "active",
             "state": raw_state, "started_at": ts}, "m", "Win")

    wts = [rec("active", 1), rec("completed", 1), rec("completed", 60),
           rec("wip", 2), rec("unused", 3)]
    active, recent, completed = derive.bucket(wts)
    assert [w["state"] for w in active] == ["ACTIVE"]
    # Both FINALs land in Completed regardless of age (1h and 60h).
    assert sorted(w["state"] for w in completed) == ["FINAL", "FINAL"]
    # Recent is whatever is neither in-session nor final.
    assert sorted(w["state"] for w in recent) == ["UNUSED", "WIP"]


def test_sessionless_flag_only_when_count_known_zero():
    """#1026: sessionless is flagged only when session_count is present and 0,
    with no turns / mux ownership and not a managed kind."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)

    def n(**extra):
        base = {"id": "lambda-core-win-zzzz", "status": "active",
                "state": "wip", "started_at": "2026-06-27T17:00:00"}
        base.update(extra)
        return derive.norm(base, "m", "Win")

    assert n(session_count=0)["sessionless"] is True            # the orphan
    assert n(session_count=2)["sessionless"] is False           # owned
    assert n()["sessionless"] is False                          # unknown (absent)
    assert n(session_count=0, turn_count=3)["sessionless"] is False   # had turns
    assert n(session_count=0, mux_attached=True)["sessionless"] is False
    assert n(session_count=0, kind="bridge")["sessionless"] is False  # managed


def _sessionless_source():
    """One normal (owned) worktree + one sessionless orphan (session_count 0)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-owned", "title": "Owned wip",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 4, "state": "wip", "session_count": 1},
        {"id": "lambda-core-win-orph", "title": "Orphan wip",
         "status": "active", "started_at": "2026-06-27T16:00:00",
         "turn_count": 0, "state": "wip", "session_count": 0,
         "pr": {"number": 99, "state": "open"}},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def test_picker_buckets_sessionless_into_unowned():
    """#1026: a worktree with no owning session lands in a distinct 'Unowned'
    section (not Recent), and its sub-menu omits Resume."""
    src = _sessionless_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            await pilot.pause()
            _cols, sections = scr.current_list()
            labels = [lbl for lbl, _rows in sections]
            unowned = next(rows for lbl, rows in sections if lbl.startswith("Unowned"))
            recent = next(rows for lbl, rows in sections if lbl == "Recent")
            assert any(lbl.startswith("Unowned") for lbl in labels)
            assert [w["id4"] for w in unowned] == ["orph"[-4:]]
            assert all(not w.get("sessionless") for w in recent)
            # Select the orphan and open its sub-menu -> no Resume offered.
            recs = scr.list_records()
            oi = next(i for i, w in enumerate(recs) if w.get("sessionless"))
            scr.sel = ("L", oi)
            scr._open_submenu()
            assert "Resume" not in scr.submenu["actions"]
            assert "Open" in scr.submenu["actions"]

    asyncio.run(run())


def test_reconcile_prs_counts_terminal_transitions(monkeypatch):
    """#1423: reconcile_prs reconciles each non-terminal active PR and counts
    those that moved to a terminal state, skipping no-PR / already-terminal."""
    from pathlib import Path

    from agent_worktrees.picker_tui import data_local

    class FakePR:
        def __init__(self, number, state):
            self.number, self.state = number, state

    class FakeRec:
        def __init__(self, pr):
            self._pr = pr

        def active_pr(self):
            return self._pr

    open_pr = FakePR(1, "open")
    recs = [FakeRec(open_pr), FakeRec(FakePR(2, "merged")), FakeRec(None)]
    monkeypatch.setattr(data_local.cfg, "load_config", lambda: object())
    monkeypatch.setattr(data_local.cfg, "tracking_dir", lambda: Path("."))
    monkeypatch.setattr(data_local.cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(data_local.tracking, "list_records",
                        lambda p, platform_filter=None: recs)

    def fake_reconcile(rec, config):
        if rec.active_pr() is open_pr:        # provider reports it merged
            open_pr.state = "merged"

    monkeypatch.setattr("agent_worktrees.pr_ops._reconcile_active_pr",
                        fake_reconcile)
    assert data_local.reconcile_prs() == 1


def test_picker_background_pr_reconcile_reloads_on_change():
    """#1423: when the background reconcile reports a change, the non-live path
    reloads local data so the render reflects the corrected PR state."""
    src = _fixture_source()
    calls = {"reconcile": 0, "load": 0}
    orig_load = src.load

    def load2():
        calls["load"] += 1
        return orig_load()

    def reconcile_prs():
        calls["reconcile"] += 1
        return 1

    src.load = load2
    src.reconcile_prs = reconcile_prs

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not scr._pr_reconciled:
                await pilot.pause()
            assert calls["reconcile"] == 1
            assert calls["load"] >= 2       # setup load + post-reconcile reload

    asyncio.run(run())


def test_picker_background_pr_reconcile_no_change_no_reload():
    """A reconcile that changes nothing must not trigger a reload."""
    src = _fixture_source()
    calls = {"load": 0}
    orig_load = src.load

    def load2():
        calls["load"] += 1
        return orig_load()

    src.load = load2
    src.reconcile_prs = lambda: 0

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not scr._pr_reconciled:
                await pilot.pause()
            assert calls["load"] == 1       # only the setup load

    asyncio.run(run())


def test_maybe_repoll_gating(monkeypatch):
    """#1421: _maybe_repoll fires once per interval, and is suppressed on the
    Profiles tab, during a progress dialog, and when POLL_SECS <= 0."""
    from agent_worktrees.picker_tui import engine as eng

    class FakeLoader:
        def __init__(self):
            self.polls = 0

        def repoll_silent(self, keys=None):
            self.polls += 1
            return 0

    loader = FakeLoader()

    def fresh_obj():
        obj = types.SimpleNamespace(
            loader=loader, progress=None, htab=0,
            pivots=[{"kind": "worktrees"}, {"kind": "maintenance"}, {"kind": "profiles"}],
            _last_poll=time.monotonic() - 1000,
            _poll_keys=lambda: {("m", "Win")})
        # Dispatch is keyed off pivot *kind* now, not the raw htab index.
        obj._kind = lambda idx=None: obj.pivots[obj.htab if idx is None else idx]["kind"]
        return obj

    monkeypatch.setattr(eng, "POLL_SECS", 45.0)
    obj = fresh_obj()

    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1                 # due -> fires
    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1                 # within interval -> no-op

    obj._last_poll = time.monotonic() - 1000
    obj.htab = 2                             # Profiles tab -> suppressed
    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1

    obj.htab = 0
    obj.progress = {"done": False}           # dialog up -> suppressed
    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1

    obj.progress = None
    monkeypatch.setattr(eng, "POLL_SECS", 0.0)   # disabled
    eng.PickerScreen._maybe_repoll(obj)
    assert loader.polls == 1


def test_bucket_fallback_no_classify_finalized_is_clean_not_wip():
    """An old remote (no --classify -> no state) must not show FINAL + unmerged."""
    # status finalized, no git classification -> display FINAL, bucket clean.
    w = derive.norm(
        {"id": "borealis-wsl-1234", "status": "finalized",
         "started_at": "2026-06-25T10:00:00"}, "Borealis", "WSL")
    assert w["state"] == "FINAL"
    assert w["cleanup_bucket"] == "clean"          # not 'wip'/'unmerged'
    assert derive.BUCKET_DISPO[w["cleanup_bucket"]] == "SAFE"

    # status active, no classification, no PR -> unknown (neutral, not unmerged).
    w2 = derive.norm(
        {"id": "borealis-wsl-5678", "status": "active",
         "started_at": "2026-06-25T10:00:00"}, "Borealis", "WSL")
    assert w2["cleanup_bucket"] == "unknown"
    assert derive.BUCKET_DISPO[w2["cleanup_bucket"]] == ""   # no chip


def test_new_picker_flag_gating(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_NEW_PICKER", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_LEGACY_PICKER", raising=False)
    assert new_picker_enabled() is False
    monkeypatch.setenv("AGENT_WORKTREES_NEW_PICKER", "1")
    assert new_picker_enabled() is True
    # Legacy override always wins (the rollback switch).
    monkeypatch.setenv("AGENT_WORKTREES_LEGACY_PICKER", "1")
    assert new_picker_enabled() is False


def test_tui_renders_local_worktrees():
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:  # noqa: F841
            scr = app.query_one(PickerScreen)
            out = str(scr.render())
            assert "Agent Worktrees" in out
            # Canonical state vocabulary (aperture-labs #1290).
            assert "WIP" in out
            assert "UNUSED" in out
            assert "FINAL" in out
            # Real machine identity from the source.
            assert "lambda-core" in out

    asyncio.run(run())


def test_topbar_repo_branch_are_data_backed():
    """The top bar's repo + default-branch segments come from the data source
    (config), not a hardcoded ``aperture-labs`` / ``master``."""
    src = _fixture_source()
    src.REPO = "copilot-extensions"
    src.BRANCH = "main"

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:  # noqa: F841
            scr = app.query_one(PickerScreen)
            out = str(scr.render())
            assert "copilot-extensions" in out
            assert "main" in out
            # The old hardcoded values must not leak in.
            assert "aperture-labs" not in out
            assert "master" not in out

    asyncio.run(run())


def test_topbar_drops_repo_branch_when_source_omits_them():
    """A source without REPO/BRANCH (e.g. a bare fixture) renders no repo or
    branch segment rather than a fabricated one."""
    src = _fixture_source()  # SimpleNamespace -> no REPO/BRANCH attrs

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:  # noqa: F841
            scr = app.query_one(PickerScreen)
            out = str(scr.render())
            assert "Agent Worktrees" in out
            assert "aperture-labs" not in out

    asyncio.run(run())


class _FakeLoader:
    """In-memory loader matching the engine's live contract (no SSH)."""

    def __init__(self, records_by_key, states):
        self._records = records_by_key
        self._states = states

    def start(self):
        pass

    def state(self, machine, env):
        return self._states.get((machine, env), "loading")

    def records(self):
        out = []
        for key, recs in self._records.items():
            if self._states.get(key) == "ready":
                out.extend(recs)
        return out

    def counts(self):
        vals = list(self._states.values())
        return (
            sum(1 for v in vals if v == "ready"),
            sum(1 for v in vals if v == "loading"),
            sum(1 for v in vals if v == "failed"),
        )

    def error(self, machine, env):
        return None


def _live_fixture_source():
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("Lambda-Core", "Win")
    remote = ("Borealis", "Win")
    local_raw = {"id": "lambda-core-win-20260627-aaaa", "title": "Local wip",
                 "status": "active", "started_at": "2026-06-27T17:00:00",
                 "turn_count": 4, "state": "wip"}
    remote_raw = {"id": "borealis-win-20260627-bbbb", "title": "Remote work",
                  "status": "active", "started_at": "2026-06-27T16:30:00",
                  "turn_count": 2, "state": "wip"}
    records_by_key = {
        local: [derive.norm(local_raw, *local)],
        remote: [derive.norm(remote_raw, *remote)],
    }
    states = {local: "ready", remote: "ready",
              ("Wheatley", "Linux"): "loading"}

    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [
        ("Lambda-Core Win", "Lambda-Core", "Win", True),
        ("Borealis Win", "Borealis", "Win", True),
        ("Wheatley Linux", "Wheatley", "Linux", True),
    ]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: []
    src.make_loader = lambda: _FakeLoader(records_by_key, states)
    return src


def test_tui_live_multi_machine():
    src = _live_fixture_source()

    async def run():
        app = PickerApp(src, live=True)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            # Switch to the "All" tab so every ready machine interleaves.
            scr.machine_idx = 0
            # Drive a render tick so live records stream in from the loader.
            scr._tick()
            await pilot.pause()
            out = str(scr.render())
            assert "Agent Worktrees" in out
            # Both ready machines' worktrees stream into the All view.
            assert "Local wip" in out
            assert "Remote work" in out

    asyncio.run(run())


def test_live_loader_classify_fallback(monkeypatch):
    """A remote that rejects --classify is retried without it (older remotes)."""
    from agent_worktrees.picker_tui import data_ssh

    calls = []

    class _Proc:
        def __init__(self, rc, stdout="", stderr=""):
            self.returncode = rc
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv, timeout):
        calls.append(list(argv))
        joined = " ".join(argv)
        if "--classify" in joined:
            return _Proc(2, stderr="error: unrecognized arguments: --classify")
        return _Proc(0, stdout='{"worktrees": []}')

    monkeypatch.setattr(data_ssh, "_run", fake_run)
    src = data_ssh.Source(
        "Borealis", "Win",
        ["ssh", "borealis", "pwsh -NoProfile -Command 'p list --json "
         "--classify --mux-details --include-other-platforms'"],
        ready=True,
    )
    recs = data_ssh._fetch(src)
    assert recs == []
    assert len(calls) == 2
    assert "--classify" in " ".join(calls[0])
    assert "--classify" not in " ".join(calls[1])
    assert src.use_classify is False


def test_resume_decision_exits_with_worktree():
    """Enter on a worktree row opens the sub-menu; Open then resumes (#1343)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            scr.sel = ("L", 0)
            scr._activate()                 # opens the sub-menu, no exit
            await pilot.pause()
            assert scr.submenu is not None
            assert scr.submenu["actions"][0] == "Open"
            scr._key_submenu("enter")       # default-focused Open -> resume
            await pilot.pause()
        assert app.result is not None
        assert app.result["action"] == "resume"
        assert app.result["worktree_id"]  # real id carried from raw record
        assert app.result["is_local"] is True

    asyncio.run(run())


def test_open_submenu_no_mux_toggle():
    """Space toggles No-mux on Open; the resume decision carries it (#1343)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            scr.sel = ("L", 0)
            scr._open_submenu()
            scr._key_submenu("space")       # toggle No-mux while Open focused
            assert scr.submenu["no_mux"] is True
            scr._key_submenu("enter")
            await pilot.pause()
        assert app.result["action"] == "resume"
        assert app.result["options"]["no_mux"] is True

    asyncio.run(run())


def test_new_worktree_decision_exits():
    """New worktree… opens the options dialog; Create exits with a decision."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 0
            scr.btn_idx = 0
            scr.sel = ("BTN", 0)
            assert scr.active_button() == "N"
            scr._activate()                 # opens the options dialog (#1346)
            await pilot.pause()
            assert scr.optmenu is not None
            assert scr.optmenu["section"] == 1   # Create default-selected
            assert all(not o["on"] for o in scr.optmenu["opts"])
            scr._dlg_confirm(om=True)       # confirm Create, no options
            await pilot.pause()
        assert app.result is not None
        assert app.result["action"] == "new"
        assert app.result["is_local"] is True
        assert app.result["options"] == {
            "anchor": False, "bare": False,
            "no_mux": False, "local_model": False,
        }

    asyncio.run(run())


def test_new_worktree_no_mux_option():
    """The New-worktree options dialog exposes a No-Mux toggle; enabling it
    carries no_mux=True into the launch decision -- on-demand no-mux for a
    fresh worktree, not just Open/Resume (#1225)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 0
            scr.btn_idx = 0
            scr.sel = ("BTN", 0)
            scr._activate()                     # opens the options dialog
            await pilot.pause()
            assert scr.optmenu is not None
            labels = [o["label"] for o in scr.optmenu["opts"]]
            assert "No Mux" in labels
            nm = labels.index("No Mux")
            scr._key_scopedlg("up", om=True)    # Create button -> options
            assert scr.optmenu["section"] == 0
            while scr.optmenu["idx"] < nm:
                scr._key_scopedlg("down", om=True)
            scr._key_scopedlg("space", om=True)  # toggle No Mux on
            assert scr.optmenu["opts"][nm]["on"] is True
            scr._key_scopedlg("enter", om=True)  # options -> button row
            scr._key_scopedlg("enter", om=True)  # confirm Create
            await pilot.pause()
        assert app.result["action"] == "new"
        assert app.result["options"]["no_mux"] is True

    asyncio.run(run())


def _drain(ex, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ex.is_done():
            return
        time.sleep(0.02)
    raise AssertionError("executor did not finish")


def test_maintenance_executor_cleanup_states():
    from agent_worktrees.picker_tui import maintenance as mnt

    def boom():
        raise RuntimeError("kaboom")

    tasks = [
        ("a", lambda: {"removed": True, "ok": True}),
        ("b", lambda: {"removed": False, "ok": False, "reason": "unsafe"}),
        ("c", boom),
    ]
    ex = mnt.MaintenanceExecutor("cleanup", tasks)
    ex.start()
    _drain(ex)
    assert ex.state("a") == "done"
    assert ex.state("b") == "failed"
    assert ex.state("c") == "failed"
    assert ex.counts() == (1, 2, 0)
    assert ex.result("c")["reason"] == "kaboom"


def test_maintenance_executor_sync_uptodate_is_success():
    from agent_worktrees.picker_tui import maintenance as mnt

    tasks = [
        ("a", lambda: {"updated": True, "reason": "updated", "behind": 2}),
        ("b", lambda: {"updated": False, "reason": "up-to-date"}),
        ("c", lambda: {"updated": False, "reason": "ahead"}),
    ]
    ex = mnt.MaintenanceExecutor("sync", tasks)
    ex.start()
    _drain(ex)
    assert ex.state("a") == "done"   # fast-forwarded
    assert ex.state("b") == "done"   # already current
    assert ex.state("c") == "failed"  # skipped (ahead)


def test_cleanup_extra_confirm_gate_and_real_executor(monkeypatch):
    """Beyond-clean cleanup requires an extra confirm, then runs the executor."""
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_REAL_OPS", "1")
    from agent_worktrees.picker_tui import maintenance as mnt

    started = {}

    class _FakeExec:
        def __init__(self, op, tasks):
            started["n"] = len(tasks)

        def start(self):
            started["started"] = True

        def state(self, key):
            return "done"

        def is_done(self):
            return True

        def counts(self):
            return (started.get("n", 0), 0, 0)

    monkeypatch.setattr(mnt, "MaintenanceExecutor", _FakeExec)
    monkeypatch.setattr(
        mnt, "build_tasks",
        lambda op, recs, src, **kw: [(w["id4"], None) for w in recs])

    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            assert scr.real_ops is True
            scr.machine_idx = scr.local_index()
            scr._open_cleanup()
            # Select a beyond-clean scope (Unused) -> extra confirm required.
            for o in scr.cleanup["opts"]:
                o["on"] = o["label"] in ("Merged & finalized", "Unused")
            scr._dlg_confirm(False)
            assert scr.progress is not None
            assert scr.progress["armed"] is False   # gated
            assert scr.executor is None              # not started yet
            # Confirm -> arm -> executor starts.
            scr._key_progress("enter")
            assert scr.progress["armed"] is True
            assert started.get("started") is True
            scr._poll_executor()
            assert scr.progress["done"] is True
            await pilot.pause()

    asyncio.run(run())


# ---- prefetch cancellation (picker-perf bug: orphaned SSH list workers) ----

def _sleeper_argv(seconds: int):
    import sys
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_live_loader_spawn_tracks_and_unregisters():
    """``_spawn`` runs a child, returns a CompletedProcess, and leaves no
    tracked process behind once it completes."""
    import sys

    from agent_worktrees.picker_tui import data_ssh

    loader = data_ssh.LiveLoader(sources=[])
    proc = loader._spawn([sys.executable, "-c", "print('hi')"], timeout=15)
    assert proc.returncode == 0
    assert "hi" in proc.stdout
    with loader._procs_lock:
        assert loader._procs == []   # unregistered after completion


def test_live_loader_spawn_after_cancel_raises():
    """Once cancelled, the loader refuses to spawn new prefetch children."""
    import sys

    import pytest

    from agent_worktrees.picker_tui import data_ssh

    loader = data_ssh.LiveLoader(sources=[])
    loader.cancel()
    with pytest.raises(RuntimeError):
        loader._spawn([sys.executable, "-c", "pass"], timeout=5)


def test_live_loader_cancel_kills_inflight_prefetch():
    """The core fix: cancelling the loader kills an in-flight prefetch child so
    it can't orphan into a heavy git-classify after the picker exits."""
    import threading
    import time

    from agent_worktrees.picker_tui import data_ssh

    loader = data_ssh.LiveLoader(sources=[])
    result = {}

    def run():
        try:
            result["proc"] = loader._spawn(_sleeper_argv(30), timeout=30)
        except Exception as exc:  # pragma: no cover - failure path
            result["err"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()

    # Wait until the child is registered as in-flight.
    for _ in range(100):
        with loader._procs_lock:
            if loader._procs:
                break
        time.sleep(0.05)
    with loader._procs_lock:
        assert loader._procs, "prefetch child should be tracked while running"
        child = loader._procs[0]

    loader.cancel()
    t.join(timeout=15)

    assert not t.is_alive(), "spawn thread must unblock after cancel kills child"
    assert child.poll() is not None, "the prefetch child must be dead"
    with loader._procs_lock:
        assert loader._procs == [], "tracked child must be removed after cancel"


def test_screen_on_unmount_cancels_loader():
    """The picker's teardown hook cancels the loader (no orphaned prefetch)."""
    from agent_worktrees.picker_tui import data_local
    from agent_worktrees.picker_tui.engine import PickerScreen

    screen = PickerScreen(data_local, live=True)
    cancelled = {"v": False}

    class _FakeLoader:
        def cancel(self):
            cancelled["v"] = True

    screen.loader = _FakeLoader()
    screen.on_unmount()
    assert cancelled["v"] is True


def test_update_indicator_focus_glyph_and_refresh():
    """The launcher-stage update state drives the version glyph, the focusable
    refresh stop, and the refresh decision (#1430)."""
    from agent_worktrees.picker_tui.engine import PickerScreen

    src = _fixture_source()
    s = PickerScreen(src, live=False)
    s.setup()
    s.htab = 0
    s.frame = 0

    # current: informational only -> no focus stop, checkmark glyph.
    s.update_state = "current"
    assert ("V", 1) not in s.stops()
    assert not s._update_actionable()
    assert "\u2713" in s._update_seg(False).plain          # ✓

    # available: focusable refresh stop, refresh glyph.
    s.update_state = "available"
    assert ("V", 1) in s.stops()
    assert s._update_actionable()
    assert "\u21bb" in s._update_seg(False).plain          # ↻

    # checking: a spinner segment, still not a focus target.
    s.update_state = "checking"
    assert ("V", 1) not in s.stops()
    assert s._update_seg(False) is not None

    # idle: no segment at all.
    s.update_state = "idle"
    assert s._update_seg(False) is None

    # Enter on the refresh icon records an action=refresh decision.
    captured = {}
    s._decide = lambda d: captured.update(d)
    s.update_state = "available"
    s.sel = ("V", 1)
    s._activate()
    assert captured == {"action": "refresh"}


def test_real_ops_default_on_and_opt_out(monkeypatch):
    """Real Maintenance ops are the default; =0 forces the mock walker (#1420)."""
    src = _fixture_source()

    def _real_ops_for_env(value):
        if value is None:
            monkeypatch.delenv("AGENT_WORKTREES_PICKER_REAL_OPS", raising=False)
        else:
            monkeypatch.setenv("AGENT_WORKTREES_PICKER_REAL_OPS", value)

        async def _run():
            app = PickerApp(src, live=False)
            async with app.run_test(size=(118, 36)):
                return app.query_one(PickerScreen).real_ops

        return asyncio.run(_run())

    # Default (unset) -> real ops on; explicit "0" -> mock walker; "1" -> on.
    assert _real_ops_for_env(None) is True
    assert _real_ops_for_env("0") is False
    assert _real_ops_for_env("1") is True


def _wait_state(loader, machine, env, want, timeout=5.0):
    deadline = time.perf_counter() + timeout
    while loader.state(machine, env) != want:
        if time.perf_counter() > deadline:
            break
        time.sleep(0.01)
    return loader.state(machine, env)


def test_live_loader_local_streams_without_blocking_start(monkeypatch):
    """start() never blocks on the local source: it threads local too, so the
    picker paints and accepts input immediately while the (sometimes
    multi-second) local git-classification streams in just like the remotes.

    Regression guard for the freeze where a slow local load held the event-loop
    thread inside on_mount -> no paint, no arrow keys -- until every source
    (including the SSH fan-out) had resolved."""
    from agent_worktrees.picker_tui import data_ssh

    sentinel = [{"id4": "abcd", "machine": "lambda-core", "env": "Win"}]
    gate = threading.Event()

    def _slow_local(m=None, e=None):
        gate.wait(5)
        return sentinel

    monkeypatch.setattr(data_ssh.data_local, "load", _slow_local)

    local = data_ssh.Source("lambda-core", "Win", None, local=True)
    remote = data_ssh.Source("borealis", "Win", _sleeper_argv(30),
                             local=False, alias="borealis", shell="pwsh")
    loader = data_ssh.LiveLoader(sources=[local, remote])
    t0 = time.perf_counter()
    loader.start()
    elapsed = time.perf_counter() - t0
    try:
        # start() returned immediately -- it did NOT wait on the slow local load.
        assert elapsed < 1.0
        assert loader.state("lambda-core", "Win") == "loading"
        # Release the local load; it resolves on its thread and streams in.
        gate.set()
        assert _wait_state(loader, "lambda-core", "Win", "ready") == "ready"
        assert loader.records() == sentinel
        # The remote is still loading throughout -- never blocked on.
        assert loader.state("borealis", "Win") == "loading"
    finally:
        gate.set()
        loader.cancel()


def test_live_loader_reload_local_refetches(monkeypatch):
    """reload() re-fetches the local source on a thread so a post-maintenance
    refresh streams back in without blocking the UI (#1421 live re-render)."""
    from agent_worktrees.picker_tui import data_ssh

    seq = [[{"id4": "a"}], [{"id4": "b"}]]   # initial load, then reload
    calls = {"n": 0}

    def _load(m=None, e=None):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(data_ssh.data_local, "load", _load)
    local = data_ssh.Source("lambda-core", "Win", None, local=True)
    loader = data_ssh.LiveLoader(sources=[local])
    loader.start()
    assert _wait_state(loader, "lambda-core", "Win", "ready") == "ready"
    assert loader.records() == [{"id4": "a"}]
    assert loader.reload("lambda-core", "Win") is True
    assert _wait_state(loader, "lambda-core", "Win", "ready") == "ready"
    assert loader.records() == [{"id4": "b"}]
    assert loader.reload("nope", "X") is False


def test_escape_on_main_view_confirms_before_quit(monkeypatch):
    """Esc/q on a main pivot view opens a quit-confirm instead of instant-quit;
    Esc/n stays, y quits, Enter acts on the focused button (default Stay) (#1429)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)):
            scr = app.query_one(PickerScreen)
            quit_called = {"v": False}
            monkeypatch.setattr(app, "exit",
                                lambda *a, **k: quit_called.__setitem__("v", True))

            assert scr.quit_confirm is None
            scr.handle_key("escape")            # main view -> confirm, no exit
            assert scr.quit_confirm is not None
            assert quit_called["v"] is False

            scr.handle_key("escape")            # Esc in the confirm -> stay
            assert scr.quit_confirm is None
            assert quit_called["v"] is False

            scr.handle_key("q")                 # q also opens the confirm
            assert scr.quit_confirm is not None
            scr.handle_key("enter")             # default focus = Stay -> cancel
            assert scr.quit_confirm is None
            assert quit_called["v"] is False

            scr.handle_key("escape")            # open again
            scr.handle_key("y")                 # y -> quit
            assert quit_called["v"] is True

    asyncio.run(run())


def test_hidden_worktrees_filtered_and_toggle():
    """Bridge/system (kind=system) worktrees are hidden by default; Toggle-hidden
    reveals them, and the button appears only when there's something to reveal (#1422)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-aaaa", "title": "Real work", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 3, "state": "wip"},
        {"id": "lambda-core-win-ssss", "title": "daemon wt", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 0, "state": "wip",
         "kind": "system", "owner": "permanent-record"},
    ]
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]

    recs = src.load()
    assert recs[0]["hidden"] is False
    assert recs[1]["hidden"] is True

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)):
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            # Default: the system worktree is hidden; the toggle button appears.
            ids = {r["id4"] for r in scr.list_records()}
            assert "aaaa" in ids and "ssss" not in ids
            assert scr._hidden_count() == 1
            assert "TH" in scr.button_set()
            # Activate the Toggle-hidden button (index 1) -> reveal.
            scr.sel = ("BTN", 0)
            scr.btn_idx = 1
            scr._activate()
            assert scr.show_hidden is True
            ids = {r["id4"] for r in scr.list_records()}
            assert "ssss" in ids

    asyncio.run(run())


def test_toggle_hidden_button_absent_when_nothing_hidden():
    """With no bridge/system worktrees, the Toggle-hidden button stays off (#1422)."""
    src = _fixture_source()   # no kind=system rows

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)):
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            assert scr._hidden_count() == 0
            assert "TH" not in scr.button_set()

    asyncio.run(run())


def test_bridge_and_system_hidden_and_marked_distinctly():
    """Bridge and system worktrees are both hidden by default and marked
    distinctly in the title ([bridge] vs [system]) (#1424 tracking)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-aaaa", "title": "Real", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 3, "state": "wip"},
        {"id": "lambda-core-win-ssss", "title": "daemon", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 0, "state": "wip",
         "kind": "system", "owner": "permanent-record"},
        {"id": "lambda-core-win-bbbb", "title": "acp", "status": "active",
         "started_at": "2026-06-27T17:00:00", "turn_count": 0, "state": "wip",
         "kind": "bridge"},
    ]
    recs = {w["id"][-4:]: derive.norm(w, *local) for w in raws}
    assert recs["aaaa"]["hidden"] is False
    assert recs["ssss"]["hidden"] is True and recs["ssss"]["kind"] == "system"
    assert recs["bbbb"]["hidden"] is True and recs["bbbb"]["kind"] == "bridge"
    assert recs["ssss"]["title"].startswith("[system] ")
    assert recs["bbbb"]["title"].startswith("[bridge] ")

    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = "lambda-core · win"
    src.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)):
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            ids = {r["id4"] for r in scr.list_records()}
            assert "aaaa" in ids and "ssss" not in ids and "bbbb" not in ids
            assert scr._hidden_count() == 2          # bridge + system
            scr.show_hidden = True
            ids = {r["id4"] for r in scr.list_records()}
            assert "ssss" in ids and "bbbb" in ids

    asyncio.run(run())


def test_banner_version_tracks_build_info(monkeypatch):
    """The picker banner version is derived from the real package version
    (``_build_info`` -> package metadata), never a hand-maintained literal.

    Regression for the banner that silently froze at ``1.5.3-dev69`` while the
    package shipped dev97: a stale constant must not be able to reappear.
    """
    from agent_worktrees import _build_info
    from agent_worktrees.picker_tui import engine

    monkeypatch.setitem(_build_info.BUILD_INFO, "version", "9.9.9-devTEST")
    assert engine._resolve_version() == "9.9.9-devTEST"


def test_banner_version_falls_back_when_build_info_blank(monkeypatch):
    """With no build-info version, fall back to installed metadata / ``dev`` --
    never the old frozen ``1.5.3-dev69`` literal."""
    from agent_worktrees import _build_info
    from agent_worktrees.picker_tui import engine

    monkeypatch.setattr(_build_info, "BUILD_INFO", {}, raising=False)
    v = engine._resolve_version()
    assert v and v != "1.5.3-dev69"


def test_run_detaches_console_stdin(monkeypatch):
    """``data_ssh._run`` must give its child an empty stdin, never the console.

    Regression for the picker freezing keyboard input until the SSH load
    fan-out exits: an ``ssh`` child that inherits the terminal stdin reads the
    operator's keystrokes out from under Textual's input reader (which reads
    the same console handle), so keys never reach the app until ssh dies.
    """
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    data_ssh._run(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert seen.get("stdin") is subprocess.DEVNULL


def test_live_loader_spawn_detaches_console_stdin(monkeypatch):
    """The killable prefetch runner (``LiveLoader._spawn``) must also detach
    stdin so a backgrounded ssh load can't swallow keyboard input."""
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    class _FakeProc:
        returncode = 0

        def __init__(self, argv, **kwargs):
            seen.update(kwargs)

        def communicate(self, timeout=None):
            return ("{}", "")

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    loader = data_ssh.LiveLoader(sources=[])
    loader._spawn(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert seen.get("stdin") is subprocess.DEVNULL


def test_maintenance_ssh_detaches_console_stdin(monkeypatch):
    """Remote Maintenance ops run while the TUI is up, so their ssh child must
    detach stdin too (same input-theft class as the load fan-out)."""
    import subprocess

    from agent_worktrees.picker_tui import maintenance

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    maintenance._ssh_json(["ssh", "host", "agent-worktrees sync"], timeout=5)
    assert seen.get("stdin") is subprocess.DEVNULL


def test_profiles_pivot_survives_empty_host_cols():
    """Arrowing through the Profiles pivot with **no** configured host columns
    must not crash (issue #149).

    ``_fixture_source`` exposes no ``host_cols`` hook, so the engine falls back
    to the empty ``_DEFAULT_HOST_COLS`` -- the exact state a machines.yaml with
    no native-terminal copilot host produces. Previously this raised
    ``IndexError`` in ``_visible_pcols`` (grid render), ``IndexError`` in the
    PR-zone footer hint, and ``ZeroDivisionError`` on Left/Right (``% len(
    host_cols)``)."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            assert scr.host_cols == []          # precondition: no host columns
            scr.htab = 2                          # Profiles pivot
            scr.sel = scr.default_sel()           # lands in the PR zone
            await pilot.pause()                   # render body + footer (no crash)
            for k in ("right", "left", "space", "down", "up", "right"):
                await pilot.press(k)
                await pilot.pause()
            # Reaching here without an exception is the assertion.

    asyncio.run(run())


def test_run_spawns_ssh_off_console_on_windows(monkeypatch):
    """``data_ssh._run`` must keep the ssh child off our console on Windows so a
    failing ssh can't clear the console VT-input mode and break arrows (#148)."""
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(data_ssh.os, "name", "nt")
    monkeypatch.setattr(data_ssh, "_CREATE_NO_WINDOW", 0x08000000)
    monkeypatch.setattr(subprocess, "run", fake_run)
    data_ssh._run(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert seen.get("creationflags", 0) & 0x08000000
    assert seen.get("stdin") is subprocess.DEVNULL


def test_run_no_creationflags_on_posix(monkeypatch):
    """On POSIX ``_run`` passes no Windows creationflags (would error)."""
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(data_ssh.os, "name", "posix")
    monkeypatch.setattr(subprocess, "run", fake_run)
    data_ssh._run(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert "creationflags" not in seen


def test_spawn_spawns_ssh_off_console_on_windows(monkeypatch):
    """``LiveLoader._spawn`` must add CREATE_NO_WINDOW on Windows (#148),
    alongside the existing CREATE_NEW_PROCESS_GROUP."""
    import subprocess

    from agent_worktrees.picker_tui import data_ssh

    seen = {}

    class _FakeProc:
        returncode = 0

        def __init__(self, argv, **kwargs):
            seen.update(kwargs)

        def communicate(self, timeout=None):
            return ("{}", "")

        def poll(self):
            return 0

    monkeypatch.setattr(data_ssh.os, "name", "nt")
    monkeypatch.setattr(data_ssh, "_CREATE_NO_WINDOW", 0x08000000)
    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    loader = data_ssh.LiveLoader(sources=[])
    loader._spawn(["ssh", "host", "agent-worktrees list"], timeout=5)
    assert seen.get("creationflags", 0) & 0x08000000
    assert seen.get("stdin") is subprocess.DEVNULL


def test_maintenance_ssh_off_console_on_windows(monkeypatch):
    """Remote Maintenance ssh ops must also run off our console on Windows."""
    import subprocess

    from agent_worktrees.picker_tui import maintenance

    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(maintenance.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    maintenance._ssh_json(["ssh", "host", "agent-worktrees sync"], timeout=5)
    assert seen.get("creationflags", 0) & 0x08000000
    assert seen.get("stdin") is subprocess.DEVNULL


# --- Registered (cross-plugin) pivot: TASKS ---------------------------------

def _write_tasks_manifest(directory):
    import json
    manifest = {
        "label": "Tasks",
        "after": "Worktrees",
        "list": ["true"],
        "entry": {
            "id": "id", "title": "title", "worktree": "target_worktree",
            "subtitle": "repo_name", "badges": ["labels"],
        },
        "empty_hint": "No proposed tasks.",
        "actions": [
            {"key": "open", "label": "Open into a CLI session", "run": ["echo", "{id}"]},
            {"key": "abandon", "label": "Abandon", "run": ["echo", "{task_id}"],
             "confirm": True},
        ],
    }
    (directory / "agent-dispatch.json").write_text(json.dumps(manifest), encoding="utf-8")


class _FakeRuntime:
    def __init__(self, rows):
        self.rows = rows
        self.actions = []
        self.invalidated = False

    def ensure(self, machine):
        pass

    def get(self, machine):
        return ("ready", self.rows, "")

    def invalidate(self, machine=None):
        self.invalidated = True

    def run_action(self, action, ctx):
        self.actions.append((action.key, dict(ctx)))
        return (True, "done")


def _seed_fake_tasks(scr, rows):
    reg = scr.registered_pivots[0]
    rt = _FakeRuntime(rows)
    scr._pivot_runtimes[reg.name] = rt
    return rt


def test_registered_pivot_inserted_between_worktrees_and_maintenance(tmp_path, monkeypatch):
    from agent_worktrees.picker_tui import pivots as pivots_mod

    d = tmp_path / "pivots"
    d.mkdir()
    _write_tasks_manifest(d)
    monkeypatch.setenv(pivots_mod.PIVOTS_DIR_ENV, str(d))

    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            assert scr.htabs == ["Worktrees", "Tasks", "Maintenance", "Profiles"]
            assert scr._kind(1) == "registered"
            assert scr._kind(2) == "maintenance"     # built-ins shifted, not renumbered logic
            assert scr._kind(3) == "profiles"

    asyncio.run(run())


def test_registered_pivot_lists_and_navigates(tmp_path, monkeypatch):
    from agent_worktrees.picker_tui import pivots as pivots_mod

    d = tmp_path / "pivots"
    d.mkdir()
    _write_tasks_manifest(d)
    monkeypatch.setenv(pivots_mod.PIVOTS_DIR_ENV, str(d))

    rows = [
        {"id": "t1", "title": "First task", "target_worktree": "wt-a",
         "repo_name": "repoA", "labels": ["handoff"]},
        {"id": "t2", "title": "Second task", "target_worktree": None,
         "repo_name": "repoB", "labels": []},
    ]
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            _seed_fake_tasks(scr, rows)
            scr.htab = scr.htabs.index("Tasks")
            scr.sel = scr.default_sel()
            await pilot.pause()

            assert scr._task_rows() == rows
            # One ('T', i) stop per task; machine sub-nav ('M') present.
            zones = [z for z, _ in scr.stops()]
            assert zones.count("T") == 2
            assert ("M", 0) in scr.stops()
            # Grouped by worktree.
            groups = dict((g, [i for i, _ in items]) for g, items in scr._task_groups())
            assert "wt-a" in groups
            # Body renders the task titles.
            plain = scr.render().plain
            assert "First task" in plain
            assert "Second task" in plain

    asyncio.run(run())


def test_registered_pivot_action_menu_runs_and_invalidates(tmp_path, monkeypatch):
    from agent_worktrees.picker_tui import pivots as pivots_mod

    d = tmp_path / "pivots"
    d.mkdir()
    _write_tasks_manifest(d)
    monkeypatch.setenv(pivots_mod.PIVOTS_DIR_ENV, str(d))

    rows = [{"id": "t1", "title": "First task", "target_worktree": "wt-a",
             "repo_name": "repoA", "labels": ["handoff"]}]
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            rt = _seed_fake_tasks(scr, rows)
            scr.htab = scr.htabs.index("Tasks")
            # Focus the first task row.
            scr.sel = ("T", 0)
            await pilot.pause()

            # Enter opens the action sub-menu with the manifest's actions.
            scr._open_task_menu()
            assert scr.task_menu is not None
            assert [a.label for a in scr.task_menu["actions"]] == [
                "Open into a CLI session", "Abandon"]

            # Select "Abandon" and run it.
            scr.task_menu_idx = 1
            scr._key_task_menu("enter")
            assert scr.task_menu is None
            assert rt.actions and rt.actions[0][0] == "abandon"
            # Placeholders resolved: {task_id} -> the entry id.
            _key, ctx = rt.actions[0]
            assert ctx["task_id"] == "t1"
            assert ctx["machine"] == "lambda-core"
            assert rt.invalidated is True

    asyncio.run(run())


def test_registered_pivot_switch_pivot_cycles_left_rail(tmp_path, monkeypatch):
    from agent_worktrees.picker_tui import pivots as pivots_mod

    d = tmp_path / "pivots"
    d.mkdir()
    _write_tasks_manifest(d)
    monkeypatch.setenv(pivots_mod.PIVOTS_DIR_ENV, str(d))

    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            await pilot.pause()
            # Profiles is hosted under ⚙ Configuration, off the left rail (#1426).
            left = scr._left_pivots()
            assert len(left) == 3
            kinds = []
            for _ in range(len(left)):
                kinds.append(scr._kind())
                scr._switch_pivot(1)
            assert kinds == ["worktrees", "registered", "maintenance"]
            assert scr.htab == 0                    # wrapped back to Worktrees
            # The left cycle never lands on the config-hosted Profiles pivot.
            seen = set()
            for _ in range(6):
                seen.add(scr._kind())
                scr._switch_pivot(1)
            assert "profiles" not in seen

    asyncio.run(run())
