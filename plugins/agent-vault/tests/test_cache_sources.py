"""Configured cache target collection has bounded, truthful partial outcomes."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import repeat

import pytest

from agent_vault import cache_sources


def test_collection_retains_partial_generator_output_and_continues_after_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(_machine: str | None) -> Iterable[str]:
        yield "Allowed/first"
        raise RuntimeError("credential-bearing private diagnostic")

    result = cache_sources.collect(
        [broken, lambda _machine: [("Allowed/second", "username"), "Allowed/first"]],
        None,
    )
    assert result.entries == [("Allowed/first", "password"), ("Allowed/second", "username")]
    assert result.failed_sources == 1 and not result.truncated
    assert "credential-bearing private diagnostic" not in caplog.text


def test_raw_item_budget_bounds_an_infinite_duplicate_source() -> None:
    observed: list[str | None] = []

    def source(machine: str | None) -> Iterable[str]:
        observed.append(machine)
        return repeat("Allowed/one")

    result = cache_sources.collect([source], "fixture-machine", max_entries=3, max_items=8)
    assert result.entries == [("Allowed/one", "password")]
    assert observed == ["fixture-machine"]
    assert result.truncated and result.failed_sources == 0


@pytest.mark.parametrize("bound", ["entries", "sources", "deadline"])
def test_collection_stops_before_requesting_unbounded_additional_targets(
    monkeypatch: pytest.MonkeyPatch,
    bound: str,
) -> None:
    calls: list[bool] = []

    def source(_machine: str | None) -> list[str]:
        calls.append(True)
        return ["Allowed/one", "Allowed/two"]

    options = {
        "entries": {"max_entries": 1},
        "sources": {"max_sources": 0},
        "deadline": {"deadline": 10.0},
    }[bound]
    monkeypatch.setattr(cache_sources.time, "monotonic", lambda: 10.0)
    result = cache_sources.collect([source], None, **options)
    assert result.truncated
    assert len(result.entries) == (1 if bound == "entries" else 0)
    assert len(calls) == (1 if bound == "entries" else 0)
