import pytest

from agent_procutil.startup import StartupClock, StartupTimeouts


def test_selected_startup_policy_is_not_a_turn_budget():
    policy = StartupTimeouts()
    assert (policy.communication, policy.process, policy.expected) == (30, 300, 60)
    now = [0.0]
    clock = StartupClock(policy, monotonic=lambda: now[0])
    now[0] = 29
    assert clock.expired() is None
    now[0] = 30
    assert clock.expired().clock == "communication"
    clock.receive("target-keepalive")
    assert clock.expired() is None
    clock.mark_ready()
    now[0] = 7200
    assert clock.expired() is None
    assert clock.remaining() is None


def test_every_received_update_renews_only_contact():
    now = [0.0]
    clock = StartupClock(StartupTimeouts(), monotonic=lambda: now[0])
    for now[0] in range(20, 300, 20):
        clock.receive("future-event")
        assert clock.expired() is None
    now[0] = 300
    clock.receive("still-starting")
    expiry = clock.expired()
    assert (expiry.clock, expiry.elapsed, expiry.silence, expiry.phase) == (
        "process", 300, 0, "still-starting",
    )


def test_local_timer_and_remote_timestamp_do_not_renew_contact():
    now = [500.0]
    clock = StartupClock(StartupTimeouts(), monotonic=lambda: now[0])
    now[0] += 20
    assert clock.remaining() == 10
    assert clock.process_remaining() == 280
    now[0] += 10
    assert clock.expired().clock == "communication"


@pytest.mark.parametrize("name", ["communication", "process", "expected"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_policy_rejects_invalid_budgets(name, value):
    with pytest.raises(ValueError, match="finite and positive"):
        StartupTimeouts(**{name: value})
