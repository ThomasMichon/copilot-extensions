"""Opt-in real PSMux creation through the generic native launcher."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest

from agent_worktrees import config, sessions
from agent_worktrees.mux_list_result import confirmed_has_session


pytestmark = [
    pytest.mark.timeout(180),
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
    roots = {os.getpid()}
    original = subprocess.Popen
    initializer = getattr(original.__init__, "_aw_orig", None)
    if initializer is not None:
        monkeypatch.setattr(original, "__init__", initializer)

    def capture(*args, **kwargs):
        child = original(*args, **kwargs)
        roots.add(child.pid)
        return child

    monkeypatch.setattr(subprocess, "Popen", capture)
    stop = threading.Event()
    pause = threading.Event()
    paused = threading.Event()

    def observe():
        while not stop.is_set():
            if pause.is_set():
                paused.set()
                stop.wait(0.01)
                continue
            paused.clear()
            table = processes()
            for root in roots.copy():
                owned.update(descendants(root, table) - initial - {os.getpid()})
            current, active = windows()
            visible.update(current.values())
            foreground.add(active)
            stop.wait(0.02)

    with ThreadPoolExecutor(max_workers=1) as executor:
        observer = executor.submit(observe)
        try:
            yield owned, pause, paused
        finally:
            stop.set()
            observer.result(timeout=3)
    assert not visible & owned, "Generic mux startup surfaced an owned visible window"
    assert not foreground & owned, "Generic mux startup took foreground focus"


def test_observer_retains_descendants_after_creation_client_exits(no_visible_launches):
    owned, pause, paused = no_visible_launches
    pause.set()
    assert paused.wait(timeout=3), "Observer did not pause before the control launch"
    command = (
        "import subprocess,sys;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
        "creationflags=subprocess.CREATE_NO_WINDOW);print(child.pid,flush=True)"
    )
    with subprocess.Popen(
        [str(Path(sys.base_prefix) / "python.exe"), "-c", command],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    ) as parent:
        stdout, stderr = parent.communicate(timeout=15)
        assert parent.returncode == 0, stderr
        child_pid = int(stdout.strip())
        assert child_pid not in owned
        pause.clear()
        deadline = time.monotonic() + 5
        while child_pid not in owned and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_pid in owned, "Observer lost a child whose creation client already exited"
    from mux_windows_observer import processes

    deadline = time.monotonic() + 15
    while child_pid in processes() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert child_pid not in processes(), "Bounded positive-control child did not exit"


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
    release = tmp_path / "release"
    wrapper.write_text(
        "[IO.File]::WriteAllText($env:FIXTURE_OUTPUT, "
        "(ConvertTo-Json -InputObject @($args) -Compress))\n"
        "$deadline=[DateTime]::UtcNow.AddSeconds(90)\n"
        "while (-not [IO.File]::Exists($env:FIXTURE_RELEASE) -and "
        "[DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }\n",
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
        started = time.monotonic()
        result = sessions.mux_new_session(
            worktree_id, str(tmp_path), ["fixture-command", "two words"],
            {"FIXTURE_OUTPUT": str(output), "FIXTURE_RELEASE": str(release)}, mux=binary,
        )
        assert result["ok"], result
        deadline = time.monotonic() + 45
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        presence = query("has-session", "-t", name)
        pending = list((tmp_path / "runtime/pane-args").glob("*.json"))
        assert output.exists(), (
            f"result={result}; query={presence.returncode}; stderr={presence.stderr!r}; "
            f"elapsed={time.monotonic() - started:.2f}s; "
            f"pending_manifests={len(pending)}; "
            f"pane={query('capture-pane', '-p', '-t', name).stdout!r}"
        )
        assert json.loads(output.read_text("utf-8")) == [
            "-AwWt", worktree_id, "fixture-command", "two words",
        ]
        assert presence.returncode == 0, (
            f"elapsed={time.monotonic() - started:.2f}s; {presence.stderr}"
        )
        assert not pending
        assert all(
            os.environ.get(key) == ("fixture-parent" if nested else None)
            for key in NESTING_KEYS
        )
    finally:
        release.touch()
        stopped = query("kill-session", "-t", name)
        assert stopped.returncode in (0, 1), stopped.stderr
        assert confirmed_has_session(
            name, query("has-session", "-t", name), binary=binary,
        ) is False
