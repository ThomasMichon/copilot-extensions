"""Real-git round trips for manual checkout dehydration (Phase 3)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from machine_transport import detect_platform

from agent_worktrees import checkout_hydration as hydration
from agent_worktrees import checkout_hydration_catalog as catalog
from agent_worktrees import config as cfg
from agent_worktrees import execution_spaces, git_ops, prune, procs, sessions, tracking

WT_ID = "fixture-win-20261121-100000-abcd"
_REAL_ADMISSION = execution_spaces.require_record_mutation


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
    # Receiving-side admission needs a machine registry; refusal tests restore it.
    monkeypatch.setattr(execution_spaces, "require_record_mutation", lambda *_a, **_k: None)
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
    entry["identity"][field] = str(env.checkout.parent / value) if field == "checkout_path" else value
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


# --- second review: hidden index flags, admission, materialization, reservations, index cache ---


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_hidden_index_flags_refuse(env: SimpleNamespace, flag: str) -> None:
    _git(env.checkout, "update-index", flag, "wip.txt")
    (env.checkout / "wip.txt").write_bytes(b"hidden edit\n")
    assert _git(env.checkout, "status", "--porcelain") == ""  # git itself hides the edit
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "hidden-index-flags" and "wip.txt" in caught.value.details["entries"]
    assert (env.checkout / "wip.txt").read_bytes() == b"hidden edit\n" and not env.entry.exists()


def _refuse(*_a: object, **_k: object) -> None:
    raise execution_spaces.ExecutionSpaceError("owned record has an unqualified owner reference")


@pytest.mark.parametrize("operation", ["dehydrate", "rehydrate", "reconcile", "release_recovery_hold"])
def test_mutation_admission_refuses_before_any_change(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    if operation != "dehydrate":
        hydration.dehydrate(WT_ID, env.config)
    before = env.entry.read_bytes() if env.entry.exists() else None
    monkeypatch.setattr(execution_spaces, "require_record_mutation", _refuse)
    with pytest.raises(hydration.HydrationRefused) as caught:
        getattr(hydration, operation)(WT_ID, env.config)
    assert caught.value.code == "mutation-refused"
    assert (env.entry.read_bytes() if env.entry.exists() else None) == before
    assert env.checkout.exists() is (operation == "dehydrate")


def test_foreign_platform_record_is_refused_by_real_admission(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_load = hydration._load

    def foreign(ledger: Path) -> object:
        record = real_load(ledger)
        other = "linux" if detect_platform() != "linux" else "windows"
        record.platform = other
        record._reporting_context = SimpleNamespace(environment=other)
        return record

    monkeypatch.setattr(hydration, "_load", foreign)
    monkeypatch.setattr(execution_spaces, "require_record_mutation", _REAL_ADMISSION)
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "mutation-refused" and "platform" in str(caught.value)
    assert env.checkout.exists() and not env.entry.exists()


def test_recover_applies_admission_before_rebuilding(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydration.dehydrate(WT_ID, env.config)
    env.ledger.unlink()
    monkeypatch.setattr(execution_spaces, "require_record_mutation", _refuse)
    item = hydration.recover(env.config, apply=True)["entries"][0]
    assert item["verdict"] == "hold" and item["applied"] is False
    assert not env.ledger.exists() and "recovery_hold" not in (catalog.read_entry(env.entry) or {})


def _crlf_fixture(env: SimpleNamespace) -> None:
    """Repo config keeps LF; the per-worktree config asks for CRLF on checkout."""
    _git(env.anchor, "config", "core.autocrlf", "false")
    (env.checkout / "lf.txt").write_bytes(b"one\ntwo\n")
    _git(env.checkout, "add", "lf.txt")
    _git(env.checkout, "commit", "-q", "-m", "lf")
    _git(env.anchor, "config", "extensions.worktreeConfig", "true")
    _git(env.checkout, "config", "--worktree", "core.autocrlf", "true")
    assert _git(env.checkout, "status", "--porcelain") == ""


def test_worktree_config_applies_before_files_materialize(env: SimpleNamespace) -> None:
    _crlf_fixture(env)
    hydration.dehydrate(WT_ID, env.config)
    hydration.rehydrate(WT_ID, env.config)
    assert (env.checkout / "lf.txt").read_bytes() == b"one\r\ntwo\r\n"
    assert _git(env.checkout, "status", "--porcelain") == "" and not env.entry.exists()


def _can_symlink(tmp: Path) -> bool:
    try:
        (tmp / "probe-link").symlink_to(tmp / "probe-target")
    except (OSError, NotImplementedError):
        return False
    (tmp / "probe-link").unlink()
    return True


def test_worktree_symlink_override_applies_on_rehydrate(env: SimpleNamespace, tmp_path: Path) -> None:
    if not _can_symlink(tmp_path):
        pytest.skip("platform cannot create symlinks")
    _git(env.anchor, "config", "core.symlinks", "true")
    _git(env.anchor, "config", "extensions.worktreeConfig", "true")
    _git(env.checkout, "config", "--worktree", "core.symlinks", "false")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=env.checkout, input="a.txt", check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    _git(env.checkout, "update-index", "--add", "--cacheinfo", f"120000,{blob},link")
    _git(env.checkout, "commit", "-q", "-m", "link")
    _git(env.checkout, "checkout", "--", "link")
    assert not (env.checkout / "link").is_symlink()
    hydration.dehydrate(WT_ID, env.config)
    hydration.rehydrate(WT_ID, env.config)
    link = env.checkout / "link"
    assert not link.is_symlink() and link.read_bytes() == b"a.txt"


@pytest.mark.parametrize("crash", ["_restore_worktree_config", "_materialize"])
def test_interrupted_no_checkout_rehydrate_is_completed_by_reconcile(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, crash: str,
) -> None:
    _crlf_fixture(env)
    hydration.dehydrate(WT_ID, env.config)

    def boom(*_a: object) -> None:
        raise RuntimeError("crash")

    with monkeypatch.context() as patch:
        patch.setattr(hydration, crash, boom)
        with pytest.raises(RuntimeError):
            hydration.rehydrate(WT_ID, env.config)
    assert [p.name for p in env.checkout.iterdir()] == [".git"] and env.entry.exists()
    assert (catalog.read_entry(env.entry) or {})["stage"] == catalog.STAGE_REHYDRATING
    report = hydration.reconcile(WT_ID, env.config)
    assert report["reconciled"] == "interrupted-rehydrate-completed"
    assert (env.checkout / "lf.txt").read_bytes() == b"one\r\ntwo\r\n"
    assert (env.checkout / "wip.txt").exists() and not env.entry.exists()


def test_unpopulated_checkout_with_stray_files_is_a_hold(env: SimpleNamespace) -> None:
    hydration.dehydrate(WT_ID, env.config)
    entry = catalog.read_entry(env.entry) or {}
    entry["stage"] = catalog.STAGE_REHYDRATING
    catalog.write_entry(env.entry, entry)
    _git(env.anchor, "worktree", "add", "-q", "--no-checkout", str(env.checkout), env.branch)
    (env.checkout / "a.txt").write_bytes(b"partial")
    assert hydration.reconcile(WT_ID, env.config)["state"] == "hold"
    assert env.entry.exists() and (env.checkout / "a.txt").read_bytes() == b"partial"


def test_partially_removed_checkout_is_not_rolled_back(env: SimpleNamespace) -> None:
    _interrupt_before_remove(env)
    (env.checkout / "wip.txt").unlink()  # removal stopped part-way
    assert hydration.reconcile(WT_ID, env.config)["state"] == "hold" and env.entry.exists()


def test_root_change_keeps_branch_reservation(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees import git_collab

    monkeypatch.setattr(cfg, "load_config", lambda: env.config)
    hydration.dehydrate(WT_ID, env.config)
    moved = tmp_path / "new-root"
    moved.mkdir()
    env.config.default_repo.worktree_root = str(moved)
    assert git_ops.delete_branch(env.branch, cwd=env.anchor, force=True) is False

    entry = catalog.read_entry(env.entry) or {}
    entry["git"]["branch"] = "feature/shared"
    catalog.write_entry(env.entry, entry)
    _git(env.anchor, "branch", "feature/shared", env.head)
    monkeypatch.setattr(tracking, "resolve_worktree_path", lambda *_a: str(env.anchor))
    assert git_collab.manage_feature_branch("other", env.config, "shared") is False
    assert _git(env.anchor, "rev-parse", "feature/shared") == env.head
    entry["git"]["branch"] = env.branch
    catalog.write_entry(env.entry, entry)
    assert hydration.rehydrate(WT_ID, env.config)["state"] == "hydrated"


def test_corrupt_entry_fails_closed_at_branch_mutation(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda: env.config)
    hydration.dehydrate(WT_ID, env.config)
    _git(env.anchor, "branch", "unrelated", env.head)
    _git(env.anchor, "branch", "unrelated2", env.head)
    env.entry.write_bytes(b"{not json")
    # The ledger still names the retained branch, so only that branch is held.
    assert git_ops.delete_branch(env.branch, cwd=env.anchor, force=True) is False
    assert git_ops.delete_branch("unrelated", cwd=env.anchor, force=True) is True
    env.ledger.unlink()  # unknown reservation: every branch of the repository is held
    assert catalog.dehydrated_branch_holder(env.anchor, "unrelated2", env.config) is not None
    assert git_ops.delete_branch("unrelated2", cwd=env.anchor, force=True) is False
    assert _git(env.anchor, "rev-parse", env.branch) == env.head


def test_unreadable_entry_and_directory_fail_closed(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydration.dehydrate(WT_ID, env.config)
    env.ledger.unlink()

    def unreadable(_path: Path) -> dict[str, object] | None:
        raise catalog.CatalogError("dehydration catalog entry is unreadable: denied")

    with monkeypatch.context() as patch:
        patch.setattr(catalog, "read_entry", unreadable)
        assert catalog.dehydrated_branch_holder(env.anchor, "anything", env.config) is not None
        assert catalog.is_dehydrated_checkout(env.checkout)

    def denied(_directory: Path) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr(catalog, "_signature", denied)
    assert catalog.dehydrated_branch_holder(env.anchor, "anything", env.config) is not None
    assert catalog.is_dehydrated_checkout(env.root / "some-other-worktree")


def test_observation_batches_parse_the_catalog_once(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydration.dehydrate(WT_ID, env.config)
    reads: list[Path] = []
    real = catalog.read_entry

    def counting(path: Path) -> dict[str, object] | None:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(catalog, "read_entry", counting)
    others = [SimpleNamespace(worktree_id=f"other-{i}", worktree_path=str(env.root / f"other-{i}")) for i in range(20)]
    for _ in range(3):
        assert not any(catalog.is_dehydrated(record) for record in others)
    assert len(reads) == 1
    entry = real(env.entry) or {}
    entry["dehydrated_at"] = "changed"
    catalog.write_entry(env.entry, entry)
    assert catalog.is_dehydrated_checkout(env.checkout) and len(reads) == 2
    catalog.delete_entry(env.entry)
    assert not catalog.is_dehydrated_checkout(env.checkout)

def _real_admission_config(env: SimpleNamespace, root: Path) -> cfg.Config:
    return cfg.Config(
        srcroot=str(env.anchor.parent), machine="fixture", platform=detect_platform(),
        repo_name="sample",
        repos={"sample": cfg.RepoConfig(anchor=str(env.anchor), worktree_root=str(root))},
    )


def test_root_change_round_trip_passes_real_admission(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees import legacy_wsl_record_locality

    record = tracking.load_record(env.ledger)
    record.platform = detect_platform()
    tracking.save_record(record, env.ledger)
    monkeypatch.setattr(execution_spaces, "require_record_mutation", _REAL_ADMISSION)
    original = _real_admission_config(env, env.root)
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: original)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: original)
    hydration.dehydrate(WT_ID, original)

    moved = tmp_path / "new-root"
    moved.mkdir()
    changed = _real_admission_config(env, moved)
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: changed)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: changed)
    assert legacy_wsl_record_locality.record_namespace_matches(tracking.load_record(env.ledger), changed)

    assert hydration.reconcile(WT_ID, changed)["state"] == "dehydrated"
    restored = hydration.rehydrate(WT_ID, changed)
    assert restored["state"] == "hydrated" and restored["head"] == env.head
    assert (env.checkout / "wip.txt").read_text(encoding="utf-8") == "wip\n"
    assert not env.entry.exists()


@pytest.mark.parametrize("field", ["creation_nonce", "branch", "anchor", "checkout_path", "repo"])
def test_mismatched_placement_evidence_is_refused_by_real_admission(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str,
) -> None:
    from agent_worktrees import legacy_wsl_record_locality

    record = tracking.load_record(env.ledger)
    record.platform = detect_platform()
    tracking.save_record(record, env.ledger)
    hydration.dehydrate(WT_ID, env.config)
    entry = catalog.read_entry(env.entry) or {}
    section = entry["git"] if field == "branch" else entry["identity"]
    section[field] = str(tmp_path / "foreign") if field in ("anchor", "checkout_path") else "foreign"
    catalog.write_entry(env.entry, entry)
    moved = tmp_path / "new-root"
    moved.mkdir()
    changed = _real_admission_config(env, moved)
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: changed)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: changed)
    monkeypatch.setattr(execution_spaces, "require_record_mutation", _REAL_ADMISSION)
    assert not legacy_wsl_record_locality.record_namespace_matches(tracking.load_record(env.ledger), changed)
    before = env.entry.read_bytes()
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.rehydrate(WT_ID, changed)
    assert caught.value.code == "mutation-refused"
    assert env.entry.read_bytes() == before and not env.checkout.exists()

def test_sequencer_state_refuses(env: SimpleNamespace) -> None:
    for name in ("x.txt", "y.txt"):
        _git(env.anchor, "checkout", "-q", "-b", f"pick-{name}", "main")
        (env.anchor / name).write_text("theirs\n", encoding="utf-8")
        _git(env.anchor, "add", name)
        _git(env.anchor, "commit", "-q", "-m", name)
    _git(env.anchor, "checkout", "-q", "main")
    (env.checkout / "x.txt").write_text("ours\n", encoding="utf-8")
    _git(env.checkout, "add", "x.txt")
    _git(env.checkout, "commit", "-q", "-m", "ours")
    subprocess.run(["git", "cherry-pick", "pick-x.txt", "pick-y.txt"], cwd=env.checkout, capture_output=True)
    (env.checkout / "x.txt").write_text("ours\n", encoding="utf-8")
    _git(env.checkout, "add", "x.txt")
    _git(env.checkout, "-c", "core.editor=true", "commit", "-q", "--allow-empty", "--no-edit")
    sequencer = Path(_git(env.checkout, "rev-parse", "--path-format=absolute", "--git-path", "sequencer"))
    assert sequencer.exists() and _git(env.checkout, "status", "--porcelain") == ""
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "operation-in-progress" and env.checkout.exists() and not env.entry.exists()


@pytest.mark.parametrize("corrupt", [
    "config-not-dict", "config-bad-base64", "config-bad-entries", "config-bad-entry-shape",
    "head", "branch", "relative-checkout", "operation-id", "prior-state",
])
def test_malformed_snapshot_is_a_hold_not_a_traceback(env: SimpleNamespace, corrupt: str) -> None:
    hydration.dehydrate(WT_ID, env.config)
    entry = catalog.read_entry(env.entry) or {}
    git, identity = entry["git"], entry["identity"]
    good_config = {"file_b64": "", "entries": []}
    mutation: dict[str, Callable[[], None]] = {
        "config-not-dict": lambda: git.update(worktree_config=["x"]),
        "config-bad-base64": lambda: git.update(worktree_config={**good_config, "file_b64": "!!notbase64"}),
        "config-bad-entries": lambda: git.update(worktree_config={**good_config, "entries": "core.x"}),
        "config-bad-entry-shape": lambda: git.update(worktree_config={**good_config, "entries": [["", 1]]}),
        "head": lambda: git.update(head="HEAD~1"),
        "branch": lambda: git.update(branch="-x"),
        "relative-checkout": lambda: identity.update(checkout_path="relative/path"),
        "operation-id": lambda: entry.update(operation_id=5),
        "prior-state": lambda: entry.update(prior_state="x"),
    }
    mutation[corrupt]()
    with pytest.raises(catalog.CatalogError):
        catalog.validate(entry)
    env.entry.write_text(json.dumps(entry), encoding="utf-8")
    assert hydration.reconcile(WT_ID, env.config)["state"] == "hold"
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.rehydrate(WT_ID, env.config)
    assert caught.value.code == "hold" and not env.checkout.exists() and env.entry.exists()


def _failing_remove(monkeypatch: pytest.MonkeyPatch, before: object = None) -> None:
    real = hydration._git

    def fake(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("worktree", "remove"):
            if callable(before):
                before()
            return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: simulated failure")
        return real(cwd, *args, check=check)

    monkeypatch.setattr(hydration, "_git", fake)


def test_failed_removal_with_partial_checkout_keeps_the_catalog(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:
        _failing_remove(patch, before=lambda: (env.checkout / "wip.txt").unlink())
        with pytest.raises(hydration.HydrationRefused) as caught:
            hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "remove-failed"
    assert caught.value.details["report"]["state"] == "hold" and env.entry.exists()
    assert (catalog.read_entry(env.entry) or {})["stage"] == catalog.STAGE_REMOVING
    assert hydration.reconcile(WT_ID, env.config)["state"] == "hold" and env.entry.exists()


def test_failed_removal_of_intact_checkout_rolls_back(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _failing_remove(monkeypatch)
    with pytest.raises(hydration.HydrationRefused) as caught:
        hydration.dehydrate(WT_ID, env.config)
    assert caught.value.code == "remove-failed"
    assert caught.value.details["report"]["reconciled"] == "interrupted-dehydrate-rolled-back"
    assert env.checkout.exists() and not env.entry.exists()


def test_ledger_branch_stays_held_when_catalog_disagrees(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: env.config)
    hydration.dehydrate(WT_ID, env.config)
    entry = catalog.read_entry(env.entry) or {}
    entry["git"]["branch"] = "other"
    catalog.write_entry(env.entry, entry)
    _git(env.anchor, "branch", "other", env.head)
    assert catalog.dehydrated_branch_holder(env.anchor, env.branch, env.config) == WT_ID
    assert git_ops.delete_branch(env.branch, cwd=env.anchor, force=True) is False
    assert git_ops.force_branch(env.branch, "main", cwd=env.anchor, config=env.config) is not None
    assert _git(env.anchor, "rev-parse", env.branch) == env.head


def _with_pr_head(env: SimpleNamespace, branch: str) -> None:
    record = tracking.load_record(env.ledger)
    record.prs = [tracking.PRRecord(state="open", branch=branch, head_sha=env.head)]
    tracking.save_record(record, env.ledger)
    _git(env.anchor, "branch", branch, env.head)


def test_dehydrated_pr_head_branch_is_reserved(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import finalize, pr_rewrite

    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: env.config)
    _with_pr_head(env, "feature/pr-x")
    hydration.dehydrate(WT_ID, env.config)
    refused = git_ops.force_branch("feature/pr-x", "main", cwd=env.anchor, config=env.config)
    assert refused is not None and WT_ID in refused
    assert git_ops.force_branch("feature/pr-x", "main", cwd=env.anchor) is not None  # default config lookup
    assert git_ops.delete_branch("feature/pr-x", cwd=env.anchor, force=True) is False
    assert _git(env.anchor, "rev-parse", "feature/pr-x") == env.head
    # Every snapshot-publication site routes its local-head write through force_branch.
    import inspect

    from agent_worktrees import pr_ops
    for module in (pr_ops, finalize, pr_rewrite):
        source = inspect.getsource(module)
        assert '"branch", "-f"' not in source and "force_branch(" in source
    hydration.rehydrate(WT_ID, env.config)
    assert git_ops.force_branch("feature/pr-x", "main", cwd=env.anchor, config=env.config) is None


def test_root_change_plus_lost_tracking_uses_the_root_registry(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees import claimant

    env.config.machine = "fixture"
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: env.config)
    monkeypatch.setattr(claimant, "_claimant_is_local", lambda *_a: True)
    monkeypatch.setattr(claimant, "_record_execution_conflicts", lambda *_a: False)
    hydration.dehydrate(WT_ID, env.config)
    assert catalog.registered_roots("sample") == [env.root]
    moved = tmp_path / "new-root"
    moved.mkdir()
    env.config.default_repo.worktree_root = str(moved)
    env.ledger.unlink()

    report = hydration.recover(env.config)
    assert [(r["catalog"], r["verdict"]) for r in report["entries"]] == [(str(env.entry), "reconstructable")]
    assert catalog.dehydrated_branch_holder(env.anchor, env.branch, env.config) == WT_ID
    assert git_ops.delete_branch(env.branch, cwd=env.anchor, force=True) is False
    assert git_ops.force_branch(env.branch, "main", cwd=env.anchor, config=env.config) is not None
    assert claimant.local_claimant_alive(f"fixture/sample/{WT_ID}") is True
    assert _git(env.anchor, "rev-parse", env.branch) == env.head


def test_corrupt_root_registry_holds_every_branch(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: env.config)
    hydration.dehydrate(WT_ID, env.config)
    _git(env.anchor, "branch", "unrelated", env.head)
    (env.root.parent / "proj" / catalog.ROOTS_FILENAME).write_text("{oops", encoding="utf-8")
    assert git_ops.delete_branch("unrelated", cwd=env.anchor, force=True) is False
    assert any(item["verdict"] == "hold" for item in hydration.recover(env.config)["entries"])


def test_claimant_resolves_the_target_projects_own_catalog(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import claimant

    env.config.machine = "fixture"
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: env.config)
    monkeypatch.setattr(claimant, "_claimant_is_local", lambda *_a: True)
    monkeypatch.setattr(claimant, "_record_execution_conflicts", lambda *_a: False)
    hydration.dehydrate(WT_ID, env.config)
    env.ledger.unlink()
    adopted = SimpleNamespace(default_repo=SimpleNamespace(worktree_root=str(env.root), anchor=str(env.anchor)))
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: adopted)
    assert catalog.project_entry_state("adopted", WT_ID, env.config) == catalog.ENTRY_PRESENT
    assert claimant.local_claimant_alive(f"fixture/adopted/{WT_ID}") is True
    assert catalog.project_entry_state("adopted", "missing-id", env.config) == catalog.ENTRY_ABSENT

    def unresolvable(*_a: object, **_k: object) -> object:
        raise RuntimeError("project config unavailable")

    monkeypatch.setattr(cfg, "load_project_config", unresolvable)
    # The root registry is written before any entry, so it alone still answers.
    assert catalog.project_entry_state("adopted", WT_ID, env.config) == catalog.ENTRY_PRESENT
    assert catalog.project_entry_state("adopted", "missing-id", env.config) == catalog.ENTRY_ABSENT
    (cfg.project_dir("adopted") / catalog.ROOTS_FILENAME).write_text("{not json", encoding="utf-8")
    assert catalog.project_entry_state("adopted", "missing-id", env.config) == catalog.ENTRY_UNKNOWN
    assert claimant.local_claimant_alive("fixture/adopted/missing-id") is None


def _feature_sync_fixture(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: env.config)
    monkeypatch.setattr(tracking, "resolve_worktree_path", lambda *_a: str(env.anchor))
    _with_pr_head(env, "feature/shared")
    _git(env.anchor, "branch", "-f", "feature/shared", "main")
    _git(env.anchor, "update-ref", "refs/remotes/origin/feature/shared", env.head)


def test_dehydration_during_feature_fetch_wins(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import git_collab

    _feature_sync_fixture(env, monkeypatch)
    main_tip = _git(env.anchor, "rev-parse", "main")
    monkeypatch.setattr(git_ops, "fetch", lambda *_a, **_k: hydration.dehydrate(WT_ID, env.config))
    assert git_collab.manage_feature_branch("other", env.config, "shared", sync=True) is False
    assert env.entry.exists() and _git(env.anchor, "rev-parse", "feature/shared") == main_tip


def test_branch_writes_wait_for_an_in_flight_dehydration(env: SimpleNamespace) -> None:
    import threading

    done: list[object] = []
    with catalog.reservation_lock(env.anchor):
        worker = threading.Thread(target=lambda: done.append(hydration.dehydrate(WT_ID, env.config)))
        worker.start()
        worker.join(1.5)
        assert worker.is_alive() and not env.entry.exists() and env.checkout.exists()
    worker.join(60)
    assert done and env.entry.exists() and not env.checkout.exists()
