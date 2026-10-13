"""Public token validation and legacy-only save fast-path boundaries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import producer_allocation as allocation, resource_producer as producer
from agent_worktrees import tracking, tracking_producer_write as writer


@pytest.mark.parametrize("value,valid", [
    ("a" * 32, True), ("A_-" * 42, True), ("a" * 128, True),
    ("a" * 31, False), ("a" * 129, False), ("a" * 32 + "\n", False),
    ("a" * 31 + "/", False), ("é" * 32, False),
    (None, False), (32, False), ([], False), ({}, False),
])
def test_public_token_contract_matches_fence_and_request_admission(
    value: object, valid: bool,
) -> None:
    assert producer.is_valid_token(value) is valid
    if valid:
        assert allocation.parse_fence({"generation": 1, "key": value}).key == value
        assert writer._input("inspect", {"token": value}) == {"token": value}
    else:
        with pytest.raises(ValueError, match="invalid allocation fence key"):
            allocation.parse_fence({"generation": 1, "key": value})
        with pytest.raises(ValueError, match="invalid producer token"):
            writer._input("inspect", {"token": value})


@pytest.mark.parametrize("has_current", [False, True])
def test_legacy_fast_path_never_indexes_refs_or_replaces_resources(
    has_current: bool, tmp_path: Path,
) -> None:
    class Legacy:
        has_producer = False

        @property
        def ref(self) -> str:
            pytest.fail("legacy-only save built producer indexes")

    resources = [Legacy(), Legacy()]
    record = SimpleNamespace(resources=resources)
    current = SimpleNamespace(resources=[Legacy()]) if has_current else None
    producer.preserve_claims(record, current, tmp_path / "record.yaml")
    assert record.resources is resources


@pytest.mark.parametrize("on_current", [False, True])
def test_malformed_present_metadata_cannot_take_legacy_fast_path(
    on_current: bool, tmp_path: Path,
) -> None:
    malformed = tracking.ResourceClaim(ref="broken", producer=None, producer_present=True)
    record = SimpleNamespace(resources=[] if on_current else [malformed])
    current = SimpleNamespace(resources=[malformed] if on_current else [])
    if on_current:
        producer.preserve_claims(record, current, tmp_path / "record.yaml")
        assert record.resources[0].has_producer and record.resources[0].is_unsettled
    else:
        with pytest.raises(ValueError, match="producer metadata may only be written"):
            producer.preserve_claims(record, current, tmp_path / "record.yaml")
