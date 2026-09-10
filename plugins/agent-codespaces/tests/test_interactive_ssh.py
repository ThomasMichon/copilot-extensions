"""Interactive CLI validation, argv integrity, and owned process lifetime."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_codespaces import __main__ as cli
from agent_codespaces import interactive


@pytest.mark.parametrize("options", [
    ["--interactive-command", ""],
    ["--interactive-command", " \n\t"],
    ["--interactive-command", "echo \0bad"],
    ["--interactive-command-file", ""],
    ["--interactive-command-file", "missing-command.txt"],
    ["--interactive-command", "true", "--interactive-command-file", "missing.txt"],
    ["--interactive-command", "true", "--remote-cmd", ""],
    ["--interactive-command-file", "missing.txt", "--remote-cmd-file", "missing.txt"],
    ["--interactive-command", "true", "--stdio"],
    ["--require-relay", "--no-relay"],
    ["--local-forward", "1:2", "--remote-cmd", "true"],
    ["--reverse-forward", "1:2", "--remote-cmd-file", "missing.txt"],
    ["--local-forward", "1:2", "--stdio"],
    ["--reverse-forward", "1:2", "--reverse-forward", "01:3"],
    ["--local-forward", "1:2", "--local-forward", "1:2"],
])
def test_invalid_options_fail_before_dispatch(monkeypatch, options):
    monkeypatch.setattr(cli, "_cmd_ssh", lambda _: pytest.fail("dispatched invalid options"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["ssh", "example-space", *options])
    assert exc.value.code == 2


@pytest.mark.parametrize("value", [
    "0:2", "1:0", "65536:2", "1:65536", "-1:2", "1:-2", "1", "1:2:3",
    "localhost:1:2", "0.0.0.0:1:2", "[::1]:1:2", "1:example.com:2",
    "+1:2", "1: 2", "1:2\n", "１:2", "1:2 -g", "1:2;true",
])
@pytest.mark.parametrize("direction", ["--local-forward", "--reverse-forward"])
def test_invalid_forward_grammar(monkeypatch, direction, value):
    monkeypatch.setattr(cli, "_cmd_ssh", lambda _: pytest.fail("dispatched invalid forward"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["ssh", "example-space", f"{direction}={value}"])
    assert exc.value.code == 2


def test_file_integrity_and_normalized_ports(tmp_path, monkeypatch):
    path = tmp_path / "command with spaces.txt"
    payload = "  cd /workspaces/example-web\r\nprintf '%s\\n' \"quotes ' café\" '$HOME & %PATH% | > ; `id`'\n"
    path.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))
    os.utime(path, (1000000000, 1000000000))
    before = path.stat().st_mtime_ns
    captured = []
    monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
    monkeypatch.setattr(cli, "_cmd_ssh", lambda args: captured.append(args) or 19)
    assert cli.main([
        "ssh", "example-space", "--interactive-command-file", str(path),
        "--local-forward", "00001:65535", "--local-forward", "2:65535",
        "--reverse-forward", "1:2", "--reverse-forward", "65535:1",
        "--no-provision", "--no-relay",
    ]) == 19
    args = captured[0]
    assert args.interactive_command == payload
    assert args.local_forward == [(1, 65535), (2, 65535)]
    assert args.reverse_forward == [(1, 2), (65535, 1)]
    assert args.remote_cmd is None and not args.stdio
    assert args.no_provision and args.no_relay
    assert not args.force and not args.force_claim
    assert path.stat().st_mtime_ns == before
    assert path.read_bytes() == b"\xef\xbb\xbf" + payload.encode("utf-8")
    wrapped = cli._build_launch_command(
        payload, ["/ignored"], is_stdio=False,
        relay_env="export EXAMPLE=1; ", breadcrumb="true",
    )
    words = shlex.split(wrapped)
    assert words[:3] == ["bash", "-l", "-c"]
    assert words[3] == "export EXAMPLE=1; true; " + payload
    assert "--plugin-dir" not in wrapped


@pytest.mark.parametrize("contents", [b"", b" \r\n\t", b"\xff", b"true\0false"])
def test_unusable_command_file(tmp_path, monkeypatch, contents):
    path = tmp_path / "command.txt"
    path.write_bytes(contents)
    monkeypatch.setattr(cli, "_cmd_ssh", lambda _: pytest.fail("dispatched invalid file"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["ssh", "example-space", "--interactive-command-file", str(path)])
    assert exc.value.code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("with_command", [False, True])
@pytest.mark.parametrize("with_forwards", [False, True])
async def test_exact_argv_and_account_pin(monkeypatch, with_command, with_forwards):
    pinned = {"GH_TOKEN": "synthetic-token", "EXAMPLE": "preserved"}
    monkeypatch.setattr("agent_codespaces.lifecycle.account_for_codespace", lambda _: "example")
    monkeypatch.setattr("agent_codespaces.gh_account.env_for_account", lambda _: pinned)
    monkeypatch.setattr(interactive, "foreground_group", lambda _: None)
    command = cli._build_launch_command(
        "printf '%s\\n' \"quotes ' $HOME &\";\nexec bash -il",
        [], is_stdio=False, relay_env="", breadcrumb="true",
    ) if with_command else None
    options = interactive.ssh_options([(8080, 3000), (8081, 3001)], [(9000, 9001)]) if with_forwards else []
    proc = SimpleNamespace(pid=1234, returncode=29, wait=AsyncMock(return_value=29))
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert await cli._interactive_ssh(
        "example-space", options, relay_port=9857, relay_token="synthetic-relay", command=command,
    ) == 29
    expected = ["gh", "codespace", "ssh", "-c", "example-space"]
    if with_command or with_forwards:
        expected += ["--"]
        if with_command:
            expected += ["-tt"]
        if with_forwards:
            expected += [
                "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-L", "127.0.0.1:8080:127.0.0.1:3000",
                "-L", "127.0.0.1:8081:127.0.0.1:3001",
                "-R", "127.0.0.1:9000:127.0.0.1:9001",
            ]
        if with_command:
            expected += [command]
    assert list(spawn.call_args.args) == expected
    kwargs = spawn.call_args.kwargs
    assert kwargs == {
        "env": {**pinned, "LC_GIT_CREDENTIAL_RELAY": "9857", "GIT_TERMINAL_PROMPT": "0",
                "LC_GIT_CREDENTIAL_RELAY_TOKEN": "synthetic-relay"},
        **interactive.process_group_options(),
    }
    assert pinned == {"GH_TOKEN": "synthetic-token", "EXAMPLE": "preserved"}


def test_openssh_accepts_exact_options_without_connecting():
    ssh = shutil.which("ssh")
    if not ssh:
        pytest.skip("OpenSSH not installed")
    result = subprocess.run([
        ssh, "-F", os.devnull, "-G", "-tt",
        *interactive.ssh_options([(8080, 3000)], [(9000, 9001)]),
        "example-host",
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    for value in ("requesttty force", "exitonforwardfailure yes", "serveraliveinterval 30",
                  "serveralivecountmax 3"):
        assert value in lines
    assert "localforward [127.0.0.1]:8080 [127.0.0.1]:3000" in lines
    assert "remoteforward [127.0.0.1]:9000 [127.0.0.1]:9001" in lines


@pytest.mark.asyncio
async def test_shell_payload_survives_real_process_argv(tmp_path, monkeypatch):
    output = tmp_path / "argv.json"
    payload = "  printf '%s\\n' \"quotes ' café\" '$HOME %PATH% & | > ; `id`'\nexec bash -il\n"
    command = cli._build_launch_command(
        payload, [], is_stdio=False, relay_env="", breadcrumb="true",
    )
    real_spawn = asyncio.create_subprocess_exec
    monkeypatch.setattr("agent_codespaces.lifecycle.account_for_codespace", lambda _: None)

    async def spawn(*args, **kwargs):
        return await real_spawn(
            sys.executable, "-c",
            "import json,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))",
            str(output), *args, **kwargs,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert await cli._interactive_ssh("example-space", [], command=command) == 0
    assert json.loads(output.read_text()) == [
        "gh", "codespace", "ssh", "-c", "example-space", "--", "-tt", command,
    ]


@pytest.mark.parametrize("no_relay", [False, True])
def test_interactive_process_preserves_relay_and_claim_lifetime(monkeypatch, ssh_runtime, no_relay):
    events, manager = ssh_runtime
    real_spawn = asyncio.create_subprocess_exec
    children = []
    argv = []

    async def spawn(*args, **kwargs):
        argv.extend(args)
        child = await real_spawn(
            sys.executable, "-c", "import time; time.sleep(.15); raise SystemExit(37)",
            **kwargs,
        )
        children.append(child)
        events.append("child")
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    options = ["--no-relay"] if no_relay else []
    assert cli.main([
        "ssh", "example-space", "--interactive-command", "exec bash -il",
        "--local-forward", "8080:3000", "--reverse-forward", "9000:9001",
        "--no-provision", *options,
    ]) == 37
    assert children[0].returncode == 37
    assert events[:2] == ["claim", "clear-status"]
    assert events.index("lock") < events.index("child") < events.index("unlock")
    if not no_relay:
        assert events.index("relay-start") < events.index("child") < events.index("relay-stop")
        assert events.count("heartbeat") >= 2
        assert events.index("relay-stop") < events.index("unlock")
    else:
        assert "relay-start" not in events
    manager.ensure_connected.assert_awaited_once()
    assert manager.ensure_connected.call_args.args[2] == []
    assert manager.disconnect.await_count == 1
    assert argv[:7] == ["gh", "codespace", "ssh", "-c", "example-space", "--", "-tt"]
    assert shlex.split(argv[-1])[3].endswith("; exec bash -il")


def test_relay_listener_conflict_precedes_claims(ssh_runtime):
    events, manager = ssh_runtime
    assert cli.main([
        "ssh", "example-space", "--reverse-forward", "9857:4000", "--no-provision",
    ]) == 2
    assert events == []
    manager.ensure_connected.assert_not_awaited()


def test_interactive_launch_failure_releases_relay_and_lock(monkeypatch, ssh_runtime):
    events, manager = ssh_runtime
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("spawn failed")))
    with pytest.raises(OSError, match="spawn failed"):
        cli.main([
            "ssh", "example-space", "--interactive-command", "true", "--no-provision",
        ])
    assert events[-2:] == ["relay-stop", "unlock"]
    assert manager.disconnect.await_count == 1


def test_interactive_timeout_reaps_child_before_resources(monkeypatch, ssh_runtime):
    events, manager = ssh_runtime
    real_spawn = asyncio.create_subprocess_exec
    children = []

    async def spawn(*args, **kwargs):
        proc = await real_spawn(
            sys.executable, "-c", "import time; time.sleep(30)", **kwargs,
        )
        children.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert cli.main([
        "ssh", "example-space", "--interactive-command", "true", "--no-provision",
        "--connect-timeout", "1",
    ]) == 124
    assert children and children[0].returncode is not None
    assert events[-2:] == ["relay-stop", "unlock"]
    assert manager.disconnect.await_count == 1


def test_new_options_do_not_bypass_busy_target(monkeypatch, ssh_runtime):
    import ssh_manager

    events, manager = ssh_runtime

    class BusyLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self, force=False):
            assert not force
            raise ssh_manager.TargetBusyError(
                "example-space", SimpleNamespace(pid=1234, op="stdio", age_seconds=1),
            )

    monkeypatch.setattr(ssh_manager, "TargetLock", BusyLock)
    assert cli.main([
        "ssh", "example-space", "--interactive-command", "true",
        "--local-forward", "8080:3000", "--no-provision", "--no-relay",
    ]) == cli._BUSY_EXIT
    manager.ensure_connected.assert_not_awaited()
    assert "relay-start" not in events


@pytest.mark.asyncio
async def test_cancellation_reaps_only_owned_child_tree(tmp_path, monkeypatch):
    real_spawn = asyncio.create_subprocess_exec
    monkeypatch.setattr("agent_codespaces.lifecycle.account_for_codespace", lambda _: None)
    child_pid_file = tmp_path / "descendant.pid"
    children = []
    # The child and its descendant stay alive until cleanup; a separate process
    # proves teardown is scoped, not a process-name or caller-group kill.
    script = (
        "import pathlib,subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(30)"
    )
    unrelated = await real_spawn(sys.executable, "-c", "import time;time.sleep(30)")

    async def spawn(*args, **kwargs):
        proc = await real_spawn(sys.executable, "-c", script, str(child_pid_file), **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(cli._interactive_ssh("example-space", [], command="true"))
    try:
        for _ in range(200):
            if child_pid_file.exists():
                break
            await asyncio.sleep(.01)
        assert child_pid_file.exists(), "child process did not become ready"
        descendant = int(child_pid_file.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
        assert unrelated.returncode is None
        if sys.platform == "win32":
            # A terminated process may still have an open handle, so query its
            # exit code instead of treating OpenProcess success as liveness.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, descendant)
            if handle:
                try:
                    code = wintypes.DWORD()
                    assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                    assert code.value != 259
                finally:
                    kernel.CloseHandle(handle)
        else:
            # A reparented zombie is dead too; ps is a read-only liveness probe.
            result = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(descendant)], capture_output=True, text=True,
            )
            assert not result.stdout.strip() or result.stdout.lstrip().startswith("Z")
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        unrelated.kill()
        await unrelated.wait()


def test_foreground_restore_and_short_child_race(monkeypatch):
    monkeypatch.setattr(interactive.sys, "platform", "linux")
    monkeypatch.setattr(interactive.sys, "stdin", SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(os, "getpgrp", lambda: 10, raising=False)
    monkeypatch.setattr(os, "tcgetpgrp", lambda _: 10, raising=False)
    changes = []
    monkeypatch.setattr(os, "tcsetpgrp", lambda *a: changes.append(a), raising=False)
    monkeypatch.setattr(os, "killpg", lambda *a: (_ for _ in ()).throw(ProcessLookupError()), raising=False)
    monkeypatch.setattr(signal, "SIGTTOU", 22, raising=False)
    monkeypatch.setattr(signal, "SIGCONT", 18, raising=False)
    monkeypatch.setattr(signal, "signal", lambda *a: signal.SIG_DFL)
    previous = interactive.foreground_group(20)
    assert previous == (0, 10)
    interactive.restore_foreground(previous)
    assert changes == [(0, 20), (0, 10)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX controlling-terminal contract")
def test_real_posix_terminal_is_borrowed_and_restored(tmp_path):
    import pty

    result = tmp_path / "terminal-result.txt"
    program = textwrap.dedent("""\
        import asyncio, fcntl, os, pathlib, sys, termios
        from agent_codespaces import __main__ as cli, lifecycle
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        initial = os.tcgetpgrp(0)
        spawn = asyncio.create_subprocess_exec
        lifecycle.account_for_codespace = lambda _: None
        async def terminal(*args, **kwargs):
            return await spawn(
                sys.executable, "-c",
                "import os; assert input() == 'ready'; "
                "assert os.tcgetpgrp(0) == os.getpgrp(); "
                "fd=os.open('/dev/tty',os.O_RDWR);os.close(fd);raise SystemExit(23)",
                **kwargs,
            )
        asyncio.create_subprocess_exec = terminal
        async def run():
            code = await asyncio.wait_for(
                cli._interactive_ssh("example-space", [], command="true"), 5,
            )
            assert code == 23
            assert os.tcgetpgrp(0) == initial
        asyncio.run(run())
        pathlib.Path(sys.argv[1]).write_text("passed")
    """)
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", program, str(result)],
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        )
        try:
            os.write(master, b"ready\n")
            assert proc.wait(timeout=10) == 0
            assert result.read_text() == "passed"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
    finally:
        os.close(master)
        os.close(slave)
