"""Schema-1 observation cannot extend a hard cap or hide malformed bounds."""

import pytest

from agent_procutil._daemon_instance_clock import WallLeaseObservation


@pytest.fixture
def record():
    return {
        "schema": "1", "purpose": "unattended-test", "deadline": "110",
        "hard_deadline": "130", "updated": "100",
    }


def test_wall_rollback_does_not_extend_original_soft_or_hard_bound(record):
    observed = WallLeaseObservation(record, wall=100, monotonic=200)
    assert not observed.expired(record, wall=80, monotonic=209)
    assert observed.expired(record, wall=80, monotonic=210)
    assert observed.past_hard(230)


def test_renewal_moves_soft_bound_but_not_original_monotonic_hard_bound(record):
    observed = WallLeaseObservation(record, wall=100, monotonic=200)
    renewed = {**record, "deadline": "125", "updated": "105"}
    assert not observed.expired(renewed, wall=105, monotonic=205)
    assert not observed.expired(renewed, wall=80, monotonic=224)
    assert observed.expired(renewed, wall=80, monotonic=225)
    assert observed.hard_monotonic == 230


@pytest.mark.parametrize("value", ["131", "129", "inf", "nan", "-1"])
def test_changed_hard_bound_refuses_instead_of_resetting_clock(record, value):
    observed = WallLeaseObservation(record, wall=100, monotonic=200)
    with pytest.raises(ValueError):
        observed.expired({**record, "hard_deadline": value}, wall=105, monotonic=205)
    assert observed.hard_monotonic == 230


@pytest.mark.parametrize(
    "change", [{"schema": "2"}, {"purpose": "production"}, {"deadline": "inf"},
               {"hard_deadline": "inf"}, {"deadline": "nan"}, {"deadline": "0"},
               {"deadline": "131"}],
)
def test_observation_rejects_nonfinite_or_foreign_policy(record, change):
    with pytest.raises(ValueError):
        WallLeaseObservation({**record, **change}, wall=100, monotonic=200)
