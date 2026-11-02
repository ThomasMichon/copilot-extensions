"""Owned allocation cannot escape the actual non-bypassable finalization gate."""

from __future__ import annotations

import pytest

from agent_worktrees import finalize, resource_producer as producer, tracking
from agent_worktrees.claim_handoff_admission import validate_producer_offer
from test_owned_worktree_metadata import bound, pending
from test_resource_producer_finalize import _final_owner_seed as _final_owner_seed
from test_resource_producer_finalize import final_owner as final_owner


@pytest.mark.parametrize("phase", ["pending", "bound", "confirmed"])
@pytest.mark.parametrize("gate", ["off", "warn", "block"])
@pytest.mark.parametrize("abandon", [False, True])
def test_actual_finalize_retains_owned_allocation_in_every_mode(
    final_owner: tuple, monkeypatch: pytest.MonkeyPatch,
    phase: str, gate: str, abandon: bool,
) -> None:
    _, path, config = final_owner
    value = pending() if phase == "pending" else bound(confirmed=phase == "confirmed")
    value["creator"] = tracking.format_claim_ref(config.machine, config.repo_name, "owner")
    record = tracking.load_record(path)
    record.resources.append(tracking.ResourceClaim(
        kind="worktree", ref=producer.claim_ref(value), producer=value,
    ))
    with producer.transaction(path):
        tracking.save_record(record, path)
    before = path.read_bytes()
    monkeypatch.setenv("AGENT_WORKTREES_OBLIGATION_GATE", gate)
    assert finalize.validate_and_finalize(
        "owner", config, abandon=abandon, handoff_to="recipient" if abandon else None,
    ) is False
    current = tracking.load_record(path)
    assert current.status == "active" and current.resources[0].is_unsettled
    assert current.resources[0].producer == value
    assert path.read_bytes() == before


def test_disposable_handoff_cannot_transfer_an_unconfirmed_owned_worktree() -> None:
    value = bound()
    claim = tracking.ResourceClaim(kind="worktree", ref=producer.claim_ref(value), producer=value)
    source = tracking.parse_claim_ref(value["creator"])
    consumer = tracking.parse_claim_ref("origin/harness/recipient")
    assert source is not None and consumer is not None
    with pytest.raises(ValueError, match="owned worktree handoff"):
        validate_producer_offer([claim], source, consumer, machine="origin", error_type=ValueError)
