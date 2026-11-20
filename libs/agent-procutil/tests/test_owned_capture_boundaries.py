"""Finite EOF compatibility and fixed post-exit settlement deadlines."""

from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import nullcontext
from collections.abc import Callable
import io
import os
import selectors
import subprocess
import sys
import threading
import time
from types import SimpleNamespace, TracebackType

import pytest

from agent_procutil import _caller_owned, owned_budget, owned_process
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, UnreapedChild


native = pytest.mark.skipif(
    os.name != "nt" and sys.platform not in {"linux", "darwin"},
    reason="caller-owned execution requires Windows, Linux or macOS",
)


def _finite_capture(
    program: str, *, exclusive: bool, timeout: float,
    budget: OwnedExecutionBudget | None = None,
) -> subprocess.CompletedProcess[object]:
    argv = [sys.executable, "-I", "-c", program]
    if exclusive:
        return owned_process._run_owned(
            argv, timeout=timeout, capture_limits=(4096, 4096),
            confirm_posix_group=True, budget=budget,
        )
    return owned_process.run_caller_owned(
        argv, timeout=timeout, max_stdout_bytes=4096, max_stderr_bytes=4096, budget=budget,
    )


@native
@pytest.mark.parametrize("exclusive", [False, True])
@pytest.mark.parametrize("aggregate", [False, True])
def test_finite_capture_keeps_descendant_output_after_root_exit(
    exclusive: bool, aggregate: bool,
) -> None:
    child = (
        "import os,time;time.sleep(.25);"
        "os.write(1,b'late-output\\r\\n');os.write(2,b'late-error\\r\\n')"
    )
    program = (
        "import os,subprocess,sys;os.write(1,b'root\\r\\n');"
        f"subprocess.Popen([sys.executable,'-I','-c',{child!r}],"
        "stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr,"
        "creationflags=(0x08000000 if sys.platform=='win32' else 0));sys.exit(7)"
    )
    result = _finite_capture(
        program, exclusive=exclusive, timeout=5,
        budget=OwnedExecutionBudget(time.monotonic() + 6, 2) if aggregate else None,
    )
    assert result.returncode == 7
    assert result.stdout == "root\nlate-output\n"
    assert result.stderr == "late-error\n"


@native
@pytest.mark.parametrize("exclusive", [False, True])
def test_finite_capture_still_times_out_when_descendant_keeps_pipe_open(
    exclusive: bool,
) -> None:
    program = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(25)'],"
        "stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr,"
        "creationflags=(0x08000000 if sys.platform=='win32' else 0));sys.exit(7)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _finite_capture(program, exclusive=exclusive, timeout=.5)


@dataclass
class _Clock:
    now: float = .9

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class _Pipe:
    def fileno(self) -> int:
        return 17


@dataclass
class _Selector:
    clock: _Clock
    after_exit: float
    mapping: dict[int, selectors.SelectorKey] = field(default_factory=dict)

    def __enter__(self) -> _Selector:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def register(self, stream: _Pipe, events: int, data: int) -> selectors.SelectorKey:
        key = selectors.SelectorKey(stream, stream.fileno(), events, data)
        self.mapping[key.fd] = key
        return key

    def unregister(self, stream: _Pipe) -> selectors.SelectorKey:
        return self.mapping.pop(stream.fileno())

    def get_map(self) -> dict[int, selectors.SelectorKey]:
        return self.mapping

    def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        assert timeout == 0
        self.clock.now = self.after_exit
        return [(key, selectors.EVENT_READ) for key in self.mapping.values()]


