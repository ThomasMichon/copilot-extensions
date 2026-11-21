"""Transport authority cannot be inherited as permission across a fork."""

import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
import sys
import time
from threading import Barrier, Event
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from agent_procutil.owned_process import run_caller_owned
from machine_transport import detect_platform
from agent_worktrees import config as cfg, git_ops, pr_transport_fence, tracking
from agent_worktrees.git_push_transport import observed_ref, pinned_destination
from agent_worktrees.providers.base import PRScope, ProviderError, PullResult
from agent_worktrees.worktree_identity import _infer_worktree_id_from_cwd
from test_pr_source_publication import publication as publication


@pytest.fixture
def native_source_owners(
    pr_repo: tuple[cfg.Config, str, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[cfg.Config, tracking.WorktreeRecord, tracking.WorktreeRecord, Path]:
    config, wid, _root, remote = pr_repo
    config = replace(config, platform=detect_platform())
    project = tmp_path / "project"
    project.mkdir()
    ledger = project / "worktrees"
    cfg.tracking_dir().rename(ledger)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: ledger)
    caller = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    caller.platform = config.platform
    tracking.save_record(caller)
    workspace = Path(config.default_repo.worktree_root) / "source-owner"
    git_ops.git("clone", "--quiet", str(remote), str(workspace), cwd=str(tmp_path))
    owner = replace(caller, worktree_id="source-owner", worktree_path=str(workspace), branch="master", prs=[])
    path = cfg.tracking_dir() / f"{owner.worktree_id}.yaml"
    tracking.save_record(owner, path)
    owner = tracking.load_record(path)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda name, **k: replace(config, repo_name=name))
    return config, caller, owner, workspace


@pytest.mark.parametrize("field,value", [
    ("creation_nonce", "replacement-incarnation"),
    ("receiving_allocation", {"identity": {"replacement": True}}),
])
def test_source_incarnation_change_refuses_transport_admission(
    native_source_owners: tuple[cfg.Config, tracking.WorktreeRecord, tracking.WorktreeRecord, Path],
    field: str, value: object,
) -> None:
    from agent_worktrees import pr_authority

    config, caller, owner, workspace = native_source_owners
    with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace) as recheck:
        raw = yaml.safe_load(owner.yaml_path.read_text(encoding="utf-8"))
        raw[field] = value
        owner.yaml_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with pytest.raises(ValueError, match="ownership changed|receiving allocation"):
            recheck()


@pytest.mark.parametrize("same_as_caller", [False, True])
def test_discovered_source_owner_requires_native_receiving_authority(
    native_source_owners: tuple[cfg.Config, tracking.WorktreeRecord, tracking.WorktreeRecord, Path],
    monkeypatch: pytest.MonkeyPatch, same_as_caller: bool,
) -> None:
    from agent_worktrees import pr_authority

    config, caller, _owner, workspace = native_source_owners
    workspace = Path(caller.worktree_path) if same_as_caller else workspace
    foreign = replace(config, platform="linux" if config.platform == "windows" else "windows")
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: foreign)
    with pytest.raises(ValueError, match="execution.*platform|receiving"):
        with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace):
            pytest.fail("A foreign source owner was admitted using the caller's authority")


@pytest.mark.parametrize("lookup", ["caller", "linked"])
def test_every_existing_source_owner_is_qualified(
    native_source_owners: tuple[cfg.Config, tracking.WorktreeRecord, tracking.WorktreeRecord, Path],
    lookup: str, tmp_path: Path,
) -> None:
    from agent_worktrees import pr_authority

    config, caller, owner, workspace = native_source_owners
    if lookup == "caller":
        workspace, owner = Path(caller.worktree_path), caller
    else:
        workspace = tmp_path / "linked-publication-source"
        git_ops.git("worktree", "add", "-b", "linked", str(workspace), "HEAD",
                    cwd=owner.worktree_path)
        git_dir = Path(git_ops.git("rev-parse", "--absolute-git-dir", cwd=workspace).stdout.strip())
        owner = replace(owner, worktree_id=git_dir.name, worktree_path=str(workspace), branch="linked")
        tracking.save_record(owner, cfg.tracking_dir() / f"{owner.worktree_id}.yaml")
    duplicate = replace(owner, worktree_id="duplicate-source-owner", status="finalized")
    tracking.save_record(duplicate, cfg.tracking_dir() / f"{duplicate.worktree_id}.yaml")
    with pytest.raises(ValueError, match="closing or finalized|receiving"):
        with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace):
            pytest.fail("An existing source owner escaped qualification")


