"""Declared source windows survive Bridge publication without being renewed."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from agent_bridge.routes import worktree_refresh as refresh


def test_declared_current_window_survives_bridge_cache_without_becoming_newer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = SimpleNamespace(now=1000.0, wall=2000.0)
    monkeypatch.setattr(refresh, "time", SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: clock.wall,
    ))
    raw = json.dumps({
        "contract_version": 1, "worktrees": [{"id": "workspace"}],
        "projection": {
            "quality": "current", "observed_at": 1960.0,
            "freshness": {"bound_s": 90.0, "refreshed_age_s": 40.0},
        },
    })
    observed, valid_until = refresh.inventory_window(raw)
    assert observed == 960.0 and valid_until == 1050.0
    owner = refresh.WorktreeRefreshOwner()
    owner.bind(("local",), ["local"], available=True)
    owner.published(
        owner.epoch, {"local": None}, observed={"local": observed},
        valid_until={"local": valid_until},
    )
    assert owner.metadata()["sources"]["local"]["freshness"] == "fresh"
    clock.now += 49
    clock.wall += 49
    assert refresh.inventory_window(raw) == (observed, valid_until)
    assert owner.metadata()["sources"]["local"]["freshness"] == "fresh"
    clock.now += 1
    clock.wall += 1
    assert owner.metadata()["sources"]["local"]["freshness"] == "stale"
    with pytest.raises(refresh.DiscoveryReadError, match="source-stale"):
        refresh.validate_inventory(raw)


@pytest.mark.parametrize("freshness", [
    {"bound_s": True, "refreshed_age_s": 0},
    {"bound_s": -1, "refreshed_age_s": 0},
    {"bound_s": float("inf"), "refreshed_age_s": 0},
    {"bound_s": 90, "refreshed_age_s": float("nan")},
    {"bound_s": 90, "refreshed_age_s": -1},
    {"bound_s": 90, "refreshed_age_s": 90},
])
def test_declared_window_never_accepts_malformed_or_expired_evidence(
    freshness: dict[str, object],
) -> None:
    raw = json.dumps({
        "contract_version": 1, "worktrees": [{"id": "workspace"}],
        "projection": {
            "quality": "current", "observed_at": time.time() - 1, "freshness": freshness,
        },
    })
    with pytest.raises(refresh.DiscoveryReadError):
        refresh.validate_inventory(raw)


@pytest.mark.parametrize("publication_age,refresh_age,read_elapsed,remaining", [
    (5.0, 85.0, 0.0, 5.0),
    (85.0, 5.0, 0.0, 5.0),
    (5.0, 85.0, 3.0, 2.0),
])
def test_expiry_uses_both_source_ages_and_the_original_read_clock(
    monkeypatch: pytest.MonkeyPatch, publication_age: float, refresh_age: float,
    read_elapsed: float, remaining: float,
) -> None:
    clock = SimpleNamespace(now=1000.0, wall=2000.0)
    monkeypatch.setattr(refresh, "time", SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: clock.wall,
    ))
    raw = json.dumps({
        "contract_version": 1, "worktrees": [{"id": "workspace"}],
        "projection": {
            "quality": "current", "observed_at": clock.wall - publication_age,
            "freshness": {"bound_s": 90.0, "refreshed_age_s": refresh_age},
        },
    })
    started = clock.now - read_elapsed
    observed, expiry = refresh.inventory_window(raw, read_started=started)
    assert expiry == clock.now + remaining
    assert observed == expiry - 90
    clock.now += remaining
    clock.wall += remaining
    with pytest.raises(refresh.DiscoveryReadError, match="source-stale"):
        refresh.inventory_window(raw, read_started=started)