@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("expired", [False, True])
def test_posix_post_exit_drain_uses_original_cleanup_deadline(
    monkeypatch: pytest.MonkeyPatch, inherited: bool, expired: bool,
) -> None:
    clock = _Clock()
    after_exit = 2.1 if expired else 1.1
    selector = _Selector(clock, after_exit)
    process = SimpleNamespace(pid=123, returncode=None, stdin=None, stdout=_Pipe(), stderr=None)
    retired: list[bool] = []
    chunks = iter((b"evidence\r\n", b""))

    def exited(root: UnreapedChild) -> object:
        assert root is process and root.returncode is None
        return object()

    def retire() -> None:
        assert process.returncode is None
        retired.append(True)
        clock.now = after_exit

    def read(fd: int, size: int) -> bytes:
        assert fd == 17
        return next(chunks)

    def set_blocking(fd: int, blocking: bool) -> None:
        assert fd == 17 and not blocking

    monkeypatch.setattr(owned_process, "time", clock)
    monkeypatch.setattr(owned_process, "os", SimpleNamespace(read=read, set_blocking=set_blocking))
    monkeypatch.setattr(owned_process, "_observe_unreaped", exited)
    monkeypatch.setattr(owned_process, "selectors", SimpleNamespace(
        DefaultSelector=lambda: selector,
        EVENT_READ=selectors.EVENT_READ, EVENT_WRITE=selectors.EVENT_WRITE,
    ))

    def communicate() -> tuple[str | bytes | None, ...]:
        return owned_process._communicate_unreaped(
            process, input_text=None, deadline=1, timeout=None, argv=["fixture"],
            capture_limits=(64, 64), on_root_exit=None if inherited else retire,
            cleanup_deadline=2,
            handoff_on_root_exit=inherited,
        )

    if expired:
        with pytest.raises(TimeoutError, match="settlement deadline expired"):
            communicate()
    else:
        assert communicate() == ("evidence\n", None)
    assert retired == ([] if inherited else [True])
    assert process.returncode is None


@dataclass
class _WindowsProcess:
    clock: _Clock
    after_exit: float
    stdout: io.BytesIO = field(default_factory=lambda: io.BytesIO(b"evidence\r\n"))
    stderr: io.BytesIO = field(default_factory=lambda: io.BytesIO(b"diagnostic\r\n"))
    stdin: None = None
    pid: int = 123
    returncode: int = 7
    retired: list[float] = field(default_factory=list)

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float) -> int:
        assert timeout > 0
        return self.returncode

    def retire_job(self, job: object, timeout: float) -> None:
        assert timeout > 0
        self.retired.append(timeout)
        self.clock.now = self.after_exit


@pytest.mark.parametrize("expired", [False, True])
def test_windows_post_exit_settlement_uses_original_cleanup_deadline(
    monkeypatch: pytest.MonkeyPatch, expired: bool,
) -> None:
    clock = _Clock()
    process = _WindowsProcess(clock, 2.1 if expired else 1.1)
    monkeypatch.setattr(owned_process, "time", clock)
    monkeypatch.setattr(owned_budget, "time", clock)

    def communicate() -> tuple[str | bytes | None, ...]:
        return owned_process._communicate_windows_bounded(
            process, object(), input_text=None, deadline=1, timeout=None, argv=["fixture"],
            capture_limits=(64, 64), budget=OwnedExecutionBudget(2, 1),
        )

    try:
        if expired:
            with pytest.raises(OwnedCleanupError) as caught:
                communicate()
            assert caught.value.process is process
            assert caught.value.diagnostics.job_settlement == "confirmed"
        else:
            assert communicate() == ("evidence\n", "diagnostic\n")
        assert process.retired and all(0 < seconds <= 1.1 for seconds in process.retired)
    finally:
        process.stdout.close()
        process.stderr.close()


