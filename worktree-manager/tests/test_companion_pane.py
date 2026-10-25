"""Same-window view actions preserve the exact registered CLI pane."""

from copy import deepcopy
import asyncio
import json
import os
import subprocess
import sys
import time
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
    monkeypatch.setattr(pane, "default_root", lambda: tmp_path)

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
    assert [c[0] for c in state.calls] == ["list-panes", "list-panes", "show-options"]


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


def test_windows_job_failure_becomes_visible_pane_error(mux, monkeypatch):
    _state, cwd = mux

    def fail(*args, **kwargs):
        raise RuntimeError("Job assignment failed")

    monkeypatch.setattr(pane, "run_mux_child", fail)
    with pytest.raises(pane.PaneError, match="Job assignment failed"):
        pane.switch("show", cwd)


def test_old_observer_never_returns_to_reused_successor_pane(mux):
    state, cwd = mux
    with pytest.raises(pane.PaneError, match="original CLI session has ended"):
        pane.switch("return", cwd, cli_pane="%42", session_incarnation="$1:10")
    assert not any(call[0] == "select-pane" for call in state.calls)


def test_navigation_guard_storage_failure_is_visible(mux, monkeypatch, tmp_path):
    state, cwd = mux
    root = tmp_path / "not-a-directory"
    root.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(pane, "default_root", lambda: root)
    with pytest.raises(pane.PaneError, match="guard unavailable"):
        pane.switch("show", cwd)
    assert not any(call[0] == "split-window" for call in state.calls)


def test_registry_storage_failure_is_visible(mux, monkeypatch):
    state, cwd = mux

    def unavailable(*args):
        raise OSError("registry unavailable")

    monkeypatch.setattr(pane, "get_mapping", unavailable)
    with pytest.raises(pane.PaneError, match="identity is unavailable"):
        pane.switch("show", cwd)
    assert not state.calls


@pytest.mark.parametrize("caller_kind", ["other-project", "neutral"])
def test_explicit_cwd_scopes_real_engine_read_without_changing_caller(tmp_path, monkeypatch, caller_kind):
    target = tmp_path / "selected worktree"
    caller = tmp_path / caller_kind
    target.mkdir()
    caller.mkdir()
    (target / "identity.json").write_text(json.dumps({
        "repo": "selected-project", "id": "selected", "path": str(target),
    }), encoding="utf-8")
    if caller_kind == "other-project":
        (caller / "identity.json").write_text(json.dumps({
            "repo": "other-project", "id": "wrong", "path": str(caller),
        }), encoding="utf-8")
    script = tmp_path / "fixture engine.py"
    script.write_text(
        "import json\nfrom pathlib import Path\n"
        "p=Path.cwd()/'identity.json'\n"
        "print(p.read_text() if p.exists() else json.dumps({'error':'untracked'}))\n",
        encoding="utf-8",
    )
    mapping = {
        "project": "selected-project", "worktree_id": "selected", "live": True,
        "mux_bin": "fixture-mux", "mux_session": "selected", "session_incarnation": "$7:100",
        "worktree_path": str(target),
        "panes": [{"pane_id": "%42", "role": "cli", "live": True}],
    }
    lookups = []

    def get_mapping(project, worktree_id):
        lookups.append((project, worktree_id))
        return mapping if (project, worktree_id) == ("selected-project", "selected") else None

    def native(mapping, *args, **kwargs):
        if args[0] == "list-panes":
            return f"%42\t@7\t0\tselected\t$7:100\t{target}"
        if args[0] == "display-message":
            return "1"
        return ""

    monkeypatch.chdir(caller)
    monkeypatch.setattr(pane, "get_mapping", get_mapping)
    monkeypatch.setattr(pane, "_call", native)
    monkeypatch.setattr(pane, "default_root", lambda: tmp_path / "manager")
    pane.ec.set_engine_command([sys.executable, str(script)])
    try:
        assert pane.run("return", str(target)) == 0
        assert set(lookups) == {("selected-project", "selected")}
        assert Path.cwd() == caller
    finally:
        pane.ec.set_engine_command(None)


def test_concurrent_processes_publish_only_one_observer(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"splits": 0, "observer": None, "panes": []}), encoding="utf-8")
    ready = tmp_path / "splitting"
    program = r'''
import json,sys,time
from pathlib import Path
from types import SimpleNamespace
from worktree_manager import companion_pane as pane
sys.modules["worktree_manager.companion_runtime"]=SimpleNamespace(
    companion_shell_command=lambda *args:"fixture")
root=Path(sys.argv[1]); state=root/"state.json"
pane.default_root=lambda:root
mapping={"project":"example","worktree_id":"fixture","mux_session":"fixture",
         "session_incarnation":"$7:100","mux_bin":"fixture"}
def read():
    return json.loads(state.read_text())
def write(value):
    temporary=root/("state-"+str(__import__("os").getpid()))
    temporary.write_text(json.dumps(value));temporary.replace(state)
def target(*a,**kw):
    return mapping,"%42","@7",read()["panes"]
def call(mapping,*args,**kw):
    value=read()
    if args[0]=="show-options":
        return "@aw_companion_42 "+value["observer"] if value["observer"] else ""
    if args[0]=="split-window":
        (root/"splitting").touch();time.sleep(0.3)
        value["splits"]+=1;value["panes"]=[{"id":"%99","window":"@7","dead":"0"}]
        write(value);return "%99"
    if args[0]=="set-option":
        value["observer"]=args[-1];write(value)
    return ""
pane._target=target;pane._call=call;pane._focus=lambda *a,**kw:None
print(pane.switch("show",str(root)),flush=True)
'''
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    processes = []
    try:
        processes.append(subprocess.Popen(
            [sys.executable, "-c", program, str(tmp_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **flags,
        ))
        deadline = time.monotonic() + 10
        while not ready.exists() and processes[0].poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if not ready.exists():
            output, error = processes[0].communicate(timeout=1)
            pytest.fail(output + error)
        processes.append(subprocess.Popen(
            [sys.executable, "-c", program, str(tmp_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **flags,
        ))
        for process in processes:
            output, error = process.communicate(timeout=15)
            assert process.returncode == 0, error
            assert output.strip() == "%99"
        assert json.loads(state.read_text(encoding="utf-8"))["splits"] == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()


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


@pytest.mark.parametrize("alive", [True, None, False])
def test_observer_exits_only_on_confirmed_cli_end(monkeypatch, alive):
    from worktree_manager import mux_companion as companion, companion_lifecycle

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: companion._CompanionData())
    calls = []

    def probe(*args, **kwargs):
        calls.append((args, kwargs))
        return alive

    monkeypatch.setattr(companion_lifecycle, "cli_alive", probe)

    async def exercise():
        app = companion.MuxCompanionApp(
            "/worktree", cli_pane="%42", project="example", worktree_id="one",
            session_incarnation="$7:100",
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert calls[0] == (("example", "one", "%42"), {"session_incarnation": "$7:100"})
            assert app.is_running is (alive is not False)
    asyncio.run(exercise())
