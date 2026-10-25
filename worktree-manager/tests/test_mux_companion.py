"""Tests for the Mux Companion (visions/mux-companion). Most cover pure
rendering/resolution logic (status explanation text, current-worktree
resolution, action-message text) without any Textual rendering; one
end-to-end test (mux-companion-manual-cutover-diagnostics, #4369) drives a
real compose/mount/button-press cycle via ``run_test()`` to verify the
"Cut over"/"Refresh" wiring itself, not just the underlying pure functions.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from worktree_manager import engine_client as ec
from worktree_manager import companion_client as cc
from worktree_manager import handoff_client as hc
from worktree_manager import mux_companion as companion


def _bundle(worktree_id="wt-ab12", *, confirmed=True):
    return {
        "worktree_id": worktree_id,
        "facts": {
            "disposition": {
                "value": {"title": "Build a widget", "summary": "Reviewing the design"},
                "confirmed": confirmed, "observed_at": 1700000000,
            },
            "claims": {
                "value": {"owner_ref": "host/project/parent", "resources": [
                    {"kind": "worktree", "ref": "host/project/child",
                     "state": "active", "note": "[bold]literal note[/bold]"},
                    {"kind": "pr", "ref": "https://example.com/pr/7",
                     "state": "released"},
                ]},
                "confirmed": confirmed, "observed_at": 1700000001,
            },
        },
    }


@pytest.fixture(autouse=True)
def _fake_status_bundle(monkeypatch):
    original = cc.worktree_status_bundle
    monkeypatch.setattr(cc, "worktree_status_bundle", lambda *a, **k: _bundle())
    return original


def test_closure_explanation_final():
    row = {
        "state": "completed",
        "closure": {"label": "FINAL", "evidence_mode": "refreshed", "blockers": []},
    }
    lines = companion._closure_explanation(row)
    assert lines[0].startswith("FINAL")


def test_closure_explanation_merged_lists_blockers_and_cached_evidence():
    row = {
        "state": "completed",
        "closure": {
            "label": "MERGED",
            "evidence_mode": "cached",
            "blockers": [
                {"code": "held-claims", "count": 2},
                {"code": "open-follow-ups", "count": 1},
            ],
        },
    }
    lines = companion._closure_explanation(row)
    joined = "\n".join(lines)
    assert joined.startswith("MERGED")
    assert "cached/fetch-free" in joined
    assert "held resource claims (2)" in joined
    follow_up_line = next(line for line in lines if "open follow-ups" in line)
    assert "(1)" not in follow_up_line


def test_closure_explanation_merged_without_descriptor_falls_back():
    row = {"state": "completed", "status": "finalized"}
    lines = companion._closure_explanation(row)
    assert lines[0].startswith("MERGED")


def test_closure_explanation_plain_state():
    row = {"state": "wip"}
    lines = companion._closure_explanation(row)
    assert "not yet on the default branch" in lines[0]


def test_closure_explanation_includes_sync_tag():
    row = {"state": "wip", "ahead": 2, "behind": 1}
    lines = companion._closure_explanation(row)
    assert any("2 ahead" in line and "1 behind" in line for line in lines)


def test_fallback_label_unknown_state():
    assert companion._fallback_label({}) == "UNKNOWN"


def test_load_current_worktree_reports_untracked_path(monkeypatch):
    monkeypatch.setattr(
        ec, "current_worktree_status",
        lambda **k: {"version": 1, "error": "not a tracked git worktree"},
    )
    data = companion._load_current_worktree("/nowhere")
    assert data.error == "not a tracked git worktree"
    assert data.worktree == {}
    assert data.sessions == []


def test_load_current_worktree_surfaces_engine_error(monkeypatch):
    def _raise(**kwargs):
        raise ec.EngineError("the agent-worktrees engine is not installed")

    monkeypatch.setattr(ec, "current_worktree_status", _raise)
    data = companion._load_current_worktree("/nowhere")
    assert data.error == "the agent-worktrees engine is not installed"


def test_load_current_worktree_degrades_when_sessions_unavailable(monkeypatch):
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)

    def _raise(*args, **kwargs):
        raise ec.EngineError("boom")

    monkeypatch.setattr(ec, "list_worktree_sessions", _raise)

    data = companion._load_current_worktree("/w")

    assert data.error is None
    assert data.worktree == payload
    assert data.sessions == []


def test_load_current_worktree_fetches_sessions_when_tracked(monkeypatch):
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "completed"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)
    session_rows = [{"id": "s1", "is_head": True}]
    monkeypatch.setattr(
        ec, "list_worktree_sessions", lambda *a, **k: session_rows
    )

    data = companion._load_current_worktree("/w")

    assert data.worktree == payload
    assert data.sessions == session_rows
    assert data.error is None


def test_load_current_worktree_skips_sessions_when_untracked(monkeypatch):
    payload = {"version": 1, "id": None, "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)
    calls = []
    monkeypatch.setattr(
        ec, "list_worktree_sessions",
        lambda *a, **k: calls.append(1) or [],
    )

    data = companion._load_current_worktree("/w")

    assert not calls  # no id -> never asks the engine for sessions
    assert data.sessions == []


def test_load_current_worktree_includes_pending_handoff(monkeypatch):
    """#3307 Phase 8: the resolved worktree path feeds the pending-handoff
    lookup, and its result surfaces on the loaded data."""
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)
    monkeypatch.setattr(ec, "list_worktree_sessions", lambda *a, **k: [])
    seen = {}

    def _fake_pending(project, worktree_path, **kwargs):
        seen["worktree_path"] = worktree_path
        return {"title": "Finish the retry-budget fix", "sessionId": "sess-1"}

    monkeypatch.setattr(hc, "pending_handoff", _fake_pending)

    data = companion._load_current_worktree("/w")

    assert seen["worktree_path"] == "/w"
    assert data.pending_handoff == {
        "title": "Finish the retry-budget fix", "sessionId": "sess-1"}


def test_load_current_worktree_degrades_when_pending_handoff_unavailable(monkeypatch):
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: payload)
    monkeypatch.setattr(ec, "list_worktree_sessions", lambda *a, **k: [])

    def _raise(*a, **k):
        raise ec.EngineError("boom")

    monkeypatch.setattr(hc, "pending_handoff", _raise)

    data = companion._load_current_worktree("/w")

    assert data.error is None
    assert data.pending_handoff is None


def test_status_text_shows_pending_handoff_headline():
    """#3307 Phase 8: a pending handoff's title renders as a read-only,
    situational-awareness line -- never shown when there's no pending baton."""
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._action_message = None
    app._data = companion._CompanionData(
        worktree={"state": "wip"},
        pending_handoff={"title": "Finish the retry-budget fix"},
    )
    text = app._status_text().plain
    assert "Pending handoff: Finish the retry-budget fix" in text

    app._data = companion._CompanionData(worktree={"state": "wip"})
    assert "Pending handoff" not in app._status_text().plain