def test_readonly_inference_defers_adoption_while_source_publication_holds_path(
    pr_repo: tuple[cfg.Config, str, Path, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_authority

    config, wid, root, _remote = pr_repo
    config = replace(config, platform=detect_platform())
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    workspace = tmp_path / "external"
    git_ops.git("worktree", "add", "-b", "source-inference", str(workspace), "HEAD", cwd=root)
    monkeypatch.setattr(cfg, "active_project", lambda: config.repo_name)
    monkeypatch.chdir(workspace)
    caller = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace):
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(_infer_worktree_id_from_cwd, config).result(timeout=2) == "external"
        assert time.monotonic() - started < 2
        assert not (cfg.tracking_dir() / "external.yaml").exists()
    assert _infer_worktree_id_from_cwd(config) == "external"
    assert (cfg.tracking_dir() / "external.yaml").is_file()


def test_real_prepush_readonly_inference_keeps_unmanaged_publication_live(
    pr_repo: tuple[cfg.Config, str, Path, Path], tmp_path: Path,
) -> None:
    import shlex
    from agent_worktrees import pr_authority, pr_publish

    config, wid, root, remote = pr_repo
    workspace = tmp_path / "external"
    git_ops.git("worktree", "add", "-b", "hook-inference", str(workspace), "HEAD", cwd=root)
    probe = tmp_path / "infer.py"
    probe.write_text(
        "from pathlib import Path\n"
        "from agent_worktrees import config as cfg, worktree_identity\n"
        f"cfg.install_dir = lambda: Path({str(cfg.install_dir())!r})\n"
        f"cfg.tracking_dir = lambda *a: Path({str(cfg.tracking_dir())!r})\n"
        "cfg.active_project = lambda: 'ext'\n"
        f"cfg.load_config = lambda: cfg.Config(srcroot={config.srcroot!r}, repo_name='ext', machine='test', "
        f"platform={detect_platform()!r}, repos={{'ext': cfg.RepoConfig("
        f"anchor={config.default_repo.anchor!r}, worktree_root={config.default_repo.worktree_root!r})}})\n"
        "assert worktree_identity._infer_worktree_id_from_cwd() == 'external'\n",
        encoding="utf-8",
    )
    hook_dir = Path(git_ops.git("rev-parse", "--git-path", "hooks", cwd=workspace).stdout.strip())
    if not hook_dir.is_absolute():
        hook_dir = workspace / hook_dir
    hook = hook_dir / "pre-push"
    hook.write_text(
        f"#!/bin/sh\nexec {shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(probe.as_posix())}\n",
        encoding="utf-8",
    )
    hook.chmod(0o700)
    caller = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace):
        result = pr_publish.push_checked(
            None, "origin", "HEAD:refs/heads/hook-inference", cwd=str(workspace), timeout=10,
        )
        assert result.ok, result.stderr
        assert not (cfg.tracking_dir() / "external.yaml").exists()
    assert git_ops.git("--git-dir", str(remote), "rev-parse", "hook-inference", cwd=root).stdout.strip() == result.published_head_sha


