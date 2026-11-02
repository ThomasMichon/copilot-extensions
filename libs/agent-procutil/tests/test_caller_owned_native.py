"""Fresh installed consoles and birth-pinned cleanup before observer retirement."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
import ctypes
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import venv
from types import SimpleNamespace

import psutil
import pytest

import agent_procutil as pu
import caller_owned_fixture as fixture


LIBRARY = Path(__file__).resolve().parents[1]


def _command(argv, *, cwd=None):
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=20,
        **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def installed_console(tmp_path_factory):
    uv = shutil.which("uv")
    if not uv:
        pytest.fail("native artifact qualification requires the existing uv tool")
    root = tmp_path_factory.mktemp("caller-wheel")
    source = root / "source"
    shutil.copytree(
        LIBRARY, source,
        ignore=shutil.ignore_patterns("tests", "__pycache__", "*.egg-info", ".pytest_cache"),
    )
    wheels = root / "wheels"
    _command([
        uv, "build", "--offline", "--no-build-isolation", "--python", sys.executable,
        "--wheel", "--out-dir", str(wheels), str(source),
    ])
    wheel, = wheels.glob("*.whl")
    slot = root / "env"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _command([
        uv, "pip", "install", "--offline", "--no-index", "--no-deps",
        "--python", str(python), str(wheel),
    ])
    script = root / "fixture.py"
    script.write_bytes(Path(fixture.__file__).read_bytes())
    shutil.rmtree(source)
    assert not source.exists()
    yield python, script, slot


class PinnedProcess:
    def __init__(self, identity):
        self.pid = identity["pid"]
        self.handle = None
        self.fd = None
        if os.name == "nt":
            self.kernel = fixture.windows_kernel()
            handle = self.kernel.OpenProcess(0x1000 | 0x100000 | 0x0001, False, self.pid)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if fixture.handle_birth(self.kernel, handle) != identity["birth"]:
                    raise AssertionError("fixture process birth changed")
            except BaseException:
                self.kernel.CloseHandle(handle)
                raise
            self.handle = handle
        else:
            fd = fixture.open_pidfd(self.pid)
            try:
                if fixture.identity(self.pid)["birth"] != identity["birth"]:
                    raise AssertionError("fixture process birth changed")
            except BaseException:
                os.close(fd)
                raise
            self.fd = fd

    def gone(self):
        if self.handle is not None:
            result = self.kernel.WaitForSingleObject(self.handle, 0)
            if result not in (0, 0x102):
                raise ctypes.WinError(ctypes.get_last_error())
            return result == 0
        return fixture.pidfd_exited(self.fd)

    def kill(self, signum=None):
        if self.gone():
            return
        if self.handle is not None:
            if not self.kernel.TerminateProcess(self.handle, 137):
                error = ctypes.get_last_error()
                if self.kernel.WaitForSingleObject(self.handle, 3000) != 0:
                    raise ctypes.WinError(error)
        else:
            fixture.signal_pidfd(self.fd, signal.SIGKILL if signum is None else signum)

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


@contextmanager
def _linux_observer():
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot inspect test subreaper")
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot establish test subreaper")
    baseline = {process.pid for process in psutil.Process().children(recursive=True)}
    try:
        yield
    finally:
        cleanup = []

        def restore():
            if libc.prctl(36, previous.value, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "cannot restore test subreaper")

        def settle():
            deadline = time.monotonic() + 3
            while cleanup and not all(pin.gone() for pin in cleanup):
                if time.monotonic() >= deadline:
                    raise AssertionError("test observer cleanup did not settle")
                time.sleep(0.01)
            for pin in cleanup:
                try:
                    os.waitpid(pin.pid, os.WNOHANG)
                except ChildProcessError:
                    pass

        try:
            for process in psutil.Process().children(recursive=True):
                if process.pid in baseline:
                    continue
                try:
                    pin = PinnedProcess(fixture.identity(process.pid))
                    if os.getpid() not in {parent.pid for parent in psutil.Process(pin.pid).parents()}:
                        pin.close()
                        raise AssertionError("observer discovered a foreign process")
                    cleanup.append(pin)
                except (ProcessLookupError, FileNotFoundError, psutil.NoSuchProcess):
                    continue
        finally:
            with ExitStack() as retirement:
                retirement.callback(restore)
                for pin in cleanup:
                    retirement.callback(pin.close)
                retirement.callback(settle)
                for pin in cleanup:
                    retirement.callback(pin.kill)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _wait(predicate, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError(message)


@pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="native Windows Job or Linux pidfd/subreaper observer required",
)
@pytest.mark.parametrize("mode", [
    "completed", "timeout", "blocked-input", "stdout", "stderr", "interrupt", "kill", "term",
    "fork-keeper",
])
def test_installed_console_faults_settle_before_observer_cleanup(
    tmp_path, installed_console, mode,
):
    if mode == "term" and os.name == "nt":
        pytest.skip("native SIGTERM case; Windows actual-console termination uses a pinned handle")
    if mode == "fork-keeper" and os.name == "nt":
        pytest.skip("fork inheritance is a native Linux contract")
    if os.name != "nt":
        descriptor = fixture.open_pidfd(os.getpid())
        try:
            fixture.signal_pidfd(descriptor, 0)
        finally:
            os.close(descriptor)
    python, script, slot = installed_console
    observer = nullcontext()
    windows = nullcontext()
    if os.name == "nt":
        from test_spawn_daemon_windows_integration import _WindowObserver, _configure_win32

        windows = _WindowObserver(*_configure_win32())
    else:
        observer = _linux_observer()
    with observer, windows as window_observer:
        for cycle in range(2):
            state = tmp_path / str(cycle)
            state.mkdir()
            pins = {}
            keeper = None
            process = job = None
            try:
                process, job = pu.spawn_sync_in_kill_on_close_job(
                    [str(python), "-I", str(script), "caller", str(state), mode],
                    require_job=True, allow_breakaway=False,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    **pu.no_window_kwargs(),
                    **({"start_new_session": True} if os.name != "nt" else {}),
                )
                pins[process.pid] = PinnedProcess(fixture.identity(process.pid))
                def ready():
                    values = [
                        _read(state / f"{role}.json") for role in ("caller", "target", "leaf")
                    ]
                    return values if all(values) else None

                observations = _wait(ready, 4, "installed fixture did not expose all live roles")
                caller, target, leaf = observations
                assert Path(caller["package"]).resolve().is_relative_to(slot.resolve())
                assert Path(caller["prefix"]).resolve() == slot.resolve()
                identities = [caller["self"], target["self"], target["parent"],
                              leaf["self"], leaf["parent"]]
                for identity in identities:
                    if identity["pid"] not in pins:
                        pins[identity["pid"]] = PinnedProcess(identity)
                if mode == "fork-keeper":
                    unrelated = _wait(
                        lambda: _read(state / "unrelated.json"), 3, "unrelated fork child missing",
                    )
                    keeper = PinnedProcess(unrelated)
                assert not any(pin.gone() for pin in pins.values())
                if os.name != "nt":
                    assert target["parent"]["pid"] == target["group"] == target["session"]
                started = time.monotonic()
                if mode in ("kill", "term", "fork-keeper"):
                    pins[caller["self"]["pid"]].kill(
                        signal.SIGTERM if mode == "term" else None,
                    )
                elif mode == "interrupt" and os.name != "nt":
                    pins[caller["self"]["pid"]].kill(signal.SIGINT)
                else:
                    (state / "gate").write_text("go", encoding="ascii")
                if mode not in ("kill", "term", "fork-keeper"):
                    outcome = _wait(
                        lambda: _read(state / "result.json"), 7, "caller did not finish its scope",
                    )
                    expected = (
                        "output-limit" if mode in ("stdout", "stderr")
                        else "timeout" if mode in ("timeout", "blocked-input") else mode
                    )
                    assert outcome["kind"] == expected
                    assert outcome["settled_before_result"], (
                        f"API returned before descendant exit: {outcome}; "
                        f"gone_now={[(pid, pins[pid].gone()) for pid in outcome.get('unsettled', [])]}"
                    )
                    if expected == "output-limit":
                        assert outcome["stream"] == mode
                        assert outcome["limit"] == 32
                _wait(
                    lambda: all(pin.gone() for pin in pins.values()),
                    3, "owned role survived before observer cleanup",
                )
                if mode in (
                    "kill", "term", "interrupt", "stdout", "stderr", "completed", "fork-keeper",
                ):
                    assert time.monotonic() - started < 3
                if keeper is not None:
                    assert not keeper.gone(), "the unrelated fork child must remain alive"
                process.wait(timeout=3)
                stdout, stderr = process.communicate(timeout=3)
                assert not stderr, stderr.decode("utf-8", errors="replace")
                if mode not in ("kill", "term", "fork-keeper"):
                    assert process.returncode == 0, stdout
            finally:
                with ExitStack() as cleanup:
                    if keeper is not None:
                        cleanup.callback(keeper.close)
                        cleanup.callback(keeper.kill)
                    for pin in pins.values():
                        cleanup.callback(pin.close)
                    if process is not None:
                        for stream in (process.stdout, process.stderr):
                            if stream is not None:
                                cleanup.callback(stream.close)
                        cleanup.callback(process.wait, timeout=3)
                    if job is not None:
                        cleanup.callback(job.terminate_and_wait, 3)
                    if process is not None:
                        cleanup.callback(lambda: process.kill() if process.poll() is None else None)
                    for pin in pins.values():
                        cleanup.callback(pin.kill)
        if os.name == "nt":
            assert not window_observer.surfaced
            assert not window_observer.focus_changes


@pytest.mark.skipif(os.name != "nt", reason="atomic Windows Job assignment")
def test_installed_caller_death_before_resume_keeps_atomic_custody(tmp_path, installed_console):
    python, script, slot = installed_console
    for cycle in range(2):
        state = tmp_path / str(cycle)
        state.mkdir()
        process = subprocess.Popen(
            [str(python), "-I", str(script), "caller", str(state), "suspended"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **pu.no_window_kwargs(),
        )
        pins = []
        try:
            caller = _wait(lambda: _read(state / "caller.json"), 4, "caller missing")
            assert Path(caller["package"]).resolve().is_relative_to(slot.resolve())
            pins.append(PinnedProcess(caller["self"]))
            suspended = _wait(
                lambda: _read(state / "suspended.json"), 4, "atomic suspended target missing",
            )
            pins.append(PinnedProcess(suspended))
            assert not any(pin.gone() for pin in pins)
            assert not (state / "target.json").exists()
            pins[0].kill()
            _wait(lambda: all(pin.gone() for pin in pins), 3, "suspended target outlived caller")
            assert not (state / "target.json").exists()
            process.communicate(timeout=3)
        finally:
            with ExitStack() as cleanup:
                for pin in pins:
                    cleanup.callback(pin.close)
                for stream in (process.stdout, process.stderr):
                    cleanup.callback(stream.close)
                cleanup.callback(process.wait, timeout=3)
                cleanup.callback(lambda: process.kill() if process.poll() is None else None)
                for pin in pins:
                    cleanup.callback(pin.kill)


@pytest.mark.skipif(os.name != "nt", reason="native atomic Windows spawn failures")
@pytest.mark.parametrize("stage", ["pipe-open", "job-attribute", "resume", "create-interrupt"])
def test_atomic_spawn_failures_do_not_release_live_processes(tmp_path, monkeypatch, stage):
    from agent_procutil import _windows_caller_owned as native
    from agent_procutil.owned_process import run_caller_owned

    kernel = native._api()
    marker = tmp_path / "must-not-run"
    pins = []
    descriptors = []
    initialize = native._Process.__init__

    def record(process, argv, info, streams):
        initialize(process, argv, info, streams)
        pins.append(PinnedProcess(fixture.identity(info.dwProcessId)))

    monkeypatch.setattr(native._Process, "__init__", record)
    if stage == "pipe-open":
        pipe = os.pipe

        def record_pipe():
            pair = pipe()
            descriptors.extend(pair)
            return pair

        def refuse(*args, **kwargs):
            raise OSError("synthetic pipe wrapping failure")

        monkeypatch.setattr(os, "pipe", record_pipe)
        monkeypatch.setattr(os, "fdopen", refuse)
    elif stage == "job-attribute":
        update = kernel.UpdateProcThreadAttribute

        def refuse(*args):
            if args[2] == 0x2000D:
                raise OSError("synthetic Job-list refusal")
            return update(*args)

        monkeypatch.setattr(kernel, "UpdateProcThreadAttribute", refuse)
    elif stage == "resume":
        def refuse(*args):
            raise OSError("synthetic resume failure")

        monkeypatch.setattr(kernel, "ResumeThread", refuse)
    else:
        create = kernel.CreateProcessW

        def interrupt(*args):
            assert create(*args)
            raise KeyboardInterrupt()

        monkeypatch.setattr(kernel, "CreateProcessW", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt if stage == "create-interrupt" else OSError):
            run_caller_owned(
                [sys.executable, "-I", "-c", f"open({str(marker)!r},'w').write('bad')"],
                timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            )
        assert not marker.exists()
        assert all(pin.gone() for pin in pins), "spawn failure released a live native target"
        assert bool(pins) == (stage in ("resume", "create-interrupt"))
        if stage == "pipe-open":
            import msvcrt

            assert len(descriptors) == 2
            for fd in descriptors:
                with pytest.raises(OSError):
                    msvcrt.get_osfhandle(fd)
    finally:
        with ExitStack() as cleanup:
            for pin in pins:
                cleanup.callback(pin.close)
                cleanup.callback(pin.kill)


def test_installed_console_refuses_nul_before_target_effect(tmp_path, installed_console):
    python, _script, _slot = installed_console
    marker = tmp_path / "must-not-run"
    target = f"open({str(marker)!r},'w').write('bad')"
    code = (
        "import sys\n"
        "from agent_procutil.owned_process import run_caller_owned\n"
        "try:\n"
        f" run_caller_owned([sys.executable,'-I','-c',{target!r},'input\\x00suffix'],"
        "timeout=5,max_stdout_bytes=32,max_stderr_bytes=32)\n"
        "except ValueError:\n"
        " print('refused')\n"
        "else:\n"
        " raise AssertionError('NUL command was not refused')\n"
    )
    result = _command([str(python), "-I", "-c", code])
    assert result.stdout.strip() == "refused"
    assert not marker.exists()


@pytest.mark.parametrize("backend", ["windows", "linux"])
@pytest.mark.parametrize("identity_state", ["matching", "mismatch", "unreadable"])
def test_pinned_observer_refuses_foreign_identity_before_termination(
    monkeypatch, backend, identity_state,
):
    closed = []
    terminated = []

    def birth(*args):
        if identity_state == "unreadable":
            raise OSError("synthetic unreadable birth")
        return 100 if identity_state == "matching" else 200

    kernel = SimpleNamespace(
        OpenProcess=lambda *args: 7,
        CloseHandle=lambda handle: closed.append(handle),
        WaitForSingleObject=lambda *args: 0x102,
        TerminateProcess=lambda handle, code: terminated.append((handle, code)) or True,
    )
    monkeypatch.setattr(fixture, "windows_kernel", lambda: kernel)
    monkeypatch.setattr(fixture, "handle_birth", birth)
    monkeypatch.setattr(fixture, "identity", lambda pid: {"pid": pid, "birth": birth()})
    monkeypatch.setattr(fixture, "pidfd_exited", lambda fd: False)
    monkeypatch.setattr(fixture, "open_pidfd", lambda pid: 7)
    monkeypatch.setattr(fixture, "signal_pidfd", lambda fd, signum: terminated.append((fd, signum)))
    monkeypatch.setattr(sys.modules[__name__], "os", SimpleNamespace(
        name="nt" if backend == "windows" else "posix",
        pidfd_open=lambda pid: 7, close=lambda fd: closed.append(fd),
    ))
    monkeypatch.setattr(sys.modules[__name__], "signal", SimpleNamespace(
        SIGKILL=9, pidfd_send_signal=lambda fd, signum: terminated.append((fd, signum)),
    ))
    if identity_state != "matching":
        with pytest.raises(OSError if identity_state == "unreadable" else AssertionError):
            PinnedProcess({"pid": 41, "birth": 100})
        assert closed == [7]
        assert terminated == []
        return
    pin = PinnedProcess({"pid": 41, "birth": 100})
    try:
        assert not pin.gone()
        pin.kill()
        assert terminated == [(7, 137 if backend == "windows" else 9)]
        assert closed == []
    finally:
        pin.close()
    assert closed == [7]


@pytest.mark.skipif(os.name != "nt", reason="native Windows worker interruption cleanup")
@pytest.mark.parametrize("boundary", ["initialization", "constructor", "start"])
def test_worker_interruption_retires_job_with_traceback_retained(monkeypatch, boundary):
    from agent_procutil import _windows_caller_owned as native
    from agent_procutil import owned_process as ownership

    pins = []
    workers = []
    initialize = native._Process.__init__

    def record(process, argv, info, streams):
        initialize(process, argv, info, streams)
        pins.append(PinnedProcess(fixture.identity(info.dwProcessId)))

    def lock():
        if boundary == "initialization":
            raise KeyboardInterrupt()
        return threading.Lock()

    def thread(*args, **kwargs):
        if boundary == "constructor":
            raise KeyboardInterrupt()
        worker = threading.Thread(*args, **kwargs)
        workers.append(worker)
        if boundary == "start":
            start = worker.start

            def interrupt():
                start()
                raise KeyboardInterrupt()

            worker.start = interrupt
        return worker

    monkeypatch.setattr(native._Process, "__init__", record)
    monkeypatch.setattr(ownership, "threading", SimpleNamespace(
        Lock=lock, Event=threading.Event, Thread=thread,
    ))
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            ownership.run_caller_owned(
                [sys.executable, "-I", "-c", "import time;time.sleep(60)"],
                timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            )
        assert caught.value.__traceback__ is not None
        assert pins and all(pin.gone() for pin in pins)
        assert all(not worker.is_alive() for worker in workers)
    finally:
        with ExitStack() as cleanup:
            for pin in pins:
                cleanup.callback(pin.close)
                cleanup.callback(pin.kill)


@pytest.mark.skipif(os.name != "nt", reason="native Windows pre-ident startup interruption")
def test_pre_ident_startup_interruption_keeps_streams_until_worker_settles(monkeypatch):
    from agent_procutil import _windows_caller_owned as native
    from agent_procutil import owned_process as ownership

    entered = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    pins = []
    processes = []
    workers = []
    failures = []
    initialize = native._Process.__init__

    def record(process, argv, info, streams):
        initialize(process, argv, info, streams)
        processes.append(process)
        pins.append(PinnedProcess(fixture.identity(info.dwProcessId)))

    def make_thread(*args, **kwargs):
        worker = threading.Thread(*args, **kwargs)
        set_ident = worker._set_ident

        def paused():
            entered.set()
            release.wait(3)
            set_ident()

        worker._set_ident = paused
        workers.append(worker)
        return worker

    def unblock():
        try:
            assert entered.wait(3), "native thread did not enter bootstrap"
            _wait(lambda: pins and all(pin.gone() for pin in pins), 2, "Job did not retire")
            time.sleep(0.05)
            assert workers[0].ident is None
            assert not returned.is_set(), "interruption escaped before bootstrap settled"
            assert not processes[0].stdout.closed and not processes[0].stderr.closed
        except BaseException as error:
            failures.append(error)
        finally:
            release.set()

    releaser = threading.Thread(target=unblock, daemon=True)
    releaser.start()
    startup_name = (
        "_start_joinable_thread" if hasattr(threading, "_start_joinable_thread")
        else "_start_new_thread"
    )
    start = getattr(threading, startup_name)

    def interrupt(*args, **kwargs):
        start(*args, **kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr(threading, startup_name, interrupt)
    monkeypatch.setattr(native._Process, "__init__", record)
    monkeypatch.setattr(ownership, "threading", SimpleNamespace(
        Lock=threading.Lock, Event=threading.Event, Thread=make_thread,
    ))
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            ownership.run_caller_owned(
                [sys.executable, "-I", "-c", "import time;time.sleep(60)"],
                timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            )
        returned.set()
        releaser.join(timeout=3)
        assert not releaser.is_alive()
        assert not failures, failures
        assert caught.value.__traceback__ is not None
        assert pins and all(pin.gone() for pin in pins)
        assert all(not worker.is_alive() for worker in workers)
    finally:
        release.set()
        releaser.join(timeout=3)
        with ExitStack() as cleanup:
            for pin in pins:
                cleanup.callback(pin.close)
                cleanup.callback(pin.kill)
