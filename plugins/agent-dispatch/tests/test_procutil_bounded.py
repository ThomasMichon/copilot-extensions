"""run_background_capture's bounded mode (max_output): an untrusted command that
floods its pipes is stopped and reported, never buffered without limit."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_dispatch import procutil


def test_output_within_the_limit_is_returned_whole():
    done = procutil.run_background_capture(
        [sys.executable, "-c", "import sys; sys.stdout.write('a' * 5000); sys.stderr.write('b' * 10)"],
        timeout=20, max_output=10_000)
    assert done is not None and (len(done.stdout), done.stderr, done.returncode) == (5000, "b" * 10, 0)


def test_a_flood_is_stopped_and_raised_within_its_timeout():
    started = time.monotonic()
    with pytest.raises(procutil.OutputLimitExceeded):
        procutil.run_background_capture(
            [sys.executable, "-c", "import sys\nwhile True: sys.stderr.write('x' * 65536)"],
            timeout=20, max_output=100_000)
    assert time.monotonic() - started < 15


def test_a_bounded_capture_that_times_out_returns_none():
    assert procutil.run_background_capture(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1, max_output=1000) is None


def test_stdin_request_is_fully_delivered_while_both_output_pipes_drain():
    payload = "original request\n" * 10000
    done = procutil.run_background_capture(
        [sys.executable, "-c",
         "import sys; sys.stderr.write('e'*100000); data=sys.stdin.read(); sys.stdout.write(data)"],
        timeout=20, input_text=payload,
    )
    assert done is not None and done.returncode == 0
    assert done.stdout == payload and done.stderr == "e" * 100000


def test_a_child_not_reading_stdin_stays_inside_the_original_timeout():
    started = time.monotonic()
    done = procutil.run_background_capture(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=1, input_text="x" * 100000,
    )
    assert done is None and time.monotonic() - started < 15


@pytest.fixture
def windows_shaped_capture(monkeypatch: pytest.MonkeyPatch) -> list[threading.Thread]:
    """Keep the Windows pipe/deadline algorithm, replacing only native Job syscalls."""
    if sys.platform != "linux":
        pytest.skip("Windows-shaped pipe qualification uses Linux unreaped group custody")
    from agent_procutil import _windows_caller_owned, owned_process
    from agent_procutil._caller_owned import _confirm_group

    workers: list[threading.Thread] = []

    class Process:
        def __init__(self, process: subprocess.Popen[bytes]) -> None:
            self.process = process

        def __getattr__(self, name: str) -> Any:
            return getattr(self.process, name)

        def poll(self) -> int | None:
            status = owned_process._observe_unreaped(self.process)
            return None if status is None else status.si_status

        def retire_job(self, job: Any, timeout: float) -> None:
            assert timeout > 0
            owned_process._retire_posix_group(self.process)
            _confirm_group(self.process)
            self.process.wait(timeout=timeout)
            job.handle = 0

        def close(self) -> None:
            assert self.process.returncode is not None

    def spawn(argv: list[str], **kwargs: Any) -> tuple[Process, Any]:
        assert kwargs.pop("require_job") is True
        assert kwargs.pop("allow_breakaway") is False
        assert kwargs.pop("creationflags") == 0x08000000
        assert kwargs["text"] is False
        assert kwargs.pop("budget").deadline > time.monotonic()
        assert kwargs.pop("command_deadline") > time.monotonic()
        assert kwargs.pop("on_settled") is None
        process = subprocess.Popen(  # noqa: S603 -- test supplies only Python child scripts
            argv, start_new_session=True, **kwargs,
        )
        return Process(process), SimpleNamespace(handle=1)

    def start_thread(*args: Any, **kwargs: Any) -> threading.Thread:
        worker = threading.Thread(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(procutil, "sys", SimpleNamespace(platform="win32", maxsize=sys.maxsize))
    monkeypatch.setattr(owned_process, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(owned_process, "no_window_kwargs", lambda: {"creationflags": 0x08000000})
    monkeypatch.setattr(owned_process, "threading", SimpleNamespace(
        Thread=start_thread, Event=threading.Event, Lock=threading.Lock,
    ))
    monkeypatch.setattr(_windows_caller_owned, "spawn", spawn)
    return workers


@pytest.mark.parametrize("child_code", [
    "import time; time.sleep(30)",
    "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])",
])
def test_windows_shaped_stdin_deadline_settles_blocked_writer_and_readers(
    windows_shaped_capture: list[threading.Thread], child_code: str,
) -> None:
    started = time.monotonic()
    done = procutil.run_background_capture(
        [sys.executable, "-c", child_code],
        timeout=2, input_text="x" * 1_000_000,
    )
    assert done is None and time.monotonic() - started < 3
    assert len(windows_shaped_capture) == 3
    assert all(not worker.is_alive() for worker in windows_shaped_capture)


def test_windows_shaped_stdin_drains_both_outputs_and_preserves_launch_context(
    windows_shaped_capture: list[threading.Thread], tmp_path: Path,
) -> None:
    payload = "original request \N{SNOWMAN}\n" * 10000
    done = procutil.run_background_capture(
        [sys.executable, "-c",
         "import os,sys; assert os.getcwd()==os.environ['CAPTURE_CWD']; "
         "sys.stdout.write('o'*100000); sys.stderr.write('e'*100000); "
         "sys.stdout.write(sys.stdin.read())"],
        timeout=20, input_text=payload, cwd=tmp_path,
        env={**os.environ, "CAPTURE_CWD": str(tmp_path), "PYTHONIOENCODING": "utf-8"},
    )
    assert done is not None and done.returncode == 0
    assert done.stdout == "o" * 100000 + payload and done.stderr == "e" * 100000
    assert len(windows_shaped_capture) == 3
    assert all(not worker.is_alive() for worker in windows_shaped_capture)


def test_windows_input_cleanup_failure_keeps_exact_custody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = object()
    failure = procutil.OwnedCleanupError(None, job)

    def run_owned(*args: Any, **kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(procutil, "sys", SimpleNamespace(platform="win32", maxsize=sys.maxsize))
    monkeypatch.setattr(procutil, "_run_owned", run_owned)
    with pytest.raises(procutil.OwnedCleanupError) as caught:
        procutil.run_background_capture(["child"], timeout=1, input_text="request")
    assert caught.value is failure and caught.value.job is job


def test_input_with_output_limit_is_refused_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_spawn(*args: Any, **kwargs: Any) -> None:
        pytest.fail("incompatible input/output bounds must refuse before spawning")

    monkeypatch.setattr(procutil.subprocess, "Popen", unexpected_spawn)
    monkeypatch.setattr(procutil, "_run_owned", unexpected_spawn)
    with pytest.raises(ValueError, match="does not support stdin"):
        procutil.run_background_capture(["child"], timeout=1, input_text="", max_output=100)
