"""Stored closeout receipts cannot discharge ownership without availability."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_worktrees import claim_providers, finalize, resource_producer as producer, tracking
from test_resource_producer import absence, call, reserve
from test_resource_producer import owner as owner


AVAILABILITY = [
    (False, None),
    (True, False),
    (True, None),
    (True, 0),
    (True, 1),
    (True, "true"),
    (True, {}),
    (True, []),
    (True, True),
]


def closeout(ref: str, present: bool, value: object) -> dict:
    receipt = absence(ref)
    if present:
        receipt["available"] = value
    else:
        receipt.pop("available")
    return receipt


def write_closed(path: Path, present: bool, value: object) -> None:
    raw = yaml.safe_load(path.read_text())
    claim = raw["resources"][0]
    claim["producer"]["phase"] = "closed"
    claim["producer"]["closeout"] = closeout(claim["ref"], present, value)
    claim["state"] = "released"
    raw["status"] = "finalized"
    path.write_text(yaml.safe_dump(raw))


@pytest.mark.parametrize("present,value", AVAILABILITY)
def test_persisted_closeout_requires_literal_available_true(
    owner: tuple, monkeypatch: pytest.MonkeyPatch, present: bool, value: object,
) -> None:
    path, _ = owner
    reservation = reserve(path)
    write_closed(path, present, value)
    before = path.read_bytes()
    record = tracking.load_record(path)
    claim = record.resources[0]
    accepted = present and value is True
    assert producer.valid_metadata(claim.producer) is accepted
    assert claim.is_unsettled is not accepted
    assert finalize._assert_obligations_settled(
        record, "owner", abandon=True, handoff_to="consumer",
    ) is accepted
    monkeypatch.setattr(
        claim_providers, "resolve_claim_status",
        lambda *args: pytest.fail("stored closeout replay reached a provider"),
    )
    replay = call(path, "close", {"token": reservation["token"]})
    if accepted:
        assert replay.get("ok") and replay["phase"] == "closed"
    else:
        assert replay["error"] == "rejected"
        assert claim.to_dict()["producer"] == {"error": "invalid-producer-metadata"}
    assert path.read_bytes() == before


@pytest.mark.parametrize("present,value", AVAILABILITY)
def test_shared_absence_predicate_requires_literal_available_true(
    present: bool, value: object,
) -> None:
    receipt = closeout("raw-resource:opaque-subject", present, value)
    assert producer.verified_absence(receipt, "raw-resource:opaque-subject") is (
        present and value is True
    )
