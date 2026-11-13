"""Incompatible owners never fall back to unclaimed writable scratch."""

from pathlib import Path

import pytest

from agent_worktrees import artifact_scratch


@pytest.mark.parametrize("support", [
    {}, {"allocator": 1},
    {"schema": artifact_scratch.SCHEMA, "allocator": True, "artifact_retention": 1},
    {"schema": artifact_scratch.SCHEMA, "allocator": 1, "artifact_retention": False},
])
def test_missing_or_ambiguous_owner_capabilities_refuse_before_allocation(monkeypatch, support):
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    from plugin_test_owned_scratch import OwnedTestSandbox
    from plugin_test_containment import ContainmentError
    monkeypatch.setattr(artifact_scratch, "capabilities", lambda: support)
    monkeypatch.setattr(artifact_scratch, "allocate", lambda *a, **k: pytest.fail("unnegotiated allocation"))
    with pytest.raises(ContainmentError, match="does not support"):
        OwnedTestSandbox("machine/project/worktree", repo, stage="tests")
