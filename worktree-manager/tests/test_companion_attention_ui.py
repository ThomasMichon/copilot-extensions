"""Actual Textual attention nudge, polling, cancellation and stale-read controls."""

import asyncio
from threading import Event

import pytest
from textual.widgets import Button, Static, TabbedContent

from worktree_manager import mux_companion as companion
from worktree_manager import companion_lifecycle as lifecycle
from test_companion_attention import envelope


def data(session_id="session-a"):
    return companion._CompanionData(
        worktree={"id": "selected", "path": "fixture-path"},
        sessions=[{"id": session_id, "is_head": True}],
        bundle={"worktree_id": "selected", "facts": {}},
    )


@pytest.fixture(autouse=True)
def no_ambient_engine(monkeypatch):
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: data())
    monkeypatch.setattr(companion.ca, "read_session_attention", lambda *a, **kw: {
        "attention": envelope(), "error": None,
    })
    monkeypatch.setattr(lifecycle, "cli_alive", lambda *a, **kw: True)
    monkeypatch.setattr(lifecycle, "observer_visible", lambda *a, **kw: True)


def test_nudge_and_click_only_navigate_never_answer(monkeypatch):
    from worktree_manager import companion_pane as pane

    actions = []
    monkeypatch.setattr(pane, "switch", lambda action, *a, **kw: actions.append(action))
    monkeypatch.setattr(companion.hc, "trigger_cutover",
                        lambda *a, **kw: pytest.fail("attention invoked cutover"))

    async def check():
        app = companion.MuxCompanionApp(cli_pane="%1")
        async with app.run_test(size=(80, 30)) as pilot:
            await app.workers.wait_for_complete()
            assert "last reported: awaiting-operator" in app.query_one("#attention", Static).content.plain
            assert str(app.query_one("#view-btn", Button).label) == "CLI (last: input)"
            assert app.query_one("#views", TabbedContent).active == "summary-tab"
            assert not actions
            await pilot.click("#view-btn")
            await app.workers.wait_for_complete()
            assert actions == ["return"]
            assert app.query_one("#cutover-btn", Button).disabled

    asyncio.run(check())


def test_polling_updates_state_backs_off_and_hidden_view_does_not_read(monkeypatch):
    calls, visible = [], [True]
    monkeypatch.setattr(lifecycle, "observer_visible", lambda *a, **kw: visible[0])

    def read(*a, **kw):
        calls.append(1)
        return {"attention": envelope("idle"), "error": None} if len(calls) == 1 else {
            "attention": None, "error": "attention-read-failed",
        }

    monkeypatch.setattr(companion.ca, "read_session_attention", read)

    async def check():
        app = companion.MuxCompanionApp(cli_pane="%1")
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert str(app.query_one("#view-btn", Button).label) == "CLI (last: idle)"
            app._poll_attention()
            await app.workers.wait_for_complete()
            assert len(calls) == 1
            app._attention_next = 0
            visible[0] = False
            app._poll_attention()
            await app.workers.wait_for_complete()
            assert len(calls) == 1
            visible[0] = None
            app._poll_attention()
            await app.workers.wait_for_complete()
            assert len(calls) == 1
            visible[0] = True
            app._poll_attention()
            await app.workers.wait_for_complete()
            assert len(calls) == 2
            assert "unknown" in app.query_one("#attention", Static).content.plain
            assert str(app.query_one("#view-btn", Button).label) == "Return to CLI"
            assert app._attention_next - companion.time.monotonic() > 25
            app._poll_attention()
            await app.workers.wait_for_complete()
            assert len(calls) == 2
            app.query_one("#views", TabbedContent).active = "recovery-tab"
            await pilot.pause()
            assert app._data.error is None

    asyncio.run(check())


def test_ambiguous_head_and_provider_failure_preserve_other_views(monkeypatch):
    current = data()
    current.sessions.append({"id": "other", "is_head": True})
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: current)
    monkeypatch.setattr(companion.ca, "read_session_attention",
                        lambda *a, **kw: pytest.fail("ambiguous head read"))

    async def check():
        app = companion.MuxCompanionApp()
        async with app.run_test():
            await app.workers.wait_for_complete()
            assert app._data.attention_error == "head-session-unavailable"
            assert app._data.attention is None
            assert app.query_one("#views", TabbedContent).active == "summary-tab"
            assert app._data.error is None

    asyncio.run(check())


def test_blocked_read_is_singleflight_and_escape_cancels(monkeypatch):
    entered, stopped = Event(), Event()
    calls = []
    monkeypatch.setattr(companion.cc, "cancellable_read_runner", lambda cancelled, **kw: cancelled)

    def read(*a, runner, **kw):
        calls.append(1)
        entered.set()
        assert runner.wait(5)
        stopped.set()
        return {"attention": None, "error": "cancelled"}

    monkeypatch.setattr(companion.ca, "read_session_attention", read)

    async def check():
        app = companion.MuxCompanionApp()
        async with app.run_test() as pilot:
            assert await asyncio.to_thread(entered.wait, 2)
            app._poll_attention(force=True)
            await pilot.pause()
            assert len(calls) == 1
            await pilot.press("escape")
        assert await asyncio.to_thread(stopped.wait, 2)

    asyncio.run(check())


