"""Real tmux navigation through an isolated socket and constructed prefix."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from worktree_manager import companion_pane, mux_mapping_registry
from _manager_process_fixture import synthetic_runtime


pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("tmux"),
    reason="Native POSIX tmux is required",
)


def test_native_tmux_option_targets_and_same_window(tmp_path, monkeypatch):
    name = "companion-" + uuid.uuid4().hex
    wrapper = tmp_path / "tmux-client"
    wrapper.write_text(
        "#!/bin/sh\nexec " + shlex.quote(shutil.which("tmux")) +
        " -L " + shlex.quote(name) + ' "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    monkeypatch.delenv("TMUX", raising=False)
    cwd = tmp_path / "checkout with spaces"
    cwd.mkdir()
    root = tmp_path / "O'Brien Manager"
    slot = synthetic_runtime(root, "1.2.3")
    observed = tmp_path / "observer.json"
    package = slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "worktree_manager"
    (package / "mux_companion.py").write_text(
        "import json,sys,time\nfrom pathlib import Path\n"
        "from worktree_manager.companion_lifecycle import cli_alive\n"
        f"Path({str(observed)!r}).write_text(json.dumps({{'argv':sys.argv[1:],'prefix':sys.prefix}}))\n"
        "options=dict(zip(sys.argv[1::2],sys.argv[2::2]))\n"
        "for _ in range(900):\n"
        " if cli_alive(options['--project'],options['--worktree-id'],options['--cli-pane'],"
        "session_incarnation=options['--session-incarnation']) is False: break\n"
        " time.sleep(0.1)\n",
        encoding="utf-8",
    )

    def mux(*args):
        result = subprocess.run(
            [str(wrapper), *args], capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.rstrip("\r\n")

    try:
        mux("new-session", "-d", "-s", name, "-c", str(cwd))
        cli, incarnation, pid = mux(
            "display-message", "-p", "-t", name,
            "#{pane_id}\t#{session_id}:#{session_created}\t#{pane_pid}",
        ).split("\t")
        mux_mapping_registry.register_mapping({
            "project": "example", "worktree_id": "fixture", "mux_bin": str(wrapper),
            "mux_session": name, "worktree_path": str(cwd),
            "session_incarnation": incarnation,
            "panes": [{"pane_id": cli, "role": "cli", "live": True}],
            "mapping_revision": 1, "live": True,
        }, root=root)
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
        assert receipt["argv"] == [
            "--cli-pane", cli, "--cwd", str(cwd.resolve()),
            "--project", "example", "--worktree-id", "fixture",
            "--session-incarnation", incarnation,
        ]
        assert companion_pane.switch("return", str(cwd), cli_pane=cli) == cli
        assert companion_pane.switch("show", str(cwd)) == observer
        assert mux("display-message", "-p", "-t", cli, "#{pane_pid}") == pid
        assert len(mux("list-windows", "-t", name, "-F", "#{window_id}").splitlines()) == 1
        assert len(mux("list-panes", "-s", "-t", name, "-F", "#{pane_id}").splitlines()) == 2
        companion_pane.switch("return", str(cwd), cli_pane=cli)
        mux("kill-pane", "-t", cli)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            gone = subprocess.run(
                [str(wrapper), "has-session", "-t", name], capture_output=True, timeout=5,
            )
            if gone.returncode:
                break
            time.sleep(0.05)
        assert gone.returncode, "Summary kept the CLI-only session alive"
        mux("new-session", "-d", "-s", name, "-c", str(cwd))
        assert len(mux("list-panes", "-s", "-t", name, "-F", "#{pane_id}").splitlines()) == 1
    finally:
        subprocess.run([str(wrapper), "kill-session", "-t", name], capture_output=True, timeout=10)