def test_status_text_includes_action_message_when_set():
    """#mux-companion-manual-cutover-diagnostics: a "Cut over"/"Refresh"
    outcome renders below the closure explanation, once set."""
    from rich.text import Text

    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._data = companion._CompanionData(worktree={"state": "wip"})
    app._action_message = Text("\u2713 Cut over: head old \u2192 new")
    assert "Cut over: head old" in app._status_text().plain


def test_cut_over_reports_change_and_updates_action_message(monkeypatch):
    """#mux-companion-manual-cutover-diagnostics: pressing "Cut over" shells
    out to handoff_client.trigger_cutover and reflects a real change."""
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._cwd = "/w"
    app._action_message = None
    app._data = companion._CompanionData(
        worktree={"id": "wt-ab12", "state": "wip"},
        pending_handoff={"title": "Finish the retry-budget fix"},
    )
    calls = []

    def _fake_trigger(project, worktree_id, **kwargs):
        calls.append(worktree_id)
        return {
            "worktree_id": worktree_id, "head_before": "old-sess",
            "head_after": "new-sess", "changed": True,
        }

    monkeypatch.setattr(hc, "trigger_cutover", _fake_trigger)
    monkeypatch.setattr(app, "_refresh_view", lambda **k: None)

    app._cut_over()

    assert calls == ["wt-ab12"]
    assert "old-sess" in app._action_message.plain
    assert "new-sess" in app._action_message.plain


def test_cut_over_reports_no_actionable_handoff(monkeypatch):
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._cwd = "/w"
    app._action_message = None
    app._data = companion._CompanionData(worktree={"id": "wt-ab12", "state": "wip"})

    monkeypatch.setattr(
        hc, "trigger_cutover",
        lambda project, worktree_id, **k: {"changed": False},
    )
    monkeypatch.setattr(app, "_refresh_view", lambda **k: None)

    app._cut_over()

    assert "no actionable pending handoff" in app._action_message.plain


