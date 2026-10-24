"""An actual interactive shell may own several groups in its private session."""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys

import psutil
import pytest
from test_interactive import _environment, _gone, _read, _wait

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not (sys.platform.startswith("linux") or sys.platform == "darwin"),
    reason="native POSIX interactive job control",
)

_LEAF = """
import json,os,signal,sys,time
from pathlib import Path
signal.signal(signal.SIGHUP,signal.SIG_IGN)
root=Path(sys.argv[1])
root.write_text(json.dumps({
    'pid':os.getpid(),'group':os.getpgrp(),'session':os.getsid(0),
    'tty':os.ttyname(2) if os.isatty(2) else None}))
while True:
    time.sleep(.1)
"""
_MANAGED = """
import json,os,sys,time
from pathlib import Path
root=Path(sys.argv[1])
root.write_text(json.dumps({'pid':os.getpid(),'session':os.getsid(0)}))
count=0
while True:
    count+=1
    root.with_suffix('.heartbeat').write_text(str(count))
    time.sleep(.05)
"""
_DETACH = """
import subprocess,sys
from agent_procutil import windowless_daemon_kwargs
subprocess.Popen(
    [sys.executable,'-c',sys.argv[1],sys.argv[2]],
    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
    **windowless_daemon_kwargs(breakaway=True))
"""
_GATE = """
import signal,sys,time
from pathlib import Path
signal.signal(signal.SIGHUP,signal.SIG_IGN)
while not Path(sys.argv[1]).exists():
    time.sleep(.01)
"""
_CALLER = """
import fcntl,json,os,sys,termios
from pathlib import Path
from agent_procutil.interactive import run_interactive
os.setsid()
fcntl.ioctl(0,termios.TIOCSCTTY,0)
before=termios.tcgetattr(0)
code=run_interactive(sys.argv[2:])
Path(sys.argv[1]).write_text(json.dumps({
    'code':code,'attributes':termios.tcgetattr(0)==before,
    'foreground':os.tcgetpgrp(0)==os.getpgrp()}))
"""


@pytest.mark.parametrize("mode", ["normal", "caller-killed", "interrupt"])
@pytest.mark.parametrize("managed", [False, True])
def test_interactive_shell_groups_retire_but_managed_detachment_survives(tmp_path, mode, managed):
    if managed and os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        pytest.skip("managed detachment qualification runs in the explicit native workflow")
    bash = shutil.which("bash")
    if not bash:
        pytest.fail("native interactive-shell qualification requires Bash")
    script = tmp_path / "jobs.sh"
    script.write_text(
        'set -m\ntrap "" HUP\n'
        '"$1" -c "$2" "$3/first.json" &\nfirst=$!\n'
        '"$1" -c "$2" "$3/second.json" &\nsecond=$!\n'
        'if [ "$4" = "managed" ]; then "$1" -c "$5" "$6" "$3/managed.json"; fi\n'
        'printf "%s %s %s\\n" "$$" "$first" "$second" > "$3/jobs"\n'
        'echo separate-jobs-ready\n'
        '"$1" -c "$7" "$3/gate"\nexit "$?"\n',
        encoding="utf-8",
    )
    receipt = tmp_path / "result.json"
    master, slave = os.openpty()
    original_tty = os.ttyname(slave)
    process = subprocess.Popen(  # noqa: S603 - isolated fixture argv
        [
            sys.executable, "-c", _CALLER, str(receipt),
            bash, "--noprofile", "--norc", "-i", str(script),
            sys.executable, _LEAF, str(tmp_path),
            "managed" if managed else "ordinary", _DETACH, _MANAGED, _GATE,
        ],
        stdin=slave, stdout=slave, stderr=slave, env=_environment(),
    )
    os.close(slave)
    pins = []
    detached = None
    output = bytearray()

    def drain():
        if select.select([master], [], [], .02)[0]:
            try:
                output.extend(os.read(master, 65536))
            except OSError:
                pass
        return True

    try:
        first = _wait(lambda: _read(tmp_path / "first.json"), "first shell job did not start")
        second = _wait(lambda: _read(tmp_path / "second.json"), "second shell job did not start")
        def jobs():
            try:
                values = list(map(int, (tmp_path / "jobs").read_text().split()))
                return values if len(values) == 3 else None
            except (OSError, ValueError):
                return None

        shell, one, two = _wait(jobs, "shell did not publish its jobs")
        assert [first["pid"], second["pid"]] == [one, two]
        assert len({shell, first["group"], second["group"]}) == 3
        assert first["session"] == second["session"] != process.pid
        assert first["tty"] == second["tty"] != original_tty
        pins = [psutil.Process(pid) for pid in (shell, one, two, first["session"])]
        if managed:
            identity = _wait(
                lambda: _read(tmp_path / "managed.json"), "managed child did not start",
            )
            assert identity["session"] != first["session"]
            detached = psutil.Process(identity["pid"])
        if mode == "caller-killed":
            process.kill()
        elif mode == "interrupt":
            process.send_signal(signal.SIGINT)
        else:
            (tmp_path / "gate").touch()
        _wait(lambda: drain() and process.poll() is not None, "interactive caller did not finish")
        if mode != "caller-killed":
            assert process.returncode == 0, output.decode(errors="replace")
            assert _read(receipt) == {
                "code": 130 if mode == "interrupt" else 0,
                "attributes": True, "foreground": True,
            }
        _wait(lambda: all(_gone(pin) for pin in pins), "owned shell job group survived completion")
        if detached is not None:
            assert not _gone(detached)
            heartbeat = tmp_path / "managed.heartbeat"

            def count():
                try:
                    return int(heartbeat.read_text())
                except (OSError, ValueError):
                    return None

            before = _wait(count, "managed workload did not publish a heartbeat")
            _wait(lambda: (count() or 0) > before, "managed workload stopped progressing")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        for pin in [*pins, *([detached] if detached is not None else [])]:
            if not _gone(pin):
                pin.kill()
                try:
                    pin.wait(timeout=5)
                except psutil.TimeoutExpired:
                    assert _gone(pin), "fixture retirement remained unconfirmed"
        os.close(master)


