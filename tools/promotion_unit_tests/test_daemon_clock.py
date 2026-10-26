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
