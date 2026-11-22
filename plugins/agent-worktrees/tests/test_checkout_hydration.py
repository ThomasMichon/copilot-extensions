"""Real-git round trips for manual checkout dehydration (Phase 3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import checkout_hydration as hydration
from agent_worktrees import checkout_hydration_catalog as catalog
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, prune, procs, sessions, tracking

WT_ID = "fixture-win-20261121-100000-abcd"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    base = tmp_path.resolve()
    monkeypatch.setattr(cfg, "_home", lambda: base / "home")
    records = base / "tracking"
    records.mkdir()
    monkeypatch.setattr(cfg, "tracking_dir", lambda _repo=None: records)
    monkeypatch.setattr(
        sessions, "verify_worktree_active",
        lambda *_a, **_k: SimpleNamespace(probes_ok=True, active=False),
    )
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda *_a, **_k: None)
    anchor = base / "anchor"
    anchor.mkdir()
    _git(anchor, "init", "-q", "-b", "main")
    _git(anchor, "config", "user.email", "t@example.invalid")
    _git(anchor, "config", "user.name", "t")
    (anchor / ".gitignore").write_text("build/\n.effort-agenda\n", encoding="utf-8")
    (anchor / "a.txt").write_text("a\n", encoding="utf-8")
    _git(anchor, "add", ".")
    _git(anchor, "commit", "-q", "-m", "init")
    root = base / "worktrees"
    root.mkdir()
    checkout = root / WT_ID
    branch = f"worktree/{WT_ID}"
    _git(anchor, "worktree", "add", "-q", "-b", branch, str(checkout))
    _git(checkout, "config", "user.email", "t@example.invalid")
    _git(checkout, "config", "user.name", "t")
    (checkout / "wip.txt").write_text("wip\n", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "wip")
    ledger = records / f"{WT_ID}.yaml"
    record = tracking.WorktreeRecord(
        worktree_id=WT_ID, branch=branch, worktree_path=str(checkout), repo="sample",
        machine="fixture", platform="windows", started_at="2026-11-21T10:00:00Z",
        last_resumed_at="2026-11-21T10:00:00Z", resume_count=0, title="t", status="active",
        completed_at=None,
    )
    tracking.save_record(record, ledger)
    config = SimpleNamespace(
        repo_name="sample",
        default_repo=SimpleNamespace(
            anchor=str(anchor), worktree_root=str(root), remote="origin", default_branch="main",
        ),
    )
    return SimpleNamespace(
        anchor=anchor, root=root, checkout=checkout, branch=branch, ledger=ledger, config=config,
        entry=catalog.entry_path(root, WT_ID), head=_git(checkout, "rev-parse", "HEAD"),
    )


def _registered(env: SimpleNamespace) -> bool:
    return str(env.checkout).replace("\\", "/").lower() in _git(env.anchor, "worktree", "list").replace(
        "\\", "/").lower()


def _state(env: SimpleNamespace) -> Any:
    return (tracking.load_record(env.ledger).hydration or {}).get("state")


def test_round_trip_preserves_branch_identity_and_removes_ignored_files(env: SimpleNamespace) -> None:
    (env.checkout / "build").mkdir()
    (env.checkout / "build" / "out.bin").write_text("artifact", encoding="utf-8")
    assert hydration.plan(WT_ID, env.config)["admissible"] is True

    result = hydration.dehydrate(WT_ID, env.config)

    assert result["state"] == "dehydrated" and result["head"] == env.head
    assert not env.checkout.exists() and not _registered(env)
    assert _git(env.anchor, "rev-parse", env.branch) == env.head
    entry = catalog.read_entry(env.entry)
    assert entry is not None and entry["stage"] == "dehydrated"
    assert entry["identity"]["checkout_path"] == str(env.checkout)
    assert entry["git"]["head"] == env.head
    record = tracking.load_record(env.ledger)
    assert record.status == "active" and catalog.is_dehydrated(record)
    assert git_ops.classify_worktree(str(env.checkout), env.branch, fetch=False).state == git_ops.WorktreeState.DEHYDRATED

    restored = hydration.rehydrate(WT_ID, env.config)

    assert restored["state"] == "hydrated" and restored["head"] == env.head
    assert _git(env.checkout, "symbolic-ref", "HEAD") == f"refs/heads/{env.branch}"
    assert (env.checkout / "wip.txt").read_text(encoding="utf-8") == "wip\n"
    assert not (env.checkout / "build").exists()
    assert not env.entry.exists() and _state(env) == "hydrated"
    assert not catalog.is_dehydrated(tracking.load_record(env.ledger))


@pytest.mark.parametrize("form", ["untracked", "modified", "staged"])
def test_dirty_checkout_refuses_without_writing_catalog(env: SimpleNamespace, form: str) -> None:
    if form == "untracked":
        (env.checkout / "new.txt").write_text("x", encoding="utf-8")
    else:
        (env.checkout / "a.txt").write_text("changed\n", encoding="utf-8")
        if form == "staged":
            _git(env.checkout, "add", "a.txt")
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "dirty-checkout"
    assert env.checkout.exists() and not env.entry.exists() and _state(env) is None


@pytest.mark.parametrize("form,code", [("detached", "detached-head"), ("switched", "branch-mismatch")])
def test_wrong_head_refuses(env: SimpleNamespace, form: str, code: str) -> None:
    if form == "detached":
        _git(env.checkout, "checkout", "-q", "--detach")
    else:
        _git(env.checkout, "checkout", "-q", "-b", "other")
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == code and env.checkout.exists() and not env.entry.exists()


def test_checkout_local_agenda_hold_refuses(env: SimpleNamespace) -> None:
    (env.checkout / ".effort-agenda").mkdir()
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "retained-data" and env.checkout.exists()


def test_live_session_refuses(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sessions, "verify_worktree_active",
        lambda *_a, **_k: SimpleNamespace(probes_ok=False, active=False),
    )
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "live-session" and env.checkout.exists()


def test_branch_survives_gc_while_dehydrated(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    _git(env.anchor, "worktree", "prune")
    _git(env.anchor, "gc", "-q", "--prune=now")
    assert _git(env.anchor, "cat-file", "-t", env.head) == "commit"
    assert _git(env.anchor, "rev-parse", env.branch) == env.head
    assert hydration.rehydrate(WT_ID, env.config)["state"] == "hydrated"


def test_moved_branch_tip_is_a_conflict(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    _git(env.anchor, "branch", "-f", env.branch, "main")
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.rehydrate(WT_ID, env.config)
    assert caught.value.code == "branch-tip-conflict"
    assert not env.checkout.exists() and env.entry.exists()
    assert _git(env.anchor, "rev-parse", env.branch) != env.head


def test_occupied_destination_refuses(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    env.checkout.mkdir()
    (env.checkout / "stray").write_text("x", encoding="utf-8")
    report = hydration.reconcile(WT_ID, env.config)
    assert report["state"] == "hold"
    with pytest.raises(hydration.HydrationRefused):
        hydration.rehydrate(WT_ID, env.config)
    assert (env.checkout / "stray").exists()


def _interrupt_before_remove(env: SimpleNamespace) -> None:
    """Leave the exact state a crash after the catalog write would leave."""
    snapshot = hydration._inspect(tracking.load_record(env.ledger), env.checkout, env.anchor)
    catalog.write_entry(env.entry, {
        "schema": catalog.SCHEMA, "schema_version": catalog.SCHEMA_VERSION, "operation_id": "op",
        "stage": catalog.STAGE_REMOVING, "prior_state": {},
        "identity": {"repo": "sample", "worktree_id": WT_ID, "checkout_path": str(env.checkout)},
        "git": snapshot, "agency": {},
    })
    hydration._set_hydration(env.ledger, {"state": catalog.STAGE_REMOVING, "catalog": str(env.entry)})


def test_interrupted_dehydrate_with_checkout_present_rolls_back(env: SimpleNamespace) -> None:
    _interrupt_before_remove(env)
    assert git_ops.classify_worktree(str(env.checkout), env.branch, fetch=False).state != git_ops.WorktreeState.GONE
    report = hydration.reconcile(WT_ID, env.config)
    assert report["reconciled"] == "interrupted-dehydrate-rolled-back"
    assert env.checkout.exists() and not env.entry.exists() and _state(env) == "hydrated"


def test_interrupted_dehydrate_after_removal_completes(env: SimpleNamespace) -> None:
    _interrupt_before_remove(env)
    _git(env.anchor, "worktree", "remove", str(env.checkout))
    report = hydration.reconcile(WT_ID, env.config)
    assert report["reconciled"] == "interrupted-dehydrate-completed"
    assert (catalog.read_entry(env.entry) or {})["stage"] == "dehydrated" and _state(env) == "dehydrated"


def test_interrupted_rehydrate_completes_without_removing_checkout(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    entry = catalog.read_entry(env.entry) or {}
    entry["stage"] = catalog.STAGE_REHYDRATING
    catalog.write_entry(env.entry, entry)
    _git(env.anchor, "worktree", "add", "-q", str(env.checkout), env.branch)
    report = hydration.reconcile(WT_ID, env.config)
    assert report["reconciled"] == "interrupted-rehydrate-completed"
    assert env.checkout.exists() and not env.entry.exists() and _state(env) == "hydrated"


def test_dehydrated_worktree_is_never_collectable(env: SimpleNamespace) -> None:
    from agent_worktrees import gc

    hydration.dehydrate(WT_ID, env.config)
    record = tracking.load_record(env.ledger)
    record.status = "finalized"
    info = git_ops.classify_worktree(str(env.checkout), env.branch, fetch=False)
    assert info.state == git_ops.WorktreeState.DEHYDRATED
    verdict = prune.assess(record, git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE))
    assert verdict.safe is False and verdict.category == "dehydrated"
    disposition = prune.cleanup_disposition(record, info)
    assert disposition.cleanable is False and disposition.bucket == "dehydrated"
    assert env.entry.parent.is_dir()
    assert gc.find_orphans(env.config.default_repo, [str(env.anchor)], [str(env.checkout)]) == []
    managed = gc.classify_managed_worktree(
        worktree_id=WT_ID, kind="system", follow_up=False, status="finalized", git_state="dehydrated",
        has_live_mux=False, attached=False, has_live_session=False, idle_secs=1e9,
    )
    assert (managed.action, managed.reason) == ("skip", "dehydrated")


def test_lost_tracking_is_recovered_as_a_held_record(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    env.ledger.unlink()
    report = hydration.recover(env.config)
    assert [r["verdict"] for r in report["entries"]] == ["reconstructable"]
    assert not env.ledger.exists()
    hydration.recover(env.config, apply=True)
    rebuilt = tracking.load_record(env.ledger)
    assert rebuilt.status == "complete" and catalog.is_dehydrated(rebuilt)
    assert hydration.rehydrate(WT_ID, env.config)["state"] == "hydrated"


def test_contradictory_catalog_is_a_hold(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    env.ledger.unlink()
    _git(env.anchor, "branch", "-f", env.branch, "main")
    report = hydration.recover(env.config, apply=True)
    assert [r["verdict"] for r in report["entries"]] == ["hold"] and not env.ledger.exists()


def test_stale_save_keeps_newer_hydration(env: SimpleNamespace) -> None:
    stale = tracking.load_record(env.ledger)
    hydration.dehydrate(WT_ID, env.config)
    stale.title = "renamed"
    tracking.save_record(stale, env.ledger)
    assert _state(env) == "dehydrated"


def test_cli_reports_structured_refusal(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    from agent_worktrees import checkout_hydration_cli as cli

    monkeypatch.setattr(cfg, "load_config", lambda: env.config)
    (env.checkout / "new.txt").write_text("x", encoding="utf-8")
    args = SimpleNamespace(command="dehydrate", worktree_id=WT_ID, plan=False, capabilities=False, json=True)
    assert cli.cmd_hydration(args) == 1  # type: ignore[arg-type]
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "dirty-checkout" and payload["complete"] is False
