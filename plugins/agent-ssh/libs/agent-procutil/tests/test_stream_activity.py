"""Actor-free finite-stream clock and activity decisions."""

import queue
import subprocess
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agent_procutil import streaming


@pytest.fixture
def stream(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(
        streaming,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.now,
            sleep=lambda _seconds: pytest.fail("fixture unexpectedly slept"),
        ),
    )
    output = streaming.SyncProcessOutput.__new__(streaming.SyncProcessOutput)

    class Events(queue.Queue):
        def get(self, block=True, timeout=None):
            if timeout is not None:
                clock.now += timeout
            return super().get(block=False)

    output.process = SimpleNamespace(args=["fixture"], poll=lambda: None, text_mode=False)
    output._input_errors, output._startup_errors = [], []
    output._pending, output._relay_failed = None, False
    output._events, output._relays = Events(), {}
    output._done, output._streams = set(), {"stdout": SimpleNamespace()}
    output._output = {"stdout": bytearray(), "stderr": bytearray()}
    output._capture, output.on_output = True, None
    output._last_output, output._has_output = 0.0, False
    output._lock = nullcontext()
    return output, clock


@pytest.mark.parametrize("active,expected", [(True, 1.0), (False, 0.25)])
def test_activity_renews_quiet_but_never_absolute_cap(stream, active, expected):
    output, clock = stream
    with pytest.raises(subprocess.TimeoutExpired):
        output.wait(0.25, deadline=1, activity_observer=lambda: active)
    assert clock.now == pytest.approx(expected)


def test_later_stall_expires_and_startup_grace_ends(stream):
    output, clock = stream
    with pytest.raises(subprocess.TimeoutExpired):
        output.wait(0.25, startup_timeout=5, activity_observer=lambda: clock.now < 0.4)
    assert 0.5 <= clock.now < 0.7


@pytest.mark.parametrize("value", [1, None, "progress"])
def test_activity_requires_strict_bool(stream, value):
    output, _ = stream
    with pytest.raises(TypeError, match="bool"):
        output.wait(1, activity_observer=lambda: value)
    assert output._relay_failed


def test_observation_error_retains_original_failure(stream):
    output, _ = stream
    failure = RuntimeError("native query refused")

    def fail():
        raise failure

    with pytest.raises(RuntimeError) as raised:
        output.wait(1, activity_observer=fail)
    assert raised.value is failure
    assert output._relay_failed


@pytest.mark.parametrize("observe", [False, True])
def test_output_bytes_and_default_quiet_behavior_are_unchanged(stream, observe):
    output, clock = stream
    output._events.put(("stdout", b"\x00\xff\r\npartial"))
    with pytest.raises(subprocess.TimeoutExpired):
        output.wait(0.25, **({"activity_observer": lambda: False} if observe else {}))
    assert output.output() == (b"\x00\xff\r\npartial", None)
    assert clock.now == pytest.approx(0.25)
