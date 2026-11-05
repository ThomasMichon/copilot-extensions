"""Actual Companion/client input with synthetic owner data, not installed pixels."""

from __future__ import annotations

import errno
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import time
import uuid
from unittest import mock

import psutil
import pytest

from worktree_manager import companion_pane, mux_mapping_registry
from _manager_process_fixture import synthetic_runtime
from _worktree_timeline_fixtures import delta
from _companion_client_fixture import fixture_engine


pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("tmux"),
    reason="Native POSIX tmux and PTYs are required",
)


class AttachedClient:
    """Keep the native client attached to a drained, caller-owned terminal."""

    def __init__(self, wrapper: Path, session: str):
        import pty

        self.master, slave = pty.openpty()
        self.process = None
        self.received = 0
        self.recent = bytearray()
        try:
            self.resize(120, 45, notify=False)
            self.process = subprocess.Popen(
                [str(wrapper), "attach-session", "-t", session],
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                env={**os.environ, "TERM": "xterm-256color"},
            )
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)

    def resize(self, columns: int, rows: int, *, notify: bool = True) -> None:
        import fcntl
        import termios

        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        if notify:
            os.kill(self.process.pid, signal.SIGWINCH)

    def drain(self) -> None:
        for _ in range(64):
            if not select.select([self.master], [], [], 0)[0]:
                return
            try:
                data = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return
                raise
            if not data:
                return
            self.received += len(data)
            self.recent.extend(data)
            del self.recent[:-262144]

    def send(self, value: bytes) -> None:
        self.drain()
        assert self.process.poll() is None, "native attached client exited"
        assert os.write(self.master, value) == len(value)

    def click(self, screen: str, label: str) -> None:
        rows = screen.splitlines()
        row = next(index for index, value in enumerate(rows) if label in value)
        column = rows[row].index(label) + len(label) // 2 + 1
        self.send(f"\x1b[<0;{column};{row + 1}M\x1b[<0;{column};{row + 1}m".encode())

    def close(self) -> None:
        try:
            self.drain()
            self.process.wait(timeout=10)
        finally:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=5)
            finally:
                os.close(self.master)


def retire_fixture(mux, client, server, session, observer) -> None:
    """Attempt every independent retirement even after a graceful command fails."""
    def close_server():
        try:
            try:
                _, error = server.communicate(timeout=10)
            finally:
                if server.poll() is None:
                    server.terminate()
                    server.communicate(timeout=5)
        finally:
            server.stderr.close()
        assert server.returncode == 0, error.decode("utf-8", errors="replace")

    with ExitStack() as cleanup:
        cleanup.callback(close_server)
        cleanup.callback(mux, "kill-session", "-t", session, check=False)
        cleanup.callback(mux, "set-option", "-g", "exit-empty", "on", check=False)
        if client is not None:
            cleanup.callback(client.close)
        cleanup.callback(mux, "detach-client", "-s", session, check=False)
        if client is not None and client.process.poll() is None and observer is not None:
            if mux("select-pane", "-t", observer, check=False).returncode == 0:
                client.send(b"\x1b")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    client.drain()
                    if observer not in mux("list-panes", "-t", session, "-F", "#{pane_id}", check=False).stdout.splitlines():
                        break
                    time.sleep(.025)


