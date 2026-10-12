"""Bounded finalized identity inspection."""

import subprocess
from types import SimpleNamespace

import pytest

from agent_worktrees import finalized_checkout


def test_identity_timeout_is_bounded_and_does_not_assert_final(monkeypatch):
    calls = []

    def git(*args, timeout, **kwargs):
        assert timeout == 5
        calls.append(args)
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(finalized_checkout.git_ops, "git", git)
    record = SimpleNamespace(
        status="finalized", worktree_path="fixture",
        finalized_checkout={"head": "a" * 40, "branch": "worktree/fixture"},
    )
    assert finalized_checkout.matches(record) is False


def test_identity_and_dirtiness_use_one_git_process(monkeypatch):
    calls = []

    def git(*args, timeout, **kwargs):
        calls.append(args)
        assert timeout == 5
        return SimpleNamespace(stdout=f"# branch.oid {'a' * 40}\n# branch.head renamed\n")

    monkeypatch.setattr(finalized_checkout.git_ops, "git", git)
    record = SimpleNamespace(
        status="finalized", worktree_path="fixture",
        finalized_checkout={"head": "a" * 40, "branch": "renamed"},
    )
    assert finalized_checkout.matches(record)
    assert len(calls) == 1


@pytest.fixture(scope="module")
def finalized_repository_template(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("finalized-template")
    git = finalized_checkout.git_ops.git
    git("init", cwd=tmp_path)
    git("config", "user.name", "Fixture", cwd=tmp_path)
    git("config", "user.email", "fixture@example.invalid", cwd=tmp_path)
    git("config", "commit.gpgsign", "false", cwd=tmp_path)
    git("config", "core.hooksPath", str(tmp_path / "no-hooks"), cwd=tmp_path)
    (tmp_path / "tracked.txt").write_text("baseline", encoding="utf-8")
    git("add", "tracked.txt", cwd=tmp_path)
    git("commit", "-m", "fixture", cwd=tmp_path)
    return tmp_path


@pytest.fixture
def finalized_repository(tmp_path, finalized_repository_template):
    import shutil

    path = tmp_path / "repo"
    shutil.copytree(finalized_repository_template, path)
    return SimpleNamespace(
        status="finalized", worktree_path=str(path),
        finalized_checkout=finalized_checkout.snapshot(str(path)),
    )


@pytest.mark.guard
def test_real_git_snapshot_survives_roundtrip_and_repeated_polling(finalized_repository):
    import json

    record = finalized_repository
    record.finalized_checkout = json.loads(json.dumps(record.finalized_checkout))
    for _ in range(3):
        assert finalized_checkout.matches(record)


@pytest.mark.parametrize("change", [
    "untracked", "staged", pytest.param("modified", marks=pytest.mark.guard),
    "deleted", "head", "branch", "reopened",
])
def test_real_git_changed_checkout_invalidates_stamp(finalized_repository, change):
    from pathlib import Path

    record = finalized_repository
    git = finalized_checkout.git_ops.git
    path = Path(record.worktree_path)
    if change in ("untracked", "staged"):
        (path / "new.txt").write_text("new responsibility", encoding="utf-8")
        if change == "staged":
            git("add", "new.txt", cwd=path)
    elif change == "modified":
        (path / "tracked.txt").write_text("edited", encoding="utf-8")
    elif change == "deleted":
        (path / "tracked.txt").unlink()
    elif change == "head":
        git("commit", "--allow-empty", "-m", "new work", cwd=path)
    elif change == "branch":
        git("branch", "-m", "renamed", cwd=path)
    else:
        record.status = "active"
    assert not finalized_checkout.matches(record)


def test_real_detached_head_stamp_is_supported(finalized_repository):
    record = finalized_repository
    finalized_checkout.git_ops.git("switch", "--detach", cwd=record.worktree_path)
    record.finalized_checkout = finalized_checkout.snapshot(record.worktree_path)
    assert record.finalized_checkout["branch"] == ""
    assert finalized_checkout.matches(record)


@pytest.mark.parametrize("responsibility", [
    pytest.param("none", marks=pytest.mark.guard), "active-claim",
    pytest.param("at-rest-claim", marks=pytest.mark.guard), "released-claim",
    "settled-session", "follow-up", "open-pr", "handoff", "reopened",
])
def test_real_stamp_production_display_never_grants_cached_removal(
    finalized_repository, responsibility,
):
    from agent_worktrees import __main__ as cli, git_ops, prune, tracking
    from agent_worktrees.picker_support import derive

    fixture = finalized_repository
    record = tracking.WorktreeRecord(
        worktree_id="fixture", branch=fixture.finalized_checkout["branch"],
        worktree_path=fixture.worktree_path, repo="fixture-project", machine="fixture",
        platform="windows", started_at="", last_resumed_at="", resume_count=0,
        title=None, status="finalized", completed_at=None,
        finalized_checkout=fixture.finalized_checkout,
    )
    if responsibility.endswith("-claim"):
        record.resources = [
            tracking.ResourceClaim(
                kind="worktree", ref="fixture-child",
                state=responsibility.removesuffix("-claim"),
            ),
        ]
    elif responsibility == "settled-session":
        record.resources = [tracking.ResourceClaim(kind="session", ref="fixture", state="at-rest")]
    elif responsibility == "follow-up":
        record.follow_up = True
    elif responsibility == "open-pr":
        record.prs = [tracking.PRRecord(number=1, state="open")]
    elif responsibility == "handoff":
        record.handoffs = [
            tracking.SessionHandoff(
                ordinal=1, token="fixture", predecessor="fixture",
                state="pending", opened_at="2026-10-12T00:00:00",
            ),
        ]
    elif responsibility == "reopened":
        record.status = "active"
    expected_final = responsibility in {"none", "released-claim", "settled-session"}
    descriptor = cli._worktree_to_dict(
        record, state_info=git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED),
    )["closure"]
    assert descriptor["display"]["finalized"] is expected_final
    interpreted = prune.interpret_descriptor_payload(descriptor)
    assert interpreted["supported"]
    assert interpreted["final"] is False
    assert interpreted["action_disposition"] == "blocked"
    assert derive._state({"state": "completed", "closure": descriptor}) == (
        "FINAL" if expected_final else "MERGED"
    )
