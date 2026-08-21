"""Tests for lease-gated periodic command emitters."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_dispatch.producers import emitter


class FakeClient:
    def __init__(self, *, granted: bool = True):
        self.granted = granted
        self.calls = []

    def acquire_schedule_lease(self, scope, holder, **kwargs):
        self.calls.append((scope, holder, kwargs))
        return {
            "granted": self.granted,
            "lease": {"scope": scope, "holder": holder if self.granted else "other"},
        }


def _spec(**over):
    spec = {
        "id": "review-inbox",
        "command": ["review-emitter", "tick"],
        "interval_seconds": 3600,
    }
    spec.update(over)
    return spec


def test_run_tick_acquires_lease_and_runs_command():
    client = FakeClient()
    calls = []
    times = iter([10.0, 12.5])

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    result = emitter.run_tick(
        client, _spec(cwd="/repo", env={"MODE": "test"}),
        holder="host-a", runner=runner, clock=lambda: next(times),
    )

    assert client.calls[0][0:2] == ("emitter:review-inbox", "host-a")
    assert calls[0][0] == ["review-emitter", "tick"]
    assert calls[0][1]["cwd"] == "/repo"
    assert calls[0][1]["env"]["MODE"] == "test"
    assert result["held"] is True
    assert result["returncode"] == 0
    assert result["duration_seconds"] == 2.5


def test_run_tick_idles_when_another_holder_owns_lease():
    client = FakeClient(granted=False)
    called = False

    def runner(*_args, **_kwargs):
        nonlocal called
        called = True

    result = emitter.run_tick(client, _spec(), holder="host-a", runner=runner)
    assert result["held"] is False
    assert called is False
    assert result["lease"]["holder"] == "other"


@pytest.mark.parametrize(
    "spec, needle",
    [
        ({"command": ["tick"], "interval_seconds": 1}, "id"),
        (_spec(command="tick"), "list"),
        (_spec(interval_seconds=0), "> 0"),
        (_spec(env={"COUNT": 1}), "string"),
    ],
)
def test_validate_spec_rejects_malformed_specs(spec, needle):
    with pytest.raises(emitter.EmitterError) as exc:
        emitter.validate_spec(spec)
    assert needle in str(exc.value)