@pytest.mark.parametrize("creator", ["create_new_record", "create_new_record_if_absent", "adopt_linked_worktree"])
def test_unmanaged_source_adoption_waits_until_transport_releases_its_source_path(
    pr_repo: tuple[cfg.Config, str, Path, Path], tmp_path: Path, creator: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_authority, worktree_identity

    config, wid, root, _remote = pr_repo
    workspace = tmp_path / "external"
    git_ops.git("worktree", "add", "-b", "source-admission", str(workspace), "HEAD", cwd=str(root))
    monkeypatch.setattr(cfg, "active_project", lambda: config.repo_name)
    caller = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    attempting, completed = Event(), Event()

    def adopt() -> None:
        attempting.set()
        if creator == "adopt_linked_worktree":
            assert worktree_identity._adopt_linked_worktree(workspace) == "external"
        else:
            getattr(tracking, creator)(
                "external", "source-admission", str(workspace), config.repo_name,
                config.machine, config.platform, cfg.tracking_dir(),
            )
        completed.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace) as recheck:
            recheck()
            adoption = pool.submit(adopt)
            assert attempting.wait(3)
            assert not completed.wait(0.2), "Native creation registered the source during transport"
        adoption.result(timeout=5)
    assert completed.is_set()


def test_fork_provider_creation_keeps_its_actual_source_head_fenced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "state")
    entered, release = Event(), Event()
    scope = PRScope(
        repo="upstream/different-name", head="alice:topic", base="main", title="Change",
        head_identity="github.com/alice/source-name", expected_head_sha="a" * 40,
    )
    observed: list[tuple[str, str]] = []

    def observe(repo: str, branch: str, **kwargs: Any) -> dict[str, str]:
        observed.append((repo, branch))
        return {"repository": repo, "branch": branch, "head_sha": "a" * 40}

    provider = SimpleNamespace(authority_endpoint=lambda base: "github.com", observe_branch=observe)

    @pr_transport_fence.provider_creation
    def create(self: Any, scope: PRScope, *, token: str | None = None) -> PullResult:
        entered.set()
        assert release.wait(5)
        return PullResult(number=1)

    @pr_transport_fence.publication
    def push(remote: str, branch: str, *, cwd: str, timeout: float) -> git_ops.PushResult:
        return git_ops.PushResult(ok=True)

    with ThreadPoolExecutor(max_workers=1) as pool:
        creating = pool.submit(create, provider, scope)
        try:
            assert entered.wait(3)
            with pinned_destination("fork", ("https://github.com/alice/source-name.git",)):
                result = push("fork", "topic", cwd=str(tmp_path), timeout=0.1)
            assert not result.ok, result
            with pinned_destination("origin", ("https://github.com/upstream/different-name.git",)):
                assert push("origin", "topic", cwd=str(tmp_path), timeout=0.1).ok
            with pytest.raises(TimeoutError), pr_transport_fence.Head(
                "github.com/upstream/different-name", "alice:topic",
            ).guard():
                pytest.fail("The exact target head observation lost its fence")
        finally:
            release.set()
        assert creating.result(timeout=3).number == 1
    assert observed == [("alice/source-name", "topic")]


@pytest.mark.parametrize("drift", ["head", "binding"])
def test_provider_creation_rechecks_published_source_at_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "state")
    scope = PRScope(
        repo="upstream/repo", head="alice:topic", base="main", title="Change",
        head_identity="github.com/alice/fork", expected_head_sha="a" * 40,
    )

    def observe(repo: str, branch: str, **kwargs: Any) -> dict[str, str]:
        if drift == "binding":
            scope.head = "alice:replacement"
        return {"repository": repo, "branch": branch, "head_sha": "b" * 40 if drift == "head" else "a" * 40}

    provider = SimpleNamespace(authority_endpoint=lambda base: "github.com", observe_branch=observe)
    attempted: list[bool] = []

    @pr_transport_fence.provider_creation
    def create(self: Any, scope: PRScope, *, token: str | None = None) -> PullResult:
        attempted.append(True)
        return PullResult(number=1)

    with pytest.raises(ProviderError, match="head|binding"):
        create(provider, scope)
    assert not attempted


