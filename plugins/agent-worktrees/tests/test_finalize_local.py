"""Remote-free finalization still requires clean, locally contained content."""

from __future__ import annotations



from argparse import Namespace
from dataclasses import replace
from pathlib import Path
import socket

import pytest
from machine_transport import detect_platform

from agent_worktrees import config as cfg
from agent_worktrees import cleanup_gc_cli, cleanup_reap, finalize, finalize_local, git_ops, procs, sessions, tracking
from pr_test_helpers import _git


pytestmark = pytest.mark.usefixtures("git_only_mux")
PRRepo = tuple[cfg.Config, str, Path, Path]
LocalRepo = tuple[cfg.Config, str, Path]


def test_local_fixture_uses_its_private_git_store(pr_repo: PRRepo) -> None:
    config, _, checkout, _ = pr_repo
    anchor_store = _git("rev-parse", "--path-format=absolute", "--git-common-dir",
                        cwd=Path(config.default_repo.anchor))
    checkout_store = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=checkout)
    assert Path(checkout_store).resolve() == Path(anchor_store).resolve()


@pytest.fixture
def local_repo(pr_repo: PRRepo, monkeypatch: pytest.MonkeyPatch) -> LocalRepo:
    config, wid, checkout, _ = pr_repo
    repo = replace(config.default_repo, pr=cfg.PRConfig())
    config = replace(config, repos={config.repo_name: repo},
                     platform=detect_platform(), machine=socket.gethostname())
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    project = checkout.parent.parent / "receiving"
    records = project / "worktrees"
    records.mkdir(parents=True)
    (cfg.tracking_dir() / f"{wid}.yaml").rename(records / f"{wid}.yaml")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: records)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    _git("merge", "--ff-only", f"worktree/{wid}", cwd=Path(repo.anchor))
    _git("remote", "remove", "origin", cwd=Path(repo.anchor))
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: True)
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
    return config, wid, checkout


@pytest.fixture
def foreign_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "foreign"
    repo.mkdir()
    _git("init", "--template=", "-b", "master", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("commit", "--allow-empty", "-m", "foreign base", cwd=repo)
    return repo


def _foreign_selectors(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo))


