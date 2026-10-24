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
_GUARD_DIAGNOSTICS = """
import faulthandler,os,traceback
from agent_procutil._interactive_groups import SessionCustody
_stack=open(os.environ['PTY_FIXTURE_GUARDIAN'],'a',encoding='utf-8')
faulthandler.dump_traceback_later(1,repeat=True,file=_stack)
_retire=SessionCustody.retire
def observed(self,*args,**kwargs):
    with open(os.environ['PTY_FIXTURE_GUARDIAN'],'a',encoding='utf-8') as stream:
        stream.write('retire-enter\\n')
        stream.flush()
        faulthandler.dump_traceback_later(1,file=stream)
        try:
            result=_retire(self,*args,**kwargs)
            stream.write('retire-complete\\n')
            stream.flush()
            return result
        except BaseException:
            traceback.print_exc(file=stream)
            try:
                stream.write('groups='+repr(self.census.groups(exclude_pid=os.getpid()))+'\\n')
            except BaseException:
                traceback.print_exc(file=stream)
            stream.flush()
            raise
        finally:
            faulthandler.cancel_dump_traceback_later()
SessionCustody.retire=observed
"""
_CALLER = """
import copy,faulthandler,fcntl,json,os,sys,termios,time
from pathlib import Path
from types import SimpleNamespace
import psutil
from agent_procutil import _interactive_terminal as terminal
from agent_procutil.interactive import run_interactive
os.setsid()
fcntl.ioctl(0,termios.TIOCSCTTY,0)
before=termios.tcgetattr(0)
def configured(values):
    # BSD PENDIN is kernel input-queue state, not configured terminal mode.
    result=list(values)
    result[3]&=~getattr(termios,'PENDIN',0)
    return result
diagnostic={}
_init=terminal._Terminal.__init__
_restore=terminal._Terminal.restore
def init(self,*args):
    _init(self,*args)
    diagnostic['snapshot']=copy.deepcopy(getattr(self,'attributes',None))
def restore(self):
    diagnostic['restore_foreground']=os.tcgetpgrp(0)
    _restore(self)
    diagnostic['restored']=termios.tcgetattr(0)
terminal._Terminal.__init__=init
terminal._Terminal.restore=restore
_confirm=terminal.confirm_session
def confirm(process,**kwargs):
    started=time.monotonic()
    try:
        return _confirm(process,**kwargs)
    finally:
        diagnostic['confirm_elapsed']=time.monotonic()-started
        try:
            owner=psutil.Process(process.pid)
            diagnostic['guardian_after_confirm']=[
                [p.pid,p.ppid(),p.status()] for p in [owner,*owner.children(recursive=True)]]
        except psutil.Error as error:
            diagnostic['guardian_after_confirm']=type(error).__name__
terminal.confirm_session=confirm
_select=terminal.select.select
_last=[0]
def observe_select(*args):
    result=_select(*args)
    frame=sys._getframe(1)
    now=time.monotonic()
    if frame.f_code.co_name=='_relay' and now-_last[0]>=1:
        _last[0]=now
        values=frame.f_locals
        state={
            'readers':args[0],'writers':args[1],
            'readable':result[0],'writable':result[1],
            'input_buffer':len(values['incoming']),
            'output_buffer':len(values['output']),
            'last_write_count':values.get('count')}
        try:
            state['pty_foreground']=os.tcgetpgrp(values['master'])
        except OSError as error:
            state['pty_query_errno']=error.errno
        try:
            state['pty_attributes']=termios.tcgetattr(values['master'])
        except OSError as error:
            state['pty_attributes_errno']=error.errno
        owner=frame.f_back.f_locals.get('process')
        if owner is not None:
            try:
                members=[psutil.Process(owner.pid),
                         *psutil.Process(owner.pid).children(recursive=True)]
                state['members']=[
                    [p.pid,p.ppid(),p.status(),os.getpgid(p.pid),os.getsid(p.pid)]
                    for p in members]
            except (OSError,psutil.Error):
                pass
        Path(sys.argv[1]).with_suffix('.relay.json').write_text(
            json.dumps(state,default=lambda value:value.hex()))
    return result
terminal.select=SimpleNamespace(select=observe_select)
os.environ['PTY_FIXTURE_GUARDIAN']=str(Path(sys.argv[1]).with_suffix('.guardian.txt'))
terminal._BOOTSTRAP=terminal._BOOTSTRAP.replace(
    'from agent_procutil._interactive_session import main;main()',
    'exec('+repr(""" + repr(_GUARD_DIAGNOSTICS) + """)+');'
    'from agent_procutil._interactive_session import main;main()')
with Path(sys.argv[1]).with_suffix('.stack.txt').open('w') as stack:
    faulthandler.dump_traceback_later(6,file=stack)
    try:
        code=run_interactive(sys.argv[2:])
    finally:
        faulthandler.cancel_dump_traceback_later()
        Path(sys.argv[1]).with_suffix('.diagnostic.json').write_text(
            json.dumps(diagnostic,default=lambda value:value.hex()))
after=termios.tcgetattr(0)
result={
    'code':code,'attributes':configured(after)==configured(before),
    'foreground':os.tcgetpgrp(0)==os.getpgrp()}
if configured(after)!=configured(before):
    result['before']=before
    result['after']=after
    result['diagnostic']=diagnostic
Path(sys.argv[1]).write_text(json.dumps(result,default=lambda value:value.hex()))
"""