def test_borrowed_scope_waits_for_bounded_pipe_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()

    class BorrowedSelector(_Selector):
        def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
            assert timeout is not None and timeout > 0
            clock.now = 1.1
            return []

    selector = BorrowedSelector(clock, 1.1)
    process = SimpleNamespace(pid=123, returncode=None, stdin=None, stdout=_Pipe(), stderr=None)

    def forbidden(root: UnreapedChild) -> object:
        pytest.fail("borrowed output was handed off before EOF")

    monkeypatch.setattr(owned_process, "time", clock)
    monkeypatch.setattr(owned_process, "os", SimpleNamespace(set_blocking=lambda fd, value: None))
    monkeypatch.setattr(owned_process, "_observe_unreaped", forbidden)
    monkeypatch.setattr(owned_process, "selectors", SimpleNamespace(
        DefaultSelector=lambda: selector,
        EVENT_READ=selectors.EVENT_READ, EVENT_WRITE=selectors.EVENT_WRITE,
    ))
    with pytest.raises(subprocess.TimeoutExpired):
        owned_process._communicate_unreaped(
            process, input_text=None, deadline=1, timeout=None,
            argv=["borrowed"], capture_limits=(64, 64), cleanup_deadline=2,
        )


@dataclass
class _FailedRetirementProcess:
    stdout: io.BytesIO
    stdin: None = None
    stderr: None = None
    pid: int = 123
    returncode: None = None
    attempted: threading.Event = field(default_factory=threading.Event)
    execution_waits: int = 0

    def retire_job(self, job: object, timeout: float) -> None:
        self.attempted.set()
        raise OSError("fixture retirement is unconfirmed")

    def poll(self) -> None:
        assert self.attempted.wait(2)
        return None

    def wait(self, *, timeout: float) -> int:
        if timeout < .1:
            self.execution_waits += 1
            if self.execution_waits >= 2:
                raise AssertionError("execution waited after a recorded pipe failure")
        raise subprocess.TimeoutExpired(["fixture"], timeout)


