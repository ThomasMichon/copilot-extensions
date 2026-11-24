"""Explicit native component qualification, separate from owner-absence proof."""

from __future__ import annotations

import os
import json
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable, cast

import pytest

from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import checkout_relocation_state as state
from agent_worktrees import checkout_durability as durability
from agent_worktrees import execution_spaces, git_ops, procs, retained_checkout, sessions, tracking
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


@pytest.mark.skipif(os.name != "nt", reason="native Windows relocation component")
@pytest.mark.timeout(120)
@pytest.mark.parametrize("phase", ["published", "git-repaired"])
def test_original_move_retries_after_git_repair_before_record_rebind(
    creator: CreatorFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, phase: str,
) -> None:
    source = Path(creator.child.worktree_path)
    destination = tmp_path / "new-root" / creator.child.worktree_id
    destination.parent.mkdir()
    future = tmp_path / "future-allocations"
    future.mkdir()
    creator.config = replace(creator.config, repos={
        "knowledge": replace(creator.config.default_repo, worktree_root=str(future)),
    })
    creator.configs["knowledge"] = creator.config
    git_ops.git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=creator.anchors["knowledge"])
    parent_before = creator.parent.yaml_path.read_bytes()
    ledger_before = creator.child.yaml_path.read_bytes()
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda _record, **_kwargs: sessions.LiveVerdict())
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda _path: None)
    files = durability.files
    save = tracking.save_record

    def interrupted_files(paths: Iterable[Path]) -> None:
        selected = tuple(paths)
        journal = state.read(creator.child.yaml_path)
        if journal is not None and journal["phase"] == "published" and destination / ".git" in selected:
            raise OSError("injected after Git repair")
        files(selected)

    def interrupted_save(record: tracking.WorktreeRecord, *args: object, **kwargs: object) -> None:
        if record.worktree_id == creator.child.worktree_id and record.worktree_path == str(destination):
            raise OSError("injected after Git repair")
        cast(Callable[..., None], save)(record, *args, **kwargs)

    with monkeypatch.context() as injection:
        if phase == "published":
            injection.setattr(durability, "files", interrupted_files)
        else:
            injection.setattr(tracking, "save_record", interrupted_save)
        with pytest.raises(OSError, match="injected after Git repair"):
            relocation.move(creator.child.worktree_id, str(destination), creator.config)
    journal = state.read(creator.child.yaml_path)
    assert journal is not None and journal["phase"] == phase
    assert creator.child.yaml_path.read_bytes() == ledger_before
    assert source.exists() and destination.exists()
    current = tracking.load_record(creator.child.yaml_path)
    assert not retained_checkout.matches(current, creator.config)
    if phase == "git-repaired":
        with pytest.raises(execution_spaces.ExecutionSpaceError, match="relocation is unfinished"):
            execution_spaces.require_record_mutation(current, creator.config, recover_receiving=False)
        path = state.journal_path(creator.child.yaml_path)
        original = path.read_bytes()
        for field, value in (
            ("phase", "copied"), ("ledger_sha256", "0" * 64), ("head", "0" * 40),
            ("anchor", str(tmp_path / "another-anchor")),
            ("identity", {**journal["identity"], "creation_nonce": "another-incarnation"}),
        ):
            path.write_text(json.dumps({**journal, field: value}), encoding="utf-8")
            with pytest.raises(ValueError, match="proven local creator"):
                relocation._receiving_admission(creator.child.yaml_path, creator.config)
            path.write_bytes(original)
        parent = tracking.load_record(creator.parent.yaml_path)
        parent.resources[0].state = "active"
        save(parent)
        with pytest.raises(ValueError, match="responsibility is missing, unsettled"):
            relocation._receiving_admission(creator.child.yaml_path, creator.config)
        parent.resources[0].state = "at-rest"
        save(parent)
    result = relocation.move(creator.child.worktree_id, str(destination), creator.config)
    assert result["phase"] == "complete" and result["stage"] == journal["stage"]
    assert not source.exists() and destination.exists()
    current = tracking.load_record(creator.child.yaml_path)
    assert current.worktree_path == str(destination)
    assert current.owner_ref == creator.child.owner_ref
    assert current.creation_nonce == creator.child.creation_nonce
    assert creator.parent.yaml_path.read_bytes() == parent_before