@pytest.mark.timeout(120)
def test_actual_companion_attached_client_browsing_resize_and_return(
    tmp_path, monkeypatch, record_property,
):
    name = "companion-app-" + uuid.uuid4().hex
    wrapper = tmp_path / "tmux-client"
    wrapper.write_text(
        "#!/bin/sh\nexec " + shlex.quote(shutil.which("tmux")) +
        " -f /dev/null -L " + shlex.quote(name) + ' "$@"\n', encoding="utf-8",
    )
    wrapper.chmod(0o700)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    profile, checkout, owner_root = [tmp_path / part for part in ("profile", "checkout", "owner")]
    profile.mkdir()
    checkout.mkdir()
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(key, str(profile))
    interpreter, calls = fixture_engine(owner_root, checkout)
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_ARGV", json.dumps([
        str(interpreter), "-m", "agent_worktrees",
    ]))
    monkeypatch.delenv("WORKTREE_MANAGER_ENGINE_CMD", raising=False)
    root = tmp_path / "manager"
    slot = synthetic_runtime(root, "1.2.3")
    package = slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages/worktree_manager"
    source = Path(companion_pane.__file__).with_name("mux_companion.py")
    assert (package / "mux_companion.py").read_bytes() == source.read_bytes()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setattr(sys, "prefix", str(slot))
    monkeypatch.setattr(companion_pane.ec, "current_worktree_status", lambda **kw: {
        "repo": "example", "id": "selected", "path": str(checkout),
    })
    client = None
    observer = None
    server = subprocess.Popen(
        [str(wrapper), "-D"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    def mux(*args, check=True):
        result = subprocess.run(
            [str(wrapper), *args], capture_output=True, text=True, timeout=5,
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.rstrip("\r\n") if check else result

    def wait_for(condition, diagnostic, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if client is not None:
                client.drain()
                assert client.process.poll() is None, diagnostic()
            assert server.poll() is None, "private foreground tmux server exited"
            if condition():
                return
            time.sleep(.025)
        pytest.fail(diagnostic())

    def screen():
        return mux("capture-pane", "-p", "-t", observer)

    def owner_calls():
        return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    try:
        wait_for(lambda: mux("list-sessions", check=False).returncode == 0 or
                 "no sessions" in mux("list-sessions", check=False).stderr,
                 lambda: "private server did not become ready")
        inert = (
            "import sys;print('INERT SOURCE CLI',flush=True)\n"
            "for line in sys.stdin: print('CLI RECEIVED '+line.strip(),flush=True)\n"
        )
        mux("new-session", "-d", "-s", name, "-c", str(checkout), shlex.join([
            sys.executable, "-I", "-u", "-c", inert,
        ]))
        cli, incarnation, pid, window = mux(
            "display-message", "-p", "-t", name,
            "#{pane_id}\t#{session_id}:#{session_created}\t#{pane_pid}\t#{window_id}",
        ).split("\t")
        client = AttachedClient(wrapper, name)
        wait_for(lambda: mux("list-clients", "-F", "#{client_pid}") == str(client.process.pid),
                 lambda: "client never attached")
        mux_mapping_registry.register_mapping({
            "project": "example", "worktree_id": "selected", "mux_bin": str(wrapper),
            "mux_session": name, "worktree_path": str(checkout),
            "session_incarnation": incarnation,
            "panes": [{"pane_id": cli, "role": "cli", "live": True}],
            "mapping_revision": 1, "live": True,
        }, root=root)
        observer = companion_pane.switch("show", str(checkout))
        wait_for(lambda: "A live Copilot session" in screen() and "Purpose" in screen(), screen)
        assert mux("display-message", "-p", "-t", name, "#{pane_id}") == observer
        assert client.received > 0

        client.send(b"\x1b[C")
        wait_for(lambda: "Observed change 40" in screen(), screen)
        wait_for(lambda: (owner_root / "feed-pid").exists(), screen)
        feed = psutil.Process(int((owner_root / "feed-pid").read_text()))
        feed_birth = feed.create_time()
        client.send(b"\t\t")
        client.send(b"\x1b[H")
        wait_for(lambda: any("--before-cursor" in call for call in owner_calls()), screen)
        client.send(b"\x1b[F")
        wait_for(lambda: "Observed change 40" in screen(), screen)
        client.send(b"\x1b[H")
        wait_for(lambda: re.search(r"Observed change 1(?!\d)", screen()), screen)
        delivery = owner_root / "push.json"
        pending = delivery.with_suffix(".pending")
        pending.write_text(json.dumps(delta()), encoding="utf-8")
        pending.replace(delivery)
        wait_for(lambda: "Updated purpose from pushed owner facts." in screen(), screen)
        assert re.search(r"Observed change 1(?!\d)", screen())
        assert "Observed change 41" not in screen()
        assert "1 unread" in screen()
        record_property("native-browsing-screen", screen())
        record_property("native-browsing-ansi-json", json.dumps(mux("capture-pane", "-e", "-p", "-t", observer)))

        client.resize(92, 38)
        wait_for(lambda: mux("display-message", "-p", "-t", name, "#{window_width}") == "92", screen)
        wait_for(lambda: "Updated purpose from pushed owner facts." in screen(), screen)
        record_property("native-resized-screen", screen())
        record_property("native-resized-ansi-json", json.dumps(mux("capture-pane", "-e", "-p", "-t", observer)))
        client.send(b"\x1b[F")
        wait_for(lambda: "Observed change 41" in screen(), screen)
        assert mux("display-message", "-p", "-t", cli, "#{pane_pid}") == pid
        assert mux("display-message", "-p", "-t", observer, "#{window_id}") == window
        assert len(mux("list-windows", "-t", name, "-F", "#{window_id}").splitlines()) == 1
        assert len(mux("list-panes", "-t", name, "-F", "#{pane_id}").splitlines()) == 2

        client.click(screen(), "Return to CLI")
        wait_for(lambda: mux("display-message", "-p", "-t", name, "#{pane_id}") == cli,
                 lambda: mux("capture-pane", "-p", "-t", cli))
        assert companion_pane.switch("show", str(checkout)) == observer
        wait_for(lambda: "Observed change 41" in screen(), screen)
        client.send(b"\x1b")
        wait_for(lambda: mux("display-message", "-p", "-t", name, "#{pane_id}") == cli,
                 lambda: mux("capture-pane", "-p", "-t", cli))
        wait_for(lambda: len(mux("list-panes", "-t", name, "-F", "#{pane_id}").splitlines()) == 1,
                 lambda: mux("list-panes", "-t", name, "-F", "#{pane_id}"))

        def feed_retired():
            try:
                return feed.create_time() != feed_birth or not feed.is_running()
            except psutil.NoSuchProcess:
                return True

        wait_for(feed_retired, lambda: "owned observation CLI survived actual app exit")
        client.send(b"native-return\r")
        wait_for(lambda: "CLI RECEIVED native-return" in mux("capture-pane", "-p", "-t", cli),
                 lambda: mux("capture-pane", "-p", "-t", cli))
        assert mux("display-message", "-p", "-t", cli, "#{pane_pid}") == pid
        assert mux("display-message", "-p", "-t", cli, "#{session_id}:#{session_created}") == incarnation
        record_property("native-client-boundary", json.dumps({
            "tmux": mux("-V"), "client_bytes": client.received,
            "app": "unmodified mux_companion.py", "owner": "synthetic wire-valid fixture",
            "cli": "inert source fixture", "input": "attached-client PTY",
            "display": "tmux server capture; not headed pixels or frame latency",
        }))
    finally:
        retire_fixture(mux, client, server, name, observer)


@pytest.mark.parametrize("failure", [
    "select-pane", "detach-client", "set-option", "kill-session", "client-close",
])
def test_native_fixture_retirement_survives_graceful_timeout(failure):
    attempted = []

    def operation(name):
        attempted.append(name)
        if failure == name:
            raise subprocess.TimeoutExpired(name, 5)

    def mux(command, *args, **kwargs):
        operation(command)
        return subprocess.CompletedProcess([], 0, stdout="")

    client, server = mock.Mock(), mock.Mock()
    client.process.poll.return_value = None
    client.close.side_effect = lambda: operation("client-close")
    server.communicate.side_effect = lambda **kwargs: (operation("server-wait"), b"")
    server.poll.return_value = 0
    server.returncode = 0
    with pytest.raises(subprocess.TimeoutExpired):
        retire_fixture(mux, client, server, "private", "%1")
    assert attempted[-5:] == [
        "detach-client", "client-close", "set-option", "kill-session", "server-wait",
    ]
    server.stderr.close.assert_called_once()


def test_native_fixture_server_timeout_still_terminates_and_closes_pipe():
    server = mock.Mock()
    server.communicate.side_effect = [subprocess.TimeoutExpired("tmux", 10), (None, b"")]
    server.poll.return_value = None
    with pytest.raises(subprocess.TimeoutExpired):
        retire_fixture(lambda *args, **kwargs: None, None, server, "private", None)
    server.terminate.assert_called_once()
    assert server.communicate.call_args_list == [mock.call(timeout=10), mock.call(timeout=5)]
    server.stderr.close.assert_called_once()


def test_native_fixture_client_timeout_still_closes_terminal():
    client = AttachedClient.__new__(AttachedClient)
    client.master, client.process, client.drain = 42, mock.Mock(), mock.Mock()
    client.process.wait.side_effect = subprocess.TimeoutExpired("tmux-client", 5)
    client.process.poll.return_value = None
    with mock.patch.object(os, "close") as close:
        with pytest.raises(subprocess.TimeoutExpired):
            client.close()
        close.assert_called_once_with(42)
    client.process.terminate.assert_called_once()
