"""Actual private popup input, not installed or headed-client acceptance."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid

import psutil
import pytest

from worktree_manager.companion_runtime import companion_shell_command
from _companion_client_fixture import fixture_engine
from _manager_process_fixture import synthetic_runtime
from test_companion_app_tmux import AttachedClient, retire_fixture


pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("tmux"),
    reason="Native POSIX tmux and PTYs are required",
)


def test_owner_fixture_records_rejected_cutover_attempt(tmp_path: Path) -> None:
    owner, checkout = tmp_path / "owner", tmp_path / "checkout"
    checkout.mkdir()
    interpreter, observation_calls = fixture_engine(owner, checkout)
    arguments = ["--project", "example", "handoff-cutover-trigger", "--worktree-id", "selected"]
    result = subprocess.run(
        [str(interpreter), "-m", "agent_worktrees", *arguments],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 2
    assert "unsupported isolated fixture command" in result.stderr
    assert not observation_calls.exists()
    recorded = [json.loads(line) for line in (owner / "invocations.jsonl").read_text().splitlines()]
    assert recorded == [arguments]


@pytest.mark.timeout(90)
@pytest.mark.parametrize("dismissal", ["escape", "ctrl-q", "button"])
@pytest.mark.parametrize("producer", ["ready", "blocked", "failed"])
def test_actual_popup_clean_exit_and_client_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None], dismissal: str, producer: str,
) -> None:
    name = "companion-popup-" + uuid.uuid4().hex
    wrapper = tmp_path / "tmux-client"
    wrapper.write_text(
        "#!/bin/sh\nexec " + shlex.quote(shutil.which("tmux")) +
        " -f /dev/null -L " + shlex.quote(name) + ' "$@"\n', encoding="utf-8",
    )
    wrapper.chmod(0o700)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    profile, checkout, owner = [tmp_path / part for part in ("profile", "checkout", "owner")]
    profile.mkdir()
    checkout.mkdir()
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(profile))
    interpreter, _ = fixture_engine(owner, checkout)
    blocked_pid = owner / "blocked-pid"
    if producer in {"blocked", "failed"}:
        module = owner / "modules/agent_worktrees/__main__.py"
        script = module.read_text(encoding="utf-8")
        status_response = (
            "        (ROOT/'blocked-pid').write_text(str(os.getpid()))\n"
            "        while True: time.sleep(.025)\n"
            if producer == "blocked" else
            "        print('fixture status failed',file=sys.stderr)\n"
            "        raise SystemExit(2)\n"
        )
        module.write_text(script.replace(
            "    args=sys.argv[1:]\n",
            "    args=sys.argv[1:]\n"
            "    if 'status-segment' in args:\n" + status_response,
        ), encoding="utf-8")
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_ARGV", json.dumps([
        str(interpreter), "-m", "agent_worktrees",
    ]))
    monkeypatch.delenv("WORKTREE_MANAGER_ENGINE_CMD", raising=False)
    root = tmp_path / "manager"
    slot = synthetic_runtime(root, "1.2.3")
    package = slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages/worktree_manager"
    assert (package / "mux_companion.py").read_bytes() == (
        Path(__file__).resolve().parents[2] / "src/worktree_manager/mux_companion.py"
    ).read_bytes()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setattr(sys, "prefix", str(slot))
    client = None
    popup = None
    owned_reader = None
    server = subprocess.Popen(
        [str(wrapper), "-D"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    def mux(*args: str, check: bool = True):
        result = subprocess.run(
            [str(wrapper), *args], capture_output=True, text=True, timeout=5,
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.rstrip("\r\n") if check else result

    def wait_for(condition: Callable[[], object], seconds: float = 15) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if client is not None:
                client.drain()
                assert client.process.poll() is None, bytes(client.recent)
            assert server.poll() is None, "private foreground server exited"
            if condition():
                return
            time.sleep(.025)
        pytest.fail(repr(bytes(client.recent) if client is not None else "no client"))

    def retired(process: psutil.Process, birth: float) -> bool:
        try:
            return process.create_time() != birth or not process.is_running()
        except psutil.NoSuchProcess:
            return True

    receipt, finished = tmp_path / "popup-pid", tmp_path / "popup-exit"
    try:
        wait_for(lambda: mux("list-sessions", check=False).returncode == 0 or
                 "no sessions" in mux("list-sessions", check=False).stderr)
        inert = (
            "import sys;print('INERT SOURCE CLI',flush=True)\n"
            "for line in sys.stdin: print('CLI RECEIVED '+line.strip(),flush=True)\n"
        )
        mux("new-session", "-d", "-s", name, "-c", str(checkout), shlex.join([
            sys.executable, "-I", "-u", "-c", inert,
        ]))
        cli, pid, incarnation = mux(
            "display-message", "-p", "-t", name,
            "#{pane_id}\t#{pane_pid}\t#{session_id}:#{session_created}",
        ).split("\t")
        client = AttachedClient(wrapper, name)
        wait_for(lambda: mux("list-clients", "-F", "#{client_pid}") == str(client.process.pid))
        app_command = companion_shell_command([
            "-m", "worktree_manager.mux_companion", "--cwd", str(checkout),
        ])
        command = (
            "printf '%s' \"$$\" > " + shlex.quote(str(receipt)) + "; " +
            app_command + "; result=$?; printf '%s' \"$result\" > " +
            shlex.quote(str(finished)) + '; exit "$result"'
        )
        mux("bind-key", "-n", "C-k", "display-popup", "-E", "-w", "100%", "-h", "100%",
            "-d", str(checkout), command)
        client.send(b"\x0b")
        wait_for(receipt.exists)
        launcher = psutil.Process(int(receipt.read_text()))
        wait_for(lambda: any(
            "worktree_manager.mux_companion" in process.cmdline()
            for process in launcher.children(recursive=True)
        ))
        popup = next(process for process in launcher.children(recursive=True)
                     if "worktree_manager.mux_companion" in process.cmdline())
        popup_birth = popup.create_time()
        if producer == "blocked":
            wait_for(blocked_pid.exists)
            owned_reader = psutil.Process(int(blocked_pid.read_text()))
        elif producer == "failed":
            wait_for(lambda: b"fixture status failed" in client.recent)
        else:
            wait_for(lambda: b"A live Copilot session" in client.recent)
            client.send(b"\x1b[C")
            wait_for(lambda: (owner / "feed-pid").exists())
            owned_reader = psutil.Process(int((owner / "feed-pid").read_text()))
        reader_birth = owned_reader.create_time() if owned_reader is not None else None
        client.send(b"\x03")
        wait_for(lambda: b"Do you want to quit?" in client.recent)
        assert not finished.exists() and not retired(popup, popup_birth)
        record_property("native-popup-ctrl-c", "actual client toast; app remains alive")
        if dismissal == "button":
            client.send(b"\x1b[Z\r")
        else:
            client.send(b"\x1b" if dismissal == "escape" else b"\x11")
        wait_for(finished.exists)
        assert finished.read_text() == "0"
        wait_for(lambda: retired(popup, popup_birth) and (
            owned_reader is None or retired(owned_reader, reader_birth)
        ))
        client.send(b"popup-return\r")
        wait_for(lambda: "CLI RECEIVED popup-return" in mux("capture-pane", "-p", "-t", cli))
        assert mux("display-message", "-p", "-t", cli, "#{pane_pid}") == pid
        assert mux("display-message", "-p", "-t", cli, "#{session_id}:#{session_created}") == incarnation
        assert mux("list-panes", "-t", name, "-F", "#{pane_id}") == cli
        recorded = [
            json.loads(line) for line in (owner / "invocations.jsonl").read_text().splitlines()
        ]
        assert recorded
        assert not any("handoff-cutover-trigger" in invocation for invocation in recorded)
        record_property("native-popup-boundary", json.dumps({
            "dismissal": dismissal, "producer": producer, "exit": 0,
            "input": "attached-client PTY; private C-k display-popup -E binding",
            "runtime": "unmodified app in constructed source prefix; synthetic owner",
            "display": "bounded actual terminal output; not headed pixels or frame latency",
        }))
    finally:
        with ExitStack() as cleanup:
            cleanup.callback(retire_fixture, mux, client, server, name, None)
            if client is not None and client.process.poll() is None:
                client.send(b"\x11")
