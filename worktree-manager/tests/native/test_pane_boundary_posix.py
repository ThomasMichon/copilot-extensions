"""Copied Bash wrapper controls with synthetic slots, not install acceptance."""

import base64
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

import pytest

from _manager_process_fixture import boundary_runtime
from worktree_manager import mux_mapping_registry as mappings

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Native POSIX shell/process groups")
WRAPPER = Path(__file__).resolve().parents[2] / "bin" / "pane-wrapper.sh"


@pytest.mark.parametrize("failure", ["prompt", "receipt", "token"])
def test_invalid_transport_cancels_its_registration_without_starting_child(tmp_path, failure):
    probe, child = tmp_path / "probe", tmp_path / "child"
    root = tmp_path / "Manager"
    boundary_runtime(root, "A")
    (root / "current-version").write_text("A\n", encoding="utf-8")
    mux = tmp_path / "mux"
    mux.write_text(
        "#!/usr/bin/env bash\nprintf probe > " + shlex.quote(str(probe)) + "\nprintf '$1:123|%%1\\n'\n",
        encoding="utf-8",
    )
    mux.chmod(0o700)
    encode = lambda value: base64.b64encode(str(value).encode()).decode()
    if failure == "prompt":
        controls = ["--aw-prompt-b64", "%%%", "--aw-prompt-receipt-b64", encode(tmp_path / "receipt")]
    elif failure == "receipt":
        blocked = tmp_path / "blocked"
        blocked.touch()
        controls = ["--aw-prompt-b64", encode("prompt"),
                    "--aw-prompt-receipt-b64", encode(blocked / "receipt")]
    else:
        controls = ["--aw-ahp-token-file", str(tmp_path / "missing-token")]
    result = subprocess.run(
        ["bash", str(WRAPPER), "--aw-manager-root", str(root),
         "--aw-project", "example", "--aw-wt", "worktree",
         "--aw-mux-session", "wt-worktree", "--aw-mux-bin", str(mux), *controls,
         "bash", "-c", 'printf started > "$1"', "child", str(child)],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert probe.exists()
    assert not child.exists()
    current = mappings.get_mapping("example", "worktree", root=root)
    assert current["live"] is False
    assert "observed_end" not in current


@pytest.mark.parametrize("replacement", ["B", "absent"])
def test_copied_wrapper_uses_current_handler_without_runtime_waiters(tmp_path, replacement):
    root = tmp_path / "Manager with spaces"
    first = boundary_runtime(root, "A")
    if replacement == "B":
        boundary_runtime(root, "B")
    marker = root / "current-version"
    marker.write_text("A\n", encoding="utf-8")
    owned = tmp_path / "session"
    owned.mkdir(mode=0o700)
    wrapper = owned / WRAPPER.name
    wrapper.write_bytes(WRAPPER.read_bytes())
    mux = owned / "mux"
    mux.write_text("#!/usr/bin/env bash\nprintf '$1:123|%%1\\n'\n", encoding="utf-8")
    mux.chmod(0o700)
    ready, proceed = owned / "ready.json", owned / "proceed"
    child = owned / "child.py"
    child.write_text(
        "import json, os, pathlib, sys, time\n"
        "ready, proceed = map(pathlib.Path, sys.argv[1:3])\n"
        "ready.write_text(json.dumps({'pid':os.getpid(), 'argv':sys.argv[3:]}))\n"
        "deadline=time.monotonic()+15\n"
        "while not proceed.exists():\n"
        "    if time.monotonic()>deadline: raise SystemExit(8)\n"
        "    time.sleep(.025)\n",
        encoding="utf-8",
    )
    arguments = ["--allow-all", "two words", "", "apostrophe'"]
    process = subprocess.Popen(
        ["bash", str(wrapper), "--aw-manager-root", str(root),
         "--aw-project", "example", "--aw-wt", "worktree",
         "--aw-mux-session", "wt-worktree", "--aw-mux-bin", str(mux),
         sys.executable, str(child), str(ready), str(proceed), *arguments],
        cwd=tmp_path, env=dict(os.environ, WORKTREE_PANE_MIN_RUNTIME="0", WORKTREE_PANE_WAIT_TIMEOUT="0"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.025)
        assert ready.exists()
        assert json.loads(ready.read_text(encoding="utf-8"))["argv"] == arguments
        assert mappings.get_mapping("example", "worktree", root=root)["live"] is True
        if replacement == "B":
            marker.write_text("B\n", encoding="utf-8")
            shutil.rmtree(first)
        else:
            shutil.rmtree(root)
        assert process.poll() is None
        proceed.touch()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stdout + stderr
        assert stdout == stderr == ""
        if replacement == "B":
            observed = mappings.get_mapping("example", "worktree", root=root)
            assert observed["observed_end"]["pane_id"] == "%1"
            assert observed["observed_end"]["exit_code"] == 0
            assert not observed["live"]
        else:
            assert not root.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
