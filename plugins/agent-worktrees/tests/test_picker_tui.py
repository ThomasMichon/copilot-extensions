"""Headless render test for the ported Worktree Picker TUI (slice 1).

Hermetic: drives the engine over a fixture source (no real tracking/git/SSH),
asserting it boots and renders real-shaped records with the canonical state
vocabulary.
"""
from __future__ import annotations

import asyncio
import datetime
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
