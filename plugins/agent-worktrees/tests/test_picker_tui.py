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
    """Enter on a worktree row exits the TUI with a resume decision."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.machine_idx = scr.local_index()
            scr.sel = ("L", 0)
            scr._activate()
            await pilot.pause()
        assert app.result is not None
        assert app.result["action"] == "resume"
        assert app.result["worktree_id"]  # real id carried from raw record
        assert app.result["is_local"] is True

    asyncio.run(run())


def test_new_worktree_decision_exits():
    """The New Worktree button exits the TUI with a create decision."""
    src = _fixture_source()

    async def run():
        app = PickerApp(src, live=False)
        async with app.run_test(size=(118, 36)) as pilot:
            scr = app.query_one(PickerScreen)
            scr.htab = 0
            scr.btn_idx = 0
            scr.sel = ("BTN", 0)
            assert scr.active_button() == "N"
            scr._activate()
            await pilot.pause()
        assert app.result is not None
        assert app.result["action"] == "new"
        assert app.result["is_local"] is True
        assert app.result["options"] == {}

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
