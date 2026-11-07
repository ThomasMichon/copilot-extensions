"""Provider summaries cannot authorize actions before verified completion."""

import sys

import pytest

from test_registered_scope_reads import engine, wait
from worktree_manager.production_picker.picker_tui import pivots, tasks

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"), pytest.mark.effect("process"),
    pytest.mark.contract("worktree_manager.view_action_readiness"),
]


@pytest.mark.parametrize("finish", ["done", "eof", "nonzero", "timeout"])
def test_finite_actions_require_successful_completion_and_no_replacement(
    tmp_path, monkeypatch, finish,
):
    release, canary = tmp_path / "release", tmp_path / "action"
    producer = tmp_path / "producer.py"
    producer.write_text(
        "import json,time\nfrom pathlib import Path\n"
        "print(json.dumps({'type':'row','entry':{'id':'one','title':'Partial'}}),flush=True)\n"
        "print(json.dumps({'type':'summary','summary':{'repairable':'true'}}),flush=True)\n"
        "deadline=time.monotonic()+15\n"
        f"while not Path({str(release)!r}).exists():\n"
        " if time.monotonic()>deadline: raise SystemExit(90)\n"
        " time.sleep(.01)\n"
        + ("print(json.dumps({'type':'done'}),flush=True)\n" if finish != "eof" else "")
        + f"raise SystemExit({7 if finish == 'nonzero' else 0})\n",
        encoding="utf-8",
    )
    command = tmp_path / "action.py"
    command.write_text(
        "import json\nfrom pathlib import Path\n"
        f"with Path({str(canary)!r}).open('a') as output: output.write('run\\n')\n"
        "print(json.dumps({'type':'done'}),flush=True)\n", encoding="utf-8",
    )
    reg = pivots.parse_manifest({
        "label": "Readiness", "scope": "global", "placement": "config", "stream": True,
        "list": [sys.executable, str(producer)],
        "view_actions": [{
            "key": "repair", "label": "Repair", "when": {"repairable": "true"},
            "run": [sys.executable, str(command)], "progress": True, "confirm": True,
        }],
    }, name="readiness", source_path="fixture")
    runtime = tasks.RegisteredPivotRuntime(reg)
    screen = engine(runtime, reg)
    timers = []

    class Timer:
        def __init__(self, _interval, callback):
            self.callback = callback
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(tasks.threading, "Timer", Timer)
    action = reg.view_actions[0]
    context = {"_manager_refresh_scopes": ("",)}

    def blocked():
        ok, message = runtime.run_action_stream(action, context, lambda *_: None)
        assert not ok and "Source read is not current" in message
        assert screen._pivot_view_actions() == []

    try:
        runtime.ensure("")
        wait(lambda: runtime.get_summary("").get("repairable") == "true" and len(timers) >= 4)
        assert runtime.get("")[0] == "ready" and runtime.read_pending("")
        blocked()
        assert not canary.exists()
        if finish == "timeout":
            timers[-1].callback()
        else:
            release.write_text("continue", encoding="utf-8")
        wait(lambda: not runtime.read_pending(""))
        if finish != "done":
            assert runtime.get("")[0] == "error" and runtime.get("")[1]
            blocked()
            assert not canary.exists()
        else:
            assert screen._pivot_view_actions() == [action]
            ok, _message = runtime.run_action_stream(action, context, lambda *_: None)
            assert ok and canary.read_text() == "run\n"
            release.unlink()
            runtime.refresh_read("")
            wait(lambda: runtime.read_pending(""))
            blocked()
            assert canary.read_text() == "run\n"
            release.write_text("continue", encoding="utf-8")
            wait(lambda: not runtime.read_pending(""))
    finally:
        runtime.close()