def test_cut_over_surfaces_engine_error_visibly(monkeypatch):
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._cwd = "/w"
    app._action_message = None
    app._data = companion._CompanionData(worktree={"id": "wt-ab12", "state": "wip"})

    def _raise(project, worktree_id, **k):
        raise ec.EngineError("the agent-worktrees engine is not installed")

    monkeypatch.setattr(hc, "trigger_cutover", _raise)
    refreshed = []
    monkeypatch.setattr(app, "_refresh_view", lambda **k: refreshed.append(k))

    app._cut_over()

    assert "Cut over failed" in app._action_message.plain
    assert refreshed  # still repaints so the error is actually shown


def test_cut_over_is_a_no_op_without_a_resolved_worktree_id(monkeypatch):
    app = companion.MuxCompanionApp.__new__(companion.MuxCompanionApp)
    app._cwd = "/w"
    app._action_message = None
    app._data = companion._CompanionData(worktree={"state": "wip"})  # no "id"
    calls = []
    monkeypatch.setattr(hc, "trigger_cutover", lambda *a, **k: calls.append(1))

    app._cut_over()

    assert not calls


def test_end_to_end_cut_over_button_press_updates_the_live_view(monkeypatch):
    """#mux-companion-manual-cutover-diagnostics: a real compose/mount/
    button-press cycle, not just the pure `_cut_over` function -- verifies
    the "Cut over" button is enabled exactly when a pending handoff exists,
    disabled after a successful cutover clears it, and the lineage table
    reflects the reloaded data."""
    payload = {"version": 1, "id": "wt-ab12", "path": "/w", "state": "wip"}
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: dict(payload))
    session_calls = {"n": 0}

    def _fake_sessions(*a, **k):
        session_calls["n"] += 1
        if session_calls["n"] == 1:
            return [{"id": "old-sess", "is_head": True, "state": "handed-off"}]
        return [
            {"id": "old-sess", "is_head": False, "state": "handed-off"},
            {"id": "new-sess", "is_head": True, "state": "active"},
        ]

    monkeypatch.setattr(ec, "list_worktree_sessions", _fake_sessions)
    pending_calls = {"n": 0}

    def _fake_pending(*a, **k):
        pending_calls["n"] += 1
        if pending_calls["n"] == 1:
            return {"title": "Finish the retry-budget fix"}
        return None  # the baton is consumed once cut over

    monkeypatch.setattr(hc, "pending_handoff", _fake_pending)
    monkeypatch.setattr(
        hc, "trigger_cutover",
        lambda project, worktree_id, **k: {
            "head_before": "old-sess", "head_after": "new-sess", "changed": True},
    )

    async def run():
        app = companion.MuxCompanionApp(cwd="/w")
        async with app.run_test() as pilot:
            from textual.widgets import Button, DataTable, TabbedContent

            await app.workers.wait_for_complete()
            app.query_one("#views", TabbedContent).active = "recovery-tab"
            await pilot.pause()
            cutover_btn = app.query_one("#cutover-btn", Button)
            assert cutover_btn.disabled is False
            table = app.query_one("#lineage", DataTable)
            assert table.row_count == 1

            await pilot.click("#cutover-btn")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert "old-sess" in app._action_message.plain
            assert "new-sess" in app._action_message.plain
            table = app.query_one("#lineage", DataTable)
            assert table.row_count == 2
            assert app.query_one("#cutover-btn", Button).disabled is True

    asyncio.run(run())


def test_summary_and_claims_preserve_reported_values_and_evidence():
    data = companion._CompanionData(bundle=_bundle(confirmed=False))
    summary = companion._summary_text(data).plain
    assert "Build a widget" in summary
    assert "Reviewing the design" in summary
    assert "2 recorded resource(s)" in summary
    assert "Last known; unconfirmed" in summary
    assert "2023-11-14" in summary
    evidence, claims = companion._claims_view(data)
    assert "host/project/parent" in evidence.plain
    assert claims[0]["state"] == "active"
    assert claims[1]["state"] == "released"
    assert claims[0]["note"] == "[bold]literal note[/bold]"


@pytest.mark.parametrize("value", [None, {}, {"resources": None}, {"resources": ["bad"]}])
def test_invalid_or_missing_claims_never_report_empty_ledger(value):
    bundle = _bundle()
    bundle["facts"]["claims"]["value"] = value
    data = companion._CompanionData(bundle=bundle)
    evidence, claims = companion._claims_view(data)
    assert claims == []
    assert "No recorded resources" not in evidence.plain
    assert "unavailable" in (evidence.plain + companion._summary_text(data).plain).lower()