def test_finalize_local_content_without_fetch_or_ref_rewrite(
    local_repo: LocalRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    config, wid, checkout = local_repo
    before = _git("show-ref", cwd=checkout)
    monkeypatch.setattr(git_ops, "fetch", lambda *a, **k: pytest.fail("no remote to fetch"))
    monkeypatch.setattr(finalize, "_reconcile_merged_pointers",
                        lambda *a, **k: pytest.fail("local finalization must not rewrite refs"))

    assert finalize.validate_and_finalize(wid, config)
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "finalized"
    assert _git("show-ref", cwd=checkout) == before
    assert "refs/heads/master" in capsys.readouterr().out


def test_explicit_retention_finalizes_idle_checkout_without_removal(
    local_repo: LocalRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    config, wid, checkout = local_repo
    before = _git("show-ref", cwd=checkout)
    monkeypatch.setattr(finalize, "_has_live_session",
                        lambda record: pytest.fail("explicit retention does not require mux absence"))
    monkeypatch.setattr(git_ops, "is_cwd_inside", lambda path: False)
    monkeypatch.setattr(git_ops, "remove_worktree",
                        lambda *a, **k: pytest.fail("explicit retention cannot remove checkout"))
    monkeypatch.setattr(procs, "checkout_retirement_blocker",
                        lambda path: pytest.fail("no removal process scan for explicit retention"))

    assert finalize.validate_and_finalize(wid, config, retain_checkout=True)
    assert checkout.is_dir()
    assert _git("show-ref", cwd=checkout) == before
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    assert record.status == "finalized"
    assert record.finalized_checkout is not None
    assert "separate cleanup operation" in capsys.readouterr().out


def test_mux_only_worktree_is_inactive_and_can_finalize_without_removal(
    local_repo: LocalRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, checkout = local_repo
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    from agent_worktrees import reclaim

    monkeypatch.setattr(reclaim, "resolve_bound_copilots", lambda **_kwargs: [])
    monkeypatch.setattr(sessions, "mux_status_many",
                        lambda *_a, **_k: {wid: sessions.MuxInfo(exists=True, clients=0)})
    verdict = sessions.verify_worktree_active(record)
    assert verdict.active is False
    assert verdict.mux_live is True
    monkeypatch.setattr(finalize, "_has_live_session", lambda _record: verdict.mux_live)
    monkeypatch.setattr(git_ops, "remove_worktree",
                        lambda *_a, **_k: pytest.fail("the mux checkout must remain"))
    assert finalize.validate_and_finalize(wid, config)
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "finalized"
    assert checkout.is_dir()


def test_explicit_retention_does_not_allow_unlanded_work(local_repo: LocalRepo) -> None:
    config, wid, checkout = local_repo
    (checkout / "unfinished.txt").write_text("keep\n")
    assert not finalize.validate_and_finalize(wid, config, retain_checkout=True)
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"
    assert (checkout / "unfinished.txt").read_text() == "keep\n"


def test_ignored_evidence_survives_until_its_owner_disposes_it(
    local_repo: LocalRepo, foreign_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, checkout = local_repo
    (checkout / ".gitignore").write_text("private-evidence/\n", encoding="utf-8")
    _git("add", ".gitignore", cwd=checkout)
    _git("commit", "-m", "declare ignored evidence", cwd=checkout)
    _git("merge", "--ff-only", f"worktree/{wid}", cwd=Path(config.default_repo.anchor))
    evidence = checkout / "private-evidence"
    evidence.mkdir()
    payload = evidence / "trace.txt"
    payload.write_bytes(b"synthetic evidence must survive")
    from agent_worktrees.checkout_data import preservation_reason
    with monkeypatch.context() as foreign:
        _foreign_selectors(foreign, foreign_repo)
        assert "ignored data" in (preservation_reason(checkout) or "")
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: False)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: None)
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: [])
    assert not _git("status", "--porcelain", cwd=checkout)

    assert finalize.validate_and_finalize(wid, config)
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "finalized"
    cleanup_gc_cli.cmd_cleanup(Namespace(clean=True, include_unused=False))
    assert payload.read_bytes() == b"synthetic evidence must survive"
    assert checkout.is_dir()

    # This fixture's owner explicitly discards its own synthetic evidence.
    payload.unlink()
    evidence.rmdir()
    cleanup_gc_cli.cmd_cleanup(Namespace(clean=True, include_unused=False))
    assert not checkout.exists()


def test_inherited_selectors_cannot_hide_configured_target_remote(
    local_repo: LocalRepo, foreign_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _, checkout = local_repo
    _git("remote", "add", "origin", str(checkout / "missing-remote"), cwd=checkout)
    _foreign_selectors(monkeypatch, foreign_repo)
    assert finalize_local.resolve_upstream(config.default_repo, pr_mode=False) == ("origin/master", False)


@pytest.mark.parametrize("state", ["dirty", "uncontained"])
def test_inherited_selectors_cannot_authorize_foreign_content(
    local_repo: LocalRepo, foreign_repo: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    config, wid, checkout = local_repo
    (checkout / "unfinished.txt").write_text("keep\n")
    if state == "uncontained":
        _git("add", "unfinished.txt", cwd=checkout)
        _git("commit", "-m", "uncontained target work", cwd=checkout)
    _foreign_selectors(monkeypatch, foreign_repo)
    assert finalize_local.content_error(
        config.default_repo, str(checkout), f"worktree/{wid}", "refs/heads/master",
    ) is not None
    assert (checkout / "unfinished.txt").read_text() == "keep\n"


def test_inherited_selectors_do_not_redirect_nested_finalization(
    local_repo: LocalRepo, foreign_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, checkout = local_repo
    (foreign_repo / "foreign-only.txt").write_text("keep\n")
    _git("remote", "add", "origin", str(foreign_repo / "missing-remote"), cwd=foreign_repo)
    before = _git("show-ref", cwd=foreign_repo)
    _foreign_selectors(monkeypatch, foreign_repo)
    assert finalize.validate_and_finalize(wid, config)
    assert _git("show-ref", cwd=foreign_repo) == before
    assert (foreign_repo / "foreign-only.txt").read_text() == "keep\n"
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "finalized"


def test_local_finalization_refuses_hidden_untracked_files(local_repo: LocalRepo) -> None:
    config, wid, checkout = local_repo
    _git("config", "status.showUntrackedFiles", "no", cwd=checkout)
    (checkout / "hidden.txt").write_text("keep\n")
    assert not finalize.validate_and_finalize(wid, config)
    assert (checkout / "hidden.txt").read_text() == "keep\n"
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"


@pytest.mark.parametrize("tracked_default", [False, True])
def test_local_finalization_preserves_declared_default_branch(
    local_repo: LocalRepo, monkeypatch: pytest.MonkeyPatch, tracked_default: bool,
) -> None:
    config, wid, checkout = local_repo
    repo = config.default_repo
    _git("switch", "-c", "anchor-alternate", cwd=Path(repo.anchor))
    _git("switch", repo.default_branch, cwd=checkout)
    if tracked_default:
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.branch = repo.default_branch
        tracking.save_record(record)
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: False)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: None)

    assert finalize.validate_and_finalize(wid, config)
    assert git_ops.ref_exists(f"refs/heads/{repo.default_branch}", cwd=repo.anchor)
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "finalized"


@pytest.mark.parametrize("retained", [False, True])
def test_local_finalize_then_cleanup_preserves_default(
    local_repo: LocalRepo, monkeypatch: pytest.MonkeyPatch, retained: bool,
) -> None:
    config, wid, checkout = local_repo
    repo = config.default_repo
    _git("switch", "-c", "anchor-alternate", cwd=Path(repo.anchor))
    _git("switch", repo.default_branch, cwd=checkout)
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    record.branch = repo.default_branch
    tracking.save_record(record)
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: retained)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: None)

    assert finalize.validate_and_finalize(wid, config)
    assert checkout.exists() is retained
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    failures, _ = cleanup_reap.reap_worktree(
        record, git_ops.WorktreeStateInfo(git_ops.WorktreeState.COMPLETED), repo, cfg.tracking_dir(),
    )
    assert failures == 0
    assert not checkout.exists()
    assert git_ops.ref_exists(f"refs/heads/{repo.default_branch}", cwd=repo.anchor)


