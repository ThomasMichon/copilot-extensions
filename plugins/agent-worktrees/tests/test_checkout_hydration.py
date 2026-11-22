"""Real-git round trips for manual checkout dehydration (Phase 3)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

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
    records = base / "proj" / "worktrees"
    records.mkdir(parents=True)
    monkeypatch.setattr(cfg, "tracking_dir", lambda _repo=None: records)
    monkeypatch.setattr(cfg, "project_dir", lambda _project=None: records.parent)
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
    assert not env.entry.exists()
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
    assert env.checkout.exists() and not env.entry.exists()


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
        "identity": {
            "repo": "sample", "worktree_id": WT_ID, "checkout_path": str(env.checkout), "anchor": str(env.anchor),
        },
        "git": snapshot, "agency": {},
    })


def test_interrupted_dehydrate_with_checkout_present_rolls_back(env: SimpleNamespace) -> None:
    _interrupt_before_remove(env)
    assert git_ops.classify_worktree(str(env.checkout), env.branch, fetch=False).state != git_ops.WorktreeState.GONE
    report = hydration.reconcile(WT_ID, env.config)
    assert report["reconciled"] == "interrupted-dehydrate-rolled-back"
    assert env.checkout.exists() and not env.entry.exists()


def test_interrupted_dehydrate_after_removal_completes(env: SimpleNamespace) -> None:
    _interrupt_before_remove(env)
    _git(env.anchor, "worktree", "remove", str(env.checkout))
    report = hydration.reconcile(WT_ID, env.config)
    assert report["reconciled"] == "interrupted-dehydrate-completed"
    assert (catalog.read_entry(env.entry) or {})["stage"] == "dehydrated"


def test_interrupted_rehydrate_completes_without_removing_checkout(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    entry = catalog.read_entry(env.entry) or {}
    entry["stage"] = catalog.STAGE_REHYDRATING
    catalog.write_entry(env.entry, entry)
    _git(env.anchor, "worktree", "add", "-q", str(env.checkout), env.branch)
    report = hydration.reconcile(WT_ID, env.config)
    assert report["reconciled"] == "interrupted-rehydrate-completed"
    assert env.checkout.exists() and not env.entry.exists()


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


def test_cli_reports_structured_refusal(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    from agent_worktrees import checkout_hydration_cli as cli

    monkeypatch.setattr(cfg, "load_config", lambda: env.config)
    (env.checkout / "new.txt").write_text("x", encoding="utf-8")
    args = SimpleNamespace(command="dehydrate", worktree_id=WT_ID, plan=False, capabilities=False, json=True)
    assert cli.cmd_hydration(args) == 1  # type: ignore[arg-type]
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "dirty-checkout" and payload["complete"] is False


def test_lost_tracking_recovers_a_held_record_until_released(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    env.ledger.unlink()
    report = hydration.recover(env.config)
    assert [r["verdict"] for r in report["entries"]] == ["reconstructable"]
    assert not env.ledger.exists()
    applied = hydration.recover(env.config, apply=True)
    assert applied["entries"][0]["applied"] is True
    rebuilt = tracking.load_record(env.ledger)
    assert rebuilt.status == "complete" and catalog.is_dehydrated(rebuilt)
    assert (catalog.read_entry(env.entry) or {})["recovery_hold"]["reason"] == "relationships-unreconciled"
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.rehydrate(WT_ID, env.config)
    assert caught.value.code == "recovery-hold" and env.entry.exists() and not env.checkout.exists()

    assert hydration.release_recovery_hold(WT_ID, env.config)["released"] is True
    assert hydration.rehydrate(WT_ID, env.config)["state"] == "hydrated"
    assert not env.entry.exists()


def test_recover_rechecks_ledger_absence_under_the_lock(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydration.dehydrate(WT_ID, env.config)
    original = env.ledger.read_bytes()
    env.ledger.unlink()
    locked = hydration._locked

    @contextmanager
    def racing(ledger: Path, root: Path) -> Iterator[None]:
        ledger.write_bytes(original)  # another owner recreated the ledger first
        with locked(ledger, root):
            yield

    monkeypatch.setattr(hydration, "_locked", racing)
    item = hydration.recover(env.config, apply=True)["entries"][0]
    assert item["verdict"] == "tracked" and item["applied"] is False
    assert env.ledger.read_bytes() == original
    assert "recovery_hold" not in (catalog.read_entry(env.entry) or {})


def test_contradictory_catalog_is_a_hold(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    env.ledger.unlink()
    _git(env.anchor, "branch", "-f", env.branch, "main")
    report = hydration.recover(env.config, apply=True)
    assert [r["verdict"] for r in report["entries"]] == ["hold"] and not env.ledger.exists()


@pytest.mark.parametrize("field,value", [
    ("creation_nonce", "other-incarnation"), ("repo", "other"), ("anchor", "elsewhere"),
    ("checkout_path", "elsewhere"),
])
def test_catalog_identity_mismatch_is_a_hold(env: SimpleNamespace, field: str, value: str) -> None:
    hydration.dehydrate(WT_ID, env.config)
    entry = catalog.read_entry(env.entry) or {}
    entry["identity"][field] = value
    catalog.write_entry(env.entry, entry)
    assert hydration.reconcile(WT_ID, env.config)["state"] == "hold"
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.rehydrate(WT_ID, env.config)
    assert caught.value.code == "hold" and not env.checkout.exists() and env.entry.exists()


def test_catalog_survives_a_worktree_root_change(env: SimpleNamespace, tmp_path: Path) -> None:
    hydration.dehydrate(WT_ID, env.config)
    moved = tmp_path / "new-root"
    moved.mkdir()
    env.config.default_repo.worktree_root = str(moved)
    assert hydration.reconcile(WT_ID, env.config)["state"] == "dehydrated"
    assert hydration.rehydrate(WT_ID, env.config)["state"] == "hydrated"
    assert env.checkout.exists() and not env.entry.exists()


_WORKTREE_CONFIG = (
    "[commit]\n\tgpgSign\n[core]\n\tbare = false\n\tsymlinks = \n"
    '[remote "origin"]\n\tfetch = +refs/heads/a:refs/remotes/origin/a\n'
    "\tfetch = +refs/heads/b:refs/remotes/origin/b\n"
)


def _write_worktree_config(env: SimpleNamespace) -> Path:
    _git(env.anchor, "config", "extensions.worktreeConfig", "true")
    path = Path(_git(env.checkout, "rev-parse", "--path-format=absolute", "--git-path", "config.worktree"))
    path.write_text(_WORKTREE_CONFIG, encoding="utf-8")
    return path


def _assert_worktree_config(env: SimpleNamespace) -> None:
    path = Path(_git(env.checkout, "rev-parse", "--path-format=absolute", "--git-path", "config.worktree"))
    assert path.read_text(encoding="utf-8") == _WORKTREE_CONFIG
    assert _git(env.checkout, "config", "--worktree", "--bool", "commit.gpgSign") == "true"
    assert _git(env.checkout, "config", "--worktree", "core.symlinks") == ""
    assert _git(env.checkout, "config", "--worktree", "--get-all", "remote.origin.fetch").splitlines() == [
        "+refs/heads/a:refs/remotes/origin/a", "+refs/heads/b:refs/remotes/origin/b",
    ]


def test_per_worktree_config_round_trips_exactly(env: SimpleNamespace) -> None:
    _write_worktree_config(env)
    hydration.dehydrate(WT_ID, env.config)
    entries = (catalog.read_entry(env.entry) or {})["git"]["worktree_config"]["entries"]
    assert ["commit.gpgsign", None] in entries and ["core.symlinks", ""] in entries
    hydration.rehydrate(WT_ID, env.config)
    _assert_worktree_config(env)


def test_interrupted_rehydrate_restores_per_worktree_config(env: SimpleNamespace) -> None:
    _write_worktree_config(env)
    hydration.dehydrate(WT_ID, env.config)
    entry = catalog.read_entry(env.entry) or {}
    entry["stage"] = catalog.STAGE_REHYDRATING
    catalog.write_entry(env.entry, entry)
    _git(env.anchor, "worktree", "add", "-q", str(env.checkout), env.branch)
    assert hydration.reconcile(WT_ID, env.config)["reconciled"] == "interrupted-rehydrate-completed"
    _assert_worktree_config(env)
    assert hydration.reconcile(WT_ID, env.config)["state"] == "hydrated"


def test_dehydrated_owner_keeps_its_claims(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import claimant, sweep

    env.config.machine = "fixture"
    monkeypatch.setattr(cfg, "load_config", lambda: env.config)
    monkeypatch.setattr(claimant, "_claimant_is_local", lambda *_a: True)
    monkeypatch.setattr(claimant, "_record_execution_conflicts", lambda *_a: False)
    ref = f"fixture/sample/{WT_ID}"
    hydration.dehydrate(WT_ID, env.config)

    assert claimant.local_claimant_alive(ref) is True  # outbound claims stay held
    assert sweep.gone_of(ref) is False
    env.ledger.unlink()  # a catalog-only recovery candidate is held too
    assert claimant.local_claimant_alive(ref) is True
    env.entry.unlink()
    assert claimant.local_claimant_alive(ref) is False


def test_dehydrated_child_is_not_a_settled_claim(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import sweep

    hydration.dehydrate(WT_ID, env.config)
    child = tracking.load_record(env.ledger)
    monkeypatch.setattr(sweep, "load_claim_child_record", lambda *_a: (child, True))
    monkeypatch.setattr(sweep, "child_branch_merged", lambda *_a: True)
    claim = SimpleNamespace(kind="worktree", ref=f"fixture/sample/{WT_ID}")
    assert sweep.safe_of(claim, env.config) is None  # type: ignore[arg-type]


def test_retained_branch_refuses_managed_moves_and_deletion(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import git_collab

    monkeypatch.setattr(cfg, "load_config", lambda: env.config)
    hydration.dehydrate(WT_ID, env.config)
    assert git_ops.delete_branch(env.branch, cwd=env.anchor, force=True) is False
    assert _git(env.anchor, "rev-parse", env.branch) == env.head

    entry = catalog.read_entry(env.entry) or {}
    entry["git"]["branch"] = "feature/shared"
    catalog.write_entry(env.entry, entry)
    _git(env.anchor, "branch", "feature/shared", env.head)
    monkeypatch.setattr(tracking, "resolve_worktree_path", lambda *_a: str(env.anchor))
    assert git_collab.manage_feature_branch("other", env.config, "shared") is False
    assert git_collab.merge_to_feature("other", env.config, "shared") is False
    assert _git(env.anchor, "rev-parse", "feature/shared") == env.head


def test_finalize_abandon_refuses_before_publishing(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import claim_handoffs, execution_spaces, finalize, finalize_obligations, tracking_claims

    hydration.dehydrate(WT_ID, env.config)
    env.config.machine = "fixture"
    monkeypatch.setattr(claim_handoffs, "active_bundle_ids_for_source", lambda *_a: [])
    published: list[object] = []

    def publish(*a: object, **_k: object) -> bool:
        published.append(a)
        return True

    monkeypatch.setattr(execution_spaces, "require_record_mutation", lambda *_a: None)
    monkeypatch.setattr(tracking, "resolve_worktree_path", lambda *_a: str(env.checkout))
    monkeypatch.setattr(finalize_obligations, "assert_obligations_settled",
                        publish)
    monkeypatch.setattr(tracking_claims, "rehome_abandoned_obligations", publish)
    ok = finalize.validate_and_finalize(
        WT_ID, env.config, abandon=True, handoff_to="fixture/sample/successor",
    )
    assert ok is False and published == []
    assert "is dehydrated" in capsys.readouterr().out
    assert tracking.load_record(env.ledger).status == "active" and env.entry.exists()


def test_first_catalog_write_syncs_the_worktree_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import checkout_durability

    synced: list[Path] = []
    monkeypatch.setattr(checkout_durability, "directory", lambda path: synced.append(Path(path)))
    root = tmp_path / "root"
    root.mkdir()
    catalog.write_entry(catalog.entry_path(root, WT_ID), {
        "schema": catalog.SCHEMA, "schema_version": catalog.SCHEMA_VERSION, "operation_id": "op",
        "stage": catalog.STAGE_REMOVING, "prior_state": {},
        "identity": {"repo": "sample", "worktree_id": WT_ID, "checkout_path": str(root / WT_ID)},
        "git": {"branch": "b", "head": "0" * 40}, "agency": {},
    })
    assert synced[0] == root and catalog.catalog_dir(root) in synced


def test_non_string_stage_is_a_catalog_error() -> None:
    with pytest.raises(catalog.CatalogError):
        catalog.validate({
            "schema": catalog.SCHEMA, "schema_version": catalog.SCHEMA_VERSION, "operation_id": "op",
            "stage": ["dehydrated"], "identity": {"worktree_id": WT_ID, "checkout_path": "x"},
            "git": {"branch": "b", "head": "0" * 40},
        })