def test_pty_relay_preserves_redirected_json_stdout(tmp_path):
    receipt = tmp_path / "result.json"
    command = (
        "import sys;"
        "print('terminal-only',file=sys.stderr,flush=True);"
        "assert input()=='answer';"
        "sys.stdout.write('{\"ok\":true}\\n');sys.stdout.flush()"
    )
    master, slave = os.openpty()
    process = subprocess.Popen(  # noqa: S603 - isolated fixture argv
        [sys.executable, "-c", _CALLER, str(receipt), sys.executable, "-c", command],
        stdin=slave, stdout=subprocess.PIPE, stderr=slave, env=_environment(),
    )
    os.close(slave)
    output = bytearray()

    def ready():
        if select.select([master], [], [], .02)[0]:
            try:
                output.extend(os.read(master, 65536))
            except OSError:
                pass
        return b"terminal-only" in output

    try:
        _wait(ready, "redirected completion did not reach its terminal")
        os.write(master, b"answer\n")
        stdout, _ = process.communicate(timeout=10)
        assert process.returncode == 0, output.decode(errors="replace")
        assert stdout == b'{"ok":true}\n'
        assert _read(receipt) == {"code": 0, "attributes": True, "foreground": True}
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
        os.close(master)


def test_pty_relay_drains_output_beyond_its_buffer_without_losing_the_tail(tmp_path):
    receipt = tmp_path / "result.json"
    size = 2 * 1024 * 1024
    command = (
        f"import sys;sys.stdout.buffer.write(b'x'*{size}+b'\\nlast-output\\n');"
        "sys.stdout.buffer.flush()"
    )
    master, slave = os.openpty()
    process = subprocess.Popen(  # noqa: S603 - isolated fixture argv
        [sys.executable, "-c", _CALLER, str(receipt), sys.executable, "-c", command],
        stdin=slave, stdout=slave, stderr=slave, env=_environment(),
    )
    os.close(slave)
    output = bytearray()

    def drain():
        if select.select([master], [], [], .01)[0]:
            try:
                output.extend(os.read(master, 65536))
            except OSError:
                pass
        return True

    try:
        _wait(
            lambda: drain() and process.poll() is not None,
            "large terminal output did not settle",
        )
        while select.select([master], [], [], 0)[0]:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            output.extend(data)
        assert process.returncode == 0, output[-4096:].decode(errors="replace")
        assert output.count(b"x") == size
        assert output.endswith(b"last-output\r\n")
        assert _read(receipt) == {"code": 0, "attributes": True, "foreground": True}
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        os.close(master)
