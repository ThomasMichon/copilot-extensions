"""Remote-free finalization still requires clean, locally contained content."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import cleanup_gc_cli, cleanup_reap, finalize, finalize_local, git_ops, tracking
from pr_test_helpers import _git


pytestmark = pytest.mark.usefixtures("git_only_mux")


def test_local_fixture_uses_its_private_git_store(pr_repo):
    config, _, checkout, _ = pr_repo
    anchor_store = _git("rev-parse", "--path-format=absolute", "--git-common-dir",
                        cwd=config.default_repo.anchor)
    checkout_store = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=checkout)
    assert Path(checkout_store).resolve() == Path(anchor_store).resolve()


@pytest.fixture
def local_repo(pr_repo, monkeypatch):
    config, wid, checkout, _ = pr_repo
    repo = replace(config.default_repo, pr=cfg.PRConfig())
    config = replace(config, repos={config.repo_name: repo})
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    _git("merge", "--ff-only", f"worktree/{wid}", cwd=repo.anchor)
    _git("remote", "remove", "origin", cwd=repo.anchor)
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: True)
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
    return config, wid, checkout


@pytest.fixture
def foreign_repo(tmp_path):
    repo = tmp_path / "foreign"
    repo.mkdir()
    _git("init", "--template=", "-b", "master", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("commit", "--allow-empty", "-m", "foreign base", cwd=repo)
    return repo


def _foreign_selectors(monkeypatch, repo):
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo))


def test_finalize_local_content_without_fetch_or_ref_rewrite(local_repo, monkeypatch, capsys):
    config, wid, checkout = local_repo
    before = _git("show-ref", cwd=checkout)
    monkeypatch.setattr(git_ops, "fetch", lambda *a, **k: pytest.fail("no remote to fetch"))
    monkeypatch.setattr(finalize, "_reconcile_merged_pointers",
                        lambda *a, **k: pytest.fail("local finalization must not rewrite refs"))

    assert finalize.validate_and_finalize(wid, config)
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "finalized"
    assert _git("show-ref", cwd=checkout) == before
    assert "refs/heads/master" in capsys.readouterr().out


def test_inherited_selectors_cannot_hide_configured_target_remote(local_repo, foreign_repo, monkeypatch):
    config, _, checkout = local_repo
    _git("remote", "add", "origin", str(checkout / "missing-remote"), cwd=checkout)
    _foreign_selectors(monkeypatch, foreign_repo)
    assert finalize_local.resolve_upstream(config.default_repo, pr_mode=False) == ("origin/master", False)


@pytest.mark.parametrize("state", ["dirty", "uncontained"])
def test_inherited_selectors_cannot_authorize_foreign_content(
    local_repo, foreign_repo, monkeypatch, state,
):
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
    local_repo, foreign_repo, monkeypatch,
):
    config, wid, checkout = local_repo
    (foreign_repo / "foreign-only.txt").write_text("keep\n")
    _git("remote", "add", "origin", str(foreign_repo / "missing-remote"), cwd=foreign_repo)
    before = _git("show-ref", cwd=foreign_repo)
    _foreign_selectors(monkeypatch, foreign_repo)
    assert finalize.validate_and_finalize(wid, config)
    assert _git("show-ref", cwd=foreign_repo) == before
    assert (foreign_repo / "foreign-only.txt").read_text() == "keep\n"
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "finalized"


def test_local_finalization_refuses_hidden_untracked_files(local_repo):
    config, wid, checkout = local_repo
    _git("config", "status.showUntrackedFiles", "no", cwd=checkout)
    (checkout / "hidden.txt").write_text("keep\n")
    assert not finalize.validate_and_finalize(wid, config)
    assert (checkout / "hidden.txt").read_text() == "keep\n"
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"


@pytest.mark.parametrize("tracked_default", [False, True])
def test_local_finalization_preserves_declared_default_branch(local_repo, monkeypatch, tracked_default):
    config, wid, checkout = local_repo
    repo = config.default_repo
    _git("switch", "-c", "anchor-alternate", cwd=repo.anchor)
    _git("switch", repo.default_branch, cwd=checkout)
    if tracked_default:
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        record.branch = repo.default_branch
        tracking.save_record(record)
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: False)
    monkeypatch.setattr(finalize.procs, "checkout_retirement_blocker", lambda path: None)

    assert finalize.validate_and_finalize(wid, config)
    assert git_ops.ref_exists(f"refs/heads/{repo.default_branch}", cwd=repo.anchor)
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "finalized"


@pytest.mark.parametrize("retained", [False, True])
def test_local_finalize_then_cleanup_preserves_default(local_repo, monkeypatch, retained):
    config, wid, checkout = local_repo
    repo = config.default_repo
    _git("switch", "-c", "anchor-alternate", cwd=repo.anchor)
    _git("switch", repo.default_branch, cwd=checkout)
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    record.branch = repo.default_branch
    tracking.save_record(record)
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: retained)
    monkeypatch.setattr(finalize.procs, "checkout_retirement_blocker", lambda path: None)

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
def test_local_finalization_refuses_ignored_dirty_submodule(local_repo, foreign_repo, dirty):
    config, wid, checkout = local_repo
    (foreign_repo / "tracked.txt").write_text("base\n")
    _git("add", "tracked.txt", cwd=foreign_repo)
    _git("commit", "-m", "submodule content", cwd=foreign_repo)
    _git("-c", "protocol.file.allow=always", "submodule", "add", str(foreign_repo), "nested",
         cwd=checkout)
    _git("commit", "-m", "add submodule", cwd=checkout)
    _git("merge", "--ff-only", f"worktree/{wid}", cwd=config.default_repo.anchor)
    _git("config", "submodule.nested.ignore", "all", cwd=checkout)
    pending = checkout / "nested" / ("tracked.txt" if dirty == "modified" else "new.txt")
    pending.write_text("keep\n")
    assert not _git("status", "--porcelain", "--untracked-files=all", cwd=checkout)

    assert not finalize.validate_and_finalize(wid, config)
    assert pending.read_text() == "keep\n"
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"


@pytest.mark.parametrize("later", ["committed", "hidden-untracked"])
def test_local_cleanup_rechecks_content_after_finalize(local_repo, monkeypatch, later):
    config, wid, checkout = local_repo
    monkeypatch.setattr(finalize.procs, "checkout_retirement_blocker", lambda path: None)
    monkeypatch.setattr(cleanup_gc_cli.sessions, "_list_mux_sessions", lambda: [])
    assert finalize.validate_and_finalize(wid, config)
    pending = checkout / "later.txt"
    pending.write_text("keep\n")
    if later == "committed":
        _git("add", "later.txt", cwd=checkout)
        _git("commit", "-m", "work after finalization", cwd=checkout)
    else:
        _git("config", "status.showUntrackedFiles", "no", cwd=checkout)

    cleanup_gc_cli.cmd_cleanup(SimpleNamespace(clean=True, include_unused=False))
    assert pending.read_text() == "keep\n"
    assert git_ops.ref_exists(f"refs/heads/worktree/{wid}", cwd=config.default_repo.anchor)


def test_local_finalization_uses_registered_anchor_store(local_repo, foreign_repo):
    config, wid, _ = local_repo
    before = _git("show-ref", cwd=config.default_repo.anchor)
    assert finalize_local.content_error(
        config.default_repo, str(foreign_repo), f"worktree/{wid}", "refs/heads/master",
    ) is not None
    assert _git("show-ref", cwd=config.default_repo.anchor) == before


@pytest.mark.parametrize("state", ["dirty", "uncontained", "missing-default", "pr-required"])
def test_finalize_local_refuses_unproved_content(local_repo, state):
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
def test_finalize_local_rechecks_content_after_retirement_inspection(local_repo, monkeypatch, late_work):
    config, wid, checkout = local_repo
    monkeypatch.setattr(finalize, "_has_live_session", lambda record: False)
    if late_work == "hidden-untracked":
        _git("config", "status.showUntrackedFiles", "no", cwd=checkout)

    def new_work(_path):
        (checkout / "later.txt").write_text("keep\n")
        if late_work == "committed":
            _git("add", "later.txt", cwd=checkout)
            _git("commit", "-m", "new work during retirement inspection", cwd=checkout)
        return None

    monkeypatch.setattr(finalize.procs, "checkout_retirement_blocker", new_work)
    assert not finalize.validate_and_finalize(wid, config)
    assert (checkout / "later.txt").read_text() == "keep\n"
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"


def test_finalize_does_not_treat_remote_failure_as_local_only(local_repo):
    config, wid, checkout = local_repo
    _git("remote", "add", "origin", str(checkout / "missing-remote"), cwd=checkout)
    before = _git("show-ref", cwd=checkout)

    with pytest.raises(git_ops.GitError):
        finalize.validate_and_finalize(wid, config)
    assert checkout.exists()
    assert _git("show-ref", cwd=checkout) == before
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").status == "active"