def test_confirmed_empty_claims_are_distinct_from_unavailable():
    bundle = _bundle()
    bundle["facts"]["claims"]["value"] = {"resources": [], "owner_ref": None}
    evidence, claims = companion._claims_view(companion._CompanionData(bundle=bundle))
    assert claims == []
    assert "No recorded resources" in evidence.plain
    failed = companion._CompanionData(bundle_error="selected engine unavailable")
    evidence, claims = companion._claims_view(failed)
    assert "selected engine unavailable" in evidence.plain
    assert "No recorded resources" not in evidence.plain


def test_load_bundle_failure_preserves_status_and_recovery(monkeypatch):
    monkeypatch.setattr(ec, "current_worktree_status",
                        lambda **k: {"id": "wt-ab12", "state": "wip"})
    monkeypatch.setattr(ec, "list_worktree_sessions",
                        lambda *a, **k: [{"id": "old", "is_head": True}])

    def fail(*a, **k):
        raise ec.EngineError("older engine has no status bundle")

    monkeypatch.setattr(cc, "worktree_status_bundle", fail)
    data = companion._load_current_worktree("/w")
    assert data.error is None
    assert data.sessions[0]["id"] == "old"
    assert data.bundle_error == "older engine has no status bundle"


def test_missing_status_payload_is_visible(monkeypatch):
    monkeypatch.setattr(ec, "current_worktree_status", lambda **k: None)
    assert companion._load_current_worktree("/w").error


@pytest.mark.parametrize("size", [(42, 16), (120, 36)])
def test_textual_summary_default_claims_and_recovery_tabs(monkeypatch, size):
    from textual.widgets import DataTable, Static, TabbedContent

    monkeypatch.setattr(ec, "current_worktree_status",
                        lambda **k: {"id": "wt-ab12", "state": "wip"})
    monkeypatch.setattr(ec, "list_worktree_sessions", lambda *a, **k: [])

    async def run():
        app = companion.MuxCompanionApp(cwd="/w")
        async with app.run_test(size=size) as pilot:
            await app.workers.wait_for_complete()
            tabs = app.query_one("#views", TabbedContent)
            assert tabs.active == "summary-tab"
            assert "Build a widget" in str(app.query_one("#summary", Static).content)
            assert not app.query_one("#cutover-btn").display or tabs.active != "recovery-tab"
            tabs.active = "claims-tab"
            await pilot.pause()
            table = app.query_one("#claims", DataTable)
            assert table.row_count == 2
            assert table.get_row_at(0)[3].plain == "[bold]literal note[/bold]"
            tabs.active = "recovery-tab"
            await pilot.pause()
            assert app.query_one("#lineage", DataTable).row_count == 0
            await pilot.press("escape")
        assert not app.is_running

    asyncio.run(run())