def test_qualified_foreign_creation_carries_its_source_into_provider_admission(
    native_source_owners: tuple[cfg.Config, tracking.WorktreeRecord, tracking.WorktreeRecord, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_checks, pr_foreign_create
    from agent_worktrees.providers import base

    config, caller, _owner, workspace = native_source_owners
    target = replace(config.default_repo, pr=cfg.PRConfig(
        enabled=True, provider="github", source_attribution=False,
    ))
    resolution = pr_config.ForeignRepoResolution(target, "upstream/repo", same_as_active=False)
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *a: resolution)
    captured: list[PRScope] = []

    def create(scope: PRScope, **kwargs: Any) -> PullResult:
        captured.append(scope)
        return PullResult(number=7, url="https://github.com/upstream/repo/pull/7")

    provider = SimpleNamespace(
        authority_endpoint=lambda base: "github.com", create_pull=create,
        find_pull_by_head=lambda *a, **k: None,
    )
    monkeypatch.setattr(base, "get_provider", lambda *a: provider)
    monkeypatch.setattr(base, "account_token_for_slug", lambda *a: None)
    monkeypatch.setattr(pr_foreign_checks, "qualify", lambda *a, **k: {"head": "a" * 40})
    monkeypatch.setattr(pr_foreign_checks, "after_create", lambda *a, **k: {"ok": True})
    result = pr_foreign_create.create_foreign_pr_from_branch(
        caller.worktree_id, config, target_repo="upstream/repo", from_branch="topic",
        title="Change", source_workspace=str(workspace), expected_head="a" * 40,
    )
    assert result.get("pr_opened") and result.get("claimed"), result
    assert len(captured) == 1
    assert captured[0].head_identity == "github.com/upstream/repo"
    assert captured[0].expected_head_sha == "a" * 40


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_invalid_fence_budget_is_rejected_before_destination_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout: object,
) -> None:
    monkeypatch.setattr(pr_transport_fence, "_targets", lambda *a, **k: pytest.fail("Invalid budget reached destination capture"))

    @pr_transport_fence.publication
    def transport(remote: str, branch: str, *, cwd: str, timeout: object) -> git_ops.PushResult:
        pytest.fail("Invalid budget reached transport")

    result = transport("origin", "topic", cwd=str(tmp_path), timeout=timeout)
    assert not result.ok


def test_unpinned_capture_respects_the_callers_fence_budget(
    pr_repo: tuple[cfg.Config, str, Path, Path],
) -> None:
    from agent_worktrees import pr_publish

    _config, _wid, root, _destination = pr_repo
    entered, release = Event(), Event()

    def capture_owner() -> None:
        with pr_publish.publish_lock(str(root), acquire_timeout=1):
            entered.set()
            release.wait(1.5)

    @pr_transport_fence.publication
    def transport(remote: str, branch: str, *, cwd: str, timeout: float) -> git_ops.PushResult:
        pytest.fail("Capture contention outlived its budget and reached transport")

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(capture_owner)
        try:
            assert entered.wait(3)
            started = time.monotonic()
            result = transport("origin", "topic", cwd=str(root), timeout=0.1)
            assert not result.ok and time.monotonic() - started < 1, result
        finally:
            release.set()
        owner.result(timeout=3)


