"""Actor-free finite-stream clock and activity decisions."""

from __future__ import annotations

import io
import queue
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_procutil import streaming


@dataclass
class _Clock:
    now: float = 0.0


class _Process(subprocess.Popen[Any]):
    """Actor-free Popen shape; no child is created."""

    def __init__(self, clock: _Clock) -> None:
        self.args = ["fixture"]
        self.text_mode = False
        self.clock = clock
        self.exit_at: float | None = None

    def poll(self) -> int | None:
        return 0 if self.exit_at is not None and self.clock.now >= self.exit_at else None


StreamFixture = tuple[streaming.SyncProcessOutput, _Clock]


@pytest.fixture
def stream(monkeypatch: pytest.MonkeyPatch) -> StreamFixture:
    clock = _Clock()
    monkeypatch.setattr(
        streaming,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.now,
            sleep=lambda _seconds: pytest.fail("fixture unexpectedly slept"),
        ),
    )
    output: Any = streaming.SyncProcessOutput.__new__(streaming.SyncProcessOutput)

    class Events(queue.Queue[tuple[str, bytes | BaseException | None]]):
        def get(
            self, block: bool = True, timeout: float | None = None,
        ) -> tuple[str, bytes | BaseException | None]:
            if timeout is not None:
                clock.now += timeout
            return super().get(block=False)

    output.process = _Process(clock)
    output._input_errors, output._startup_errors = [], []
    output._pending, output._relay_failed = None, False
    output._best_effort, output.presentation_incomplete = False, False
    output._events, output._relays = Events(), {}
    output._done, output._streams = set(), {"stdout": io.BytesIO()}
    output._output = {"stdout": bytearray(), "stderr": bytearray()}
    output._capture, output.on_output = True, None
    output._last_output, output._has_output = 0.0, False
    output._lock = threading.Lock()
    return output, clock


@pytest.mark.parametrize("active,expected", [(True, 1.0), (False, 0.25)])
def test_activity_renews_quiet_but_never_absolute_cap(
    stream: StreamFixture, active: bool, expected: float,
) -> None:
    output, clock = stream
    with pytest.raises(subprocess.TimeoutExpired):
        output.wait(0.25, deadline=1, activity_observer=lambda: active)
    assert clock.now == pytest.approx(expected)


def test_later_stall_expires_and_startup_grace_ends(stream: StreamFixture) -> None:
    output, clock = stream
    with pytest.raises(subprocess.TimeoutExpired):
        output.wait(0.25, startup_timeout=5, activity_observer=lambda: clock.now < 0.4)
    assert 0.5 <= clock.now < 0.7


@pytest.mark.parametrize("value", [1, None, "progress"])
def test_activity_requires_strict_bool(stream: StreamFixture, value: object) -> None:
    output, _ = stream
    with pytest.raises(TypeError, match="bool"):
        output.wait(1, activity_observer=cast(Callable[[], bool], lambda: value))
    assert output._relay_failed


def test_observation_error_retains_original_failure(stream: StreamFixture) -> None:
    output, _ = stream
    failure = RuntimeError("native query refused")

    def fail() -> bool:
        raise failure

    with pytest.raises(RuntimeError) as raised:
        output.wait(1, activity_observer=fail)
    assert raised.value is failure
    assert output._relay_failed


@pytest.mark.parametrize("observe", [False, True])
def test_output_bytes_and_default_quiet_behavior_are_unchanged(
    stream: StreamFixture, observe: bool,
) -> None:
    output, clock = stream
    output._events.put(("stdout", b"\x00\xff\r\npartial"))
    with pytest.raises(subprocess.TimeoutExpired):
        output.wait(0.25, activity_observer=(lambda: False) if observe else None)
    assert output.output() == (b"\x00\xff\r\npartial", None)
    assert clock.now == pytest.approx(0.25)


def test_soft_timeout_warns_once_without_shortening_the_hard_deadline(
    stream: StreamFixture, caplog: pytest.LogCaptureFixture,
) -> None:
    output, clock = stream
    with pytest.raises(subprocess.TimeoutExpired):
        output.wait(None, deadline=1, soft_timeout=.25)
    assert clock.now == pytest.approx(1)
    assert len(caplog.records) == 1
    assert "continuing until the hard deadline" in caplog.text


@pytest.mark.parametrize("name", ["stdout", "stderr"])
def test_soft_warning_resets_on_partial_output_bytes(
    stream: StreamFixture, caplog: pytest.LogCaptureFixture, name: str,
) -> None:
    output, clock = stream
    output._streams = {name: io.BytesIO()}
    emitted: list[float] = []

    def produce() -> None:
        if len(emitted) < 2 and clock.now >= (.4, .9)[len(emitted)]:
            emitted.append(clock.now)
            output._last_output, output._has_output = clock.now, True
            output._events.put((name, b"partial"))

    cast(_Process, output.process).exit_at = 1.4
    output.wait(
        None, deadline=2, soft_timeout=.25, on_wait=produce, wait_for_eof=False,
    )
    assert len(caplog.records) == 3
    assert output.output()[0 if name == "stdout" else 1] == b"partialpartial"


def test_continuing_output_cannot_renew_the_absolute_deadline(
    stream: StreamFixture, caplog: pytest.LogCaptureFixture,
) -> None:
    output, clock = stream

    def produce() -> None:
        output._last_output, output._has_output = clock.now, True
        output._events.put(("stdout", b"x"))

    with pytest.raises(subprocess.TimeoutExpired):
        output.wait(None, deadline=1, soft_timeout=.25, on_wait=produce)
    assert clock.now == pytest.approx(1)
    assert not caplog.records
    assert output.output()[0]


def test_non_output_activity_does_not_reset_the_soft_warning(
    stream: StreamFixture, caplog: pytest.LogCaptureFixture,
) -> None:
    output, clock = stream
    with pytest.raises(subprocess.TimeoutExpired):
        output.wait(None, deadline=1, soft_timeout=.25, activity_observer=lambda: True)
    assert clock.now == pytest.approx(1)
    assert len(caplog.records) == 1


def test_soft_timeout_requires_a_hard_deadline(stream: StreamFixture) -> None:
    output, _ = stream
    with pytest.raises(ValueError, match="absolute deadline"):
        output.wait(None, soft_timeout=.25)
