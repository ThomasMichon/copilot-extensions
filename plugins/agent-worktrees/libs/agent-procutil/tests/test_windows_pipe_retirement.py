"""Available bytes drain before idle retirement; native query errors remain failures."""

import io
import queue
import threading
from types import SimpleNamespace

import pytest

from agent_procutil import streaming


@pytest.mark.parametrize("ending", ["idle", "eof", "query-error"])
def test_windows_idle_retirement_drains_available_bytes_without_hiding_query_errors(
    monkeypatch: pytest.MonkeyPatch, ending: str,
) -> None:
    output = streaming.SyncProcessOutput.__new__(streaming.SyncProcessOutput)
    output._archive_fds, output._best_effort = {}, False
    output._events = queue.Queue()
    output._lock = threading.Lock()
    output._last_output, output._has_output = 0.0, False
    content = b"\x00\xff\r\npartial"
    sizes: list[int | None] = []

    class Pipe(io.BytesIO):
        def read1(self, size: int | None = -1, /) -> bytes:
            sizes.append(size)
            return super().read1(size)

    pipe = Pipe(content + b"not-yet-available")
    remaining = iter([2, len(content) - 2])
    failure = OSError("pipe query unavailable")

    def available() -> int | None:
        count = next(remaining, None)
        if count is not None:
            return count
        if ending == "query-error":
            raise failure
        return 0 if ending == "idle" else None

    output._stop_at_idle = threading.Event()
    output._stop_at_idle.set()
    monkeypatch.setattr(streaming, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(streaming, "_windows_pipe_probe", lambda binary: available)
    output._read("stdout", pipe)
    events = []
    while not output._events.empty():
        events.append(output._events.get_nowait())
    assert sizes == [2, len(content) - 2]
    assert events[:2] == [("stdout", content[:2]), ("stdout", content[2:])]
    assert events[2:] == (
        [("stdout", failure), ("stdout", None)]
        if ending == "query-error" else [("stdout", None)]
    )


def test_retirement_reprobes_bytes_written_after_the_initial_idle_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = streaming.SyncProcessOutput.__new__(streaming.SyncProcessOutput)
    output._archive_fds, output._best_effort = {}, False
    output._events = queue.Queue()
    output._lock = threading.Lock()
    output._last_output, output._has_output = 0.0, False
    output._stop_at_idle = threading.Event()
    pipe = io.BytesIO()
    content = b"final-output"

    class Retirement:
        signaled = False

        def is_set(self) -> bool:
            return self.signaled

        def wait(self, timeout: float) -> bool:
            assert not self.signaled
            pipe.write(content)
            pipe.seek(0)
            self.signaled = True
            return True

    monkeypatch.setattr(output, "_stop_at_idle", Retirement())
    monkeypatch.setattr(streaming, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        streaming, "_windows_pipe_probe", lambda binary: lambda: len(pipe.getvalue()) - pipe.tell(),
    )
    output._read("stdout", pipe)
    assert output._events.get_nowait() == ("stdout", content)
    assert output._events.get_nowait() == ("stdout", None)
    assert output._events.empty()
