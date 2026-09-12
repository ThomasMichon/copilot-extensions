"""Direct unit tests for the pure spawn-attempt/dead-letter projection.

Also exercised indirectly through the reviewer-loop and repository-issue-loop
``status``/``doctor`` CLI commands in ``tests/test_cli.py`` and
``tests/test_repository_issue_loop_cli.py``; this file pins down the pure
decision logic in isolation so a future change to it fails fast and locally
rather than only via a much larger CLI-output assertion.
"""

from __future__ import annotations

from agent_dispatch.spawn_attempt_projection import spawn_attempt_projection


def _task(**overrides):
    task = {"id": "t1", "status": "queued", "owner": None, "labels": []}
    task.update(overrides)
    return task


def test_not_dead_lettered_when_failures_below_default_cap():
    result = spawn_attempt_projection(
        _task(),
        failures=2,
        default_max_attempts=3,
        label_max_attempts={},
    )
    assert result == {
        "failed_spawns": 2,
        "max_attempts": 3,
        "dead_lettered": False,
    }


def test_dead_lettered_at_default_cap_with_rearm_when_failures_at_least_three():
    result = spawn_attempt_projection(
        _task(),
        failures=3,
        default_max_attempts=3,
        label_max_attempts={},
    )
    assert result["dead_lettered"] is True
    assert result["rearm"] == "agent-dispatch reservations rearm t1 --permit --reason <reason>"
    assert "recovery" not in result


def test_dead_lettered_below_three_failures_gets_recovery_note_not_rearm():
    result = spawn_attempt_projection(
        _task(),
        failures=1,
        default_max_attempts=1,
        label_max_attempts={},
    )
    assert result["dead_lettered"] is True
    assert "rearm" not in result
    assert "at least 3 failed spawns" in result["recovery"]


def test_label_max_attempts_overrides_default_using_highest_matching_cap():
    result = spawn_attempt_projection(
        _task(labels=["urgent", "flaky"]),
        failures=4,
        default_max_attempts=3,
        label_max_attempts={"urgent": 5, "flaky": 2},
    )
    # highest of the matching label caps (5) wins over the lower one (2) and
    # over the default (3)
    assert result["max_attempts"] == 5
    assert result["dead_lettered"] is False


def test_zero_max_attempts_never_dead_letters():
    result = spawn_attempt_projection(
        _task(),
        failures=100,
        default_max_attempts=0,
        label_max_attempts={},
    )
    assert result["dead_lettered"] is False


def test_only_unowned_queued_tasks_can_be_dead_lettered():
    claimed = spawn_attempt_projection(
        _task(status="queued", owner="someone"),
        failures=10,
        default_max_attempts=1,
        label_max_attempts={},
    )
    assert claimed["dead_lettered"] is False

    started = spawn_attempt_projection(
        _task(status="started", owner=None),
        failures=10,
        default_max_attempts=1,
        label_max_attempts={},
    )
    assert started["dead_lettered"] is False
