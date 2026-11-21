"""Cached status is bounded metadata, never a recursive cache scan."""
from pathlib import Path
from typing import Any

import pytest

from agent_procutil import python_cache_status as status
from agent_procutil import python_cache_store as store


@pytest.fixture
def area(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(store, "user_cache", lambda: tmp_path / "os-cache")
    return tmp_path / "runtime"


def observation() -> dict[str, Any]:
    return {
        "schema": "agent-procutil.python-cache-reclamation", "version": 1,
        "summary": {"measured_bytes": 100, "held_bytes": 100, "eligible_bytes": 0,
                    "unmeasured_prefixes": 8},
        "pending": True, "reasons": {"liveness-unproven": 1}, "candidates": ["not persisted"],
    }


def test_missing_observation_is_unknown_and_does_not_create_metadata(area: Path) -> None:
    result = status.describe(area, argv=["cache-tool", "--root", str(area)])
    assert result["observation"] == "not-measured"
    assert "summary" not in result
    assert not store.installation_root(area).exists()


def test_observation_round_trip_never_scans_legacy_content(area: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status.write(area, observation())
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rglob", lambda *args: pytest.fail("status recursively scanned cache"))
        patch.setattr(Path, "iterdir", lambda *args: pytest.fail("status listed a cache"))
        result = status.describe(area, argv=["cache-tool", "--root", str(area)])
    assert result["summary"] == observation()["summary"]
    assert result["observation"] == "cached" and result["observation_age_seconds"] >= 0
    assert "candidates" not in result


def test_malformed_observation_is_not_zero_bytes(area: Path) -> None:
    status.write(area, observation())
    target = store.installation_root(area) / "legacy-prefix-status.json"
    target.write_bytes(b"not-json")
    result = status.describe(area, argv=["cache-tool"])
    assert result["observation"] == "unavailable"
    assert "summary" not in result


def test_cached_status_replacement_keeps_latest_observation(area: Path) -> None:
    status.write(area, observation())
    value = observation()
    value["summary"]["measured_bytes"] = 200
    status.write(area, value)
    result = status.read(area)
    assert result is not None and result["summary"]["measured_bytes"] == 200


def test_unknown_inventory_round_trips_without_zero_claim(area: Path) -> None:
    value = observation()
    value["inventory_complete"] = False
    value["scan_complete"] = False
    value["summary"]["unmeasured_prefixes"] = None
    status.write(area, value)
    result = status.describe(area, argv=["cache-tool"])
    assert result["observation"] == "cached"
    assert result["summary"]["unmeasured_prefixes"] is None
    assert not result["inventory_complete"] and not result["scan_complete"]
