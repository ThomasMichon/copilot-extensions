"""Opt-in real PSMux creation through the generic native launcher."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
from uuid import uuid4

import pytest

from agent_worktrees import config, sessions


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
ObservationFixture = tuple[set[int], threading.Event, threading.Event]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.fixture(autouse=True)
def no_visible_launches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> Iterator[ObservationFixture]:
    from agent_remote_driver import mux_creation
    from tools.plugin_test_command_safety import private_mux_args

    namespace = private_mux_args(tmp_path)
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", namespace[1])
    root = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(root / "worktree-manager/tests"))
    from mux_windows_observer import descendants, processes, windows

    owned: set[int] = set()
    visible: set[int] = set()
    foreground: set[int] = set()
    initial = descendants(os.getpid(), processes()) - {os.getpid()}
    roots = {os.getpid()}
    original = subprocess.Popen
    initializer = getattr(original.__init__, "_aw_orig", None)
    if initializer is not None:
        monkeypatch.setattr(original, "__init__", initializer)

    def capture(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        if args and isinstance(args[0], list) and Path(args[0][0]).stem.casefold() == "psmux":
            command = args[0]
            args = ([command[0], *namespace, *command[1:]], *args[1:])
        child = original(*args, **kwargs)
        roots.add(child.pid)
        return child

    monkeypatch.setattr(subprocess, "Popen", capture)

    def private_presence(binary: str, session: str, *, timeout: float = 2) -> bool:
        environment = {key: value for key, value in os.environ.items() if key not in NESTING_KEYS}
        result = subprocess.run(
            [binary, "has-session", "-t", session], env=environment,
            cwd=Path.home(), capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError("Private mux fixture presence is unconfirmed")
        return result.returncode == 0

    # Machine-wide inventory cannot establish absence inside a private fixture root.
    # Exercise transport with the actual scoped provider, not an invented absent reply.
    monkeypatch.setattr(mux_creation, "has_session", private_presence)
    stop = threading.Event()
    pause = threading.Event()
    paused = threading.Event()

    def observe() -> None:
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


def test_observer_retains_descendants_after_creation_client_exits(
    no_visible_launches: ObservationFixture,
) -> None:
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
        text=True, creationflags=CREATE_NO_WINDOW,
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


def test_detached_creation_does_not_read_caller_input(tmp_path: Path) -> None:
    from agent_procutil.owned_process import run_caller_owned

    program = (
        "import sys\n"
        "from agent_remote_driver import mux_creation\n"
        "mux_creation.has_session=lambda *args,**kwargs:False\n"
        "result=mux_creation.run_session("
        "[sys.executable,'-c','import json,sys;print(json.dumps(sys.stdin.read()))'],"
        "'fixture',lambda:None)\n"
        "sys.stdout.write(result.stdout)\n"
        "raise SystemExit(result.returncode)\n"
    )
    result = run_caller_owned(
        [sys.executable, "-I", "-c", program], cwd=tmp_path,
        input_text="caller input must remain private\n", timeout=20,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ""


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("formatted", [False, True])
def test_generic_mux_starts_file_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_visible_launches: ObservationFixture,
    nested: bool, formatted: bool,
) -> None:
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

    def build(
        worktree_id: str, work_dir: str, cmd: list[str], env: dict[str, str] | None = None,
        *, mux: str | None = None, initial_prompt: str | None = None,
        prompt_receipt: str | None = None,
    ) -> list[str]:
        argv = original(
            worktree_id, work_dir, cmd, env, mux=mux, pane_wrapper=str(wrapper),
            initial_prompt=initial_prompt, prompt_receipt=prompt_receipt,
        )
        if not formatted:
            index = argv.index("-P")
            del argv[index:index + 3]
        return argv

    monkeypatch.setattr(sessions, "build_mux_new_session_argv", build)
    worktree_id = "native-fixture-" + uuid4().hex
    name = sessions.mux_session_name(worktree_id)
    observed_env = {key: value for key, value in os.environ.items() if key not in NESTING_KEYS}

    def query(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [binary, *args], env=observed_env, cwd=home, capture_output=True,
            text=True, timeout=5, creationflags=CREATE_NO_WINDOW,
        )

    def diagnostics() -> dict[int, dict[str, tuple[str, int] | str | None]]:
        from agent_remote_driver.mux_process_identity import command_line
        from mux_windows_observer import processes

        table = processes()
        owned = no_visible_launches[0]
        result = {
            pid: {"image": table[pid], "command": command_line(pid)}
            for pid in owned & table.keys()
            if table[pid][0].casefold() in {"psmux.exe", "pwsh.exe", "powershell.exe", "conhost.exe"}
        }
        return result

    try:
        started = time.monotonic()
        result = sessions.mux_new_session(
            worktree_id, str(tmp_path), ["fixture-command", "two words"],
            {"FIXTURE_OUTPUT": str(output), "FIXTURE_RELEASE": str(release)}, mux=binary,
        )
        assert result["ok"], f"{result}; processes={diagnostics()}"
        deadline = time.monotonic() + 45
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        presence = query("has-session", "-t", name)
        pending = list((tmp_path / "runtime/pane-args").glob("*.json"))
        assert output.exists(), (
            f"result={result}; query={presence.returncode}; stderr={presence.stderr!r}; "
            f"elapsed={time.monotonic() - started:.2f}s; "
            f"pending_manifests={len(pending)}; "
            f"pane={query('capture-pane', '-p', '-t', name).stdout!r}; "
            f"processes={diagnostics()}"
        )
        assert json.loads(output.read_text("utf-8")) == [
            "-AwWt", worktree_id, "fixture-command", "two words",
        ]
        from agent_remote_driver.mux_observation import observe_psmux

        observed = observe_psmux(name)
        assert observed.status == "present", observed
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
        assert query("has-session", "-t", name).returncode == 1
