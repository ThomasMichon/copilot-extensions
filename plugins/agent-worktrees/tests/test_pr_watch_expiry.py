"""Ordinary synchronous waits retain their outage and deadline contract."""

from __future__ import annotations

import pytest

from agent_worktrees import pr_contract as pc
from agent_worktrees.pr_watch import run_wait
from agent_worktrees.providers import ProviderError


@pytest.mark.parametrize("outcome", ["recovery", "timeout", "terminal-at-deadline"])
def test_synchronous_wait_preserves_outage_deadline_and_recovery(outcome: str) -> None:
    elapsed = [0.0]
    sleeps: list[float] = []
    failures: list[ProviderError] = []
    observed: list[pc.PRSnapshot] = []
    calls: list[int] = []
    unchanged = pc.PRSnapshot(pr_state="open", reviews=(pc.Review(1, "APPROVED", "bob"),))
    recovered = pc.PRSnapshot(
        pr_state="open",
        reviews=(pc.Review(1, "APPROVED", "bob"), pc.Review(2, "APPROVED", "carol")),
    )
    terminal = pc.PRSnapshot(pr_state="closed", merged=True)

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        elapsed[0] += seconds

    def fetch() -> pc.PRSnapshot:
        calls.append(len(calls) + 1)
        if len(calls) <= 3 or outcome == "timeout":
            raise ProviderError("controlled repeated outage", transient=True)
        if outcome == "terminal-at-deadline":
            return terminal
        return recovered if len(calls) == 6 else unchanged

    result = run_wait(
        repo="o/r", pr=1, until=["approved", "merged"], baseline=None,
        fetch=fetch, timeout=3.0 if outcome != "recovery" else 10.0,
        interval=1.0, now=lambda: elapsed[0], sleep=sleep,
        on_error=failures.append, on_poll=observed.append,
    )
    if outcome == "timeout":
        assert not result.matched
        assert result.payload == {"repo": "o/r", "pr": 1, "timed_out": True}
        assert observed == [] and len(failures) == len(calls) == 4
    elif outcome == "terminal-at-deadline":
        assert result.matched and result.payload["transitions"] == ["merged"]
        assert observed == [terminal] and len(failures) == 3 and len(calls) == 4
    else:
        assert result.matched and result.payload["transitions"] == ["approved"]
        assert result.payload["events"][0]["review"]["id"] == 2
        assert observed == [unchanged, unchanged, recovered]
        assert len(failures) == 3 and len(calls) == 6
    assert sleeps == [1.0] * (5 if outcome == "recovery" else 3)
    assert elapsed[0] == sum(sleeps)
