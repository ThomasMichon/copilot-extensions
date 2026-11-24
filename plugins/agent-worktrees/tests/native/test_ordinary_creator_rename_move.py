"""Explicit native component qualification, separate from owner-absence proof."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Callable, cast

import pytest

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import git_ops, procs, sessions, tracking
from test_relocation_creator_rename import CreatorFixture, creator as creator


@pytest.mark.skipif(os.name != "nt", reason="native Windows relocation component")
@pytest.mark.timeout(120)
@pytest.mark.parametrize("changed_responsibility", [False, True])
def test_native_component_move_preserves_renamed_creator_and_original_claim(
    creator: CreatorFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    changed_responsibility: bool,
) -> None:
    source = Path(creator.child.worktree_path)
    destination = tmp_path / "new-root" / creator.child.worktree_id
    destination.parent.mkdir()
    if changed_responsibility:
        future = tmp_path / "future-allocations"
        future.mkdir()
        creator.config = replace(creator.config, repos={
            "knowledge": replace(creator.config.default_repo, worktree_root=str(future)),
        })
        creator.configs["knowledge"] = creator.config
    git_ops.git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=creator.anchors["knowledge"])
    parent_before = creator.parent.yaml_path.read_bytes()
    original_owner = creator.child.owner_ref
    original_nonce = creator.child.creation_nonce
    observed: list[str] = []

    def inactive(record: tracking.WorktreeRecord, *, strict: bool = False) -> sessions.LiveVerdict:
        assert record.worktree_id == creator.child.worktree_id and strict
        observed.append(record.worktree_path)
        return sessions.LiveVerdict()

    # Native owner absence is an independent contract, not this component's proof.
    monkeypatch.setattr(sessions, "verify_worktree_active", inactive)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda _path: None)
    if changed_responsibility:
        save = tracking.save_record

        def reactivated(record: tracking.WorktreeRecord, *args: object, **kwargs: object) -> None:
            if record.worktree_id == creator.child.worktree_id and record.worktree_path == str(destination):
                parent = tracking.load_record(creator.parent.yaml_path)
                parent.resources[0].state = "active"
                save(parent)
            cast(Callable[..., None], save)(record, *args, **kwargs)

        with monkeypatch.context() as injection:
            injection.setattr(tracking, "save_record", reactivated)
            with pytest.raises(ValueError, match="responsibility is missing, unsettled"):
                relocation.move(creator.child.worktree_id, str(destination), creator.config)
        assert source.exists() and destination.exists()
        parent = tracking.load_record(creator.parent.yaml_path)
        parent.resources[0].state = "at-rest"
        save(parent)
    result = relocation.move(creator.child.worktree_id, str(destination), creator.config)
    assert result["phase"] == "complete" and observed
    current = tracking.load_record(creator.child.yaml_path)
    assert current.worktree_path == str(destination) and not source.exists()
    assert current.creation_nonce == original_nonce and current.owner_ref == original_owner
    assert creator.parent.yaml_path.read_bytes() == parent_before
    assert not git_ops.git("status", "--porcelain", cwd=destination).stdout