def _diagnostics(path):
    values = []
    for suffix in (".guardian.txt", ".stack.txt", ".diagnostic.json", ".relay.json"):
        try:
            values.append(path.with_suffix(suffix).read_text()[:12000])
        except OSError:
            pass
    return "\n".join(values)


def _retire_fixture(process):
    children = []
    if process.poll() is None:
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            pass
        process.kill()
    for child in reversed(children):
        if not _gone(child):
            child.kill()
    process.wait(timeout=10)


@pytest.mark.parametrize("mode", [
    "normal", "caller-killed", "terminate",
    *(pytest.param("interrupt", id=f"interrupt-{trial}") for trial in range(5)),
])
@pytest.mark.parametrize("managed", [False, True])
def test_interactive_shell_groups_retire_but_managed_detachment_survives(tmp_path, mode, managed):
    import termios

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
    original_attributes = termios.tcgetattr(master)
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
        assert isinstance(first["tty"], str)
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
        elif mode == "terminate":
            process.terminate()
        else:
            (tmp_path / "gate").touch()
        _wait(lambda: drain() and process.poll() is not None, "interactive caller did not finish")
        if mode == "terminate":
            assert process.returncode == -signal.SIGTERM, (
                output.decode(errors="replace") + _diagnostics(receipt)
            )
            restored = termios.tcgetattr(master)
            restored[3] &= ~getattr(termios, "PENDIN", 0)
            original_attributes[3] &= ~getattr(termios, "PENDIN", 0)
            assert restored == original_attributes
        elif mode != "caller-killed":
            assert process.returncode == 0, output.decode(errors="replace") + _diagnostics(receipt)
            assert _read(receipt) == {
                "code": 130 if mode == "interrupt" else 0,
                "attributes": True, "foreground": True,
            }
        _wait(lambda: all(_gone(pin) for pin in pins), "owned shell job group survived completion")
        assert "retire-enter" in receipt.with_suffix(".guardian.txt").read_text()
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
    except BaseException:
        print(output[-4096:].decode(errors="replace"), _diagnostics(receipt))
        raise
    finally:
        _retire_fixture(process)
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
        "import json,os,sys,termios;"
        "print('terminal-only '+json.dumps({"
        "'input_tty':os.isatty(0),'output_tty':os.isatty(1),"
        "'group':os.getpgrp(),'foreground':os.tcgetpgrp(0),"
        "'input_blocking':os.get_blocking(0),'attributes':termios.tcgetattr(0)"
        "},default=lambda value:value.hex()),file=sys.stderr,flush=True);"
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
        # Keep the terminal consumer alive through BSD's final TTY close.
        _wait(lambda: ready() and process.poll() is not None, "redirected caller did not exit")
        stdout, _ = process.communicate(timeout=10)
        assert process.returncode == 0, output.decode(errors="replace") + _diagnostics(receipt)
        assert stdout == b'{"ok":true}\n'
        assert _read(receipt) == {"code": 0, "attributes": True, "foreground": True}
    except BaseException:
        print(output[-4096:].decode(errors="replace"), _diagnostics(receipt))
        raise
    finally:
        _retire_fixture(process)
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
        received = 0
        while received < 65536:
            if not select.select([master], [], [], .01 if not received else 0)[0]:
                break
            try:
                data = os.read(master, 65536 - received)
            except OSError:
                break
            if not data:
                break
            output.extend(data)
            received += len(data)
        return True

    try:
        import time

        deadline = time.monotonic() + 10
        while process.poll() is None:
            assert time.monotonic() < deadline, "large terminal output did not settle"
            drain()
        while select.select([master], [], [], 0)[0]:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            output.extend(data)
        assert process.returncode == 0, (
            output[-4096:].decode(errors="replace") + _diagnostics(receipt)
        )
        assert output.count(b"x") == size
        assert output.endswith(b"last-output\r\n")
        assert _read(receipt) == {"code": 0, "attributes": True, "foreground": True}
    except BaseException:
        print(_diagnostics(receipt))
        raise
    finally:
        _retire_fixture(process)
        os.close(master)


@pytest.mark.parametrize("closed", range(1, 8))
def test_completion_with_closed_standard_streams_keeps_private_control_pipes(tmp_path, closed):
    receipt = tmp_path / "result.json"
    ready = tmp_path / "ready.json"
    gate = tmp_path / "gate"
    command = """
import json,os,sys,time
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid()}))
while not Path(sys.argv[2]).exists():
    time.sleep(.01)
raise SystemExit(7)
"""
    caller = """
import json,os,sys
from pathlib import Path
from agent_procutil.interactive import run_interactive
for fd in range(3):
    if int(sys.argv[2]) & (1<<fd):
        os.close(fd)
code=run_interactive([sys.executable,'-c',sys.argv[3],*sys.argv[4:]])
Path(sys.argv[1]).write_text(json.dumps({'code':code}))
"""
    process = subprocess.Popen(  # noqa: S603 - isolated native fixture
        [sys.executable, "-c", caller, str(receipt), str(closed), command, str(ready), str(gate)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=_environment(), start_new_session=True,
    )
    child = None
    try:
        state = _wait(lambda: _read(ready), "closed-stdio completion did not start")
        child = psutil.Process(state["pid"])
        assert process.poll() is None and not _gone(child), "private lifetime was closed prematurely"
        gate.touch()
        assert process.wait(timeout=10) == 0
        assert _read(receipt) == {"code": 7}
        assert _gone(child)
    finally:
        _retire_fixture(process)
        if child is not None and not _gone(child):
            child.kill()
