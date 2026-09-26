"""Deliberate, obviously-synthetic validation probe.

Phase 1.5 (live validation) of the promotion-failure-reactive-fix-agent
effort (efforts/active/promotion-failure-reactive-fix-agent/README.md):
this test exists ONLY to produce one real, controlled red
`full - agent-worktrees` job so the `report-failure` watchdog
(tools/ci_failure_watchdog.py) can be observed reacting to a genuine
failure signature -- including the dedup/rate-limit path specifically,
which had not yet been exercised against a real repeat.

Safe to delete once observed -- see the effort's own Journal for the
follow-up revert PR. Never touches existing test logic, has no side
effects, and this file exists only for the duration of the probe.
"""

from __future__ import annotations


def test_promotion_failure_reactive_fix_agent_phase_1_5_probe() -> None:
    assert False, (
        "Deliberate Phase 1.5 validation probe for "
        "promotion-failure-reactive-fix-agent -- safe to delete, see "
        "efforts/active/promotion-failure-reactive-fix-agent/README.md"
    )
