"""Bounded startup controls, not Companion or installed-runtime qualification."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest


pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        os.name != "nt" or not (os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux"))
        or not shutil.which("pwsh"),
        reason="Native Windows PSMux, PowerShell and ConPTY are required",
    ),
]


@pytest.mark.parametrize("layer", ["direct-venv", "mux-venv", "mux-base"])
def test_inert_python_startup_under_strict_conpty(
    layer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_property,
) -> None:
    from _terminal_console_fixture import Console

    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    session = "python-startup-" + uuid.uuid4().hex
    profile, checkout = tmp_path / "profile", tmp_path / "checkout"
    profile.mkdir()
    checkout.mkdir()
    for key in list(os.environ):
        if key.startswith("PSMUX_") or key in {"TMUX", "TMUX_PANE", "WT_SESSION"}:
            monkeypatch.delenv(key)
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(profile))
    monkeypatch.setenv("PSMUX_NO_WARM", "1")
    environment = dict(os.environ)
    config = tmp_path / "psmux.conf"
    config.write_text("", encoding="utf-8")
    script, started = tmp_path / "inert.py", tmp_path / "started.json"
    script.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text(json.dumps({{'pid':os.getpid(),"
        "'executable':sys.executable}),encoding='utf-8')\n"
        "print('INERT PYTHON STARTED',flush=True)\n"
        "for line in sys.stdin: print(line.strip(),flush=True)\n",
        encoding="utf-8",
    )
    interpreter = sys._base_executable if layer == "mux-base" else sys.executable
    assert not os.path.samefile(sys.executable, sys._base_executable), (
        "Startup comparison requires distinct virtualenv and base interpreters",
    )
    argv = [interpreter, "-I", "-u", str(script)]
    use_mux = layer != "direct-venv"
    if use_mux:
        argv = [
            binary, "-f", str(config), "new-session", "-s", session, "-c", str(checkout),
            "--", *argv,
        ]

    def mux(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [binary, *args], capture_output=True, encoding="utf-8", timeout=5,
            env=dict(environment, PSMUX_SESSION=session),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    pinned = {}
    with Console(argv, env=environment, cwd=checkout) as client:
        cli_pid = client.process.pid if not use_mux else None
        screen = ""
        began = time.monotonic()
        try:
            deadline = began + 15
            while time.monotonic() < deadline:
                assert not client.exited(client.process.process), client.output.decode("utf-8", "replace")
                assert client.read_error is None, client.read_error
                if use_mux:
                    if cli_pid is None:
                        result = mux("display-message", "-p", "-t", session, "#{pane_pid}")
                        if result.returncode == 0 and result.stdout.strip().isdigit():
                            cli_pid = int(result.stdout.strip())
                            pinned.update(client.pin_descendants())
                            assert cli_pid in pinned, "Pane escaped the strict caller-owned tree"
                    result = mux("capture-pane", "-p", "-t", session)
                    screen = result.stdout if result.returncode == 0 else result.stderr
                else:
                    screen = client.output.decode("utf-8", "replace")
                if "INERT PYTHON STARTED" in screen and started.exists():
                    receipt = json.loads(started.read_text(encoding="utf-8"))
                    pinned.update(client.pin_descendants())
                    handle = (
                        client.process.process if receipt["pid"] == client.process.pid
                        else pinned[receipt["pid"]][1]
                    )
                    assert not client.exited(handle)
                    record_property("native-python-startup", json.dumps({
                        "layer": layer, "seconds": time.monotonic() - began,
                        "interpreter": interpreter, "receipt": receipt,
                        "script_birth": client.birth(handle), "screen": screen,
                        "boundary": "private strict Job/ConPTY, not Companion acceptance",
                    }))
                    break
                time.sleep(.025)
            else:
                pinned.update(client.pin_descendants())
                evidence = {
                    "layer": layer, "seconds": time.monotonic() - began,
                    "interpreter": interpreter, "screen": screen,
                    "client": client.output.decode("utf-8", "replace"),
                    "started": started.read_text(encoding="utf-8") if started.exists() else None,
                    "root": client.process_state(client.process.pid),
                    "cli": client.process_state(cli_pid) if cli_pid is not None else None,
                    "descendants": {
                        pid: {"name": name, "birth": client.birth(handle)}
                        for pid, (name, handle) in pinned.items()
                    },
                }
                record_property("native-python-startup", json.dumps(evidence))
                pytest.fail(json.dumps(evidence))
        finally:
            try:
                if use_mux:
                    mux("kill-session", "-t", session)
            finally:
                client.job.close()
                client.job = None
                assert client.exited(client.process.process, 5000)
                assert all(client.exited(handle, 5000) for _, handle in pinned.values()), (
                    "Native startup process survived strict Job cleanup",
                )
    assert client.handles == []
