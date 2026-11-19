"""Finite EOF compatibility and fixed post-exit settlement deadlines."""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import os
import selectors
import subprocess
import sys
import time
from types import SimpleNamespace, TracebackType

import pytest

from agent_procutil import owned_budget, owned_process
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
