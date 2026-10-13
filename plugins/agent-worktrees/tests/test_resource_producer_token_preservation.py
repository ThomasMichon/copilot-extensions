"""Token ambiguity must not be collapsed by generic or authorized saves."""

from __future__ import annotations

import copy

import pytest
import yaml

from agent_worktrees import resource_producer as producer, tracking
from test_resource_producer import reserve
from test_resource_producer import owner as owner


@pytest.mark.parametrize("authorized", [False, True])
def test_duplicate_tokens_under_distinct_refs_reject_stale_save(owner, authorized):
    path, _ = owner
    stale = tracking.load_record(path)
    pending = reserve(path)
    raw = yaml.safe_load(path.read_text())
    duplicate = copy.deepcopy(raw["resources"][0])
    duplicate["producer"]["namespace"] = "other-provider"
    duplicate["ref"] = producer.claim_ref(duplicate["producer"])
    raw["resources"].append(duplicate)
    path.write_text(yaml.safe_dump(raw))
    before = path.read_bytes()
    assert duplicate["ref"] != pending["ref"]
    if authorized:
        with producer.transaction(path), pytest.raises(ValueError, match="duplicate producer token"):
            tracking.save_record(stale, path)
    else:
        with pytest.raises(ValueError, match="duplicate producer token"):
            tracking.save_record(stale, path)
    assert path.read_bytes() == before
    current = tracking.load_record(path)
    assert len(current.resources) == 2 and all(claim.is_unsettled for claim in current.resources)
