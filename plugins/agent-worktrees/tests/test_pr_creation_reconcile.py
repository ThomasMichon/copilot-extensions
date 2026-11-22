"""Explicit legacy-attempt retirement is not cancellation or provider closure."""

from dataclasses import dataclass, field, replace
from collections.abc import Iterator
import json
import os
import runpy
import subprocess
import time
from types import SimpleNamespace
from pathlib import Path
from typing import Any, Callable, cast

import pytest
import yaml
from machine_transport import detect_platform

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg
from agent_worktrees import finalize, finalize_open_pr_gate, git_ops, tracking
from agent_worktrees import pr_creation_state as state
from agent_worktrees.finalize_lock import FinalizeLock
from agent_worktrees.pr_publish import _remote_snapshot as native_remote_snapshot
from agent_worktrees.tracking_claims import ResourceClaim
from test_pr_creation_cancel import reservation as reservation, source_snapshot
from test_pr_legacy_admission import modeled_seal

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr_publish.publication_state"),
    pytest.mark.usefixtures("git_only_mux"),
]
Legacy = tuple[cfg.Config, str, Path, Path, Path, tracking.WorktreeRecord, Any]
snapshot = cast(Callable[[Path], tuple[str, bytes, str, str]], source_snapshot)


@pytest.fixture
def pr_repo(
    pr_repo: tuple[cfg.Config, str, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[cfg.Config, str, Path, Path]:
    config, wid, wt, remote = pr_repo
    project = tmp_path / "project"
    project.mkdir()
    ledger = project / "worktrees"
    cfg.tracking_dir().rename(ledger)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: ledger)
    config = replace(config, platform=detect_platform())
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    return config, wid, wt, remote


def selected(record: tracking.WorktreeRecord) -> tracking.PRRecord:
    pr = record.pr
    assert pr is not None
    return pr


def test_missing_checkout_qualification_refusal_does_not_abort_later_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from _cleanup_revalidation_helpers import CleanupHarness, S, info, make_record

    harness = CleanupHarness(monkeypatch, tmp_path)
    held = make_record(tmp_path, wt_id="retired", status="active", exists=False)
    held.prs = [tracking.PRRecord(state="creating", creation={"recovery_revision": "1"})]
    harness.seed(held)
    harness.seed(make_record(tmp_path, wt_id="later"))
    harness.set_classifier(info(S.COMPLETED))
    harness.set_branch_merged_sequence([True])
    report = harness.run_sweep()
    reason = next(item["reason"] for item in report["skipped"] if item["id"] == "retired")
    assert "qualification" in reason.lower()
    assert [item["id"] for item in report["removed"]] == ["later"]
    assert harness.record_path("retired").is_file()
    assert all(wid != "retired" for wid, _state in harness.reaped)


@pytest.fixture
def sealed_admission() -> bool:
    return True


@pytest.fixture
def modeled_complete_coverage() -> bool:
    return True


@pytest.fixture
def legacy(
    reservation: Legacy, monkeypatch: pytest.MonkeyPatch, sealed_admission: bool,
    modeled_complete_coverage: bool,
) -> Legacy:
    from agent_worktrees import pr_publish
    config, wid, wt, remote, path, _record, provider = reservation
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda _wid: None)
    identity = pr_publish._identity
    private_store = identity(str(remote), str(wt))
    assert private_store
    # One private Git store substitutes for one forge repository in probes and transport.
    def store_identity(url: str, cwd: str) -> str:
        actual = identity(url, cwd)
        return "github.com/example/repo" if actual == private_store else actual
    monkeypatch.setattr(pr_publish, "_identity", store_identity)
    def destination(remote_name: str, *, cwd: str) -> Any:
        fetch, urls, target = native_remote_snapshot(remote_name, cwd=cwd)
        if target.head_identity == "github.com/example/repo":
            target = pr_publish.PushTarget(remote_name, "example/repo", target.head_identity)
        return fetch, urls, target
    monkeypatch.setattr(pr_publish, "_remote_snapshot", destination)
    record = tracking.load_record(path)
    selected(record).creation["phase"] = "publishing"
    selected(record).pr_revision += 1
    tracking.save_record(record)
    # Realign only disposable fixture refs; the recovery command must never do it.
    git_ops.git("reset", "--hard", "origin/master", cwd=str(wt))
    git_ops.git("branch", selected(record).branch, "HEAD", cwd=str(wt))
    provider.confirm_reconciled_head_v1 = lambda *a, **k: True
    if sealed_admission:
        modeled_seal(cfg.install_dir(), monkeypatch)
        from agent_worktrees import pr_legacy_admission
        # Isolate downstream receipt/consumer controls; full admission remains
        # refused by the real runtime and is tested separately.
        if modeled_complete_coverage:
            monkeypatch.setattr(pr_legacy_admission, "_require_complete_coverage", lambda _seal: None)
    return config, wid, wt, remote, path, record, provider


def invoke(legacy: Legacy, capfd: pytest.CaptureFixture[str], **overrides: str | int | None) -> dict[str, Any]:
    from agent_worktrees.pr_state_cli import cmd_pr_abandon

    _config, wid, wt, _remote, _path, record, _provider = legacy
    options: dict[str, str | int | None] = {
        "expected-revision": str(selected(record).pr_revision),
        "expected-head": git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip(),
        "reason": "Superseded planning retained upstream; retire this local attempt",
    }
    options.update(overrides)
    argv = ["pr-abandon", wid, "--reconcile-creation", selected(record).pr_id, "--confirm", "--json"]
    for key, value in options.items():
        if value is not None:
            argv += [f"--{key}", str(value)]
    args = cli.build_parser().parse_args(argv)
    code = cmd_pr_abandon(args)
    response: dict[str, Any] = json.loads(capfd.readouterr().out)
    assert code == (0 if response["success"] else 1)
    return response


