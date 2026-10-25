"""Same-window view actions preserve the exact registered CLI pane."""

from copy import deepcopy
import asyncio
from threading import Event
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import companion_pane as pane


@pytest.fixture
def mux(monkeypatch, tmp_path):
    mapping = {
        "project": "example", "worktree_id": "work-1", "live": True,
        "mux_bin": "/owned/tmux", "mux_session": "example-work-1",
        "worktree_path": str(tmp_path), "session_incarnation": "$7:123",
        "panes": [{"pane_id": "%42", "role": "cli", "live": True}],
    }
    state = SimpleNamespace(
        mapping=mapping, calls=[], zoom="0",
        panes=[["%42", "@7", "0", mapping["mux_session"], "$7:123", str(tmp_path)]],
        observer=None,
    )
    monkeypatch.setattr(pane.ec, "current_worktree_status", lambda **kw: {
        "repo": "example", "id": "work-1", "path": str(tmp_path),
    })
    monkeypatch.setattr(pane, "get_mapping", lambda *a: state.mapping)

    def call(argv, **kwargs):
        assert argv[0] == "/owned/tmux"
        assert kwargs["timeout"] == 5
        state.calls.append(argv[1:])
        command = argv[1]
        if "-t" in argv:
            native_target = argv[argv.index("-t") + 1]
            if native_target.startswith(mapping["mux_session"] + ":"):
                argv = list(argv)
                argv[argv.index("-t") + 1] = native_target.split(":", 1)[1]
        output = ""
        if command == "list-panes":
            output = "\n".join("\t".join(row) for row in state.panes) + "\n"
        elif command == "split-window":
            assert argv[argv.index("-t") + 1] == "%42"
            state.panes.append(["%99", "@7", "0", mapping["mux_session"], "$7:123", str(tmp_path)])
            output = "%99\n"
        elif command == "set-option":
            assert argv[-2:] == ["@aw_companion_42", "%99"]
            state.observer = "%99"
        elif command == "show-options":
            output = f"@aw_companion_42 {state.observer}\n" if state.observer else ""
        elif command == "select-pane":
            state.zoom = "0"
        elif command == "display-message":
            output = state.zoom + "\n"
        elif command == "resize-pane":
            state.zoom = "1"
        else:
            pytest.fail(f"Unexpected mux command: {command}")
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(pane, "run_mux_child", call)
    # Runtime selection has separate installed-consumer tests.
    import sys
    monkeypatch.setitem(sys.modules, "worktree_manager.companion_runtime", SimpleNamespace(
        companion_shell_command=lambda args: "installed companion",
    ))
    return state, str(tmp_path)


def test_show_reuses_observer_and_return_preserves_cli(mux):
    state, cwd = mux
    original = deepcopy(state.mapping)
    assert pane.switch("show", cwd) == "%99"
    assert pane.switch("return", cwd, cli_pane="%42") == "%42"
    assert pane.switch("show", cwd) == "%99"
    assert [c[0] for c in state.calls].count("split-window") == 1
    assert state.mapping == original
    assert len({p[1] for p in state.panes}) == 1
    assert not any(c[0] in {"new-window", "kill-pane", "respawn-pane", "send-keys"} for c in state.calls)


@pytest.mark.parametrize("panes", [
    [],
    [{"pane_id": "%42", "role": "cli", "live": False}],
    [{"pane_id": "%42", "role": "unknown", "live": True}],
    [{"pane_id": "%42", "role": "cli", "live": True}] * 2,
])
def test_missing_or_ambiguous_registered_cli_refuses_before_mutation(mux, panes):
    state, cwd = mux
    state.mapping["panes"] = panes
    with pytest.raises(pane.PaneError, match="identity"):
        pane.switch("show", cwd)
    assert state.calls == []


def test_changed_cli_argument_cannot_target_successor(mux):
    state, cwd = mux
    with pytest.raises(pane.PaneError, match="changed"):
        pane.switch("return", cwd, cli_pane="%1")
    assert state.calls == []