@pytest.mark.parametrize("io_failure", [False, True])
def test_pipe_failure_stops_execution_wait_even_when_retirement_fails(io_failure: bool) -> None:
    class BrokenPipe(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise OSError("fixture stream failed")

    stream = BrokenPipe() if io_failure else io.BytesIO(b"x" * 33)
    process = _FailedRetirementProcess(stream)
    job = object()
    try:
        with pytest.raises(OwnedCleanupError) as caught:
            owned_process._communicate_windows_bounded(
                process, job, input_text=None, deadline=None, timeout=None,
                argv=["fixture"], capture_limits=(32, 32),
            )
        failure = caught.value
        assert failure.process is process and failure.job is job
        assert failure.diagnostics.job_settlement == "unconfirmed"
        assert isinstance(
            failure.operation_error, OSError if io_failure else owned_process.OutputLimitExceeded,
        )
        assert process.execution_waits == 0
    finally:
        stream.close()


@dataclass
class _PosixProcess:
    stdin: None = None
    stdout: None = None
    stderr: None = None
    pid: int = 123
    returncode: int | None = None
    waits: int = 0

    def wait(self, *, timeout: float) -> int:
        self.waits += 1
        self.returncode = 7
        return 7


def test_confirmed_root_exit_reuses_its_two_scan_group_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _PosixProcess()
    scans: list[bool] = []
    signals: list[bool] = []
    notifications: list[bool] = []

    def scan(path: str) -> nullcontext[list[object]]:
        assert path == "/proc" and process.returncode is None
        scans.append(True)
        return nullcontext([])

    def retire(root: UnreapedChild, *, inherit_process_group: bool = False) -> None:
        assert root is process and root.returncode is None and not inherit_process_group
        signals.append(True)

    def communicate(
        root: object, *, on_root_exit: Callable[[], None] | None, **options: object,
    ) -> tuple[str, str]:
        assert root is process and on_root_exit is not None
        on_root_exit()
        return "root\n", ""

    def spawn(argv: list[str], **options: object) -> tuple[_PosixProcess, None]:
        return process, None

    monkeypatch.setattr(owned_process, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(owned_process, "_require_posix_execution_support", lambda: None)
    monkeypatch.setattr(owned_process, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(owned_process, "_communicate_unreaped", communicate)
    monkeypatch.setattr(owned_process, "_retire_posix_group", retire)
    monkeypatch.setattr(_caller_owned, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(_caller_owned, "os", SimpleNamespace(scandir=scan))
    monkeypatch.setattr(_caller_owned, "_observe_unreaped", lambda root: object())
    result = owned_process._run_owned(
        ["fixture"], timeout=None, capture_limits=(64, 64), confirm_posix_group=True,
        on_settled=lambda: notifications.append(True),
    )
    assert result.returncode == 7 and result.stdout == "root\n"
    assert scans == [True, True] and signals == [True] and process.waits == 1
    assert notifications == [True]


@pytest.mark.parametrize(
    ("timeout", "deadline", "observe", "expected_wait"),
    [(4.0, 5.0, False, 4.1), (None, 5.0, False, 4.1),
     (None, None, False, None), (None, 5.0, True, .05), (None, None, True, .05)],
)
def test_posix_selector_polls_only_when_root_observation_is_enabled(
    monkeypatch: pytest.MonkeyPatch, timeout: float | None, deadline: float | None,
    observe: bool, expected_wait: float | None,
) -> None:
    clock = _Clock()
    waits: list[float | None] = []
    observations: list[bool] = []

    class Selector(_Selector):
        def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
            waits.append(timeout)
            return [(key, selectors.EVENT_READ) for key in self.mapping.values()]

    selector = Selector(clock, 0)
    process = SimpleNamespace(pid=123, returncode=None, stdin=None, stdout=_Pipe(), stderr=None)

    def exited(root: UnreapedChild) -> object | None:
        observations.append(True)
        return None if observe and len(observations) == 1 else object()

    monkeypatch.setattr(owned_process, "time", clock)
    monkeypatch.setattr(owned_process, "os", SimpleNamespace(
        read=lambda fd, size: b"", set_blocking=lambda fd, value: None,
    ))
    monkeypatch.setattr(owned_process, "_observe_unreaped", exited)
    monkeypatch.setattr(owned_process, "selectors", SimpleNamespace(
        DefaultSelector=lambda: selector,
        EVENT_READ=selectors.EVENT_READ, EVENT_WRITE=selectors.EVENT_WRITE,
    ))
    assert owned_process._communicate_unreaped(
        process, input_text=None, deadline=deadline, timeout=timeout, argv=["fixture"],
        capture_limits=(64, 64), handoff_on_root_exit=observe,
    ) == ("", None)
    assert len(waits) == 1
    assert waits[0] == (pytest.approx(expected_wait) if expected_wait is not None else None)
    assert len(observations) == (2 if observe else 1)


@pytest.mark.parametrize("failure_type", [OSError, ValueError])
def test_windows_post_root_read_errors_cannot_become_truncated_success(
    failure_type: type[OSError] | type[ValueError],
) -> None:
    ready, retired = threading.Event(), threading.Event()
    failure = failure_type("post-exit capture failed")

    class Stream(io.BytesIO):
        emitted = False

        def read(self, size: int | None = -1) -> bytes:
            if not self.emitted:
                self.emitted = True
                ready.set()
                return b"prefix"
            assert retired.wait(2)
            raise failure

    class Process(_WindowsProcess):
        def poll(self) -> int:
            assert ready.wait(2)
            return self.returncode

        def retire_job(self, job: object, timeout: float) -> None:
            self.retired.append(timeout)
            retired.set()

    process = Process(_Clock(), 0, stdout=Stream())
    try:
        with pytest.raises(failure_type) as caught:
            owned_process._communicate_windows_bounded(
                process, object(), input_text=None, deadline=None, timeout=None,
                argv=["fixture"], capture_limits=(64, 64),
            )
        assert caught.value is failure
        assert len(process.retired) == 1
    finally:
        retired.set()
        process.stdout.close()
        process.stderr.close()
