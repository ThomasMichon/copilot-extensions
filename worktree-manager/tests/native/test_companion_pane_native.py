"""Real PSMux view navigation in one uniquely owned disposable session."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from worktree_manager import companion_lifecycle, companion_pane, mux_daemon, mux_daemon_cli, mux_mapping_registry
from _manager_process_fixture import synthetic_runtime


pytestmark = pytest.mark.skipif(
    os.name != "nt" or not (os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux"))
    or not shutil.which("pwsh"),
    reason="Native Windows PSMux and PowerShell are required",
)


def test_native_same_window_preserves_cli_identity(tmp_path, monkeypatch):
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    session = "companion-proof-" + uuid.uuid4().hex
    cwd = tmp_path / "checkout with spaces"
    cwd.mkdir()
    root = tmp_path / "O'Brien Manager"
    slot = synthetic_runtime(root, "1.2.3")
    observed = tmp_path / "observer.json"
    package = slot / "Lib" / "site-packages" / "worktree_manager"
    (package / "mux_companion.py").write_text(
        "import json,os,sys,time\n"
        "from pathlib import Path\n"
        "from worktree_manager.companion_lifecycle import cli_alive\n"
        f"Path({str(observed)!r}).write_text(json.dumps({{'argv':sys.argv[1:],'prefix':sys.prefix,'pid':os.getpid(),'pane':os.environ.get('TMUX_PANE')}}))\n"
        "options=dict(zip(sys.argv[1::2],sys.argv[2::2]))\n"
        "for _ in range(900):\n"
        " if cli_alive(options['--project'],options['--worktree-id'],options['--cli-pane'],"
        "session_incarnation=options['--session-incarnation']) is False: break\n"
        " time.sleep(0.1)\n",
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"TMUX", "TMUX_PANE", "WT_SESSION", "PSMUX_SESSION"}
    }

    def mux(*args):
        args = list(args)
        if "-t" in args and args[args.index("-t") + 1].startswith("%"):
            index = args.index("-t") + 1
            args[index] = session + ":" + args[index]
        result = subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=10,
            env=environment, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.rstrip("\r\n")

    original_run = companion_pane.run_mux_child

    def clean_child(command, **kwargs):
        selected_environment = dict(environment)
        selected_environment["PSMUX_SESSION"] = kwargs.pop("env", {}).get("PSMUX_SESSION", session)
        return original_run(command, env=selected_environment, **kwargs)

    monkeypatch.setattr(companion_pane, "run_mux_child", clean_child)
    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", clean_child)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *a, **kw: True)
    monkeypatch.setattr(mux_daemon, "publish_live_observation", lambda *a, **kw: {"applied": True})
    try:
        mux("new-session", "-d", "-s", session, "-c", str(cwd))
        cli, incarnation, pid = mux(
            "display-message", "-p", "-t", session,
            "#{pane_id}\t#{session_id}:#{session_created}\t#{pane_pid}",
        ).split("\t")
        mapping = {
            "project": "example", "worktree_id": "fixture", "mux_bin": binary,
            "mux_session": session, "worktree_path": str(cwd),
            "session_incarnation": incarnation,
            "panes": [{"pane_id": cli, "role": "cli", "live": True}],
            "mapping_revision": 1, "live": True,
        }
        mux_mapping_registry.register_mapping(mapping, root=root)
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
        monkeypatch.setattr(sys, "prefix", str(slot))
        monkeypatch.setattr(companion_pane.ec, "current_worktree_status", lambda **kw: {
            "repo": "example", "id": "fixture", "path": str(cwd),
        })
        observer = companion_pane.switch("show", str(cwd))
        deadline = time.monotonic() + 15
        while not observed.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert observed.exists(), mux("capture-pane", "-p", "-t", observer)
        receipt = json.loads(observed.read_text(encoding="utf-8"))
        assert Path(receipt["prefix"]).resolve() == slot.resolve()
        assert receipt["pane"] == observer
        assert companion_lifecycle.observer_visible("example", "fixture", observer, incarnation) is False
        assert receipt["argv"] == [
            "--cli-pane", cli, "--cwd", str(cwd.resolve()),
            "--project", "example", "--worktree-id", "fixture",
            "--session-incarnation", incarnation,
        ]
        marker = mux("show-options", "-v", "-t", session, "@aw_companion_" + cli[1:])
        assert marker == observer
        def pane_rows():
            return {
                fields[0]: fields[1:]
                for line in mux("list-panes", "-s", "-t", session, "-F",
                                "#{pane_id}\t#{pane_pid}\t#{pane_active}").splitlines()
                if len(fields := line.split("\t")) == 3
            }
        for _ in range(3):
            assert companion_pane.switch("return", str(cwd), cli_pane=cli) == cli
            assert mux("display-message", "-p", "-t", cli, "#{window_zoomed_flag}") == "1"
            assert pane_rows()[cli][1] == "1"
            assert companion_pane.switch("show", str(cwd)) == observer
            assert pane_rows()[observer][1] == "1"
        assert mux("list-windows", "-t", session, "-F", "#{window_id}").count("\n") == 0
        assert pane_rows()[cli][0] == pid
        assert len(mux("list-panes", "-s", "-t", session, "-F", "#{pane_id}").splitlines()) == 2
        mux("kill-pane", "-t", observer)
        assert pane_rows()[cli][0] == pid
        replacement = companion_pane.switch("show", str(cwd))
        assert replacement != cli
        assert replacement != observer
        assert pane_rows()[cli][0] == pid
        assert len(pane_rows()) == 2
        companion_pane.switch("return", str(cwd), cli_pane=cli)
        mux("kill-pane", "-t", cli)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            gone = subprocess.run(
                [binary, "has-session", "-t", session], env=environment,
                capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if gone.returncode:
                break
            time.sleep(0.05)
        assert gone.returncode, "Summary kept the CLI-only session alive"
        mux("new-session", "-d", "-s", session, "-c", str(cwd))
        assert len(mux("list-panes", "-s", "-t", session, "-F", "#{pane_id}").splitlines()) == 1
        new_cli, new_incarnation = mux(
            "display-message", "-p", "-t", session, "#{pane_id}\t#{session_id}:#{session_created}",
        ).split("\t")
        assert new_incarnation != incarnation
        relaunched = dict(mapping, session_incarnation=new_incarnation,
                          panes=[{"pane_id": new_cli, "role": "cli", "live": True}])
        relaunched.pop("mapping_revision")
        assert mux_daemon_cli._register_reconnect(relaunched, creation=True)["applied"]
        assert mux_mapping_registry.get_mapping("example", "fixture")["panes"] == relaunched["panes"]
    finally:
        # Only this uniquely named test session; never the user's server.
        subprocess.run(
            [binary, "kill-session", "-t", session], env=environment,
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
