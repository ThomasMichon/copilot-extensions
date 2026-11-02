"""Pure record-clock contracts; no process or native namespace observation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs/agent-procutil/src/agent_procutil/_daemon_instance_clock.py"
spec = importlib.util.spec_from_file_location("unit_daemon_clock", SOURCE)
clock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clock)


def test_domain_binds_boot_and_time_namespace_without_wall_time(monkeypatch):
    paths = []

    def path(value):
        paths.append(value)
        return SimpleNamespace(
            read_text=lambda **kwargs: "fixture-boot\n",
            stat=lambda: SimpleNamespace(st_dev=4, st_ino=17),
        )

    monkeypatch.setattr(clock, "Path", path)
    assert clock.domain() == "linux-monotonic:fixture-boot:4:17"
    assert paths == ["/proc/sys/kernel/random/boot_id", "/proc/self/ns/time"]


def test_publication_and_observation_share_absolute_monotonic_bounds(monkeypatch):
    monkeypatch.setattr(clock, "domain", lambda: "fixture-domain")
    record = clock.fields(10, 30, 100)
    assert record == {
        "schema": "2", "clock_domain": "fixture-domain",
        "deadline_mono": "110", "hard_deadline_mono": "130",
    }
    assert clock.bounds(record) == (110, 130)
    record.update(deadline="1", hard_deadline="2", updated="99999999999")
    assert clock.bounds(record) == (110, 130)


@pytest.mark.parametrize("fault", ["legacy", "namespace", "nan", "infinite", "over-cap", "negative"])
def test_unknown_or_invalid_timing_never_falls_back_to_epoch(monkeypatch, fault):
    monkeypatch.setattr(clock, "domain", lambda: "fixture-domain")
    record = clock.fields(10, 30, 100)
    if fault == "legacy":
        record["schema"] = "1"
    elif fault == "namespace":
        record["clock_domain"] = "foreign-domain"
    else:
        record["deadline_mono"] = {
            "nan": "nan", "infinite": "inf", "over-cap": "131", "negative": "-1",
        }[fault]
    with pytest.raises(ValueError):
        clock.bounds(record)


@pytest.mark.parametrize("ttl,maximum,now", [
    (0, 10, 100), (11, 10, 100), (1, float("inf"), 100),
    (1, 10, -1), (1, 1e308, 1e308),
])
def test_invalid_publication_refuses(monkeypatch, ttl, maximum, now):
    monkeypatch.setattr(clock, "domain", lambda: pytest.fail("invalid policy acquired a domain"))
    with pytest.raises(ValueError, match="invalid daemon monotonic lifetime"):
        clock.fields(ttl, maximum, now)


def _wall_record():
    return {
        "schema": "1", "purpose": "unattended-test", "deadline": "110",
        "hard_deadline": "130", "updated": "100",
    }


def test_schema1_retains_original_monotonic_soft_and_hard_bounds():
    record = _wall_record()
    observed = clock.WallLeaseObservation(record, wall=100, monotonic=200)
    assert not observed.expired(record, wall=80, monotonic=209)
    assert observed.expired(record, wall=80, monotonic=210)
    assert observed.past_hard(230)


def test_schema1_renewal_never_reanchors_original_hard_limit():
    record = _wall_record()
    observed = clock.WallLeaseObservation(record, wall=100, monotonic=200)
    renewed = {**record, "deadline": "125", "updated": "105"}
    assert not observed.expired(renewed, wall=105, monotonic=205)
    assert observed.expired(renewed, wall=80, monotonic=225)
    assert observed.hard_monotonic == 230


def test_schema1_rejects_changed_or_nonfinite_hard_bound():
    record = _wall_record()
    observed = clock.WallLeaseObservation(record, wall=100, monotonic=200)
    for hard in ("131", "129", "inf", "nan", "-1"):
        with pytest.raises(ValueError):
            observed.expired({**record, "hard_deadline": hard}, wall=105, monotonic=205)
        assert observed.hard_monotonic == 230


def test_schema1_observation_rejects_production_and_other_schemas():
    for change in (
        {"schema": "2"}, {"purpose": "production"}, {"deadline": "inf"},
        {"hard_deadline": "inf"}, {"deadline": "nan"}, {"deadline": "0"},
        {"deadline": "131"},
    ):
        with pytest.raises(ValueError):
            clock.WallLeaseObservation({**_wall_record(), **change}, wall=100, monotonic=200)
