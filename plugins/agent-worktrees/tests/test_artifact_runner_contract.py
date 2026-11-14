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


def test_early_failure_is_retained_after_it_leaves_the_result_tail(monkeypatch):
    from types import SimpleNamespace
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    from plugin_test_owned_scratch import OwnedTestSandbox
    captured = []
    scope = OwnedTestSandbox.__new__(OwnedTestSandbox)
    scope.provider = SimpleNamespace(
        settle_after_cleanup=lambda *a, **k: captured.append(k["diagnostic"]),
        dispose_consumed=lambda *a, **k: pytest.fail("failed evidence was deleted"),
    )
    scope.allocation = {"token": "fixture"}
    scope.ledger, scope.owner, scope.forbidden, scope.stage = Path("unused"), "owner", (), "tests"
    scope.results, scope.group_count, scope.failed = [], 0, False
    for group in range(17):
        scope.record_result(group, int(group == 0))
    assert all(result["exit_code"] == 0 for result in scope.results)
    scope.__exit__(None, None, None)
    assert captured[0] is not None


def test_preparation_preserves_limits_and_the_remaining_deadline(monkeypatch):
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    import plugin_test_owned_scratch as owned
    captured = []
    monkeypatch.setattr(owned, "run_contained", lambda *a, **k: captured.append(k["limits"]) or 0)
    limits = owned.Limits(wall_seconds=20, max_processes=3, max_memory_mb=100, max_temp_mb=50)
    owned.prepare_owned("fixture", "uv", False, retained=repo, environment={}, sandbox=repo,
                        limits=limits, wall_seconds=4)
    actual = captured[0]
    assert actual.wall_seconds == 4
    assert (actual.max_processes, actual.max_memory_mb, actual.max_temp_mb) == (3, 100, 50)
    with pytest.raises(owned.ContainmentError, match="deadline"):
        owned.prepare_owned("fixture", "uv", False, retained=repo, environment={}, sandbox=repo,
                            limits=limits, wall_seconds=0)
    assert len(captured) == 1
