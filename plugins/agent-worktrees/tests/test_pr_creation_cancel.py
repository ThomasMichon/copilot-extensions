"""Local cancellation exercises real creation, persistence and finalization owners."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg
from agent_worktrees import finalize, finalize_open_pr_gate, git_ops, pr_creation_cancel
from agent_worktrees import pr_creation_state as state
from agent_worktrees import pr_ops, pr_publish, providers, tracking
from agent_worktrees.providers import github_head_absence
from agent_worktrees.providers.github import GitHubProvider

pytestmark = pytest.mark.contract("agent_worktrees.pr_publish.publication_state")


@pytest.fixture
def reservation(pr_repo, monkeypatch):
    config, wid, wt, remote = pr_repo
    config = replace(config, repos={
        config.repo_name: replace(
            config.default_repo, pr=replace(config.default_repo.pr, provider="github"),
        ),
    })
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(git_ops, "remote_slug", lambda *a, **k: "example/repo")
    snapshot = pr_publish._remote_snapshot
    def synthetic_destination(remote, *, cwd):
        fetch, urls, _destination = snapshot(remote, cwd=cwd)
        return fetch, urls, pr_publish.PushTarget(remote, "example/repo", "github.com/example/repo")
    monkeypatch.setattr(pr_publish, "_remote_snapshot", synthetic_destination)
    real_rebase = git_ops.rebase
    monkeypatch.setattr(git_ops, "rebase", lambda *a, **k: False)
    failed = pr_ops.create_pr(wid, config, title="Example contribution", open_pr=False)
    assert not failed["success"] and "genuine conflicts" in failed["error"]
    monkeypatch.setattr(git_ops, "rebase", real_rebase)
    path = cfg.tracking_dir() / f"{wid}.yaml"
    record = tracking.load_record(path)
    assert state.reserved(record.pr, record)
    provider = SimpleNamespace(
        authority_endpoint=lambda *a: "github.com",
        confirm_no_pull_by_head_v1=lambda *a, **k: True,
    )
    monkeypatch.setattr(providers, "get_provider", lambda *a: provider)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: "")
    return config, wid, wt, remote, path, record, provider


def cancel(reservation, **overrides):
    config, wid, _wt, _remote, _path, record, _provider = reservation
    options = dict(
        creation_id=record.pr.pr_id, expected_revision=record.pr.pr_revision,
        reason="Contribution declined; no remaining useful delta", confirm=True,
    )
    options.update(overrides)
    return pr_ops.abandon_pr(wid, config, **options)


def source_snapshot(wt):
    def git(*args):
        return git_ops.git(*args, cwd=str(wt)).stdout
    index = Path(git("rev-parse", "--git-path", "index").strip())
    return git("rev-parse", "HEAD"), index.read_bytes(), git("status", "--porcelain"), git("show-ref")


def test_actual_cli_cancels_only_selected_attempt_and_preserves_audit(reservation, monkeypatch, capfd):
    config, wid, wt, _remote, path, record, _provider = reservation
    sibling = replace(record.pr, pr_id="other-attempt", branch="pr/other", creation={})
    record.prs.append(sibling)
    record.resources.append(tracking.ResourceClaim(
        kind="worktree", ref="test/ext/child", state="active", created_at=tracking._now_iso(),
    ))
    tracking.save_record(record)
    source = source_snapshot(wt)
    before = tracking.load_record(path)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "test-session")
    args = cli.build_parser().parse_args([
        "pr-abandon", wid, "--creation", record.prs[0].pr_id,
        "--expected-revision", str(record.prs[0].pr_revision),
        "--reason", "Contribution declined", "--confirm", "--json",
    ])
    from agent_worktrees.pr_state_cli import cmd_pr_abandon
    assert cmd_pr_abandon(args) == 0
    response = json.loads(capfd.readouterr().out)
    assert response["creation_cancelled"] and not response["provider_closed"]
    assert not response["claim_released"]
    current = tracking.load_record(path)
    selected = current.prs[0]
    assert state.cancelled(selected)
    assert selected.creation["reason"] == "Contribution declined"
    assert selected.creation["actor"] == "test-session"
    assert selected.pr_id == before.prs[0].pr_id
    assert selected.attribution_mode == before.prs[0].attribution_mode
    assert selected.attribution_explicit == before.prs[0].attribution_explicit
    assert current.prs[1] == before.prs[1] and current.resources == before.resources
    assert source_snapshot(wt) == source
    assert current.active_pr().pr_id == sibling.pr_id


def test_idempotent_exact_confirmation_does_not_rewrite_receipt(reservation):
    first = cancel(reservation)
    assert first["success"], first
    path = reservation[4]
    saved = path.read_bytes()
    assert cancel(reservation)["already_cancelled"]
    assert path.read_bytes() == saved
    assert not cancel(reservation, reason="different reason")["success"]
    assert not cancel(reservation, expected_revision=first["pr_revision"])["success"]


@pytest.mark.parametrize("confirm,reason", [(False, "Declined"), (False, ""), (True, " ")])
def test_unconfirmed_or_missing_reason_performs_no_record_or_provider_read(monkeypatch, confirm, reason):
    monkeypatch.setattr(tracking, "load_record", lambda *a: pytest.fail("record read"))
    monkeypatch.setattr(providers, "get_provider", lambda *a: pytest.fail("provider read"))
    result = pr_ops.abandon_pr(
        "example", SimpleNamespace(), creation_id="attempt", reason=reason, confirm=confirm,
    )
    assert not result["success"]
    assert result.get("needs_confirm", False) == bool(reason.strip() and not confirm)


@pytest.mark.parametrize("field", [
    "url", "number", "base_sha", "head_sha", "patch_id", "rewrite_owner",
    "rewrite_identity", "remote", "head_repo", "head_identity", "head_owner",
    "head_observed_at", "head_observed_api_base", "attribution_head", "closed_at",
])
def test_every_publication_evidence_excludes_cancellation(reservation, field):
    record = reservation[5]
    pr = replace(record.pr)
    setattr(pr, field, 7 if field == "number" else "publication-evidence")
    assert not state.reserved(pr, record)


@pytest.mark.parametrize("change", ["legacy", "publishing", "binding", "owner", "revision", "duplicate"])
def test_legacy_or_changed_authority_is_retained_without_observation(reservation, monkeypatch, change):
    path = reservation[4]
    record = tracking.load_record(path)
    if change == "legacy":
        record.pr.creation = {}
    elif change == "publishing":
        record.pr.creation = {**record.pr.creation, "phase": "publishing"}
    elif change in ("binding", "owner"):
        record.pr.creation = {**record.pr.creation, change: "changed"}
    elif change == "revision":
        record.pr.pr_revision += 1
    else:
        record.prs.append(replace(record.pr))
    tracking.save_record(record)
    before = path.read_bytes()
    monkeypatch.setattr(pr_creation_cancel, "_absence", lambda *a: pytest.fail("unqualified observation"))
    result = cancel(reservation)
    assert not result["success"], result
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ["ref-exists", "ref-unknown", "provider-exists", "provider-unknown", "unsupported"])
def test_absence_must_be_authoritative_and_complete(reservation, monkeypatch, failure):
    path, provider = reservation[4], reservation[6]
    if failure.startswith("ref"):
        monkeypatch.setattr(pr_publish, "_tip", lambda *a: "published" if failure == "ref-exists" else None)
    elif failure == "provider-exists":
        provider.confirm_no_pull_by_head_v1 = lambda *a, **k: False
    elif failure == "provider-unknown":
        def unavailable(*a, **k):
            raise providers.ProviderError("provider unavailable")
        provider.confirm_no_pull_by_head_v1 = unavailable
    else:
        del provider.confirm_no_pull_by_head_v1
    before = path.read_bytes()
    result = cancel(reservation)
    assert not result["success"] and "refused" in result["error"].lower()
    assert path.read_bytes() == before


def test_racing_authority_change_wins_over_observation(reservation, monkeypatch):
    original = pr_creation_cancel._absence
    path = reservation[4]
    def race(record, pr, config):
        original(record, pr, config)
        changed = tracking.load_record(path)
        changed.pr.pr_revision += 1
        changed.pr.head_sha = "new-publication"
        tracking.save_record(changed)
    monkeypatch.setattr(pr_creation_cancel, "_absence", race)
    assert not cancel(reservation)["success"]
    assert tracking.load_record(path).pr.head_sha == "new-publication"


def test_stale_stamp_cannot_resurrect_cancelled_attempt(reservation):
    path = reservation[4]
    stale = tracking.load_record(path)
    assert cancel(reservation)["success"]
    stale.title = "An unrelated status update"
    tracking.save_record(stale)
    fresh = tracking.load_record(path)
    assert state.cancelled(fresh.pr) and fresh.title == stale.title


def test_first_push_is_durably_uncertain_before_transport(reservation, monkeypatch):
    config, wid, _wt, _remote, path, record, _provider = reservation
    calls = []
    def fail_push(*a, **k):
        assert tracking.load_record(path).pr.creation["phase"] == "publishing"
        calls.append(True)
        return git_ops.PushResult(ok=False, stderr="outcome unavailable")
    monkeypatch.setattr(git_ops, "push", fail_push)
    result = pr_ops.create_pr(wid, config, title="Example contribution", open_pr=False)
    assert not result["success"] and calls
    assert not cancel(reservation, expected_revision=tracking.load_record(path).pr.pr_revision)["success"]


def test_cancelled_is_not_merged_and_retains_source_finalization_gate(reservation, monkeypatch):
    config, _wid, wt, _remote, path, _record, _provider = reservation
    assert cancel(reservation)["success"]
    record = tracking.load_record(path)
    monkeypatch.setattr(finalize_open_pr_gate, "pull_for", lambda *a: pytest.fail("invented provider PR"))
    assert finalize_open_pr_gate._pr_entry_merge_status(record.pr, config.default_repo) is False
    assert not finalize._pr_finalize_precondition(record, config.default_repo, str(wt), config.default_repo.anchor)[0]
    # Only the disposable fixture is realigned; cancellation itself never does this.
    git_ops.git("reset", "--hard", "origin/master", cwd=str(wt))
    assert finalize._pr_finalize_precondition(record, config.default_repo, str(wt), config.default_repo.anchor)[0]
    record.pr.creation = {}
    monkeypatch.setattr(finalize_open_pr_gate, "pull_for", lambda *a: None)
    assert finalize_open_pr_gate._pr_entry_merge_status(record.pr, config.default_repo) is not False


def test_local_head_or_repointed_remote_is_not_absence(reservation, monkeypatch):
    _config, _wid, wt, _remote, path, record, _provider = reservation
    git_ops.git("branch", record.pr.branch, cwd=str(wt))
    before = path.read_bytes()
    assert not cancel(reservation)["success"]
    assert path.read_bytes() == before
    monkeypatch.setattr(pr_publish, "push_identity", lambda *a, **k: "github.com/elsewhere/repo")
    assert not cancel(reservation)["success"]


@pytest.mark.parametrize("existing", ["local", "remote"])
def test_inherited_git_context_cannot_hide_actual_head(reservation, monkeypatch, tmp_path, existing):
    _config, _wid, wt, _remote, path, record, _provider = reservation
    foreign = tmp_path / "foreign.git"
    git_ops.git("init", "--bare", str(foreign), cwd=str(wt), isolated_repository=True)
    git_ops.git(
        "--git-dir", str(foreign), "config", "remote.origin.url", str(foreign),
        cwd=str(wt), isolated_repository=True,
    )
    if existing == "local":
        git_ops.git("branch", record.pr.branch, cwd=str(wt), isolated_repository=True)
    else:
        git_ops.git(
            "push", "origin", f"HEAD:refs/heads/{record.pr.branch}",
            cwd=str(wt), isolated_repository=True,
        )
    before = path.read_bytes()
    monkeypatch.setenv("GIT_DIR", str(foreign))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "foreign-working-tree"))
    result = cancel(reservation)
    assert not result["success"], result
    assert path.read_bytes() == before


def test_unavailable_git_credentials_are_a_structured_refusal(reservation, monkeypatch):
    path = reservation[4]
    before = path.read_bytes()
    def unavailable(*a, **k):
        raise git_ops.GitError(["git-auth"], 1, "repository account unavailable")
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url", unavailable)
    result = cancel(reservation)
    assert not result["success"] and "repository account unavailable" in result["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("case", ["missing-revision", "repo-override", "numbered-live", "numbered-missing-head"])
def test_invalid_or_external_selection_never_enters_local_absence(reservation, monkeypatch, case):
    path = reservation[4]
    options = {}
    if case == "missing-revision":
        options["expected_revision"] = None
    elif case == "repo-override":
        options["target_repo"] = "elsewhere/repo"
    else:
        record = tracking.load_record(path)
        record.pr.number = 42
        record.pr.state = "open"
        if case == "numbered-live":
            record.pr.head_sha = "published"
        tracking.save_record(record)
    before = path.read_bytes()
    monkeypatch.setattr(pr_creation_cancel, "_absence", lambda *a: pytest.fail("unqualified observation"))
    assert not cancel(reservation, **options)["success"]
    assert path.read_bytes() == before


def test_record_incarnation_race_never_reports_success(reservation, monkeypatch):
    path = reservation[4]
    original = pr_creation_cancel._absence
    def race(record, pr, config):
        original(record, pr, config)
        changed = tracking.load_record(path)
        changed.branch = "worktree/replaced"
        tracking.save_record(changed)
    monkeypatch.setattr(pr_creation_cancel, "_absence", race)
    assert not cancel(reservation)["success"]
    assert tracking.load_record(path).pr.state == "creating"


def test_failed_record_commit_preserves_attempt(reservation, monkeypatch):
    path = reservation[4]
    before = path.read_bytes()
    def failed(*a, **k):
        raise OSError("test-owned persistence failure")
    monkeypatch.setattr(tracking, "save_record", failed)
    result = cancel(reservation)
    assert not result["success"] and "persistence failure" in result["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("key,value", [
    ("contract", "unknown"), ("binding", "changed"), ("reason", ""),
    ("actor", ""), ("at", "changed"), ("revision", "unknown"), ("phase", "reserved"),
])
def test_malformed_cancellation_receipt_is_not_finality_proof(reservation, key, value):
    assert cancel(reservation)["success"]
    record = tracking.load_record(reservation[4])
    assert state.cancelled(record.pr)
    record.pr.creation[key] = value
    assert not state.cancelled(record.pr)


@pytest.mark.parametrize("field,value", [
    ("number", "unknown"), ("number", True), ("number", {}),
    ("number", ""), ("number", "null"),
    ("pr_revision", "unknown"), ("pr_revision", True), ("pr_revision", -1),
    ("pr_revision", tracking.MAX_PERSISTED_COUNTER + 1),
])
def test_lossy_legacy_identity_parse_cannot_retain_eligibility(reservation, field, value):
    record = reservation[5]
    raw = tracking._pr_to_yaml_dict(record.pr)
    raw[field] = value
    parsed = tracking._parse_pr_mapping(raw, record.repo)
    assert not state.reserved(parsed, record)
    assert not state.cancelled(parsed)


@pytest.mark.parametrize("options", [
    ["--creation", "example", "--pr", "7"],
    ["--creaton", "example"],
    ["--creation", "example", "--expected-revision", "not-an-integer"],
])
def test_parser_preserves_strict_rejection(options):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["pr-abandon", "example", "--reason", "declined", *options])
    assert exc.value.code == 2


@pytest.mark.parametrize("gate", ["none", "dirty", "child", "other-pr"])
def test_ordinary_finalization_keeps_all_other_gates(reservation, gate):
    config, wid, wt, _remote, path, _record, _provider = reservation
    assert cancel(reservation)["success"]
    git_ops.git("reset", "--hard", "origin/master", cwd=str(wt))
    record = tracking.load_record(path)
    if gate == "dirty":
        (wt / "uncommitted.txt").write_text("Retain this file\n")
    elif gate == "child":
        record.resources.append(tracking.ResourceClaim(
            kind="worktree", ref="test/ext/child", state="active", created_at=tracking._now_iso(),
        ))
        tracking.save_record(record)
    elif gate == "other-pr":
        record.prs.append(tracking.PRRecord(
            state="open", branch="pr/other", repo="example/repo", provider="github", number=42,
        ))
        tracking.save_record(record)
    result = finalize.validate_and_finalize(wid, config)
    assert result is (gate == "none")
    if gate != "none":
        assert wt.is_dir()
        assert tracking.load_record(path).status == "active"


@pytest.mark.parametrize("body,code,expected", [
    ("[]", 0, True), ('[{"number":7}]', 0, False),
    ("{}", 0, None), ("", 0, None), ("not-json", 0, None), ("[]", 1, None),
])
def test_github_absence_reads_exact_all_state_head(monkeypatch, body, code, expected):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, code, stdout=body, stderr="read failed")
    monkeypatch.setattr(github_head_absence, "run_cli", run)
    provider = GitHubProvider()
    if expected is None:
        with pytest.raises(providers.ProviderError):
            provider.confirm_no_pull_by_head_v1("example/repo", "pr/example")
    else:
        assert provider.confirm_no_pull_by_head_v1("example/repo", "pr/example") is expected
    argv = calls[0][0]
    assert "GET" in argv and "state=all" in argv and "head=example:pr/example" in argv
    assert argv[argv.index("--hostname") + 1] == "github.com"


def test_status_declares_runtime_contract_and_exact_attempt(reservation):
    result = pr_ops.pr_status(reservation[1], config=reservation[0], all_prs=True, live=False)
    assert result["creation_cancellation_contract"] == state.CONTRACT
    assert result["pr_id"] == reservation[5].pr.pr_id
    assert result["pr_revision"] == reservation[5].pr.pr_revision
    assert result["prs"][0]["creation"]["phase"] == "reserved"