def test_textual_summary_through_real_engine_subprocess(tmp_path, _fake_status_bundle, monkeypatch):
    from textual.widgets import DataTable, Static

    payloads = {
        "status-segment": {"id": "wt-ab12", "state": "wip"},
        "list-sessions": {"sessions": [{"id": "s-one", "is_head": True}]},
        "worktree-status-bundle": _bundle(),
    }
    script = tmp_path / "example engine.py"
    script.write_text(
        "import json, sys\n"
        f"payloads = json.loads({json.dumps(payloads)!r})\n"
        "print(json.dumps(payloads[sys.argv[1]]))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cc, "worktree_status_bundle", _fake_status_bundle)
    ec.set_engine_command([sys.executable, str(script)])

    async def run():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert app._data.error is None
            assert app._data.bundle_error is None
            assert "Build a widget" in str(app.query_one("#summary", Static).content)
            assert app.query_one("#claims", DataTable).row_count == 2
            assert app.query_one("#lineage", DataTable).row_count == 1
            await pilot.press("escape")

    try:
        asyncio.run(run())
    finally:
        ec.set_engine_command(None)


def test_long_purpose_and_claim_values_remain_literal_and_complete():
    bundle = _bundle()
    title = "[red]" + "long purpose " * 100
    bundle["facts"]["disposition"]["value"]["title"] = title
    note = "[b]" + "long claim note " * 100
    bundle["facts"]["claims"]["value"]["resources"][0]["note"] = note
    data = companion._CompanionData(bundle=bundle)
    assert title in companion._summary_text(data).plain
    assert companion._claims_view(data)[1][0]["note"] == note


def test_targeted_worktree_handoff_does_not_use_process_cwd(
    tmp_path, monkeypatch, _fake_status_bundle,
):
    from textual.widgets import Button

    process_dir = tmp_path / "other worktree"
    target = tmp_path / "selected worktree"
    for path, title in ((process_dir, "Wrong worktree"), (target, "Selected handoff")):
        handoffs = path / ".state" / "handoff"
        handoffs.mkdir(parents=True)
        (handoffs / "handoff-example.json").write_text(json.dumps({
            "kind": "context-handoff", "title": title, "createdAt": "2026-01-01",
        }), encoding="utf-8")
    payloads = {
        "status-segment": {"id": "wt-ab12", "state": "wip", "path": str(target)},
        "list-sessions": {"sessions": []},
        "worktree-status-bundle": _bundle(),
    }
    script = tmp_path / "example engine.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"payloads = json.loads({json.dumps(payloads)!r})\n"
        "if sys.argv[1] == 'get':\n"
        "    print(Path.cwd() / '.state')\n"
        "else:\n"
        "    print(json.dumps(payloads[sys.argv[1]]))\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(process_dir)
    monkeypatch.setattr(cc, "worktree_status_bundle", _fake_status_bundle)
    ec.set_engine_command([sys.executable, str(script)])

    async def run():
        app = companion.MuxCompanionApp(cwd=str(target))
        async with app.run_test():
            await app.workers.wait_for_complete()
            assert app._data.pending_handoff["title"] == "Selected handoff"
            assert not app.query_one("#cutover-btn", Button).disabled

    try:
        asyncio.run(run())
    finally:
        ec.set_engine_command(None)


def test_slow_refresh_keeps_textual_responsive_and_retries_initial_error(monkeypatch):
    import threading
    from textual.widgets import Static

    started = threading.Event()
    finish = threading.Event()
    calls = []

    def load(cwd, **kwargs):
        calls.append(cwd)
        if len(calls) == 1:
            return companion._CompanionData(error="temporary engine failure")
        started.set()
        assert finish.wait(5), "test did not release the slow read"
        return companion._CompanionData(worktree={"id": "wt-ab12"}, bundle=_bundle())

    monkeypatch.setattr(companion, "_load_current_worktree", load)

    async def run():
        app = companion.MuxCompanionApp(cwd="/w")
        try:
            async with app.run_test() as pilot:
                await app.workers.wait_for_complete()
                assert "temporary engine failure" in app._status_text().plain
                await pilot.click("#refresh-btn")
                assert await asyncio.to_thread(started.wait, 2)
                # A second input event is processed while the producer is still blocked.
                await pilot.press("tab")
                assert app._loading
                finish.set()
                await app.workers.wait_for_complete()
                assert "Build a widget" in str(app.query_one("#summary", Static).content)
                assert app._data.error is None
        finally:
            finish.set()

    asyncio.run(run())


def test_companion_process_exits_while_owned_engine_is_blocked(tmp_path):
    import os
    import subprocess
    from worktree_manager import engine_process

    started = tmp_path / "producer-started"
    producer = tmp_path / "blocked engine.py"
    producer.write_text(
        "import time\n"
        "from pathlib import Path\n"
        f"Path({str(started)!r}).write_text('started')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    driver = tmp_path / "companion driver.py"
    driver.write_text(
        "import asyncio, sys\n"
        "from pathlib import Path\n"
        "from worktree_manager import engine_client as ec\n"
        "from worktree_manager.mux_companion import MuxCompanionApp\n"
        f"ec.set_engine_command([sys.executable, {str(producer)!r}])\n"
        "async def main():\n"
        "    app = MuxCompanionApp()\n"
        "    async with app.run_test() as pilot:\n"
        "        for _ in range(100):\n"
        f"            if Path({str(started)!r}).exists(): break\n"
        "            await asyncio.sleep(0.02)\n"
        f"        assert Path({str(started)!r}).exists()\n"
        "        await pilot.press('escape')\n"
        "asyncio.run(main())\n",
        encoding="utf-8",
    )
    # The producer is never released; a real process exit must cancel its owned read.
    result = engine_process.run(
        [sys.executable, str(driver)], timeout=8,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=dict(os.environ), stdin=subprocess.DEVNULL,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert started.exists()
