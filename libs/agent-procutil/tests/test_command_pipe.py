"""Nonblocking command-pipe backpressure retains the original finite deadline."""

import errno
from types import SimpleNamespace

import pytest

from agent_procutil import command_pipe


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_crt_full_pipe_is_retryable_only_on_windows(
    monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    writes: list[bytes] = []
    sleeps: list[float] = []

    def write(fd: int, data: memoryview) -> int:
        writes.append(bytes(data))
        if len(writes) == 1:
            raise OSError(errno.ENOSPC, "fixture full pipe")
        return len(data)

    monkeypatch.setattr(command_pipe, "os", SimpleNamespace(name=platform, write=write))
    monkeypatch.setattr("agent_procutil.command_pipe.time.sleep", sleeps.append)
    pipe = command_pipe.CommandPipe()
    pipe._stdin = SimpleNamespace(fileno=lambda: 42)
    if platform == "nt":
        pipe.write(b"frame\n")
        assert writes == [b"frame\n", b"frame\n"] and len(sleeps) == 1
    else:
        with pytest.raises(OSError) as caught:
            pipe.write(b"frame\n")
        assert caught.value.errno == errno.ENOSPC
        assert len(writes) == 1 and not sleeps


def test_crt_full_pipe_still_obeys_original_write_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    writes: list[bytes] = []

    def write(fd: int, data: memoryview) -> int:
        writes.append(bytes(data))
        raise OSError(errno.ENOSPC, "fixture full pipe")

    monkeypatch.setattr(command_pipe, "os", SimpleNamespace(name="nt", write=write))
    monkeypatch.setattr(command_pipe, "time", SimpleNamespace(
        monotonic=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    ))
    pipe = command_pipe.CommandPipe()
    pipe._stdin = SimpleNamespace(fileno=lambda: 42)
    with pytest.raises(TimeoutError):
        pipe.write(b"frame\n")
    assert 1 <= now[0] < 1.006 and writes
