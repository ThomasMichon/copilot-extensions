"""Guarded source admission composes the real legacy Git reconciliation owner."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from collections.abc import Iterator
from typing import Any, Callable, cast

import pytest
from machine_transport import detect_platform

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg, front_door_cli, git_ops, output, pr_complete, tracking
from agent_worktrees.tracking_lifecycle import create_new_record
from test_pr_complete import _git, _squash_merge_upstream
from test_pr_source_publication import publication as publication, publish as source_publish

PrRepo = tuple[cfg.Config, str, Path, Path]

@pytest.fixture
def source(pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PrRepo:
    config, wid, root, remote = pr_repo
    config = replace(config, platform=detect_platform())
    directory = tmp_path / "manager" / "worktrees"
    directory.mkdir(parents=True)
    record = tracking.load_record_by_id(wid)
    assert record is not None
    record.platform = config.platform
    tracking.save_record(record, directory / f"{wid}.yaml")
    monkeypatch.setattr(cfg, "tracking_dir", lambda *_: directory)
    monkeypatch.setattr(cfg, "project_dir", lambda *_: directory.parent)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(front_door_cli, "resolve_invocation_project", lambda *_: True)
    monkeypatch.chdir(root)
    return config, wid, root, remote


@pytest.mark.parametrize("change", ["foreign", "incarnation", "allocation"])
def test_completion_rechecks_native_source_authority_before_mutation(
    source: PrRepo, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_worktrees import execution_spaces

    config, wid, root, _remote = source
    args = arguments(source)
    path = cfg.tracking_dir() / f"{wid}.yaml"
    before = _git("rev-parse", "HEAD", cwd=root)
    original = git_ops.fetch
    if change == "foreign":
        foreign = replace(config, platform="linux" if config.platform == "windows" else "windows")
        monkeypatch.setattr(execution_spaces, "project_record_config", lambda *_: foreign)
    else:
        def fetch(*a: Any, **kw: Any) -> Any:
            value = original(*a, **kw)
            import yaml
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            raw["creation_nonce" if change == "incarnation" else "receiving_allocation"] = (
                "replacement" if change == "incarnation" else {"identity": {"replacement": True}}
            )
            path.write_text(yaml.safe_dump(raw), encoding="utf-8")
            return value
        monkeypatch.setattr(git_ops, "fetch", fetch)
    code, value = invoke(args)
    assert code == 1 and not value["ok"], value
    assert "receiving" in value["error"] or "ownership changed" in value["error"]
    assert _git("rev-parse", "HEAD", cwd=root) == before
    assert not _git("for-each-ref", "--format=%(refname)", "refs/pre-complete-backups", cwd=root)


def arguments(source: PrRepo, *extra: str) -> list[str]:
    _config, _wid, root, remote = source
    return [
        "pr-complete", f"--repo={git_ops.slug_from_url(str(remote))}",
        f"--expected-branch={_git('symbolic-ref', '--short', 'HEAD', cwd=root)}",
        f"--expected-head={_git('rev-parse', 'HEAD', cwd=root)}", "--json", *extra,
    ]


def invoke(argv: list[str]) -> tuple[int, dict[str, Any]]:
    with output.capture_json_output() as capture:
        code = cli.main(argv)
    response: dict[str, Any] = json.loads(capture.getvalue())
    return code, response


def test_opt_in_lifecycle_reentrancy_keeps_default_and_peer_exclusion(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from agent_worktrees import worktree_lifecycle
    path = tmp_path / "source.yaml"
    def blocked() -> None:
        with pytest.raises(TimeoutError), worktree_lifecycle.guard(path, timeout=0):
            pytest.fail("another thread entered the same lifecycle")
    with ThreadPoolExecutor(max_workers=1) as pool, worktree_lifecycle.guard(path):
        with worktree_lifecycle.guard(path, timeout=0, reentrant=True):
            pool.submit(blocked).result(timeout=3)
        with pytest.raises(TimeoutError), worktree_lifecycle.guard(path, timeout=0):
            pytest.fail("default guard became reentrant")
        pool.submit(blocked).result(timeout=3)
    with worktree_lifecycle.guard(path, timeout=0):
        pass


@pytest.mark.parametrize("managed_source", [
    pytest.param(False, id="unmanaged"), pytest.param(True, id="managed"),
])
def test_guarded_publisher_keeps_checkout_lifecycle_through_probes_and_transport(
    publication: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, managed_source: bool,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from agent_worktrees import pr_source_publication, worktree_lifecycle
    from agent_worktrees.git_push_transport import observed_ref
    paths = [cfg.tracking_dir() / f"{publication.wid}.yaml"]
    if managed_source:
        from machine_transport import detect_platform
        from agent_worktrees import execution_spaces, pr_publication_checks
        manager = tmp_path / "source-manager"
        manager.mkdir()
        directory = manager / "worktrees"
        cfg.tracking_dir().rename(directory)
        monkeypatch.setattr(cfg, "tracking_dir", lambda *_: directory)
        monkeypatch.setattr(cfg, "project_dir", lambda *_: manager)
        publication.caller = replace(publication.caller, platform=detect_platform())
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: publication.caller)
        paths[0] = directory / f"{publication.wid}.yaml"
        source_id = "managed-source-owner"
        root = Path(publication.caller.default_repo.worktree_root) / source_id
        branch = "users/demo/managed-source"
        git_ops.git("worktree", "add", "-b", branch, str(root), "origin/master",
                    cwd=publication.caller.default_repo.anchor)
        (root / "source.txt").write_text("source-only\n")
        git_ops.git("add", "source.txt", cwd=str(root))
        git_ops.git("commit", "-m", "Managed source candidate", cwd=str(root))
        source_record = create_new_record(
            source_id, branch, str(root), publication.caller.repo_name,
            publication.caller.machine, publication.caller.platform, cfg.tracking_dir(),
        )
        execution_spaces.require_record_mutation(source_record, publication.caller, recover_receiving=False)
        publication.workspace, publication.branch = root, branch
        publication.head = git_ops.git("rev-parse", "HEAD", cwd=str(root)).stdout.strip()
        pr_publication_checks.record_self_review(
            root, "origin/master", publication.target.pr.publication_checks,
            lenses=["source"], summary="reviewed exact managed source",
        )
        paths.append(cfg.tracking_dir() / f"{source_id}.yaml")
    original_probe, original_push = observed_ref, git_ops.push
    phases: list[str] = []
    def blocked() -> None:
        for path in paths:
            with pytest.raises(TimeoutError), worktree_lifecycle.guard(path, timeout=0):
                pytest.fail("publisher lost caller or actual source lifecycle exclusion")
    with ThreadPoolExecutor(max_workers=1) as pool:
        def probe(*args: Any, **kwargs: Any) -> Any:
            pool.submit(blocked).result(timeout=3)
            phases.append("probe")
            return original_probe(*args, **kwargs)
        def push(*args: Any, **kwargs: Any) -> Any:
            pool.submit(blocked).result(timeout=3)
            phases.append("transport")
            return original_push(*args, **kwargs)
        monkeypatch.setattr(pr_source_publication, "observed_ref", probe)
        monkeypatch.setattr(git_ops, "push", push)
        result = cast(Callable[..., dict[str, Any]], source_publish)(publication)
    assert result["ok"], result
    assert "probe" in phases and "transport" in phases
    for path in paths:
        with worktree_lifecycle.guard(path, timeout=0):
            pass


def test_guarded_completion_keeps_checkout_lifecycle_during_real_fetch(
    source: PrRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from agent_worktrees import worktree_lifecycle
    _config, wid, root, _remote = source
    path = cfg.tracking_dir() / f"{wid}.yaml"
    original_fetch = git_ops.fetch
    checked: list[bool] = []
    def blocked() -> None:
        with pytest.raises(TimeoutError), worktree_lifecycle.guard(path, timeout=0):
            pytest.fail("completion lost lifecycle exclusion")
    with ThreadPoolExecutor(max_workers=1) as pool:
        def fetch(*args: Any, **kwargs: Any) -> Any:
            pool.submit(blocked).result(timeout=3)
            checked.append(True)
            return original_fetch(*args, **kwargs)
        monkeypatch.setattr(git_ops, "fetch", fetch)
        code, result = invoke([*arguments(source), "--dry-run"])
    assert code == 0 and result["ok"] and checked, result
    assert root.exists()
    with worktree_lifecycle.guard(path, timeout=0):
        pass


def _history_source(source: PrRepo, mode: str) -> tuple[list[str], str, bytes]:
    config, wid, root, _remote = source
    anchor = Path(config.default_repo.anchor)
    if mode in ("squash", "followup"):
        _squash_merge_upstream(anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash")
    if mode == "followup":
        record = tracking.load_record_by_id(wid)
        assert record is not None
        record.pr = tracking.PRRecord(
            state="merged", head_sha=_git("rev-parse", "HEAD", cwd=root),
            base_sha=_git("merge-base", "origin/master", "HEAD", cwd=root),
        )
        tracking.save_record(record)
        (root / "later.txt").write_text("keep local follow-up\n")
        _git("add", "later.txt", cwd=root)
        _git("commit", "-m", "later work", cwd=root)
        _squash_merge_upstream(anchor, files={"a.txt": "later upstream\n"}, msg="overlap")
    if mode in ("fast-forward", "noop"):
        _git("reset", "--hard", "origin/master", cwd=root)
    if mode in ("unmerged", "fast-forward"):
        _squash_merge_upstream(anchor, files={"upstream.txt": "advance\n"}, msg="advance")
    record_before = (cfg.tracking_dir() / f"{wid}.yaml").read_bytes()
    before = _git("rev-parse", "HEAD", cwd=root)
    args = arguments(source)
    return args, before, record_before


@pytest.mark.parametrize("mode", ["squash", "followup", "unmerged", "fast-forward", "noop"])
def test_guarded_preview_preserves_original_tip_and_ledger(source: PrRepo, mode: str) -> None:
    _config, _wid, root, _remote = source
    args, before, record_before = _history_source(source, mode)
    preview_code, preview = invoke([*args, "--dry-run"])
    assert preview_code == 0 and preview["status"] == "preview", preview
    assert _git("rev-parse", "HEAD", cwd=root) == before
    assert not _git("for-each-ref", "--format=%(refname)", "refs/pre-complete-backups", cwd=root)
    assert (cfg.tracking_dir() / f"{_wid}.yaml").read_bytes() == record_before


@pytest.mark.parametrize("mode", ["squash", "followup", "unmerged", "fast-forward", "noop"])
def test_guarded_real_history_preserves_original_tip_and_subsequent_work(source: PrRepo, mode: str) -> None:
    config, wid, root, _remote = source
    args, before, record_before = _history_source(source, mode)
    code, value = invoke(args)
    assert code == 0 and value["status"] == "confirmed", json.dumps(value, indent=2)
    result = value["result"]
    assert result["worktree_id"] == wid and value["source"]["expected_head"] == before
    assert result["action"] == {
        "squash": "reset-past-squash", "followup": "rebased",
        "unmerged": "rebased", "fast-forward": "fast-forwarded", "noop": "up-to-date",
    }[mode]
    if mode in ("squash", "followup", "unmerged"):
        assert _git("rev-parse", result["backup_ref"], cwd=root) == before
    if mode == "followup":
        assert result["kept"] == 1
        assert (root / "later.txt").read_text() == "keep local follow-up\n"
        assert (root / "a.txt").read_text() == "later upstream\n"
    if mode == "unmerged":
        assert result["kept"] == 2 and (root / "b.txt").read_text() == "two\n"
    assert (cfg.tracking_dir() / f"{wid}.yaml").read_bytes() == record_before


@pytest.mark.parametrize("damage", [
    "head", "branch", "dirty", "foreign", "record", "terminal", "anchor", "common", "detached", "detached-name", "remote",
])
def test_bad_source_refuses_before_fetch_backup_or_reconciliation(source: PrRepo, monkeypatch: pytest.MonkeyPatch, damage: str) -> None:
    config, wid, root, _remote = source
    args = arguments(source)
    if damage == "head":
        args[3] = "--expected-head=" + "0" * 40
    elif damage == "branch":
        args[2] = "--expected-branch=other"
    elif damage == "dirty":
        (root / "dirty.txt").write_text("uncommitted")
    elif damage == "foreign":
        args[1] = "--repo=other/repository"
    elif damage in ("record", "terminal"):
        path = cfg.tracking_dir() / f"{wid}.yaml"
        record = tracking.load_record(path)
        if damage == "record":
            record.repo = "other-project"
        else:
            record.status = "finalized"
        tracking.save_record(record, path)
    elif damage == "anchor":
        monkeypatch.chdir(config.default_repo.anchor)
    elif damage == "common":
        anchor = root.parents[1] / "other-anchor"
        _git("init", "--template=", str(anchor), cwd=root)
        config = replace(config, repos={"ext": replace(config.default_repo, anchor=str(anchor))})
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    elif damage in ("detached", "detached-name"):
        _git("switch", "--detach", "HEAD", cwd=root)
        if damage == "detached-name":
            args[2] = "--expected-branch=(detached)"
    elif damage == "remote":
        _git("remote", "add", "other", str(root.parents[1] / "other.git"), cwd=root)
        config = replace(config, repos={"ext": replace(config.default_repo, remote="other")})
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(pr_complete, "complete_worktree", lambda *a, **k: pytest.fail("owner invoked"))
    monkeypatch.setattr(git_ops, "fetch", lambda *a, **k: pytest.fail("fetch"))
    code, value = invoke(args)
    assert code == 1 and value["status"] == "not-attempted" and value["error"]
    assert not _git("for-each-ref", "--format=%(refname)", "refs/pre-complete-backups", cwd=root)


def test_inherited_git_selectors_cannot_redirect_owner_or_leak_out_of_request(source: PrRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _wid, root, _remote = source
    anchor = Path(config.default_repo.anchor)
    _squash_merge_upstream(anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash")
    args = arguments(source)
    anchor_head = _git("rev-parse", "HEAD", cwd=anchor)
    index = anchor / "unrelated-index"
    monkeypatch.setenv("GIT_DIR", str(anchor / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(index))
    code, value = invoke(args)
    assert code == 0 and value["status"] == "confirmed", value
    assert not index.exists()
    assert os.environ["GIT_DIR"] == str(anchor / ".git")
    assert os.environ["GIT_INDEX_FILE"] == str(index)
    assert Path(git_ops.git("rev-parse", "--absolute-git-dir", cwd=root).stdout.strip()).resolve() == (anchor / ".git").resolve()
    assert git_ops.git("rev-parse", "HEAD", cwd=anchor, isolated_repository=True).stdout.strip() == anchor_head
    assert git_ops.git("rev-parse", "HEAD", cwd=root, isolated_repository=True).stdout.strip() == anchor_head


@pytest.mark.parametrize("extra", [
    ["--expected-head=abcd"], ["--expected-head=HEAD"], ["--expected-head=" + "A" * 40],
    ["--expected-branch="], ["--config=worker.json"], ["other-worktree"],
])
def test_guarded_fields_cannot_degrade_to_legacy_selection(source: PrRepo, extra: list[str]) -> None:
    code, value = invoke([*arguments(source), *extra])
    assert code == 1 and value["status"] == "not-attempted", value


@pytest.mark.parametrize("after_backup", [False, True])
def test_rechecks_after_fetch_and_before_history_mutation(source: PrRepo, monkeypatch: pytest.MonkeyPatch, after_backup: bool) -> None:
    config, _wid, root, _remote = source
    _squash_merge_upstream(
        Path(config.default_repo.anchor), files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash",
    )
    args = arguments(source)
    before = _git("rev-parse", "HEAD", cwd=root)
    if after_backup:
        real = git_ops.git
        def run(*argv: Any, **kwargs: Any) -> Any:
            result = real(*argv, **kwargs)
            if argv[0] == "update-ref" and argv[1].startswith("refs/pre-complete-backups/"):
                (root / "new.txt").write_text("concurrent edit")
            return result
        monkeypatch.setattr(git_ops, "git", run)
    else:
        real_fetch = git_ops.fetch
        def fetch(*argv: Any, **kwargs: Any) -> None:
            real_fetch(*argv, **kwargs)
            (root / "new.txt").write_text("concurrent edit")
        monkeypatch.setattr(git_ops, "fetch", fetch)
    code, value = invoke(args)
    assert code == 1 and value["status"] == "unknown", value
    assert _git("rev-parse", "HEAD", cwd=root) == before
    assert (root / "new.txt").read_text() == "concurrent edit"
    if after_backup:
        assert _git("rev-parse", value["result"]["backup_ref"], cwd=root) == before
    else:
        assert not _git("for-each-ref", "--format=%(refname)", "refs/pre-complete-backups", cwd=root)


def test_owner_conflict_keeps_unknown_result_and_original_tip(source: PrRepo) -> None:
    config, _wid, root, _remote = source
    _squash_merge_upstream(Path(config.default_repo.anchor), files={"a.txt": "conflict\n"}, msg="conflict")
    before = _git("rev-parse", "HEAD", cwd=root)
    code, value = invoke(arguments(source))
    assert code == 1 and value["status"] == "unknown", value
    assert "aborted" in value["result"]["error"]
    assert _git("rev-parse", value["result"]["backup_ref"], cwd=root) == before
    assert _git("rev-parse", "HEAD", cwd=root) == before
    assert git_ops.is_clean(cwd=root)


def test_guarded_backup_failure_remains_unknown_and_never_reconciles(source: PrRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    config, _wid, root, _remote = source
    _squash_merge_upstream(
        Path(config.default_repo.anchor), files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash",
    )
    args = arguments(source)
    before = _git("rev-parse", "HEAD", cwd=root)
    real = git_ops.git
    def run(*argv: Any, **kwargs: Any) -> Any:
        if argv[0] == "update-ref" and argv[1].startswith("refs/pre-complete-backups/"):
            return subprocess.CompletedProcess(argv, 1, "", "ref locked")
        return real(*argv, **kwargs)
    monkeypatch.setattr(git_ops, "git", run)
    code, value = invoke(args)
    assert code == 1 and value["status"] == "unknown"
    assert "recovery ref" in value["result"]["error"]
    assert _git("rev-parse", "HEAD", cwd=root) == before


def test_post_backup_validation_timeout_preserves_owner_result_and_recovery(source: PrRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    config, _wid, root, _remote = source
    _squash_merge_upstream(
        Path(config.default_repo.anchor), files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash",
    )
    args = arguments(source)
    before = _git("rev-parse", "HEAD", cwd=root)
    real = git_ops.git
    saved: list[str] = []
    def run(*argv: Any, **kwargs: Any) -> Any:
        if saved and argv[:3] == ("--no-replace-objects", "remote", "get-url"):
            raise subprocess.TimeoutExpired(["git", *argv], 30)
        result = real(*argv, **kwargs)
        if argv[0] == "update-ref" and argv[1].startswith("refs/pre-complete-backups/"):
            saved.append(argv[1])
        return result
    monkeypatch.setattr(git_ops, "git", run)
    code, value = invoke(args)
    assert code == 1 and value["status"] == "unknown", value
    assert "timed out" in value["error"]
    assert value["result"]["backup_ref"] == saved[0]
    assert _git("rev-parse", saved[0], cwd=root) == before
    assert _git("rev-parse", "HEAD", cwd=root) == before


@pytest.mark.parametrize("operation", ["reset", "rebase"])
def test_post_mutation_head_failure_retains_accumulated_owner_and_original_tip(
    source: PrRepo, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    import subprocess

    config, wid, root, _remote = source
    before_pr = _git("rev-parse", "HEAD", cwd=root)
    record = tracking.load_record_by_id(wid)
    assert record is not None
    record.pr = tracking.PRRecord(
        state="merged", head_sha=before_pr,
        base_sha=_git("merge-base", "origin/master", "HEAD", cwd=root),
    )
    tracking.save_record(record)
    _squash_merge_upstream(
        Path(config.default_repo.anchor), files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash",
    )
    if operation == "rebase":
        (root / "later.txt").write_text("preserved follow-up\n")
        _git("add", "later.txt", cwd=root)
        _git("commit", "-m", "follow-up", cwd=root)
    args = arguments(source)
    before = _git("rev-parse", "HEAD", cwd=root)
    real = git_ops.git
    mutated: list[tuple[Any, ...]] = []
    def run(*argv: Any, **kwargs: Any) -> Any:
        if mutated and argv == ("rev-parse", "--short", "HEAD"):
            if operation == "reset":
                raise OSError("Git launch failed after reset")
            raise subprocess.TimeoutExpired(["git", *argv], 30)
        result = real(*argv, **kwargs)
        if argv[0] == operation and result.returncode == 0:
            mutated.append(argv)
        return result
    monkeypatch.setattr(git_ops, "git", run)
    code, value = invoke(args)
    assert code == 1 and value["status"] == "unknown" and mutated, value
    assert value["result"]["worktree_id"] == wid
    assert value["result"]["branch"] == args[2].partition("=")[2]
    assert _git("rev-parse", value["result"]["backup_ref"], cwd=root) == before
    assert _git("rev-parse", "HEAD", cwd=root) != before
    if operation == "rebase":
        assert (root / "later.txt").read_text() == "preserved follow-up\n"
    else:
        assert _git("rev-parse", "HEAD", cwd=root) == _git("rev-parse", "origin/master", cwd=root)


@pytest.mark.parametrize("operation", ["completion", "source-publication"])
def test_source_operations_share_publication_then_metadata_order_with_recovery(
    source: PrRepo, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event
    from types import SimpleNamespace
    from agent_worktrees import pr_publish, pr_source_complete, pr_source_publication

    config, wid, root, remote = source
    requested = Event()
    real_publish = pr_publish.publish_lock
    real_metadata = pr_publish.metadata_lock
    @contextmanager
    def publish(path: str, **kwargs: Any) -> Iterator[None]:
        requested.set()
        with real_publish(path, acquire_timeout=5):
            yield
    monkeypatch.setattr(pr_publish, "publish_lock", publish)
    def evidence(*args: Any, **kwargs: Any) -> Any:
        raise pr_source_publication.SourcePublicationError("bounded publication control")
    monkeypatch.setattr(pr_source_publication, "source_evidence", evidence)
    args = SimpleNamespace(
        repo=git_ops.slug_from_url(str(remote)), worktree_id=None, config=None,
        expected_branch=_git("symbolic-ref", "--short", "HEAD", cwd=root),
        expected_head=_git("rev-parse", "HEAD", cwd=root), dry_run=True, json=True,
    )
    def action() -> dict[str, Any]:
        if operation == "completion":
            return cast(Callable[[Any], dict[str, Any]], pr_source_complete.complete)(args)
        return pr_source_publication.publish_source(
            wid, config, target_repo=args.repo, workspace=str(root),
            branch="feature/topic", expected_head=args.expected_head,
        )
    with ThreadPoolExecutor(max_workers=1) as pool:
        # Existing rewrite/recovery paths acquire this real prefix in this order.
        with real_publish(str(root), acquire_timeout=5):
            future = pool.submit(action)
            assert requested.wait(5), "source operation did not reach publication admission"
            with real_metadata(wid, project=config.repo_name, timeout=2):
                pass
        value = future.result(timeout=15)
    if operation == "completion":
        assert value["status"] == "preview" and value["ok"], value
    else:
        assert value["publication_state"] == "not_pushed"
        assert value["error"] == "bounded publication control"


def test_legacy_parser_still_runs_without_guards_and_projectless_new_form_is_structured(source: PrRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    _config, wid, _root, _remote = source
    code, value = invoke(["pr-complete", wid, "--dry-run", "--json"])
    assert code == 0 and "source" not in value and value["success"], json.dumps(value, indent=2)
    monkeypatch.setattr(front_door_cli, "resolve_invocation_project", lambda *_: False)
    code, value = invoke(arguments(source))
    assert code == 1 and value["status"] == "not-attempted"
    with pytest.raises(SystemExit) as exc:
        invoke([*arguments(source), "--expected-hedd=x"])
    assert exc.value.code == 2


def process_main(directory: str | Path, argv: list[str], *, prior: bool = False) -> int:
    directory = Path(directory)
    config = cfg.Config(
        srcroot=str(directory), machine="test", platform=detect_platform(), repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor=str(directory / "anchor"), worktree_root=str(directory / "worktrees"),
            default_branch="master", remote="origin",
            pr=cfg.PRConfig(enabled=True, provider="gitea", head_scheme="snapshot"),
        )},
    )
    cfg.load_config = lambda *_a, **_k: config
    cfg.load_project_config = lambda *_a, **_k: config
    cfg.tracking_dir = lambda *_: directory / "manager" / "worktrees"
    cfg.project_dir = lambda *_: directory / "manager"
    front_door_cli.resolve_invocation_project = lambda *_: True
    if prior:
        from agent_worktrees import pr_author_cli
        assert argv[0] == "pr-author-capabilities", "New syntax reached a prior receiver"
        repository = next(word.partition("=")[2] for word in argv if word.startswith("--repo="))
        value = cast(Callable[[cfg.Config, str], dict[str, Any]], pr_author_cli.capabilities)(config, repository)
        value["operations"].pop("local-source-complete")
        output._json_output(value)
        return 0
    return cli.main(argv)


@pytest.mark.parametrize("prior", [False, True])
def test_real_frontend_and_selected_owner_process_reconcile_actual_source(source: PrRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], prior: bool) -> None:
    import agent_worktrees

    config, wid, root, _remote = source
    _squash_merge_upstream(
        Path(config.default_repo.anchor), files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash",
    )
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import __main__ as frontend, native_cli
    args = frontend.build_parser().parse_args([
        "complete", *arguments(source)[1:], "--worktrees-command", "selected-owner",
    ])
    code = (
        "import sys; from test_pr_source_complete import process_main; "
        "raise SystemExit(process_main(sys.argv[1],sys.argv[3:],prior=sys.argv[2]=='prior'))"
    )
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((
        str(Path(agent_worktrees.__file__).parent.parent), str(Path(__file__).parent),
        os.environ.get("PYTHONPATH", ""),
    )))
    before = _git("rev-parse", "HEAD", cwd=root)
    assert native_cli.run(args, prefix_factory=lambda: [
        sys.executable, "-c", code, str(root.parents[1]), "prior" if prior else "current",
    ]) == (1 if prior else 0)
    value = json.loads(capsys.readouterr().out)
    if prior:
        assert value["status"] == "not-attempted"
        assert _git("rev-parse", "HEAD", cwd=root) == before
        assert not _git("for-each-ref", "--format=%(refname)", "refs/pre-complete-backups", cwd=root)
        return
    assert value["status"] == "confirmed" and value["source"]["worktree_id"] == wid
    assert value["result"]["action"] == "reset-past-squash"
    assert _git("rev-parse", "HEAD", cwd=root) == _git("rev-parse", "origin/master", cwd=root)


@pytest.mark.parametrize("repository", ["repository", "different"])
def test_azure_remote_aliases_preserve_repository_binding(source: PrRepo, monkeypatch: pytest.MonkeyPatch, repository: str) -> None:
    from agent_worktrees import pr_config, pr_source_publication

    config, _wid, _root, _remote = source
    args = arguments(source)
    repo = replace(config.default_repo, pr=replace(config.default_repo.pr, provider="azure-devops"))
    config = replace(config, repos={config.repo_name: repo})
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        pr_config, "resolve_repo_config_for_slug",
        lambda *a: pr_config.ForeignRepoResolution(repo, config.repo_name, same_as_active=True),
    )
    monkeypatch.setattr(
        pr_config, "_resolve_repo_remote",
        lambda *a: "https://dev.azure.com/example/project/_git/repository",
    )
    original = pr_source_publication._git
    def git(root: Path, *argv: str) -> str:
        if argv == ("remote", "get-url", repo.remote):
            return f"git@ssh.dev.azure.com:v3/example/project/{repository}"
        return original(root, *argv)
    monkeypatch.setattr(pr_source_publication, "_git", git)
    code, value = invoke(args)
    assert code == (0 if repository == "repository" else 1), value
    assert value["status"] == ("confirmed" if repository == "repository" else "not-attempted")


@pytest.mark.parametrize("preview", [False, True])
def test_completion_fetch_and_merge_hook_leave_linked_publication_unlocked(
    source: PrRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, preview: bool,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import shlex
    from agent_worktrees import pr_publish

    config, wid, root, _remote = source
    _git("reset", "--hard", "origin/master", cwd=root)
    other = tmp_path / "linked"
    _git("worktree", "add", "--detach", str(other), "HEAD", cwd=root)
    _squash_merge_upstream(
        Path(config.default_repo.anchor), files={"advance.txt": "upstream\n"}, msg="advance",
    )
    marker = tmp_path / "hook-acquired"
    source_git_dir = Path(_git("rev-parse", "--absolute-git-dir", cwd=root))
    probe = tmp_path / "hook-probe.py"
    probe.write_text(
        "from pathlib import Path\n"
        "from agent_worktrees import pr_publish\n"
        f"with pr_publish.publish_lock({str(other)!r}, acquire_timeout=1):\n"
        f"    Path({str(marker)!r}).write_text('acquired')\n",
    )
    hook_dir = Path(_git("rev-parse", "--git-path", "hooks", cwd=root))
    if not hook_dir.is_absolute():
        hook_dir = root / hook_dir
    hook_dir.mkdir(exist_ok=True)
    hook = hook_dir / "post-merge"
    hook.write_text(f"#!/bin/sh\n{shlex.quote(sys.executable)} {shlex.quote(str(probe))}\n")
    hook.chmod(0o700)
    original = git_ops.fetch
    phases: list[str] = []
    def acquire() -> bool:
        with pr_publish.publish_lock(str(other), acquire_timeout=1), pr_publish.metadata_lock(
            wid, project=config.repo_name, timeout=1,
        ):
            with (source_git_dir / "agent-worktrees-complete.lock").open("a+b") as handle:
                acquired = pr_publish._try_publish_lock(handle)
                if acquired:
                    pr_publish._unlock_publish_file(handle)
                assert not acquired, "the admitted source lost completion exclusion"
            return True
    with ThreadPoolExecutor(max_workers=1) as pool:
        def fetch(*args: Any, **kwargs: Any) -> Any:
            assert pool.submit(acquire).result(timeout=3)
            phases.append("fetch")
            return original(*args, **kwargs)
        monkeypatch.setattr(git_ops, "fetch", fetch)
        code, value = invoke(arguments(source, *(["--dry-run"] if preview else [])))
    assert code == 0 and value["ok"], value
    assert phases == ["fetch"]
    assert marker.exists() is not preview
