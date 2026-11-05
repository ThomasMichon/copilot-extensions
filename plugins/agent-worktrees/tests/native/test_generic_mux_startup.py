"""Opt-in real PSMux creation through the generic native launcher."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from uuid import uuid4

import pytest

from agent_worktrees import config, sessions


pytestmark = [
    pytest.mark.timeout(90),
    pytest.mark.skipif(
        os.name != "nt" or os.environ.get("PSMUX_FILE_LAUNCH_E2E") != "1",
        reason="opt-in real Windows PSMux consumer check",
    ),
]
NESTING_KEYS = (
    "TMUX", "TMUX_PANE", "PSMUX_SESSION", "PSMUX_TARGET_SESSION",
    "PSMUX_CLAUDE_TEAMMATE_MODE",
)


@pytest.fixture(autouse=True)
def no_visible_launches(monkeypatch):
    root = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(root / "worktree-manager/tests"))
    from mux_windows_observer import descendants, processes, windows

    owned = set()
    visible = set()
    foreground = set()
    initial = descendants(os.getpid(), processes()) - {os.getpid()}
    stop = threading.Event()

    def observe():
        while not stop.is_set():
            owned.update(descendants(os.getpid(), processes()) - initial - {os.getpid()})
            current, active = windows()
            visible.update(current.values())
            foreground.add(active)
            stop.wait(0.02)

    with ThreadPoolExecutor(max_workers=1) as executor:
        observer = executor.submit(observe)
        try:
            yield
        finally:
            stop.set()
            observer.result(timeout=3)
    assert not visible & owned, "Generic mux startup surfaced an owned visible window"
    assert not foreground & owned, "Generic mux startup took foreground focus"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("formatted", [False, True])
def test_generic_mux_starts_file_transport(tmp_path, monkeypatch, nested, formatted):
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    shell = shutil.which("pwsh.exe")
    if not binary or not shell:
        pytest.skip("PSMux and PowerShell 7 are required")
    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(home))
    for key in NESTING_KEYS:
        monkeypatch.delenv(key, raising=False)
    if nested:
        for key in NESTING_KEYS:
            monkeypatch.setenv(key, "fixture-parent")
    monkeypatch.setenv("PSMUX_NO_WARM", "1")
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path / "psmux-data"))
    monkeypatch.setenv("PSMUX_CONFIG_FILE", os.devnull)
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path / "runtime")
    root = Path(__file__).resolve().parents[4]
    wrapper = tmp_path / "pane-wrapper.ps1"
    output = tmp_path / "consumed.json"
    wrapper.write_text(
        "[IO.File]::WriteAllText($env:FIXTURE_OUTPUT, "
        "(ConvertTo-Json -InputObject @($args) -Compress))\n"
        "Start-Sleep -Seconds 30\n",
        encoding="utf-8",
    )
    shutil.copy2(root / "worktree-manager/bin/pane-launch.ps1", tmp_path / "pane-launch.ps1")
    original = sessions.build_mux_new_session_argv

    def build(*args, **kwargs):
        argv = original(*args, **kwargs, pane_wrapper=str(wrapper))
        if not formatted:
            index = argv.index("-P")
            del argv[index:index + 3]
        return argv

    monkeypatch.setattr(sessions, "build_mux_new_session_argv", build)
    worktree_id = "native-fixture-" + uuid4().hex
    name = sessions.mux_session_name(worktree_id)
    observed_env = {key: value for key, value in os.environ.items() if key not in NESTING_KEYS}

    def query(*args):
        return subprocess.run(
            [binary, *args], env=observed_env, cwd=home, capture_output=True,
            text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
        )

    try:
        result = sessions.mux_new_session(
            worktree_id, str(tmp_path), ["fixture-command", "two words"],
            {"FIXTURE_OUTPUT": str(output)}, mux=binary,
        )
        deadline = time.monotonic() + 15
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        presence = query("has-session", "-t", name)
        pending = list((tmp_path / "runtime/pane-args").glob("*.json"))
        assert output.exists(), (
            f"result={result}; query={presence.returncode}; stderr={presence.stderr!r}; "
            f"pending_manifests={len(pending)}; "
            f"pane={query('capture-pane', '-p', '-t', name).stdout!r}"
        )
        assert json.loads(output.read_text("utf-8")) == [
            "-AwWt", worktree_id, "fixture-command", "two words",
        ]
        assert presence.returncode == 0, presence.stderr
        assert not pending
        assert all(
            os.environ.get(key) == ("fixture-parent" if nested else None)
            for key in NESTING_KEYS
        )
    finally:
        stopped = query("kill-session", "-t", name)
        assert stopped.returncode in (0, 1), stopped.stderr