def test_capture_lock_recomputes_remaining_budget_after_its_git_probe(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import git_acquisition_budget, pr_publish

    _config, _wid, root, _destination = pr_repo
    entered, release = Event(), Event()
    probe, acquire = git_ops.git, pr_publish._lock_publish_file
    clock = [0.0]
    waits: list[float] = []

    def owner() -> None:
        with pr_publish.publish_lock(str(root), acquire_timeout=1):
            entered.set()
            assert release.wait(5)

    def slow_probe(*args: Any, **kwargs: Any) -> Any:
        result = probe(*args, **kwargs)
        if args == ("rev-parse", "--git-common-dir"):
            clock[0] = 0.8
        return result

    def measured(handle: Any, *, timeout: float) -> None:
        waits.append(timeout)
        acquire(handle, timeout=timeout)

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(owner)
        try:
            assert entered.wait(3)
            monkeypatch.setattr(git_acquisition_budget, "time", SimpleNamespace(monotonic=lambda: clock[0]))
            monkeypatch.setattr(git_ops, "git", slow_probe)
            monkeypatch.setattr(pr_publish, "_lock_publish_file", measured)
            with git_acquisition_budget.acquisition_budget(1), pytest.raises(TimeoutError):
                with pr_publish.publish_lock(str(root), acquire_timeout=1):
                    pytest.fail("Contended publication lock was acquired")
            assert waits == [pytest.approx(0.2)]
        finally:
            release.set()
        holder.result(timeout=3)


def test_all_head_fences_share_the_remaining_admission_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "state")
    guard = pr_transport_fence.Head.guard
    waits: list[float] = []

    @contextmanager
    def measured(head: pr_transport_fence.Head, *, timeout: float = 0) -> Iterator[None]:
        waits.append(timeout)
        time.sleep(0.02)
        with guard(head, timeout=timeout):
            yield

    monkeypatch.setattr(pr_transport_fence.Head, "guard", measured)

    @pr_transport_fence.publication
    def transport(remote: str, branch: str, *, cwd: str, timeout: float) -> git_ops.PushResult:
        return git_ops.PushResult(ok=True)

    with pinned_destination("origin", (
        "https://example.com/owner/first.git", "https://example.com/owner/second.git",
    )):
        result = transport("origin", "topic", cwd=str(tmp_path), timeout=1)
    assert result.ok and len(waits) == 2, result
    assert 0 < waits[1] < waits[0] <= 1


@pytest.mark.parametrize("managed", [False, True])
def test_linked_source_requires_an_owner_only_inside_declared_managed_roots(
    pr_repo: tuple[cfg.Config, str, Path, Path], tmp_path: Path, managed: bool,
) -> None:
    from agent_worktrees import pr_authority

    config, wid, _root, remote = pr_repo
    parent = tmp_path / "external"
    linked = (Path(config.default_repo.worktree_root) if managed else tmp_path) / "linked-source"
    git_ops.git("clone", "--quiet", str(remote), str(parent), cwd=str(tmp_path))
    git_ops.git("worktree", "add", "--detach", str(linked), "HEAD", cwd=str(parent))
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    if managed:
        with pytest.raises(ValueError, match="native owner"):
            with pr_authority.publication_lifecycle(config, record=record, workspace=linked):
                pytest.fail("A managed source without its ledger was admitted")
    else:
        with pr_authority.publication_lifecycle(config, record=record, workspace=linked) as recheck:
            recheck()


