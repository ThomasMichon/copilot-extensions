"""Tests for the scheduler/timer producer."""

from __future__ import annotations

from datetime import datetime

import pytest

from agent_dispatch.producers import schedule


class FakeClient:
    """A DispatchClient stand-in that emulates dedup_key idempotency."""

    def __init__(self):
        self.by_dedup: dict[str, dict] = {}
        self.calls: list[dict] = []

    def create(self, title, **kwargs):
        self.calls.append({"title": title, **kwargs})
        key = kwargs.get("dedup_key")
        if key and key in self.by_dedup:
            return self.by_dedup[key]
        task = {"id": f"t{len(self.by_dedup)}", "title": title, "status": "queued", **kwargs}
        if key:
            self.by_dedup[key] = task
        return task

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None


def test_interval_occurrences_are_multiples_within_window():
    now = 10_000.0
    occ = schedule._interval_occurrences(100.0, now, horizon=100.0, lookback=100.0)
    assert occ  # non-empty
    assert all(t % 100 == 0 for t in occ)
    assert all(now - 100 <= t <= now + 100 for t in occ)
    # deterministic + de-duplicated
    assert occ == schedule._interval_occurrences(100.0, now, 100.0, 100.0)


def test_daily_occurrences_match_requested_local_times():
    now = (
        datetime.now()
        .astimezone()
        .replace(hour=12, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    occ = schedule._daily_occurrences(["09:00", "17:00"], now, horizon=86400.0, lookback=3600.0)
    labels = {datetime.fromtimestamp(t).astimezone().strftime("%H:%M") for t in occ}
    assert labels <= {"09:00", "17:00"}
    assert "17:00" in labels  # 17:00 today is within the 24h horizon from noon


def test_due_occurrences_requires_exactly_one_cadence():
    with pytest.raises(schedule.ScheduleError):
        schedule.due_occurrences({"id": "x"}, now=0.0)
    with pytest.raises(schedule.ScheduleError):
        schedule.due_occurrences({"id": "x", "interval_seconds": 1, "at": ["09:00"]}, now=0.0)


def test_run_tick_creates_deferred_deduped_tasks():
    client = FakeClient()
    spec = {
        "default_repo": "example.com/acme/widget",
        "schedules": [
            {"id": "hourly", "title": "Hourly sweep", "interval_seconds": 3600},
        ],
    }
    now = 7200.0  # a multiple of 3600 so an occurrence lands exactly on 'now'
    result = schedule.run_tick(client, spec, now=now)
    assert result["errors"] == []
    assert result["created"]
    for task in result["created"]:
        assert task["source"] == "schedule"
        assert task["origin_ref"] == "schedule/hourly"
        assert task["dedup_key"].startswith("sched:hourly:")
        assert task["not_before"] % 3600 == 0
        assert task["repo"] == "example.com/acme/widget"


def test_run_tick_is_idempotent():
    client = FakeClient()
    spec = {
        "default_repo": "example.com/acme/widget",
        "schedules": [{"id": "hourly", "title": "Hourly", "interval_seconds": 3600}],
    }
    first = schedule.run_tick(client, spec, now=7200.0)
    before = len(client.by_dedup)
    second = schedule.run_tick(client, spec, now=7200.0)
    # same occurrences -> same dedup_keys -> no new tasks materialized
    assert len(client.by_dedup) == before
    ids_first = {t["id"] for t in first["created"]}
    ids_second = {t["id"] for t in second["created"]}
    assert ids_second <= ids_first


def test_run_tick_reports_missing_lane_and_bad_cadence():
    client = FakeClient()
    spec = {
        "schedules": [
            {"id": "no-lane", "title": "x", "interval_seconds": 60},
            {"id": "bad", "title": "y", "repo": "example.com/a/b"},
        ],
    }
    result = schedule.run_tick(client, spec, now=0.0)
    errors = {e["id"]: e["error"] for e in result["errors"]}
    assert "no repo" in errors["no-lane"]
    assert "interval_seconds" in errors["bad"] or "at" in errors["bad"]


def test_load_spec_rejects_malformed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"not_schedules": 1}', encoding="utf-8")
    with pytest.raises(schedule.ScheduleError):
        schedule.load_spec(bad)
