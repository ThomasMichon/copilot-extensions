"""Short ``<codename>-<4hex>`` IDs for new session worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import codename, codename_tracking, config as cfg
from agent_worktrees import creation_allocation, execution_spaces, session_id_allocation, tracking
from agent_worktrees.creation_tail_repair import _allocation
from agent_worktrees.worktree_naming import (
    allocation_matches, codename_session_worktree_id, pair_stub,
    parse_codename_session_worktree_id, session_worktree_id,
)

OLD_ID = "host-win-20261123-111212-5906"
SYS_ID = "sys-daemon-20261123-111212-5906"


def _record(worktree_id: str, codename: str | None = None) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id=worktree_id, branch=f"worktree/{worktree_id}",
        worktree_path="/tmp/worktree", repo="project", machine="host-win",
        platform="windows", started_at="", last_resumed_at="", resume_count=0,
        title=None, status="active", completed_at=None, codename=codename,
    )


def _config(tmp_path: Path) -> Any:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    subprocess.run(["git", "init", "-q", str(anchor)], check=True)
    subprocess.run(
        ["git", "-C", str(anchor), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "--allow-empty", "-m", "init"], check=True,
    )
    root = tmp_path / "root"
    root.mkdir()
    return SimpleNamespace(
        repo_name="project",
        default_repo=SimpleNamespace(anchor=str(anchor), worktree_root=str(root), codename=None),
    )


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / "state" / str(name))
    monkeypatch.setattr(session_id_allocation, "_knowledge_namespace", lambda _c: None)


CUSTOM = codename.Wordlist(nouns=("potato",), adjectives=("thermal",))


def _draws(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    queue = list(names)
    monkeypatch.setattr(
        codename, "assign_codename",
        lambda taken, **_kw: next(n for n in queue if n not in taken),
    )


def test_codename_id_round_trip_and_old_formats_are_not_codename_ids() -> None:
    assert codename_session_worktree_id("thermal-potato", "461b") == "thermal-potato-461b"
    assert parse_codename_session_worktree_id("thermal-potato-461b") == ("thermal-potato", "461b")
    for other in (
        OLD_ID, SYS_ID, "thermal-potato-461b-k", "@anchor", "thermal-potato",
        "other-wsl-20261013-100000-abcd",
    ):
        assert parse_codename_session_worktree_id(other) is None
    with pytest.raises(ValueError):
        codename_session_worktree_id("Bad Name", "461b")


def test_allocation_matches_and_pair_stub_cover_both_formats() -> None:
    assert allocation_matches(OLD_ID, "20261123-111212", "5906")
    assert not allocation_matches(OLD_ID, "20261123-111213", "5906")
    assert allocation_matches("thermal-potato-461b", "20261123-111212", "461b")
    assert not allocation_matches("thermal-potato-461b", "20261123-111212", "461c")
    assert pair_stub(OLD_ID, "20261123-111212", "5906") == "20261123-111212-5906"
    assert pair_stub("thermal-potato-461b", "20261123-111212", "461b") == "thermal-potato-461b"
    assert pair_stub("thermal-potato-461b", None, "461b") == "thermal-potato-461b"
    with pytest.raises(ValueError, match="creation timestamp"):
        pair_stub(OLD_ID, None, "5906")


def test_tail_repair_recovers_codename_id_allocation_from_record() -> None:
    assert _allocation(_record("thermal-potato-461b")) == (None, "461b", "win")
    assert _allocation(_record(OLD_ID)) == ("20261123-111212", "5906", "win")


def test_allocate_skips_taken_codename_folder_and_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    records = tmp_path / "tracking"
    records.mkdir()
    tracking.save_record(_record(OLD_ID, "used-name"), records / f"{OLD_ID}.yaml")
    (Path(config.default_repo.worktree_root) / "has-folder-461b").mkdir()
    subprocess.run(
        ["git", "-C", config.default_repo.anchor, "branch", "worktree/has-branch-461b"], check=True,
    )
    _draws(monkeypatch, "used-name", "has-folder", "has-branch", "free-name")
    assert session_id_allocation.allocate(config, "461b", tracking_path=records) == "free-name-461b"


def test_pending_requests_never_share_an_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    records = tmp_path / "tracking"
    _draws(monkeypatch, "same-name", "other-name")
    first = session_id_allocation.allocate(config, "461b", tracking_path=records)
    second = session_id_allocation.allocate(config, "461b", tracking_path=records)
    assert (first, second) == ("same-name-461b", "other-name-461b")


def test_reservations_are_pruned_once_recorded_or_abandoned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    config = _config(tmp_path)
    records = tmp_path / "tracking"
    records.mkdir()
    _draws(monkeypatch, "recorded", "abandoned", "pending", "next")
    for _ in range(3):
        session_id_allocation.allocate(config, "461b", tracking_path=records)
    reserved = tmp_path / "reserved-worktree-ids"
    tracking.save_record(_record("recorded-461b", "recorded"), records / "recorded-461b.yaml")
    os.utime(reserved / "abandoned-461b", (0, 0))
    # The abandoned name is free again; the recorded one stays taken by its record.
    assert session_id_allocation.allocate(config, "461b", tracking_path=records) == "abandoned-461b"
    assert sorted(p.name for p in reserved.iterdir()) == ["abandoned-461b", "pending-461b"]


def test_allocate_fails_closed_when_branches_cannot_be_inspected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import git_ops

    def broken(*_a: Any, **_k: Any) -> Any:
        raise OSError("git unavailable")

    config = _config(tmp_path)
    monkeypatch.setattr(git_ops, "git", broken)
    with pytest.raises(RuntimeError, match="cannot inspect worktree branches"):
        session_id_allocation.allocate(config, "461b", tracking_path=tmp_path / "tracking")


def test_harness_projects_sharing_knowledge_get_distinct_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    knowledge = _config(knowledge_root)
    shared = (knowledge, knowledge_root / "tracking")
    (Path(knowledge.default_repo.worktree_root) / "in-knowledge-461b-k").mkdir()
    harness_a, harness_b = tmp_path / "a", tmp_path / "b"
    harness_a.mkdir()
    harness_b.mkdir()
    _draws(monkeypatch, "in-knowledge", "same-name", "other-name")
    first = session_id_allocation.allocate(
        _config(harness_a), "461b", tracking_path=harness_a / "tracking", knowledge=shared,
    )
    second = session_id_allocation.allocate(
        _config(harness_b), "461b", tracking_path=harness_b / "tracking", knowledge=shared,
    )
    assert (first, second) == ("same-name-461b", "other-name-461b")


def test_codename_for_uses_id_codename_and_recovers_from_a_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    records = tmp_path / "tracking"
    records.mkdir()
    _draws(monkeypatch, "fresh-name")
    monkeypatch.setattr(
        codename_tracking, "assign_new_codename", lambda *_a: "fresh-name",
    )
    assert session_id_allocation.codename_for("thermal-potato-461b", records, CUSTOM) == "thermal-potato"
    assert session_id_allocation.codename_for(OLD_ID, records, None) == "fresh-name"
    tracking.save_record(_record("thermal-potato-0000", "thermal-potato"), records / "x.yaml")
    assert session_id_allocation.codename_for("thermal-potato-461b", records, CUSTOM) == "fresh-name"
    assert "is taken or outside the current vocabulary" in capsys.readouterr().err


def test_knowledge_sibling_prefers_harness_codename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = tmp_path / "tracking"
    records.mkdir()
    monkeypatch.setattr(
        codename_tracking, "assign_new_codename", lambda *_a: "other-name",
    )
    assert session_id_allocation.knowledge_codename("thermal-potato-461b", records, CUSTOM) == "thermal-potato"
    assert session_id_allocation.knowledge_codename(OLD_ID, records, None) == "other-name"
    tracking.save_record(_record("thermal-potato-0000-k", "thermal-potato"), records / "k.yaml")
    assert session_id_allocation.knowledge_codename("thermal-potato-461b", records, CUSTOM) == "other-name"


def test_codename_outside_current_vocabulary_is_not_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = tmp_path / "tracking"
    records.mkdir()
    monkeypatch.setattr(codename_tracking, "assign_new_codename", lambda *_a: "rusty-gizmo")
    # A custom-vocabulary harness name is not copied into a built-in project,
    # and a removed custom vocabulary does not leave a custom name labeled built-in.
    assert session_id_allocation.knowledge_codename("thermal-potato-461b", records, None) == "rusty-gizmo"
    assert session_id_allocation.codename_for("thermal-potato-461b", records, None) == "rusty-gizmo"
    paired = codename.Wordlist(nouns=("potato",), pairs=(("thermal", "potato"),))
    assert session_id_allocation.codename_for("thermal-potato-461b", records, paired) == "thermal-potato"
    assert session_id_allocation.codename_for("potato-461b", records, CUSTOM) == "potato"


def _fields(worktree_id: str, suffix: str = "461b") -> dict[str, Any]:
    return {
        "version": 1, "project": "project", "anchor": "/a", "worktree_root": "/r",
        "worktree_id": worktree_id, "branch": f"worktree/{worktree_id}",
        "path": str(Path("/r") / worktree_id), "machine": "host", "platform": "windows",
        "timestamp": "20261123-111212", "suffix": suffix,
    }


def test_admitted_allocation_accepts_codename_and_old_ids() -> None:
    assert creation_allocation.AdmittedAllocation.from_fields(
        _fields("thermal-potato-461b"),
    ).worktree_id == "thermal-potato-461b"
    assert creation_allocation.AdmittedAllocation.from_fields(
        _fields("host-win-20261123-111212-461b"),
    ).worktree_id == "host-win-20261123-111212-461b"
    for bad in ("thermal-potato-461c", "other-host-win-20261123-111212-461b"):
        with pytest.raises(ValueError):
            creation_allocation.AdmittedAllocation.from_fields(_fields(bad))
    broken = {**_fields("thermal-potato-461b"), "branch": "worktree/other-461b"}
    with pytest.raises(ValueError):
        creation_allocation.AdmittedAllocation.from_fields(broken)


def test_fresh_session_creation_uses_codename_id_but_replays_keep_old_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(session_id_allocation, "allocate", lambda _c, suffix: f"new-name-{suffix}")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    config.machine = "host-win"
    common: dict[str, Any] = {
        "kind": "session", "machine_identity": None, "timestamp": "20261123-111212",
        "suffix": "461b", "system_slug": "", "admitted": None,
    }
    wid, branch, path = creation_allocation.creation_parameters(config, partial=False, **common)
    assert (wid, branch) == ("new-name-461b", "worktree/new-name-461b")
    assert path == str(Path(config.default_repo.worktree_root) / wid)
    replay, _, _ = creation_allocation.creation_parameters(config, partial=True, **common)
    assert replay == "host-win-20261123-111212-461b"
    system, _, _ = creation_allocation.creation_parameters(
        config, partial=False, **{**common, "kind": "system", "system_slug": "daemon"},
    )
    assert system == "sys-daemon-20261123-111212-461b"


def test_admission_writer_names_new_worktree_from_codename_and_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.machine = "host-win"
    monkeypatch.setattr(execution_spaces, "require_current_execution_space", lambda _c: None)
    monkeypatch.setattr(creation_allocation, "new_machine_identity", lambda _c, _m: None)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(creation_allocation.AdmittedAllocation, "require_current", lambda _s, _c: None)
    _draws(monkeypatch, "fresh-name")
    old_caller = creation_allocation.admit(config, "20261021-010203", "abcd", "legacy")
    assert old_caller.worktree_id == session_worktree_id("host-win", "windows", "20261021-010203", "abcd")
    allocation = creation_allocation.admit(
        config, "20261021-010203", "abcd", "legacy", codename_id=True,
    )
    assert allocation.worktree_id == "fresh-name-abcd"
    assert allocation.branch == "worktree/fresh-name-abcd"
    assert allocation.path == str(Path(config.default_repo.worktree_root) / "fresh-name-abcd")
    assert (allocation.timestamp, allocation.suffix) == ("20261021-010203", "abcd")