def test_source_push_admission_does_not_wait_on_unrelated_creation_authority(
    publication: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_authority, pr_publish, pr_source_publication as source

    p = publication
    entered, release = Event(), Event()
    observe = observed_ref
    started = False
    admitted = False

    def creation() -> None:
        with pr_authority.guard():
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = None

        def observed(*args: Any, **kwargs: Any) -> str:
            nonlocal started, owner
            result = observe(*args, **kwargs)
            if not started:
                started = True
                owner = pool.submit(creation)
                assert entered.wait(3)
            return result

        monkeypatch.setattr(source, "observed_ref", observed)

        def transport(*args: Any, **kwargs: Any) -> git_ops.PushResult:
            nonlocal admitted
            assert entered.is_set() and not release.is_set()
            admitted = True
            release.set()
            return git_ops.PushResult(ok=False, stderr="Source admitted; transport not requested by this control")

        monkeypatch.setattr(pr_publish, "push_checked", transport)
        try:
            result = source.publish_source(
                p.wid, p.caller, target_repo=p.repo, workspace=p.workspace,
                branch=p.branch, expected_head=p.head, expected_remote_head="",
            )
        finally:
            release.set()
        assert owner is not None
        owner.result(timeout=3)
    assert admitted and not result["ok"] and "Source admitted" in result["error"], result
    assert observe("origin", (str(p.remote),), f"refs/heads/{p.branch}", str(p.workspace)) == ""


def test_unmanaged_source_rechecks_new_native_owner_in_an_existing_ledger(
    pr_repo: tuple[cfg.Config, str, Path, Path], tmp_path: Path,
) -> None:
    from agent_worktrees import pr_authority

    config, wid, _root, remote = pr_repo
    workspace = tmp_path / "external"
    git_ops.git("clone", "--quiet", str(remote), str(workspace), cwd=str(tmp_path))
    caller = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    with pr_authority.publication_lifecycle(config, record=caller, workspace=workspace) as recheck:
        recheck()
        owner = replace(caller, worktree_id="new-source", worktree_path=str(workspace), branch="master", prs=[])
        tracking.save_record(owner, cfg.tracking_dir() / f"{owner.worktree_id}.yaml")
        with pytest.raises(ValueError, match="Source owner routing changed"):
            recheck()


def test_unpinned_pushurl_is_captured_before_a_remote_repoint(
    pr_repo: tuple[cfg.Config, str, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config, _wid, root, destination = pr_repo
    fetch_store, replacement = tmp_path / "fetch.git", tmp_path / "replacement.git"
    for store in (fetch_store, replacement):
        git_ops.git("init", "--bare", str(store), cwd=str(root))
    git_ops.git("remote", "add", "split", str(fetch_store), cwd=str(root))
    git_ops.git("remote", "set-url", "--add", "--push", "split", str(destination), cwd=str(root))
    head = git_ops.git("rev-parse", "HEAD", cwd=str(root)).stdout.strip()
    original = git_ops.git
    changed = False
    def git(*args: Any, **kwargs: Any) -> Any:
        nonlocal changed
        if "push" in args and not changed:
            changed = True
            original("remote", "set-url", "--push", "split", str(replacement), cwd=str(root))
        return original(*args, **kwargs)
    monkeypatch.setattr(git_ops, "git", git)
    result = git_ops.push("split", "HEAD:refs/heads/topic", cwd=str(root))
    assert result.ok and changed, result.stderr
    assert original("--git-dir", str(destination), "rev-parse", "topic", cwd=str(root)).stdout.strip() == head
    assert original("--git-dir", str(replacement), "show-ref", "--verify", "refs/heads/topic",
                    cwd=str(root), check=False).returncode != 0


@pytest.mark.parametrize("refspec", ["HEAD", "HEAD:refs/heads/topic"])
@pytest.mark.parametrize("move_source", [False, True])
def test_unpinned_push_refreshes_its_resolved_tracking_ref_without_a_remote_probe(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch,
    refspec: str, move_source: bool,
) -> None:
    _config, _wid, root, destination = pr_repo
    head = git_ops.git("rev-parse", "HEAD", cwd=root).stdout.strip()
    branch = git_ops.current_branch(root) if refspec == "HEAD" else "topic"
    original = git_ops.git
    probes: list[tuple[Any, ...]] = []

    def git(*args: Any, **kwargs: Any) -> Any:
        if "ls-remote" in args:
            probes.append(args)
        if "push" in args and move_source:
            original("commit", "--allow-empty", "-m", "Source moved after capture", cwd=root)
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", git)
    result = git_ops.push("origin", refspec, cwd=root)
    assert result.ok and not result.stderr, result
    assert not probes, "A successful push added an independent remote observation"
    assert original("--git-dir", str(destination), "rev-parse", f"refs/heads/{branch}",
                    cwd=root).stdout.strip() == head
    assert original("rev-parse", f"refs/remotes/origin/{branch}", cwd=root).stdout.strip() == head


def test_unrelated_heads_keep_concurrent_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "state")
    both = Barrier(2, timeout=5)
    @pr_transport_fence.publication
    def transport(remote: str, branch: str, *, cwd: str, timeout: float) -> git_ops.PushResult:
        both.wait()
        return git_ops.PushResult(ok=True)
    def publish(branch: str) -> git_ops.PushResult:
        with pinned_destination("origin", ("https://example.com/owner/repo.git",)):
            return transport("origin", branch, cwd=str(tmp_path), timeout=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ("topic-a", "topic-b")))
    assert all(result.ok for result in results)


def test_same_head_transport_remains_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "state")
    entered, release = Event(), Event()
    @pr_transport_fence.publication
    def transport(remote: str, branch: str, *, cwd: str, timeout: float) -> git_ops.PushResult:
        entered.set()
        assert release.wait(10)
        return git_ops.PushResult(ok=True)
    def publish() -> git_ops.PushResult:
        with pinned_destination("origin", ("https://example.com/owner/repo.git",)):
            return transport("origin", "topic", cwd=str(tmp_path), timeout=0.1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(publish)
        try:
            assert entered.wait(5)
            second = publish()
            assert not second.ok and "fenced" in second.stderr
        finally:
            release.set()
        assert first.result(timeout=3).ok


def test_prequalified_reciprocal_owners_share_one_physical_head_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_creation_reconcile

    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "state")
    record = tracking.WorktreeRecord(
        worktree_id="first", branch="worktree/first", worktree_path=str(tmp_path / "first"),
        repo="example", machine="test", platform="windows", started_at="", last_resumed_at="",
        resume_count=0, title=None, status="finalized", completed_at=None,
        prs=[tracking.PRRecord(state="creating")],
    )
    peer = replace(record, worktree_id="second", worktree_path=str(tmp_path / "second"))
    repo = cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))
    head = pr_transport_fence.Head("example.com/owner/repo", "topic")
    scope = pr_transport_fence.Qualification()
    keys = {str(owner.yaml_path.resolve()) for owner in (record, peer)}
    for key in keys:
        scope.heads[key] = (head,)
    monkeypatch.setattr(pr_creation_reconcile, "finalization_error", lambda *a: None)
    token = pr_transport_fence.current.set(scope)

    def competing_transport() -> bool:
        with pytest.raises(TimeoutError), head.guard():
            pytest.fail("A shared cleanup head became available before both owners finished")
        return True

    try:
        with pr_transport_fence.cleanup_guard(record, repo), pr_transport_fence.cleanup_guard(peer, repo):
            assert scope.protected == keys
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(competing_transport).result(timeout=3)
        assert not scope.protected and not scope.cleanup
        with head.guard():
            pass
    finally:
        pr_transport_fence.current.reset(token)


def test_forked_publisher_cannot_reuse_parent_authority(tmp_path: Path) -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("POSIX fork-specific authority boundary")
    code = """
import multiprocessing
from pathlib import Path
import sys
from agent_worktrees import config, pr_authority
config.install_dir = lambda: Path(sys.argv[1])
def probe(sender):
    try:
        with pr_authority.guard(timeout=0):
            sender.send("bypassed")
    except pr_authority.PublicationAuthorityBusy:
        sender.send("held")
    finally:
        sender.close()
ctx = multiprocessing.get_context("fork")
receiver, sender = ctx.Pipe(duplex=False)
child = ctx.Process(target=probe, args=(sender,))
try:
    with pr_authority.guard():
        child.start()
        sender.close()
        assert receiver.poll(3), "child did not report authority"
        assert receiver.recv() == "held", "fork inherited publication permission"
    child.join(3)
    assert child.exitcode == 0
finally:
    if child.is_alive():
        child.terminate()
        child.join(3)
    receiver.close()
"""
    result = run_caller_owned(
        [sys.executable, "-c", code, str(tmp_path)], timeout=15,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