def test_refresh_clears_nudge_and_late_read_cannot_replace_new_head(monkeypatch):
    entered, release, finished = Event(), Event(), Event()
    loads, calls = [], []

    def load(*a, **kw):
        loads.append(1)
        return data("session-a" if len(loads) == 1 else "successor")

    def read(project, worktree_id, session_id, **kw):
        calls.append(session_id)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            finished.set()
        value = envelope("idle" if session_id == "successor" else "awaiting-operator")
        value["session_id"] = session_id
        return {"attention": value, "error": None}

    monkeypatch.setattr(companion, "_load_current_worktree", load)
    monkeypatch.setattr(companion.ca, "read_session_attention", read)

    async def check():
        app = companion.MuxCompanionApp()
        try:
            async with app.run_test() as pilot:
                assert await asyncio.to_thread(entered.wait, 2)
                app.action_refresh()
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert app._data.attention["session_id"] == "successor"
                release.set()
                assert await asyncio.to_thread(finished.wait, 2)
                await pilot.pause()
                assert app._data.attention["rest"] == "idle"
                assert app._data.attention["session_id"] == "successor"
        finally:
            release.set()

    asyncio.run(check())


def test_changed_head_holds_polling_until_explicit_refresh(monkeypatch):
    calls = []

    def read(*a, **kw):
        calls.append(1)
        return {"attention": None, "error": "head-changed-refresh-required"}

    monkeypatch.setattr(companion.ca, "read_session_attention", read)

    async def check():
        app = companion.MuxCompanionApp()
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert "Refresh required" in app.query_one("#attention", Static).content.plain
            app._attention_next = 0
            app._poll_attention()
            await app.workers.wait_for_complete()
            assert len(calls) == 1
            app.action_refresh()
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert len(calls) == 2

    asyncio.run(check())


def test_refresh_replacement_remains_singleflight_across_another_poll(monkeypatch):
    first, second, release = Event(), Event(), Event()
    calls = []
    monkeypatch.setattr(companion.cc, "cancellable_read_runner", lambda cancelled, **kw: cancelled)

    def read(*a, runner, **kw):
        calls.append(1)
        if len(calls) == 1:
            first.set()
            assert runner.wait(5)
            return {"attention": None, "error": "cancelled"}
        second.set()
        assert release.wait(5)
        return {"attention": envelope("idle"), "error": None}

    monkeypatch.setattr(companion.ca, "read_session_attention", read)

    async def check():
        app = companion.MuxCompanionApp()
        try:
            async with app.run_test() as pilot:
                assert await asyncio.to_thread(first.wait, 2)
                app.action_refresh()
                assert await asyncio.to_thread(second.wait, 2)
                assert app._attention_busy
                app._attention_next = 0
                app._poll_attention()
                await pilot.pause()
                assert len(calls) == 2
                release.set()
                await app.workers.wait_for_complete()
                assert app._data.attention["rest"] == "idle"
                assert not app._attention_busy
                assert app._attention_next > companion.time.monotonic()
        finally:
            release.set()

    asyncio.run(check())


def test_actual_companion_process_cancels_blocked_attention_child(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from worktree_manager import engine_process
    from test_companion_attention import head

    started = tmp_path / "attention-started"
    producer = tmp_path / "blocked attention engine.py"
    producer.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        "if sys.argv[1:] == ['head-session','--worktree','selected','--json']:\n"
        f"    print({json.dumps(head())!r})\n"
        "else:\n"
        "    assert sys.argv[1:] == ['session-attention','session-a','--json']\n"
        f"    Path({str(started)!r}).write_text('started')\n"
        "    time.sleep(60)\n", encoding="utf-8",
    )
    driver = tmp_path / "companion driver.py"
    driver.write_text(
        "import asyncio,sys\nfrom pathlib import Path\n"
        "from worktree_manager import mux_companion as companion\n"
        "from worktree_manager import engine_client as ec\n"
        f"ec.set_engine_command([sys.executable, {str(producer)!r}])\n"
        "companion._load_current_worktree=lambda *a,**kw: companion._CompanionData("
        "worktree={'id':'selected'},sessions=[{'id':'session-a','is_head':True}])\n"
        "original=ec.run_json\n"
        "def long_read(*a,**kw):\n"
        "    kw['timeout']=60\n"
        "    return original(*a,**kw)\n"
        "ec.run_json=long_read\n"
        "async def main():\n"
        "    app=companion.MuxCompanionApp()\n"
        "    async with app.run_test() as pilot:\n"
        "        for _ in range(100):\n"
        f"            if Path({str(started)!r}).exists(): break\n"
        "            await asyncio.sleep(0.02)\n"
        f"        assert Path({str(started)!r}).exists()\n"
        "        await pilot.press('escape')\n"
        "asyncio.run(main())\n", encoding="utf-8",
    )
    result = engine_process.run(
        [sys.executable, str(driver)], timeout=8,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert started.exists()