def test_actual_deadlock_and_owner_cli_retirement(legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> None:
    config, wid, wt, _remote, path, record, _provider = legacy
    monkeypatch.setattr(finalize_open_pr_gate, "pull_for", lambda *a: None)
    assert not state.reserved(selected(record), record)
    assert not finalize_open_pr_gate.assert_no_live_pr(record, config, wid)
    assert finalize_open_pr_gate.assert_no_live_pr(record, config, wid, force=True)
    assert not finalize._pr_finalize_precondition(
        record, config.default_repo, str(wt), config.default_repo.anchor,
    )[0]
    capfd.readouterr()
    before = snapshot(wt)
    result = invoke(legacy, capfd)
    assert result["success"], result
    assert result["publication_outcome"] == "unknown"
    assert result["creation_reconciled"] and not result["provider_closed"]
    assert not result["claim_released"] and not result["creation_cancelled"]
    current = tracking.load_record(path)
    assert selected(current).number is None and not selected(current).url and not selected(current).head_sha
    assert selected(current).pr_id == selected(record).pr_id
    assert selected(current).creation["phase"] == "publishing"
    assert selected(current).creation["binding"] == selected(record).creation["binding"]
    assert selected(current).attribution_mode == selected(record).attribution_mode
    assert snapshot(wt) == before
    assert finalize_open_pr_gate.assert_no_live_pr(current, config, wid)


def test_confirmed_retry_keeps_original_receipt(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    assert invoke(legacy, capfd)["success"]
    path = legacy[4]
    saved = path.read_bytes()
    assert invoke(legacy, capfd)["already_reconciled"]
    assert path.read_bytes() == saved


def test_receiver_only_status_does_not_advertise_a_complete_recovery_contract(legacy: Legacy) -> None:
    from agent_worktrees import pr_ops

    config, wid, _wt, _remote, _path, _original, _provider = legacy
    report = pr_ops.pr_status(wid, all_prs=True, live=False, config=config)
    assert "creation_reconciliation_contract" not in report
    admission = report["legacy_publication_admission"]
    assert admission["implementation"] == "receiver-only"
    assert admission["recovery_supported"] is False and admission["seal_operation"] is None


def test_retired_attempt_is_not_an_implicit_or_same_head_publication_retry(
    legacy: Legacy, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import pr_ops
    config, wid, _wt, _remote, path, original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    before = path.read_bytes()
    implicit = pr_ops.create_pr(wid, config, title="Another contribution", open_pr=False)
    assert not implicit["success"] and "--new" in implicit["error"]
    replay = pr_ops.create_pr(
        wid, config, title="Another contribution", branch=selected(original).branch, new=True, open_pr=False,
    )
    assert not replay["success"] and "cannot be replayed" in replay["error"]
    assert path.read_bytes() == before


def test_set_pr_does_not_assign_a_provider_identity_to_retirement(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    from agent_worktrees import pr_ops
    config, wid, _wt, _remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    before = path.read_bytes()
    result = pr_ops.set_pr(wid, url="https://github.com/example/repo/pull/44", number=44, config=config)
    assert not result["success"] and "cannot be reassigned" in result["error"]
    assert path.read_bytes() == before


def test_optional_pr_mode_does_not_turn_retirement_into_direct_publication(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    config, wid, wt, remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    (wt / "later.txt").write_text("new work remains owned\n")
    git_ops.git("add", "later.txt", cwd=str(wt))
    git_ops.git("commit", "-m", "New contribution", cwd=str(wt))
    config = replace(config, repos={config.repo_name: replace(
        config.default_repo, pr=replace(config.default_repo.pr, required=False),
    )})
    before = path.read_bytes(), git_ops.git("--git-dir", str(remote), "rev-parse", "master", cwd=str(wt)).stdout
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("direct publication attempted"))
    assert not finalize.push_changes(wid, config)
    assert (path.read_bytes(), git_ops.git("--git-dir", str(remote), "rev-parse", "master", cwd=str(wt)).stdout) == before
    assert (wt / "later.txt").read_text() == "new work remains owned\n"


def test_explanation_uses_the_same_retirement_refusal(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    from agent_worktrees import finalize_landing
    from agent_worktrees.finalize_ref import landing
    config, _wid, wt, _remote, path, _original, provider = legacy
    assert invoke(legacy, capfd)["success"]
    record = tracking.load_record(path)
    upstream = "refs/remotes/origin/master"
    checked = {upstream: landing("HEAD", upstream, str(wt))}
    assert checked[upstream].landed
    provider.confirm_reconciled_head_v1 = lambda *a, **k: False
    landed, error, _evidence = finalize_landing._pr_content_landed(
        record, config.default_repo, "HEAD", cwd=str(wt), checked=checked, existing_bases={upstream},
    )
    assert landed is False and error and "retirement requires renewed qualification" in error


def test_provider_creation_attempt_invalidates_prior_head_observation(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees.providers import ProviderError, github
    from agent_worktrees.providers.base import PRScope
    branch = selected(legacy[5]).branch
    monkeypatch.setattr(
        github, "run_cli", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "request outcome unavailable"),
    )
    def observe(*args: Any, **kwargs: Any) -> bool:
        with pytest.raises(ProviderError):
            github.GitHubProvider().create_pull(PRScope(
                repo="example/repo", head=branch, base="another-base", title="Existing head",
                api_base="github.com",
            ), token="")
        return True
    legacy[6].confirm_reconciled_head_v1 = observe
    before = legacy[4].read_bytes()
    result = invoke(legacy, capfd)
    assert not result["success"] and "intervened" in result["error"]
    assert legacy[4].read_bytes() == before


def test_explicit_new_contribution_preserves_the_retired_attempt(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import pr_ops
    config, wid, wt, _remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    before = yaml.safe_load(path.read_text(encoding="utf-8"))["prs"][0]
    git_ops.git("commit", "--allow-empty", "-m", "New contribution", cwd=str(wt))
    monkeypatch.setattr(git_ops, "rebase", lambda *a, **k: False)
    result = pr_ops.create_pr(wid, config, title="Distinct new contribution", new=True, open_pr=False)
    assert not result["success"] and "genuine conflicts" in result["error"]
    fresh = tracking.load_record(path)
    assert len(fresh.prs) == 2 and fresh.prs[1].pr_id != fresh.prs[0].pr_id
    assert fresh.prs[1].branch != fresh.prs[0].creation["recovery_branch"]
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["prs"][0] == before


def test_hidden_untracked_work_is_not_clean_source(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    wt, path = legacy[2], legacy[4]
    (wt / "untracked.txt").write_text("retain useful work\n")
    git_ops.git("config", "status.showUntrackedFiles", "no", cwd=str(wt))
    before = path.read_bytes()
    result = invoke(legacy, capfd)
    assert not result["success"] and "Dirty source" in result["error"]
    assert path.read_bytes() == before and (wt / "untracked.txt").exists()


def test_ignored_dirty_submodule_is_not_clean_source(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    wt, remote, path = legacy[2], legacy[3], legacy[4]
    git_ops.git("-c", "protocol.file.allow=always", "submodule", "add", str(remote), "module", cwd=str(wt))
    git_ops.git("commit", "-am", "Add already-landed submodule", cwd=str(wt))
    git_ops.git("push", "origin", "HEAD:master", cwd=str(wt))
    git_ops.git("config", "submodule.module.ignore", "all", cwd=str(wt))
    module = wt / "module"
    tracked = git_ops.git("ls-files", cwd=str(module)).stdout.splitlines()[0]
    (module / tracked).write_text("uncommitted submodule work\n")
    assert not git_ops.git("status", "--porcelain", cwd=str(wt)).stdout.strip()
    before = path.read_bytes()
    result = invoke(legacy, capfd)
    assert not result["success"] and "Dirty source" in result["error"]
    assert path.read_bytes() == before


def test_replace_refs_cannot_make_unlanded_source_an_ancestor(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    wt, path = legacy[2], legacy[4]
    git_ops.git("commit", "--allow-empty", "-m", "Unlanded source", cwd=str(wt))
    head = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
    upstream = git_ops.git("rev-parse", "origin/master", cwd=str(wt)).stdout.strip()
    tree = git_ops.git("rev-parse", "origin/master^{tree}", cwd=str(wt)).stdout.strip()
    replacement = git_ops.git("commit-tree", tree, "-p", head, "-m", "Replacement", cwd=str(wt)).stdout.strip()
    git_ops.git("replace", upstream, replacement, cwd=str(wt))
    assert git_ops.git("merge-base", "--is-ancestor", "HEAD", "origin/master", cwd=str(wt)).returncode == 0
    before = path.read_bytes()
    result = invoke(legacy, capfd)
    assert not result["success"] and "Every owned local" in result["error"]
    assert path.read_bytes() == before


def test_unrelated_clone_cannot_stand_in_for_recorded_checkout(legacy: Legacy, tmp_path: Path) -> None:
    from agent_worktrees import pr_creation_reconcile as recovery
    config, _wid, wt, remote, _path, record, _provider = legacy
    shadow = tmp_path / "shadow"
    git_ops.git("clone", "--quiet", str(remote), str(shadow), cwd=str(wt))
    git_ops.git("checkout", "-qb", record.branch, cwd=str(shadow))
    with pytest.raises(ValueError, match="configured anchor"):
        recovery._source(replace(record, worktree_path=str(shadow)), config.default_repo)


@pytest.mark.parametrize("snapshot_number", [1, 2])
def test_native_execution_space_refusal_preserves_attempt(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], snapshot_number: int,
) -> None:
    from agent_worktrees import execution_spaces
    real = execution_spaces.require_record_mutation
    calls = 0
    def check(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == snapshot_number:
            raise execution_spaces.ExecutionSpaceError("foreign receiving subject")
        real(*args, **kwargs)
    monkeypatch.setattr(execution_spaces, "require_record_mutation", check)
    before = legacy[4].read_bytes()
    result = invoke(legacy, capfd)
    assert not result["success"] and "foreign receiving subject" in result["error"]
    assert calls == snapshot_number and legacy[4].read_bytes() == before


@pytest.mark.parametrize("damage", ["none", "contract", "all-recovery", "all-creation"])
def test_retirement_requires_qualification_even_when_pr_mode_is_disabled(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], damage: str,
) -> None:
    from agent_worktrees import finalize_landing, pr_reconcile
    config, wid, wt, _remote, path, _record, provider = legacy
    assert invoke(legacy, capfd)["success"]
    current = tracking.load_record(path)
    retired = selected(current)
    if damage == "contract":
        del retired.creation["recovery_contract"]
    elif damage == "all-recovery":
        retired.creation = {k: v for k, v in retired.creation.items() if not k.startswith("recovery_")}
    elif damage == "all-creation":
        retired.creation = {}
    tracking.save_record(current)
    provider.find_pull_by_head = lambda *a, **k: pytest.fail("retired attempt was healed")
    assert not pr_reconcile.heal_numberless_active_pr(current, config)
    if damage != "none":
        current.prs.append(tracking.PRRecord(
            pr_id="other-attempt", branch="pr/other", state="merged", number=12,
            repo="example/repo", provider="github",
        ))
        tracking.save_record(current)
    repo = replace(config.default_repo, pr=replace(config.default_repo.pr, enabled=False))
    assert finalize_landing.tracks_pr(current, repo)
    monkeypatch.setattr(finalize_open_pr_gate, "pull_for", lambda *a: None)
    landed, reason = finalize._pr_finalize_precondition(current, repo, str(wt), repo.anchor)
    assert landed is (damage == "none"), reason
    if damage != "none":
        assert reason and "retirement proof is invalid" in reason
    assert current.worktree_id == wid


def test_provider_probe_does_not_hold_clone_or_installation_locks(
    legacy: Legacy, capfd: pytest.CaptureFixture[str],
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from agent_worktrees import pr_authority, pr_publish
    config, wid, wt, _remote, _path, _record, provider = legacy
    def acquire() -> bool:
        with pr_authority.guard(timeout=0), pr_publish.publish_lock(str(wt), acquire_timeout=1), \
                pr_publish.metadata_lock(wid, project=config.repo_name, timeout=1):
            return True
    with ThreadPoolExecutor(max_workers=1) as pool:
        def observe(*args: Any, **kwargs: Any) -> bool:
            return pool.submit(acquire).result(timeout=3)
        provider.confirm_reconciled_head_v1 = observe
        result = invoke(legacy, capfd)
    assert result["success"], result


def test_active_record_publisher_refuses_retirement_without_waiting_on_its_authority(
    legacy: Legacy, capfd: pytest.CaptureFixture[str],
) -> None:
    from threading import Event, Thread
    from agent_worktrees import pr_authority
    entered, release = Event(), Event()
    def publisher() -> None:
        with pr_authority.guard():
            entered.set()
            assert release.wait(5)
    thread = Thread(target=publisher)
    def observe(*args: Any, **kwargs: Any) -> bool:
        thread.start()
        assert entered.wait(2)
        return True
    legacy[6].confirm_reconciled_head_v1 = observe
    before = legacy[4].read_bytes()
    try:
        result = invoke(legacy, capfd)
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert not result["success"] and "authority" in result["error"]
    assert legacy[4].read_bytes() == before


@pytest.mark.parametrize("sealed_admission", [False])
def test_legacy_publisher_after_admission_blocks_retirement(
    legacy: Legacy, capfd: pytest.CaptureFixture[str],
) -> None:
    from threading import Event, Thread
    from agent_worktrees import pr_authority, pr_publish

    _config, _wid, wt, remote, path, record, provider = legacy
    admitted, resume = Event(), Event()
    branch = selected(record).branch
    head = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
    pushes: list[subprocess.CompletedProcess[str]] = []
    failures: list[BaseException] = []

    def legacy_publisher() -> None:
        try:
            with pr_authority.guard(), pr_publish.publish_lock(str(wt)):
                assert git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip() == head
            admitted.set()
            assert resume.wait(15)
            pushes.append(git_ops.git(
                "push", str(remote), f"{head}:refs/heads/{branch}", cwd=str(wt), check=False,
            ))
        except BaseException as exc:
            failures.append(exc)

    publisher = Thread(target=legacy_publisher)
    provider.confirm_reconciled_head_v1 = lambda *a, **k: pytest.fail(
        "Unsealed legacy admission must refuse before provider observations.",
    )
    before = path.read_bytes()
    publisher.start()
    assert admitted.wait(3)
    try:
        result = invoke(legacy, capfd)
    finally:
        resume.set()
        if publisher.ident is not None:
            publisher.join(timeout=5)
    assert not publisher.is_alive() and not failures, failures
    assert len(pushes) == 1 and pushes[0].returncode == 0
    assert git_ops.git(
        "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}", cwd=str(wt),
    ).stdout.strip() == head
    assert not result["success"], "Retirement raced a legacy publisher outside the new fences."
    assert path.read_bytes() == before


@pytest.mark.parametrize("modeled_complete_coverage", [False])
def test_authority_barrier_and_modeled_boot_do_not_admit_uncovered_legacy_cli(
    legacy: Legacy, capfd: pytest.CaptureFixture[str],
) -> None:
    legacy[6].confirm_reconciled_head_v1 = lambda *a, **k: pytest.fail(
        "Incomplete legacy coverage must refuse before provider observations.",
    )
    before = legacy[4].read_bytes()
    result = invoke(legacy, capfd)
    assert not result["success"] and "foreign-provider" in result["error"]
    assert legacy[4].read_bytes() == before


def test_native_cleanup_invalidates_a_publication_after_prequalification(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import hooks
    config, wid, wt, _remote, path, original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    branch = selected(original).branch
    head = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
    save = tracking.save_record
    intervened = False
    def freeze(record: tracking.WorktreeRecord, *args: Any, **kwargs: Any) -> None:
        nonlocal intervened
        save(record, *args, **kwargs)
        if record.status == "finalizing" and not intervened:
            intervened = True
            with hooks.allow_pr_push():
                assert git_ops.push("origin", f"{head}:refs/heads/{branch}", cwd=config.default_repo.anchor)
                assert git_ops.push("origin", f":refs/heads/{branch}", cwd=config.default_repo.anchor)
    monkeypatch.setattr(tracking, "save_record", freeze)
    assert not finalize.validate_and_finalize(wid, config)
    assert intervened and wt.exists()
    assert tracking.load_record(path).status == "active"


@pytest.mark.parametrize("change", ["source", "branch", "revision", "owner", "binding", "prepared", "legacy", "expected-head"])
def test_changed_or_unlanded_inputs_refuse_without_rewriting(legacy: Legacy, capfd: pytest.CaptureFixture[str], change: str) -> None:
    _config, _wid, wt, _remote, path, _record, _provider = legacy
    record = tracking.load_record(path)
    overrides: dict[str, str] = {}
    if change in ("source", "branch"):
        git_ops.git("commit", "--allow-empty", "-m", "Unlanded planning", cwd=str(wt))
        if change == "branch":
            git_ops.git("branch", "-f", selected(record).branch, "HEAD", cwd=str(wt))
            git_ops.git("reset", "--hard", "origin/master", cwd=str(wt))
    elif change == "revision":
        selected(record).pr_revision += 1
    elif change in ("owner", "binding"):
        selected(record).creation[change] = "changed"
    elif change == "prepared":
        selected(record).creation["push_head"] = "unknown"
    elif change == "expected-head":
        overrides["expected-head"] = "changed"
    else:
        selected(record).creation = {}
    tracking.save_record(record)
    before = path.read_bytes(), snapshot(wt)
    result = invoke(legacy, capfd, **overrides)
    assert not result["success"], result
    assert (path.read_bytes(), snapshot(wt)) == before


@pytest.mark.parametrize("failure", ["provider-false", "provider-unknown", "remote", "remote-unknown", "upstream"])
def test_incomplete_observations_hold_original_unknown_outcome(legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], failure: str) -> None:
    from agent_worktrees import pr_publish
    from agent_worktrees.providers import ProviderError

    provider, path = legacy[6], legacy[4]
    if failure == "provider-false":
        provider.confirm_reconciled_head_v1 = lambda *a, **k: False
    elif failure == "provider-unknown":
        def unavailable(*a: Any, **k: Any) -> None:
            raise ProviderError("observation unavailable")
        provider.confirm_reconciled_head_v1 = unavailable
    elif failure == "upstream":
        tip = pr_publish._tip
        branch = legacy[0].default_repo.default_branch
        monkeypatch.setattr(
            pr_publish, "_tip",
            lambda remote, head, cwd: "stale" if head == branch else tip(remote, head, cwd),
        )
    else:
        monkeypatch.setattr(pr_publish, "_tip", lambda *a: "unexpected" if failure == "remote" else None)
    before = path.read_bytes()
    assert not invoke(legacy, capfd)["success"]
    assert path.read_bytes() == before


def test_immutable_id_selects_duplicate_without_changing_proper_mapping(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    config, _wid, wt, _remote, path, original, _provider = legacy
    record = tracking.load_record(path)
    merged = replace(
        selected(record), pr_id="numbered-attempt", creation={}, state="merged", number=12,
        url="https://github.com/example/repo/pull/12",
        head_sha=git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip(),
    )
    record.prs.insert(0, merged)
    tracking.save_record(record)
    result = invoke(legacy, capfd, **{"merged-pr": 12})
    assert result["success"], result
    fresh = tracking.load_record(path)
    assert fresh.prs[0] == merged
    assert fresh.prs[1].pr_id == selected(original).pr_id
    assert finalize._pr_finalize_precondition(
        fresh, config.default_repo, str(wt), config.default_repo.anchor,
    )[0]


def test_late_callback_and_stale_stamp_cannot_resurrect_retirement(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    from agent_worktrees import pr_publication_state

    path = legacy[4]
    stale = tracking.load_record(path)
    assert invoke(legacy, capfd)["success"]
    with pytest.raises(ValueError, match="authority changed"):
        pr_publication_state.require_current(stale, stale.pr)
    stale.title = "Unrelated status stamp"
    tracking.save_record(stale)
    fresh = tracking.load_record(path)
    assert selected(fresh).state == "creating" and not selected(fresh).branch
    assert selected(fresh).creation["recovery_contract"]


@pytest.mark.parametrize("drop_receipt", [False, True])
def test_new_wire_disposition_holds_the_actual_unaware_legacy_reader(
    legacy: Legacy, capfd: pytest.CaptureFixture[str], drop_receipt: bool,
) -> None:
    from agent_worktrees import pr_creation_reconcile as recovery, pr_ops
    config, _wid, _wt, _remote, path, original, provider = legacy
    assert invoke(legacy, capfd)["success"]
    wire = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert wire["prs"][0]["creation"]["recovery_branch"] == selected(original).branch
    if drop_receipt:
        wire["prs"][0].pop("creation")
        path.write_text(yaml.safe_dump(wire), encoding="utf-8")
    report = pr_ops.pr_status(legacy[1], all_prs=True, live=False, config=config)
    if drop_receipt:
        assert "local_disposition" not in report
    else:
        assert report["local_disposition"] == "retired" and report["publication_outcome"] == "unknown"
    provider.find_pull_by_head = lambda *a, **k: pytest.fail("unaware healer reassigned the attempt")
    namespace = runpy.run_path(str(Path(__file__).parent / "fixtures" / "legacy_numberless_pr_healer.py"))
    heal = cast(Callable[[tracking.WorktreeRecord, cfg.Config], bool], namespace["heal_numberless_active_pr"])
    unaware = tracking.load_record(path)
    assert not heal(unaware, config)
    assert unaware.has_live_pr()
    assert selected(unaware).pr_id == selected(original).pr_id
    assert selected(unaware).number is None and not selected(unaware).url
    if drop_receipt:
        assert recovery.requires_qualification(selected(unaware)) and not recovery.retired(selected(unaware))


def test_preexisting_numberless_abandonment_is_not_reclassified_as_retirement(legacy: Legacy) -> None:
    from agent_worktrees import finalize_landing, pr_creation_reconcile as recovery
    config, _wid, _wt, _remote, _path, record, _provider = legacy
    selected(record).state = "abandoned"
    selected(record).creation = {}
    repo = replace(config.default_repo, pr=replace(config.default_repo.pr, enabled=False))
    assert not recovery.requires_qualification(selected(record))
    assert not finalize_landing.tracks_pr(record, repo)


@pytest.mark.parametrize("status", ["complete", "completed", "pushed"])
def test_nonfinalized_owner_status_can_reconcile(
    legacy: Legacy, capfd: pytest.CaptureFixture[str], status: tracking.WorktreeStatus,
) -> None:
    record = tracking.load_record(legacy[4])
    record.status = status
    tracking.save_record(record)
    result = invoke(legacy, capfd)
    assert result["success"] and result["local_disposition"] == "retired", result
    assert tracking.load_record(legacy[4]).status == status


@pytest.mark.parametrize("route", ["origin", "split"])
def test_intervening_transport_invalidates_even_restored_remote_absence(
    legacy: Legacy, capfd: pytest.CaptureFixture[str], tmp_path: Path, route: str,
) -> None:
    from agent_worktrees import hooks
    config, _wid, wt, _remote, path, record, provider = legacy
    branch = selected(record).branch
    head = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
    if route == "split":
        fetch_store = tmp_path / "fetch.git"
        git_ops.git("init", "--bare", str(fetch_store), cwd=str(wt))
        git_ops.git("remote", "add", route, str(fetch_store), cwd=str(wt))
        git_ops.git("remote", "set-url", "--add", "--push", route, str(legacy[3]), cwd=str(wt))
    def observe(*args: Any, **kwargs: Any) -> bool:
        with hooks.allow_pr_push():
            assert git_ops.push(route, f"{head}:refs/heads/{branch}", cwd=config.default_repo.anchor)
            assert git_ops.push(route, f":refs/heads/{branch}", cwd=config.default_repo.anchor)
        return True
    provider.confirm_reconciled_head_v1 = observe
    before = path.read_bytes()
    result = invoke(legacy, capfd)
    assert not result["success"] and "during observation" in result["error"]
    assert path.read_bytes() == before


def test_git_probe_timeout_is_bounded_and_structured(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    original = git_ops.git
    def git(*args: Any, **kwargs: Any) -> Any:
        if "--no-replace-objects" in args and "status" in args:
            assert kwargs["timeout"] == 30 and kwargs["kill_tree"] is True
            raise subprocess.TimeoutExpired("source probe", 30)
        return original(*args, **kwargs)
    monkeypatch.setattr(git_ops, "git", git)
    before = legacy[4].read_bytes()
    result = invoke(legacy, capfd)
    assert not result["success"] and "source probe" in result["error"]
    assert legacy[4].read_bytes() == before


def test_observation_cannot_outvote_revision_drift(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    path, original, provider = legacy[4], legacy[5], legacy[6]
    def race(*args: Any, **kwargs: Any) -> bool:
        current = tracking.load_record(path)
        selected(current).pr_revision += 1
        tracking.save_record(current)
        return True
    provider.confirm_reconciled_head_v1 = race
    result = invoke(legacy, capfd)
    assert not result["success"] and "authority changed" in result["error"]
    fresh = tracking.load_record(path)
    assert selected(fresh).pr_revision == selected(original).pr_revision + 1
    assert selected(fresh).state == "creating" and "recovery_contract" not in selected(fresh).creation


def test_unconfirmed_reconciliation_does_not_read_the_record(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees.pr_creation_reconcile import reconcile

    monkeypatch.setattr(tracking, "load_record", lambda *a: pytest.fail("unconfirmed record read"))
    result = reconcile(
        "example", cfg.Config(srcroot="", machine="test", platform="test"), creation_id="attempt", expected_revision=None,
        expected_head="", merged_pr=None, reason="Superseded", confirm=False,
    )
    assert result["needs_confirm"] and not result["success"]


@pytest.mark.parametrize("options", [
    ["--reconcile-creation", "", "--expected-revision", "0", "--expected-head", "abc"],
    ["--expected-head", ""],
    ["--merged-pr", "0"],
])
def test_invalid_local_selector_never_falls_through_to_provider_abandon(monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], options: list[str]) -> None:
    from agent_worktrees import config, pr_ops, worktree_identity
    from agent_worktrees.pr_state_cli import cmd_pr_abandon

    monkeypatch.setattr(config, "load_config", lambda *a: SimpleNamespace())
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda wid, config: wid)
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda wid: wid)
    monkeypatch.setattr(pr_ops, "abandon_pr", lambda *a, **k: pytest.fail("unintended provider abandonment"))
    args = cli.build_parser().parse_args([
        "pr-abandon", "example", "--reason", "Retire only the local attempt",
        "--confirm", "--json", *options,
    ])
    assert cmd_pr_abandon(args) == 1
    assert not json.loads(capfd.readouterr().out)["success"]


def test_other_unlanded_ref_and_post_receipt_drift_still_block(legacy: Legacy, capfd: pytest.CaptureFixture[str]) -> None:
    config, _wid, wt, _remote, path, _record, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    _provider.confirm_reconciled_head_v1 = lambda *a, **k: False
    current = tracking.load_record(path)
    assert not finalize._pr_finalize_precondition(
        current, config.default_repo, str(wt), config.default_repo.anchor,
    )[0]
    _provider.confirm_reconciled_head_v1 = lambda *a, **k: True
    git_ops.git("commit", "--allow-empty", "-m", "New responsibility", cwd=str(wt))
    current = tracking.load_record(path)
    assert not finalize._pr_finalize_precondition(
        current, config.default_repo, str(wt), config.default_repo.anchor,
    )[0]


@dataclass
class RetainedFlow:
    home: Path
    phase: str = "new"
    legacy: Legacy | None = None
    patches: list[tuple[Any, str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)


@pytest.fixture(scope="class")
def retained_flow(tmp_path_factory: pytest.TempPathFactory, damage: str) -> Iterator[RetainedFlow]:
    flow = RetainedFlow(tmp_path_factory.mktemp(f"retained-{damage}"))
    yield flow
    assert flow.phase == "checked", "Select all ordered stages of the retained-receipt flow."
    assert time.monotonic() - flow.started < 300


@pytest.fixture
def retained_flow_environment(
    retained_flow: RetainedFlow, monkeypatch: pytest.MonkeyPatch,
    _isolate_agent_worktrees_home: Path,
) -> None:
    from agent_worktrees import sessions

    for name in ("AGENT_HOME", "USERPROFILE", "HOME"):
        monkeypatch.setenv(name, os.fspath(retained_flow.home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: retained_flow.home))
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {})
    for module, name, value in retained_flow.patches:
        monkeypatch.setattr(module, name, value)


def remember_flow(flow: RetainedFlow, legacy: Legacy) -> None:
    from agent_worktrees import pr_legacy_admission, pr_publish, providers
    from update_driver import boot

    flow.legacy = legacy
    for module, names in (
        (cfg, ("install_dir", "project_dir", "tracking_dir", "load_config")),
        (providers, ("get_provider", "account_token_for_slug")),
        (pr_publish, ("_identity", "_remote_snapshot")),
        (git_ops, ("remote_slug",)),
        (finalize, ("_warn_of_codespace_claims_for_worktree",)),
        (boot, ("native_execution_domain_boot_evidence",)),
        (pr_legacy_admission, ("_require_complete_coverage", "_native_volume_type")),
    ):
        flow.patches.extend((module, name, getattr(module, name)) for name in names)


@pytest.mark.usefixtures("retained_flow_environment")
@pytest.mark.parametrize("damage", ["none", "receipt"], scope="class")
class TestNativeFinalizationRetirement:
    def test_native_finalization_reconciliation(
        self, retained_flow: RetainedFlow, legacy: Legacy, capfd: pytest.CaptureFixture[str],
    ) -> None:
        assert retained_flow.phase == "new"
        assert invoke(legacy, capfd)["success"]
        remember_flow(retained_flow, legacy)
        retained_flow.phase = "reconciled"

    def test_native_finalization_cannot_disable_retirement_qualification(
        self, retained_flow: RetainedFlow, damage: str,
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from agent_worktrees import pr_authority

        assert retained_flow.phase == "reconciled" and retained_flow.legacy is not None
        config, wid, wt, _remote, path, _record, provider = retained_flow.legacy
        valid = damage == "none"
        record = tracking.load_record(path)
        if not valid:
            del selected(record).creation["recovery_contract"]
            tracking.save_record(record)
        provider.find_pull_by_head = lambda *a, **k: pytest.fail(
            "retired attempt was adopted by heuristic healing",
        )
        config = replace(config, repos={config.repo_name: replace(
            config.default_repo, pr=replace(config.default_repo.pr, enabled=False),
        )})
        def unlocked_probe() -> bool:
            with tracking._RecordLock(path, timeout=0.1, require_sidecar=True), \
                    FinalizeLock(Path(config.default_repo.worktree_root) / ".finalize.lock", timeout=0.1), \
                    pr_authority.guard(timeout=0):
                return True
        with ThreadPoolExecutor(max_workers=1) as pool:
            provider.confirm_reconciled_head_v1 = lambda *a, **k: pool.submit(unlocked_probe).result(timeout=3)
            result = finalize.validate_and_finalize(wid, config)
        assert result is valid
        assert wt.exists() is not valid
        retained_flow.phase = "checked"


@pytest.mark.usefixtures("retained_flow_environment")
@pytest.mark.parametrize("damage", ["none", "receipt", "provider", "claim", "new-pr", "mux"], scope="class")
class TestRetainedFinalizationCleanup:
    def test_retained_finalization_reconciliation(
        self, retained_flow: RetainedFlow, legacy: Legacy, capfd: pytest.CaptureFixture[str],
    ) -> None:
        assert retained_flow.phase == "new"
        assert invoke(legacy, capfd)["success"]
        remember_flow(retained_flow, legacy)
        retained_flow.phase = "reconciled"

    def test_retained_finalization(self, retained_flow: RetainedFlow) -> None:
        assert retained_flow.phase == "reconciled" and retained_flow.legacy is not None
        config, wid, wt, _remote, path, _original, _provider = retained_flow.legacy
        assert finalize.validate_and_finalize(wid, config, retain_checkout=True)
        assert wt.exists() and tracking.load_record(path).status == "finalized"
        retained_flow.phase = "finalized"

    def test_retained_finalization_exact_cleanup(
        self, retained_flow: RetainedFlow, monkeypatch: pytest.MonkeyPatch, damage: str,
    ) -> None:
        from agent_worktrees import sessions, worktree_identity

        assert retained_flow.phase == "finalized" and retained_flow.legacy is not None
        _config, wid, wt, _remote, path, _original, provider = retained_flow.legacy
        record = tracking.load_record(path)
        if damage == "receipt":
            del selected(record).creation["recovery_contract"]
        elif damage == "provider":
            provider.confirm_reconciled_head_v1 = lambda *a, **k: False
        elif damage == "claim":
            record.resources.append(ResourceClaim(
                kind="worktree", ref="test/other/child", state="active",
            ))
        elif damage == "new-pr":
            record.prs.append(tracking.PRRecord(
                pr_id="new-pr", state="open", number=91, branch="pr/new", repo="example/repo",
            ))
        elif damage == "mux":
            monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {sessions.mux_session_name(wid): 0})
        tracking.save_record(record)
        before = path.read_bytes()
        monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
        result = cli.reap_one(wid)
        assert result["removed"] is (damage == "none"), result
        assert wt.exists() is (damage != "none")
        if damage != "none":
            assert path.read_bytes() == before
        else:
            retained = tracking.load_record(path)
            assert retained.status == "archived"
            assert selected(retained).creation == selected(record).creation
        retained_flow.phase = "checked"


@pytest.mark.parametrize("state", ["creating", "closed"])
def test_receipt_alone_never_settles_record_retirement(
    legacy: Legacy, capfd: pytest.CaptureFixture[str], state: str,
) -> None:
    from agent_worktrees import retirement_safety

    assert invoke(legacy, capfd)["success"]
    record = tracking.load_record(legacy[4])
    selected(record).state = state
    tracking.save_record(record)
    assert retirement_safety.retirement_blocker(record) is not None
    with pytest.raises(ValueError, match="still open"):
        tracking.retire_record(record, legacy[4].parent)
    assert legacy[2].exists()


@pytest.mark.parametrize("landed", [False, True])
def test_missing_finalized_checkout_still_qualifies_every_owned_ref(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], landed: bool,
) -> None:
    from agent_worktrees import sessions

    config, wid, wt, _remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    record = tracking.load_record(path)
    if not landed:
        git_ops.git("commit", "--allow-empty", "-m", "Unlanded source remains owned", cwd=str(wt))
    tracking.update_status(record, "finalized")
    assert git_ops.remove_worktree(config.default_repo.anchor, str(wt))
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {})
    before = path.read_bytes()
    result = cli.reap_one(wid)
    assert result["removed"] is landed, result
    assert path.exists()
    if landed:
        assert tracking.load_record(path).status == "archived"
    else:
        assert path.read_bytes() == before
        assert git_ops.ref_exists(f"refs/heads/{record.branch}", cwd=config.default_repo.anchor)


@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("force", [False, True])
def test_managed_removal_uses_fresh_receipt_qualification(
    legacy: Legacy, capfd: pytest.CaptureFixture[str], valid: bool, force: bool,
) -> None:
    from agent_worktrees import managed_worktree_guard as managed
    from agent_worktrees import retirement_safety

    config, wid, wt, _remote, path, _original, provider = legacy
    assert invoke(legacy, capfd)["success"]
    record = tracking.load_record(path)
    record.kind = "system"
    tracking.save_record(record)
    if not valid:
        provider.confirm_reconciled_head_v1 = lambda *a, **k: False
    actions = []
    def remove(record: tracking.WorktreeRecord, repo: cfg.RepoConfig, tracking_path: Path, *, force: bool) -> tuple[bool, list[str]]:
        assert retirement_safety.retirement_blocker(record) is None
        actions.append((record.worktree_id, force))
        return True, []
    result = managed.perform(
        wid, path, config, force=force, remove_fn=remove, tracking_path=path.parent,
    )
    assert result.ok is valid, result
    assert actions == ([(wid, force)] if valid else [])
    assert wt.exists()


def test_nonforced_managed_removal_preserves_a_late_live_mux(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import managed_worktree_guard as managed, reap_cli, sessions

    config, wid, wt, _remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    record = tracking.load_record(path)
    record.kind = "system"
    tracking.save_record(record)
    before = path.read_bytes()
    monkeypatch.setattr(sessions, "kill_tmux_session", lambda *a: pytest.fail("Nonforced removal killed a live mux"))

    def remove(record: tracking.WorktreeRecord, repo: cfg.RepoConfig, tracking_path: Path, *, force: bool) -> tuple[bool, list[str]]:
        monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {sessions.mux_session_name(wid): object()})
        return reap_cli._remove_managed_worktree(record, repo, tracking_path, force=force)

    result = managed.perform(
        wid, path, config, force=False, remove_fn=remove, tracking_path=path.parent,
    )
    assert result.ok and not result.removed, result
    assert result.warnings == ["live mux appeared before removal"]
    assert wt.exists() and path.read_bytes() == before


@pytest.mark.parametrize("missing", [False, True])
def test_saved_recovery_head_remains_owned_after_refs_are_replaced(
    legacy: Legacy, capfd: pytest.CaptureFixture[str], missing: bool,
) -> None:
    from agent_worktrees import pr_creation_reconcile as reconcile

    config, _wid, wt, _remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    record = tracking.load_record(path)
    saved_head = selected(record).creation["recovery_head"]
    assert reconcile._source(record, config.default_repo) == saved_head
    tree = git_ops.git("rev-parse", "HEAD^{tree}", cwd=str(wt)).stdout.strip()
    replacement = git_ops.git("commit-tree", tree, "-m", "Unrelated replacement root", cwd=str(wt)).stdout.strip()
    assert replacement != saved_head
    git_ops.git("reset", "--hard", replacement, cwd=str(wt))
    git_ops.git("branch", "--force", selected(record).creation["recovery_branch"], replacement, cwd=str(wt))
    git_ops.git("update-ref", "refs/remotes/origin/master", replacement, cwd=str(wt))
    if missing:
        tracking.update_status(record, "finalized")
        assert git_ops.remove_worktree(config.default_repo.anchor, str(wt))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Every owned local source/PR ref"):
        reconcile._source(record, config.default_repo)
    assert path.read_bytes() == before


def test_missing_finalized_checkout_qualifies_from_bare_anchor(
    legacy: Legacy, capfd: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    from agent_worktrees import pr_creation_reconcile as reconcile

    config, _wid, wt, _remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    record = tracking.load_record(path)
    tracking.update_status(record, "finalized")
    assert git_ops.remove_worktree(config.default_repo.anchor, str(wt))
    bare = tmp_path / "bare-anchor.git"
    git_ops.git("clone", "--mirror", config.default_repo.anchor, str(bare), cwd=str(tmp_path))
    assert git_ops.git("--git-dir", str(bare), "rev-parse", "--is-bare-repository", cwd=str(bare)).stdout.strip() == "true"
    repo = replace(config.default_repo, anchor=str(bare))
    before = path.read_bytes()
    assert reconcile._source(record, repo) == ""
    git_ops.git("--git-dir", str(bare), "update-ref", "-d", "refs/remotes/origin/master", cwd=str(bare))
    with pytest.raises(ValueError, match="Every owned local source/PR ref"):
        reconcile._source(record, repo)
    assert path.read_bytes() == before


@pytest.mark.parametrize("lease", ["lifecycle", "admission"])
def test_managed_removal_keeps_an_unrelated_launch_lease(
    legacy: Legacy, capfd: pytest.CaptureFixture[str], lease: str,
) -> None:
    from agent_worktrees import launch_admission, managed_worktree_guard as managed, worktree_lifecycle

    config, wid, wt, _remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    before = path.read_bytes()
    def remove(*args: Any, **kwargs: Any) -> tuple[bool, list[str]]:
        pytest.fail("Removal crossed an unrelated active launch")
    guard = worktree_lifecycle.guard if lease == "lifecycle" else launch_admission.guard
    with guard(path):
        result = managed.perform(
            wid, path, config, force=True, remove_fn=remove, tracking_path=path.parent,
        )
    assert not result.ok and wt.exists()
    assert path.read_bytes() == before


@pytest.mark.parametrize("direct", [False, True])
def test_managed_removal_keeps_its_record_transaction_with_a_live_monitor(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch, direct: bool,
) -> None:
    from agent_worktrees import launch_seed_state, locks, managed_worktree_guard as managed
    from agent_worktrees import reap_cli, sessions, status_monitor_runtime, tracking_write

    config, wid, wt, _remote = pr_repo
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    path = cfg.tracking_dir() / f"{wid}.yaml"
    record = tracking.load_record(path)
    record.kind = "system"
    tracking.save_record(record)
    received: list[str] = []

    def compute(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        received.append(payload["verb"])
        return cast(dict[str, Any], tracking_write.compute(kind, payload))

    server = tracking_write.start_server(compute)
    server.start()
    endpoint = tracking_write.rendezvous_fields(server)
    monkeypatch.setattr(locks, "read_lock", lambda *a: endpoint)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(status_monitor_runtime, "_ensure_status_monitor", lambda: pytest.fail("Private monitor was already live"))
    monkeypatch.setattr(sessions, "kill_tmux_session", lambda *a: None)
    try:
        seed = launch_seed_state.stage(path, kind="resume", text="Disposable managed prompt")
        assert seed is not None and received == ["launch_seed_stage"]
        with tracking._RecordLock(path.with_name("unrelated.yaml"), require_sidecar=True):
            assert launch_seed_state.remove(path)["removed"]
        assert received == ["launch_seed_stage", "launch_seed_remove"]
        seed = launch_seed_state.stage(path, kind="resume", text="Disposable managed prompt")
        assert seed is not None
        before_remove = list(received)
        if direct:
            removed, warnings = reap_cli._remove_managed_worktree(
                tracking.load_record(path), config.default_repo, path.parent, force=True,
            )
            assert removed and not warnings, warnings
        else:
            result = managed.perform(
                wid, path, config, force=True, remove_fn=reap_cli._remove_managed_worktree,
                tracking_path=path.parent,
            )
            assert result.ok and result.removed and not result.warnings, result
        assert not wt.exists() and not path.exists()
        assert not launch_seed_state.state_path(path).exists()
        assert received == before_remove, "An owned record transaction delegated its nested write"
    finally:
        server.close()


@pytest.mark.parametrize("late_commit", [False, True])
def test_nonforced_managed_removal_qualifies_squash_content_before_teardown(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch,
    late_commit: bool,
) -> None:
    from agent_worktrees import managed_worktree_guard as managed, reap_cli, sessions

    config, wid, wt, _remote = pr_repo
    path = cfg.tracking_dir() / f"{wid}.yaml"
    record = tracking.load_record(path)
    record.kind = "system"
    tracking.save_record(record)
    anchor = config.default_repo.anchor
    git_ops.git("merge", "--squash", record.branch, cwd=anchor)
    git_ops.git("commit", "-m", "Squashed feature", cwd=anchor)
    git_ops.git("update-ref", "refs/remotes/origin/master", "HEAD", cwd=anchor)
    assert git_ops.git(
        "merge-base", "--is-ancestor", record.branch, "origin/master",
        cwd=anchor, check=False,
    ).returncode == 1
    assert git_ops.is_branch_merged(record.branch, "origin/master", cwd=anchor)
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {})

    def remove(record: tracking.WorktreeRecord, repo: cfg.RepoConfig, tracking_path: Path, *, force: bool) -> tuple[bool, list[str]]:
        if late_commit:
            (wt / "late.txt").write_text("Not landed\n", encoding="utf-8")
            git_ops.git("add", "late.txt", cwd=wt)
            git_ops.git("commit", "-m", "Late unlanded work", cwd=wt)
        return reap_cli._remove_managed_worktree(record, repo, tracking_path, force=force)

    result = managed.perform(
        wid, path, config, force=False, remove_fn=remove, tracking_path=path.parent,
    )
    assert result.ok and result.removed is not late_commit, result
    assert wt.exists() is late_commit and path.exists() is late_commit
    assert git_ops.ref_exists(f"refs/heads/{record.branch}", cwd=anchor) is late_commit


def test_managed_removal_locks_the_retirement_pair_before_teardown(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import managed_worktree_guard as managed
    from agent_worktrees.tracking_claims import format_claim_ref

    config, wid, wt, _remote = pr_repo
    path = cfg.tracking_dir() / f"{wid}.yaml"
    record = tracking.load_record(path)
    record.pair_id = "fixture-pair"
    record.pair_ref = format_claim_ref(record.execution_machine, "peer", "aaa-peer")
    tracking.save_record(record)
    peer_path = cfg.project_dir("peer") / "worktrees" / "aaa-peer.yaml"
    acquired: list[Path] = []
    class ObservedLock(tracking._RecordLock):
        def __enter__(self) -> tracking._RecordLock:
            result = super().__enter__()
            if self._yaml_path.suffix == ".yaml":
                acquired.append(self._yaml_path.resolve())
            return result

    def remove(*args: Any, **kwargs: Any) -> tuple[bool, list[str]]:
        assert acquired == sorted([path.resolve(), peer_path.resolve()], key=str)
        return False, []

    monkeypatch.setattr(tracking, "_RecordLock", ObservedLock)
    result = managed.perform(
        wid, path, config, force=True, remove_fn=remove, tracking_path=path.parent,
    )
    assert result.ok and not result.removed and wt.exists()


def test_removal_transaction_preserves_receiving_authority_checks(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import launch_seed_state
    from agent_worktrees.execution_spaces import ExecutionSpaceError

    _config, wid, _wt, _remote = pr_repo
    path = cfg.tracking_dir() / f"{wid}.yaml"
    before = path.read_bytes()

    def reject(record: tracking.WorktreeRecord) -> None:
        raise ExecutionSpaceError("Receiving owner changed")

    monkeypatch.setattr(launch_seed_state, "require_project_record_mutation", reject)
    with launch_seed_state.removal_transaction(path):
        with pytest.raises(ValueError, match="Receiving owner changed"):
            launch_seed_state.remove(path, remove_record=True)
    assert path.read_bytes() == before


def test_managed_removal_acquires_global_lock_before_lifecycle(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import managed_worktree_guard as managed, worktree_lifecycle

    config, wid, wt, _remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    record = tracking.load_record(path)
    record.kind = "system"
    tracking.save_record(record)
    acquired: list[Path] = []

    class OrderedLock(FinalizeLock):
        def acquire(self) -> None:
            assert not getattr(worktree_lifecycle._held, "paths", set()), "Lifecycle lease preceded the global mutation lock"
            super().acquire()
            acquired.append(self.lock_path)

    monkeypatch.setattr(finalize, "FinalizeLock", OrderedLock)
    result = managed.perform(
        wid, path, config, force=False, remove_fn=lambda *a, **k: (False, []),
        tracking_path=path.parent,
    )
    assert result.ok and acquired == [Path(config.default_repo.worktree_root) / ".finalize.lock"], result
    assert wt.exists()


def test_forced_managed_removal_refuses_a_receipt_arriving_after_preflight(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import managed_worktree_guard as managed

    config, wid, wt, _remote, path, _original, _provider = legacy
    preflight = tracking.load_record(path)
    preflight.kind = "system"
    assert invoke(legacy, capfd)["success"]
    retired = tracking.load_record(path)
    retired.kind = "system"
    tracking.save_record(retired)
    load = tracking.load_record
    stale = True

    def raced(candidate: Path) -> tracking.WorktreeRecord:
        nonlocal stale
        if candidate == path and stale:
            stale = False
            return preflight
        return load(candidate)

    monkeypatch.setattr(tracking, "load_record", raced)
    result = managed.perform(
        wid, path, config, force=True,
        remove_fn=lambda *a, **k: pytest.fail("Forced removal skipped the newly retained receipt"),
        tracking_path=path.parent,
    )
    assert not result.ok and "qualification" in result.message, result
    assert wt.exists() and selected(tracking.load_record(path)).creation == selected(retired).creation


def test_exact_cleanup_requalifies_after_native_fetch(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    from agent_worktrees import sessions

    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {})
    config, wid, wt, _remote, path, _original, _provider = legacy
    assert invoke(legacy, capfd)["success"]
    record = tracking.load_record(path)
    tracking.update_status(record, "finalized")
    tree = git_ops.git("rev-parse", "HEAD^{tree}", cwd=str(wt)).stdout.strip()
    replacement = git_ops.git("commit-tree", tree, "-m", "Unrelated upstream root", cwd=str(wt)).stdout.strip()
    fetch = git_ops.fetch
    fetched: list[str] = []

    def rewound(remote: str, **kwargs: Any) -> Any:
        result = fetch(remote, **kwargs)
        git_ops.git("update-ref", "refs/remotes/origin/master", replacement, cwd=kwargs["cwd"])
        fetched.append(remote)
        return result

    monkeypatch.setattr(git_ops, "fetch", rewound)
    monkeypatch.setattr(cli, "_reap_worktree", lambda *a, **k: pytest.fail("Cleanup used a proof from before fetch"))
    before = path.read_bytes()
    result = cli.reap_one(wid, force=True)
    assert fetched == ["origin"] and not result["removed"], result
    assert "Publication intervened" in result.get("reason", ""), result
    assert wt.exists() and path.read_bytes() == before


def test_exact_cleanup_does_not_resolve_a_vanished_owner_again(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import worktree_identity

    _config, wid, _wt, _remote = pr_repo
    path = cfg.tracking_dir() / f"{wid}.yaml"
    other = path.with_name(f"other-{wid}.yaml")
    record = tracking.load_record(path)
    record.kind = "system"
    tracking.save_record(record)
    other.write_bytes(path.read_bytes())
    before = other.read_bytes()
    resolve = worktree_identity._resolve_worktree_id
    resolutions: list[str] = []

    def vanished(raw_id: str, *, project: str | None = None) -> str:
        selected_id = resolve(raw_id, project=project)
        resolutions.append(selected_id)
        if len(resolutions) == 1:
            path.unlink()
        return selected_id

    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", vanished)
    result = cli.reap_one(wid, force=True)
    assert not result["removed"] and result["worktree_id"] == wid, result
    assert "not found" in result["reason"]
    assert resolutions == [wid] and other.read_bytes() == before


@pytest.mark.parametrize("operation", ["cleanup", "managed", "conclude"])
@pytest.mark.parametrize("failure", [OSError, ValueError, TimeoutError])
def test_native_retirement_failure_is_not_a_qualification_skip(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch,
    operation: str, failure: type[Exception],
) -> None:
    from agent_worktrees import managed_worktree_guard as managed
    from agent_worktrees import terminal_conclusion as terminal

    config, wid, _wt, _remote = pr_repo
    path = cfg.tracking_dir() / f"{wid}.yaml"
    effects: list[str] = []

    def failed(*args: Any, **kwargs: Any) -> Any:
        effects.append("native operation started")
        raise failure("Native outcome must remain a failure")

    with pytest.raises(failure, match="Native outcome"):
        if operation == "cleanup":
            monkeypatch.setattr(cli, "_reap_one", failed)
            cli.reap_one(wid)
        elif operation == "managed":
            monkeypatch.setattr(managed, "_perform", failed)
            managed.perform(
                wid, path, config, force=False, remove_fn=lambda *a, **k: (False, []),
                tracking_path=path.parent,
            )
        else:
            monkeypatch.setattr(terminal, "_conclude_disposable_worktree", failed)
            terminal.conclude_disposable_worktree(
                path, config.default_repo, session_id=None, owner="fixture",
                policy=terminal.DISPOSABLE_CLI_POLICY,
            )
    assert effects == ["native operation started"]


@pytest.mark.parametrize("valid", [False, True])
def test_terminal_conclusion_uses_fresh_receipt_qualification(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], valid: bool,
) -> None:
    from agent_worktrees import terminal_conclusion as terminal

    config, _wid, _wt, _remote, path, _original, provider = legacy
    assert invoke(legacy, capfd)["success"]
    monkeypatch.setattr(terminal, "_session_is_live", lambda record: None)
    if not valid:
        provider.confirm_reconciled_head_v1 = lambda *a, **k: False
    before = path.read_bytes()
    result = terminal.conclude_disposable_worktree(
        path, config.default_repo, session_id=None, owner="fixture",
        policy=terminal.DISPOSABLE_CLI_POLICY,
    )
    assert result["managed_gc_eligible"] is valid, result
    if not valid:
        assert path.read_bytes() == before


@pytest.mark.usefixtures("retained_flow_environment")
@pytest.mark.parametrize("damage", ["finalized", "archived"], scope="class")
class TestMissingTerminalReceipt:
    def test_terminal_conclusion_prepare_missing_terminal_receipt(
        self, retained_flow: RetainedFlow, legacy: Legacy, capfd: pytest.CaptureFixture[str],
        damage: tracking.WorktreeStatus,
    ) -> None:
        assert retained_flow.phase == "new"
        config, _wid, wt, _remote, path, _original, _provider = legacy
        assert invoke(legacy, capfd)["success"]
        record = tracking.load_record(path)
        tracking.update_status(record, damage)
        assert git_ops.remove_worktree(config.default_repo.anchor, str(wt))
        remember_flow(retained_flow, legacy)
        retained_flow.phase = "missing"

    @pytest.mark.parametrize("repeat", [False, True])
    def test_terminal_conclusion_preserves_missing_terminal_receipt(
        self, retained_flow: RetainedFlow, monkeypatch: pytest.MonkeyPatch,
        damage: tracking.WorktreeStatus, repeat: bool,
    ) -> None:
        from agent_worktrees import pr_creation_reconcile, terminal_conclusion as terminal

        assert retained_flow.phase == ("concluded" if repeat else "missing")
        assert retained_flow.legacy is not None
        config, _wid, _wt, _remote, path, _original, _provider = retained_flow.legacy
        monkeypatch.setattr(terminal, "_session_is_live", lambda record: None)
        result = terminal.conclude_disposable_worktree(
            path, config.default_repo, session_id=None, owner="fixture",
            policy=terminal.DISPOSABLE_CLI_POLICY,
        )
        assert result["managed_gc_eligible"], result
        concluded = tracking.load_record(path)
        assert concluded.status == damage
        if repeat:
            assert result["action"] == "already-primed"
            assert pr_creation_reconcile.finalization_error(concluded, config.default_repo) is None
        retained_flow.phase = "checked" if repeat else "concluded"