@pytest.mark.parametrize("dirty", ["modified", "untracked"])
def test_local_finalization_refuses_ignored_dirty_submodule(
    local_repo: LocalRepo, foreign_repo: Path, dirty: str,
) -> None:
    config, wid, checkout = local_repo
    (foreign_repo / "tracked.txt").write_text("base\n")
    _git("add", "tracked.txt", cwd=foreign_repo)
    _git("commit", "-m", "submodule content", cwd=foreign_repo)
    _git("-c", "protocol.file.allow=always", "submodule", "add", str(foreign_repo), "nested",
         cwd=checkout)
    _git("commit", "-m", "add submodule", cwd=checkout)
    _git("merge", "--ff-only", f"worktree/{wid}", cwd=Path(config.default_repo.anchor))
    _git("config", "submodule.nested.ignore", "all", cwd=checkout)
    pending = checkout / "nested" / ("tracked.txt" if dirty == "modified" else "new.txt")
    pending.write_text("keep\n")
    assert not _git("status", "--porcelain", "--untracked-files=all", cwd=checkout)

    assert not finalize.validate_and_finalize(wid, config)
    assert pending.read_text() == "keep\n"
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"


def test_ignored_submodule_evidence_survives_until_owner_disposition_and_deinitialization(
    local_repo: LocalRepo, foreign_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, checkout = local_repo
    (foreign_repo / ".gitignore").write_text("*.evidence\n", encoding="utf-8")
    (foreign_repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git("add", ".gitignore", "tracked.txt", cwd=foreign_repo)
    _git("commit", "-m", "submodule fixture", cwd=foreign_repo)
    _git("-c", "protocol.file.allow=always", "submodule", "add", str(foreign_repo), "nested", cwd=checkout)
    _git("commit", "-m", "add submodule fixture", cwd=checkout)
    _git("merge", "--ff-only", f"worktree/{wid}", cwd=Path(config.default_repo.anchor))
    evidence = checkout / "nested" / "capture.evidence"
    evidence.write_bytes(b"synthetic ignored submodule evidence")
    assert not _git("status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none", cwd=checkout)
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: False)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: None)
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: [])

    assert finalize.validate_and_finalize(wid, config)
    cleanup_gc_cli.cmd_cleanup(Namespace(clean=True, include_unused=False))
    assert evidence.read_bytes() == b"synthetic ignored submodule evidence"
    assert checkout.is_dir()

    evidence.unlink()
    _git("submodule", "deinit", "--all", cwd=checkout)
    cleanup_gc_cli.cmd_cleanup(Namespace(clean=True, include_unused=False))
    assert not checkout.exists()


