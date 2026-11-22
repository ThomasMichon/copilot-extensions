"""Existing-task recovery and pair-scoped removal through native consumers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
import test_creation_tail_repair as fixture_source

from agent_worktrees import cleanup_gc_cli, cleanup_reap, context_cli, gc, git_ops
from agent_worktrees import config as cfg
from agent_worktrees import launch_admission, launch_seed_state, paired_cleanup, procs
from agent_worktrees import sessions, tracking, worktree_creation, worktree_lifecycle

_creation_anchor_seed = fixture_source._creation_anchor_seed


@pytest.fixture
def existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _creation_anchor_seed: Path,
) -> SimpleNamespace:
    build = cast(Callable[..., SimpleNamespace], fixture_source._creation_fixture)
    fixture = build(tmp_path, monkeypatch, anchor_seed=_creation_anchor_seed)
    for anchor in fixture.anchors.values():
        exclude = anchor / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("\n.github/copilot/settings.local.json\n")
    launch_seed_state.take(fixture.record.yaml_path, seed_id=fixture.seed.seed_id)
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda record, **kw: sessions.LiveVerdict())
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: set())
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda records: sessions.SessionContext())
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: None)
    return fixture


def _recover(fixture: SimpleNamespace, *, apply: bool = True) -> tuple[int, dict[str, Any]]:
    from agent_worktrees import output

    with output.capture_json_output() as stream:
        code = context_cli.cmd_knowledge_dispatch([
            "repair-pair", "--worktree-id", fixture.record.worktree_id, "--json",
            *(["--apply", "--creation-nonce", fixture.record.creation_nonce] if apply else []),
        ])
    return code, json.loads(stream.getvalue())


def _pair(fixture: SimpleNamespace) -> tuple[tracking.WorktreeRecord, tracking.WorktreeRecord]:
    code, report = _recover(fixture)
    assert code == 0, json.dumps(report)
    parent = tracking.load_record(fixture.record.yaml_path)
    child = tracking.find_paired_record(parent)
    assert child is not None
    return parent, child


def _finalized_pair(fixture: SimpleNamespace) -> tuple[tracking.WorktreeRecord, tracking.WorktreeRecord]:
    parent, child = _pair(fixture)
    # Explicitly dispose only the synthetic overlay produced by this fixture.
    (Path(parent.worktree_path) / ".github" / "copilot" / "settings.local.json").unlink()
    for member in (parent, child):
        assert git_ops.is_clean(cwd=member.worktree_path), git_ops.get_dirty_files(member.worktree_path)
    for anchor in fixture.anchors.values():
        git_ops.git("fetch", "origin", "main", cwd=anchor)
    child.status = "finalized"
    tracking.save_record(child)
    assert parent.pair_ref is not None
    tracking.settle_resource_claim(parent, parent.pair_ref)
    tracking.release_resource_claim(parent, parent.pair_ref)
    parent = tracking.load_record(parent.yaml_path)
    parent.status = "finalized"
    tracking.save_record(parent)
    return parent, child


def _joint_cli(
    parent: tracking.WorktreeRecord, capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any]]:
    from agent_worktrees import output

    parser = argparse.ArgumentParser()
    cleanup_gc_cli.add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["cleanup", "--worktree-id", parent.worktree_id, "--paired", "--json"])
    with output.capture_json_output() as stream:
        code = cleanup_gc_cli._cleanup_one(args)
    capsys.readouterr()
    return code, json.loads(stream.getvalue())


@pytest.mark.parametrize("seed_state", ["consumed", "absent"])
def test_live_existing_harness_recovers_without_new_and_replays(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, seed_state: str,
) -> None:
    seed_path = launch_seed_state.state_path(existing.record.yaml_path)
    if seed_state == "absent":
        seed_path.unlink()
    before_seed = seed_path.read_bytes() if seed_path.exists() else None
    before_record = existing.record.yaml_path.read_bytes()
    monkeypatch.setattr(
        sessions, "verify_worktree_active",
        lambda record, **kw: sessions.LiveVerdict(active=record.repo == "harness"),
    )
    code, report = _recover(existing, apply=False)
    assert code == 0 and report["action"] == "allocate" and not report["applied"]
    assert existing.record.yaml_path.read_bytes() == before_record
    parent, child = _pair(existing)
    nonce = child.creation_nonce
    code, report = _recover(existing)
    assert code == 0 and report["action"] == "compose"
    assert tracking.load_record(child.yaml_path).creation_nonce == nonce
    sibling = tracking.find_paired_record(parent)
    assert sibling is not None and sibling.worktree_path == child.worktree_path
    assert (seed_path.read_bytes() if seed_path.exists() else None) == before_seed


def test_lost_checkout_restores_original_retained_branch(existing: SimpleNamespace) -> None:
    parent, child = _pair(existing)
    target = Path(child.worktree_path)
    (target / "retained.txt").write_text("retained branch content", encoding="utf-8")
    git_ops.git("add", "retained.txt", cwd=target)
    git_ops.git(
        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "retained", cwd=target,
    )
    original = git_ops.git("rev-parse", "HEAD", cwd=target).stdout.strip()
    shutil.rmtree(target)
    code, report = _recover(existing)
    assert code == 0, json.dumps(report)
    assert report["action"] == "restore-retained-branch"
    assert (target / "retained.txt").read_text(encoding="utf-8") == "retained branch content"
    assert git_ops.git("rev-parse", "HEAD", cwd=target).stdout.strip() == original
    assert tracking.load_record(child.yaml_path).creation_nonce == child.creation_nonce
    sibling = tracking.find_paired_record(parent)
    assert sibling is not None and sibling.worktree_path == str(target)


@pytest.mark.parametrize("operation", ["recover", "cleanup"])
def test_pair_operations_exclude_inherited_foreign_git_identity(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], operation: str,
) -> None:
    parent, child = _pair(existing) if operation == "recover" else _finalized_pair(existing)
    original = git_ops.git("rev-parse", "HEAD", cwd=child.worktree_path).stdout.strip()
    foreign = existing.anchors["harness"].parent / "foreign"
    foreign.mkdir()
    git_ops.git("init", "--template=", "-b", "main", cwd=foreign)
    git_ops.git(
        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "foreign", cwd=foreign,
    )
    git_ops.git("checkout", "-b", parent.branch, cwd=foreign)
    git_ops.git("branch", child.branch, cwd=foreign)
    foreign_tip = git_ops.git("rev-parse", "HEAD", cwd=foreign).stdout.strip()
    if operation == "recover":
        shutil.rmtree(child.worktree_path)
    with monkeypatch.context() as environment:
        environment.setenv("GIT_DIR", str(foreign / ".git"))
        environment.setenv("GIT_WORK_TREE", str(foreign))
        code, report = _recover(existing) if operation == "recover" else _joint_cli(parent, capsys)
    assert code == 0, json.dumps(report)
    if operation == "recover":
        assert git_ops.git("rev-parse", "HEAD", cwd=child.worktree_path).stdout.strip() == original
    else:
        assert not Path(parent.worktree_path).exists() and not Path(child.worktree_path).exists()
    assert git_ops.git("rev-parse", child.branch, cwd=foreign).stdout.strip() == foreign_tip


def test_late_binding_reserves_the_receivers_current_allocation_root(existing: SimpleNamespace) -> None:
    receiving = existing.configs["knowledge"]
    target_root = existing.anchors["knowledge"].parent / "independent-knowledge-root"
    existing.configs["knowledge"] = replace(receiving, repos={
        "knowledge": replace(receiving.default_repo, worktree_root=str(target_root)),
    })
    _, child = _pair(existing)
    assert Path(child.worktree_path).parent == target_root
    assert existing.record.yaml_path.with_suffix(".paired-allocation.json").is_file()


def test_partial_pair_publication_reuses_original_child(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = worktree_creation._stamp_and_compose_paired_knowledge
    monkeypatch.setattr(
        worktree_creation, "_stamp_and_compose_paired_knowledge",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture stamp failure")),
    )
    code, report = _recover(existing)
    assert code == 1 and "stamp failure" in report["error"]
    children = list(cfg.tracking_dir("knowledge").glob("*.yaml"))
    assert len(children) == 1
    original_child = tracking.load_record(children[0])
    monkeypatch.setattr(worktree_creation, "_stamp_and_compose_paired_knowledge", original)
    monkeypatch.setattr(
        worktree_creation, "_carve_paired_knowledge",
        lambda *a, **kw: pytest.fail("allocated over retained companion"),
    )
    _, child = _pair(existing)
    assert child.creation_nonce == original_child.creation_nonce


@pytest.mark.parametrize("kind", ["dirty", "missing-record", "live", "unknown", "lost-branch"])
def test_retained_companion_conflicts_preserve_evidence(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    _, child = _pair(existing)
    target = Path(child.worktree_path)
    if kind == "dirty":
        (target / "private.txt").write_text("retain", encoding="utf-8")
    elif kind == "missing-record":
        child.yaml_path.unlink()
    elif kind in {"live", "unknown"}:
        monkeypatch.setattr(
            sessions, "verify_worktree_active",
            lambda record, **kw: sessions.LiveVerdict(active=kind == "live", probes_ok=kind != "unknown"),
        )
    else:
        git_ops.git("worktree", "remove", "--force", str(target), cwd=existing.anchors["knowledge"])
        git_ops.delete_branch(child.branch, cwd=existing.anchors["knowledge"], force=True)
    before = existing.record.yaml_path.read_bytes()
    code, report = _recover(existing)
    assert code == 1 and report["error"] and not report["applied"]
    assert existing.record.yaml_path.read_bytes() == before
    if kind == "dirty":
        assert (target / "private.txt").read_text(encoding="utf-8") == "retain"


@pytest.mark.parametrize("lease", [launch_admission.guard, worktree_lifecycle.guard])
def test_restore_obeys_native_launch_and_lifecycle_admission(
    existing: SimpleNamespace, lease: Callable[[Path], AbstractContextManager[None]],
) -> None:
    with lease(existing.record.yaml_path):
        code, report = _recover(existing)
    assert code == 1 and report["error"]
    assert not (existing.roots["knowledge"] / f"{existing.record.worktree_id}-k").exists()


def test_companion_only_cleanup_and_orphan_gc_refuse(existing: SimpleNamespace) -> None:
    _, child = _finalized_pair(existing)
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    failures, warnings = cleanup_reap.reap_worktree(
        child, info, existing.configs["knowledge"].default_repo, child.yaml_path.parent,
    )
    assert failures and "admitted together" in warnings[0]
    assert not gc.remove_tree(Path(child.worktree_path))[0]
    assert Path(child.worktree_path).is_dir() and child.yaml_path.exists()


@pytest.mark.parametrize("signal", ["mux", "lock", "unknown", "process"])
def test_finalized_live_harness_vetoes_joint_removal(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], signal: str,
) -> None:
    parent, child = _finalized_pair(existing)
    if signal == "process":
        monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: "fixture process uses checkout")
    else:
        monkeypatch.setattr(
            sessions, "verify_worktree_active",
            lambda record, **kw: sessions.LiveVerdict(
                active=record.repo == "harness" and signal != "unknown",
                mux_live=signal == "mux", live_session_ids=["fixture"] if signal == "lock" else [],
                probes_ok=signal != "unknown",
            ),
        )
    code, report = _joint_cli(parent, capsys)
    assert code == 1 and not report["removed"]
    expected_reason = "fixture process" if signal == "process" else "execution is live or unknown"
    assert expected_reason in report["reason"], json.dumps(report)
    assert Path(parent.worktree_path).exists() and Path(child.worktree_path).exists()


def test_joint_cleanup_admits_both_members_and_removes_together(
    existing: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    parent, child = _finalized_pair(existing)
    code, report = _joint_cli(parent, capsys)
    assert code == 0, json.dumps(report)
    assert report["removed"]
    assert not Path(parent.worktree_path).exists() and not Path(child.worktree_path).exists()
    assert not paired_cleanup.journal_path(parent).exists()
    for member in (parent, child):
        retained = tracking.load_record(member.yaml_path)
        assert retained.reaped_at and retained.creation_nonce == member.creation_nonce


def test_interrupted_joint_cleanup_preserves_records_and_can_retry(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    parent, child = _finalized_pair(existing)
    remove = git_ops.remove_worktree

    def fail_second(anchor: str | Path, path: str) -> bool:
        if path == child.worktree_path:
            raise OSError("fixture second-half failure")
        return remove(anchor, path)

    monkeypatch.setattr(git_ops, "remove_worktree", fail_second)
    code, report = _joint_cli(parent, capsys)
    assert code == 1 and not report["removed"]
    assert parent.yaml_path.exists() and child.yaml_path.exists()
    assert paired_cleanup.journal_path(parent).exists(), json.dumps(report)
    assert not Path(parent.worktree_path).exists(), json.dumps(report)
    assert Path(child.worktree_path).exists()
    monkeypatch.setattr(git_ops, "remove_worktree", remove)
    code, report = _joint_cli(parent, capsys)
    assert code == 0, json.dumps(report)


def test_joint_removal_holds_both_record_fences(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    parent, child = _finalized_pair(existing)
    remove = git_ops.remove_worktree
    observed = []

    def remove_under_fence(anchor: str | Path, path: str) -> bool:
        for member in (parent, child):
            def attempt_write() -> None:
                try:
                    with tracking._RecordLock(member.yaml_path, timeout=0.05, require_sidecar=True):
                        observed.append("unfenced")
                except TimeoutError:
                    observed.append("fenced")
            peer = threading.Thread(target=attempt_write)
            peer.start()
            peer.join(2)
            assert not peer.is_alive()
        return remove(anchor, path)

    monkeypatch.setattr(git_ops, "remove_worktree", remove_under_fence)
    code, report = _joint_cli(parent, capsys)
    assert code == 0, json.dumps(report)
    assert observed == ["fenced"] * 4


def test_companion_ignored_data_is_admitted_before_either_removal(
    existing: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    parent, child = _finalized_pair(existing)
    with (existing.anchors["knowledge"] / ".git" / "info" / "exclude").open("a") as stream:
        stream.write("\nretained.bin\n")
    retained = Path(child.worktree_path) / "retained.bin"
    retained.write_bytes(b"retain this fixture evidence")
    code, report = _joint_cli(parent, capsys)
    assert code == 1 and "ignored data" in report["reason"], json.dumps(report)
    assert Path(parent.worktree_path).exists() and Path(child.worktree_path).exists()
    assert retained.read_bytes() == b"retain this fixture evidence"
    assert not paired_cleanup.journal_path(parent).exists()


def test_missing_parent_without_joint_receipt_does_not_authorize_child_removal(
    existing: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    parent, child = _finalized_pair(existing)
    shutil.rmtree(parent.worktree_path)
    code, report = _joint_cli(parent, capsys)
    assert code == 1 and not report["removed"]
    assert "original receiving allocation or proven local parent" in report["reason"]
    assert Path(child.worktree_path).exists() and child.yaml_path.exists()


@pytest.mark.parametrize("lease", [launch_admission.guard, worktree_lifecycle.guard])
def test_joint_cleanup_cannot_race_harness_launch(
    existing: SimpleNamespace, lease: Callable[[Path], AbstractContextManager[None]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    parent, child = _finalized_pair(existing)
    with lease(parent.yaml_path):
        code, report = _joint_cli(parent, capsys)
    assert code == 1 and not report["removed"]
    assert "busy" in report["reason"].lower() or "lock" in report["reason"].lower()
    assert Path(parent.worktree_path).exists() and Path(child.worktree_path).exists()


def test_new_companion_obligation_at_joint_admission_preserves_both(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    parent, child = _finalized_pair(existing)
    original = cleanup_gc_cli._revalidate_cleanup_safety

    def race(worktree_id: str, **kwargs: Any) -> cleanup_gc_cli.RevalidationResult:
        if worktree_id == child.worktree_id:
            current = tracking.load_record(child.yaml_path)
            current.follow_up = True
            tracking.save_record(current)
        return original(worktree_id, **kwargs)

    monkeypatch.setattr(cleanup_gc_cli, "_revalidate_cleanup_safety", race)
    code, report = _joint_cli(parent, capsys)
    assert code == 1 and not report["removed"]
    assert "follow" in report["reason"].lower()
    assert Path(parent.worktree_path).exists() and Path(child.worktree_path).exists()