def test_stale_native_incarnation_refuses_before_mutation(mux):
    state, cwd = mux
    state.panes[0][4] = "$7:999"
    with pytest.raises(pane.PaneError, match="no longer current"):
        pane.switch("show", cwd)
    assert [c[0] for c in state.calls] == ["list-panes"]


@pytest.mark.parametrize("dead", ["1", ""])
def test_dead_or_unknown_cli_is_not_replaced(mux, dead):
    state, cwd = mux
    state.panes[0][2] = dead
    with pytest.raises(pane.PaneError, match="no longer live"):
        pane.switch("show", cwd)
    assert [c[0] for c in state.calls] == ["list-panes"]


def test_observer_in_other_window_is_not_selected(mux):
    state, cwd = mux
    state.panes.append(["%8", "@8", "0", state.mapping["mux_session"], "$7:123", cwd])
    state.observer = "%8"
    with pytest.raises(pane.PaneError, match="no longer belongs"):
        pane.switch("show", cwd)
    assert not any(c[0] == "select-pane" for c in state.calls)


def test_invalid_observer_identity_refuses_without_guessing(mux):
    state, cwd = mux
    state.observer = "bad-pane"
    with pytest.raises(pane.PaneError, match="invalid or ambiguous"):
        pane.switch("show", cwd)
    assert [c[0] for c in state.calls] == ["list-panes", "show-options"]


def test_mux_failure_is_visible_not_success(mux, monkeypatch, capsys):
    _state, cwd = mux
    monkeypatch.setattr(pane, "run_mux_child", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr="native refusal",
    ))
    assert pane.run("return", cwd) == 1
    assert "native refusal" in capsys.readouterr().err


def test_registered_cli_path_not_session_home_attributes_worktree(mux):
    state, cwd = mux
    state.mapping["session_path"] = "/home/example"
    assert pane.switch("return", cwd) == "%42"
    state.panes[0][5] = str(Path(cwd).parent)
    state.calls.clear()
    with pytest.raises(pane.PaneError, match="different worktree path"):
        pane.switch("show", cwd)
    assert [c[0] for c in state.calls] == ["list-panes"]


def test_cancelled_view_switch_never_mutates_mux(mux):
    state, cwd = mux
    cancelled = Event()
    cancelled.set()
    with pytest.raises(pane.PaneError, match="cancelled"):
        pane.switch("show", cwd, cancelled=cancelled)
    assert state.calls == []


@pytest.mark.parametrize("cli_pane,action,label", [
    (None, "show", "Show Summary pane"),
    ("%42", "return", "Return to CLI"),
])
def test_companion_button_calls_explicit_navigation(monkeypatch, cli_pane, action, label):
    from worktree_manager import mux_companion as companion
    from textual.widgets import Button

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: companion._CompanionData())
    calls = []
    monkeypatch.setattr(pane, "switch", lambda *a, **kw: calls.append((a, kw)))
    async def exercise():
        app = companion.MuxCompanionApp("/worktree", cli_pane=cli_pane)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert str(app.query_one("#view-btn", Button).label) == label
            await pilot.click("#view-btn")
            await pilot.pause()
            assert calls[0][0] == (action, "/worktree")
            assert calls[0][1]["cli_pane"] == cli_pane
            assert app.is_running is bool(cli_pane)
    asyncio.run(exercise())


def test_navigation_refusal_remains_dismissible_and_visible(monkeypatch):
    from worktree_manager import mux_companion as companion
    from textual.widgets import Static

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: companion._CompanionData())

    def refuse(*args, **kwargs):
        raise pane.PaneError("registered identity changed")

    monkeypatch.setattr(pane, "switch", refuse)
    async def exercise():
        app = companion.MuxCompanionApp("/worktree")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.click("#view-btn")
            await pilot.pause()
            assert "registered identity changed" in str(app.query_one("#status", Static).content)
            assert app.is_running
            await pilot.press("escape")
    asyncio.run(exercise())