@pytest.mark.parametrize("later", ["committed", "hidden-untracked", "missing-checkout"])
def test_local_cleanup_rechecks_content_after_finalize(
    local_repo: LocalRepo, monkeypatch: pytest.MonkeyPatch, later: str,
) -> None:
    config, wid, checkout = local_repo
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: None)
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: [])
    assert finalize.validate_and_finalize(wid, config)
    pending = checkout / "later.txt"
    pending.write_text("keep\n")
    if later in ("committed", "missing-checkout"):
        _git("add", "later.txt", cwd=checkout)
        _git("commit", "-m", "work after finalization", cwd=checkout)
    else:
        _git("config", "status.showUntrackedFiles", "no", cwd=checkout)
    if later == "missing-checkout":
        assert git_ops.remove_worktree(config.default_repo.anchor, str(checkout))

    cleanup_gc_cli.cmd_cleanup(Namespace(clean=True, include_unused=False))
    if later != "missing-checkout":
        assert pending.read_text() == "keep\n"
    assert git_ops.ref_exists(f"refs/heads/worktree/{wid}", cwd=config.default_repo.anchor)
    if later != "hidden-untracked":
        assert _git("show", f"worktree/{wid}:later.txt", cwd=Path(config.default_repo.anchor)) == "keep"


def test_local_cleanup_retires_removed_checkout_and_branch(
    local_repo: LocalRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, checkout = local_repo
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: False)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: None)
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: [])
    assert finalize.validate_and_finalize(wid, config)
    assert not checkout.exists()
    assert not git_ops.ref_exists(f"refs/heads/worktree/{wid}", cwd=config.default_repo.anchor)

    cleanup_gc_cli.cmd_cleanup(Namespace(clean=True, include_unused=False))
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "archived"
    assert git_ops.ref_exists("refs/heads/master", cwd=config.default_repo.anchor)


def test_local_finalization_uses_registered_anchor_store(local_repo: LocalRepo, foreign_repo: Path) -> None:
    config, wid, _ = local_repo
    before = _git("show-ref", cwd=Path(config.default_repo.anchor))
    assert finalize_local.content_error(
        config.default_repo, str(foreign_repo), f"worktree/{wid}", "refs/heads/master",
    ) is not None
    assert _git("show-ref", cwd=Path(config.default_repo.anchor)) == before


@pytest.mark.parametrize("state", ["dirty", "uncontained", "missing-default", "pr-required"])
def test_finalize_local_refuses_unproved_content(local_repo: LocalRepo, state: str) -> None:
    config, wid, checkout = local_repo
    if state == "dirty":
        (checkout / "uncommitted.txt").write_text("keep\n")
    elif state == "uncontained":
        (checkout / "unlanded.txt").write_text("keep\n")
        _git("add", "unlanded.txt", cwd=checkout)
        _git("commit", "-m", "unlanded local work", cwd=checkout)
    elif state == "missing-default":
        repo = replace(config.default_repo, default_branch="missing")
        config = replace(config, repos={config.repo_name: repo})
    else:
        repo = replace(config.default_repo, pr=cfg.PRConfig(required=True))
        config = replace(config, repos={config.repo_name: repo})
    before = _git("show-ref", cwd=checkout)

    assert not finalize.validate_and_finalize(wid, config)
    assert checkout.exists()
    assert _git("show-ref", cwd=checkout) == before
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"


@pytest.mark.parametrize("late_work", ["committed", "hidden-untracked"])
def test_finalize_local_rechecks_content_after_retirement_inspection(
    local_repo: LocalRepo, monkeypatch: pytest.MonkeyPatch, late_work: str,
) -> None:
    config, wid, checkout = local_repo
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: False)
    if late_work == "hidden-untracked":
        _git("config", "status.showUntrackedFiles", "no", cwd=checkout)

    def new_work(_path: str) -> None:
        (checkout / "later.txt").write_text("keep\n")
        if late_work == "committed":
            _git("add", "later.txt", cwd=checkout)
            _git("commit", "-m", "new work during retirement inspection", cwd=checkout)
        return None

    monkeypatch.setattr(procs, "checkout_retirement_blocker", new_work)
    assert not finalize.validate_and_finalize(wid, config)
    assert (checkout / "later.txt").read_text() == "keep\n"
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"


def test_finalize_does_not_treat_remote_failure_as_local_only(local_repo: LocalRepo) -> None:
    config, wid, checkout = local_repo
    _git("remote", "add", "origin", str(checkout / "missing-remote"), cwd=checkout)
    before = _git("show-ref", cwd=checkout)

    with pytest.raises(git_ops.GitError):
        finalize.validate_and_finalize(wid, config)
    assert checkout.exists()
    assert _git("show-ref", cwd=checkout) == before
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"
