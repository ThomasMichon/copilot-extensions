"""First-push retries require intent persisted before the uncertain transport."""

from dataclasses import replace

import pytest
import yaml

from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_creation_state, pr_ops, pr_publication_state, pr_publish, tracking

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr_publish.publication_state"),
    pytest.mark.timeout(120),
]


def uncertain_push(pr_repo, monkeypatch, *, scheme="snapshot", accepted=True):
    config, wid, wt, remote = pr_repo
    config = replace(config, repos={config.repo_name: replace(
        config.default_repo, pr=replace(
            config.default_repo.pr, head_scheme=scheme, api_base="https://example.com/gitea",
        ),
    )})
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    real_push = git_ops.push
    calls = []
    path = cfg.tracking_dir() / f"{wid}.yaml"

    def push(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            retained = tracking.load_record(path)
            assert retained.pr.creation["push_head"]
            assert retained.pr.creation["phase"] == "publishing"
            assert not pr_creation_state.reserved(retained.pr, retained)
            if accepted:
                result = real_push(*args, **kwargs)
                assert result, result.stderr
            return git_ops.PushResult(ok=False, stderr="fixture acknowledgement timeout")
        return real_push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", push)
    result = pr_ops.create_pr(wid, config, title="Example contribution", open_pr=False)
    assert not result["success"] and "acknowledgement timeout" in result["error"]
    record = tracking.load_record(path)
    assert record.pr.state == "creating" and not record.pr.head_sha
    assert record.pr.number is None
    return config, wid, wt, remote, path, record, calls


@pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
@pytest.mark.parametrize("accepted", [False, True])
def test_actual_create_pr_resumes_same_prepared_attempt(pr_repo, monkeypatch, scheme, accepted):
    config, wid, wt, remote, path, before, calls = uncertain_push(
        pr_repo, monkeypatch, scheme=scheme, accepted=accepted,
    )
    saved_head = before.pr.creation["push_head"]
    source = git_ops.git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    result = pr_ops.create_pr(wid, config, title="Example contribution", open_pr=False)
    assert result["success"], result
    after = tracking.load_record(path)
    assert len(calls) == 2 and len(after.prs) == 1
    assert after.pr.pr_id == before.pr.pr_id and after.pr.branch == before.pr.branch
    assert after.pr.head_sha == result["head_sha"] == saved_head == source
    assert after.pr.base_sha == before.pr.creation["push_base"]
    assert after.pr.patch_id and after.pr.state == "open"
    assert git_ops.git("rev-parse", "HEAD", cwd=wt).stdout.strip() == source
    assert git_ops._get_current_branch_safe(str(wt)) == before.branch
    assert pr_publish._tip("origin", after.pr.branch, str(wt)) == saved_head


@pytest.mark.parametrize("change", [
    "source", "remote", "destination", "owner", "incarnation", "binding", "endpoint", "unknown",
])
def test_changed_or_unknown_evidence_never_retries_transport(pr_repo, monkeypatch, change):
    config, wid, wt, remote, path, record, calls = uncertain_push(pr_repo, monkeypatch)
    if change == "source":
        (wt / "later.txt").write_text("unpublished work\n")
        git_ops.git("add", "later.txt", cwd=wt)
        git_ops.git("commit", "-m", "Later work", cwd=wt)
    elif change == "remote":
        git_ops.git(
            "--git-dir", str(remote), "update-ref", f"refs/heads/{record.pr.branch}",
            record.pr.creation["push_base"],
        )
    elif change == "destination":
        replacement = wt.parent / "other.git"
        git_ops.git("init", "--template=", "--bare", str(replacement))
        git_ops.git("remote", "set-url", "origin", str(replacement), cwd=wt)
    elif change == "incarnation":
        raw = yaml.safe_load(path.read_text())
        raw["creation_nonce"] = "new-incarnation"
        path.write_text(yaml.safe_dump(raw))
    elif change in ("owner", "binding"):
        record.pr.creation = {**record.pr.creation, change: "changed"}
        tracking.save_record(record)
    elif change == "endpoint":
        config = replace(config, repos={config.repo_name: replace(
            config.default_repo, pr=replace(config.default_repo.pr, api_base="https://other.example.com"),
        )})
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    else:
        monkeypatch.setattr(pr_publish, "_tip", lambda *a, **k: None)
    before = path.read_bytes()
    result = pr_ops.create_pr(wid, config, title="Example contribution", open_pr=False)
    assert not result["success"], result
    assert len(calls) == 1 and path.read_bytes() == before


def test_equal_legacy_head_is_not_upgraded_to_prepared_proof(pr_repo, monkeypatch):
    config, wid, wt, remote, path, record, calls = uncertain_push(pr_repo, monkeypatch)
    record.pr.creation = {
        k: v for k, v in record.pr.creation.items() if not k.startswith("push_")
    }
    tracking.save_record(record)
    git_ops.git("switch", record.pr.branch, cwd=wt)
    before = path.read_bytes()
    result = pr_ops.create_pr(wid, config, title="Example contribution", open_pr=False)
    assert not result["success"] and "Unleased provisional" in result["error"]
    assert len(calls) == 1 and path.read_bytes() == before


@pytest.mark.parametrize("change", ["authority", "incarnation"])
def test_action_time_authority_race_prevents_repush(pr_repo, monkeypatch, change):
    config, wid, wt, remote, path, record, calls = uncertain_push(pr_repo, monkeypatch)
    settings = pr_publish._transport_settings

    def changed(*args, **kwargs):
        result = settings(*args, **kwargs)
        if change == "incarnation":
            raw = yaml.safe_load(path.read_text())
            raw["creation_nonce"] = "new-incarnation"
            path.write_text(yaml.safe_dump(raw))
        else:
            current = tracking.load_record(path)
            current.pr.creation = {**current.pr.creation, "owner": "new-owner"}
            current.pr.pr_revision += 1
            tracking.save_record(current)
        return result

    monkeypatch.setattr(pr_publish, "_transport_settings", changed)
    result = pr_ops.create_pr(wid, config, title="Example contribution", open_pr=False)
    assert not result["success"] and "changed" in result["error"]
    assert len(calls) == 1
    current = tracking.load_record(path)
    if change == "incarnation":
        assert current.creation_nonce == "new-incarnation"
    else:
        assert current.pr.creation["owner"] == "new-owner"


def test_prepared_retry_refuses_changed_target_repository(pr_repo, monkeypatch):
    config, wid, wt, remote, path, record, calls = uncertain_push(pr_repo, monkeypatch)
    before = path.read_bytes()
    result = pr_ops.create_pr(
        wid, config, title="Example contribution", target_repo="other/repository", open_pr=False,
    )
    assert not result["success"] and "destination changed" in result["error"]
    assert len(calls) == 1 and path.read_bytes() == before


def test_remote_rewind_at_transport_keeps_exact_lease(pr_repo, monkeypatch):
    config, wid, wt, remote, path, record, calls = uncertain_push(pr_repo, monkeypatch)
    push = git_ops.push
    expected = record.pr.creation["push_head"]
    base = record.pr.creation["push_base"]

    def rewind(*args, **kwargs):
        assert kwargs["force_with_lease_expect"] == expected
        git_ops.git("--git-dir", str(remote), "update-ref", f"refs/heads/{record.pr.branch}", base)
        return push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", rewind)
    result = pr_ops.create_pr(wid, config, title="Example contribution", open_pr=False)
    assert not result["success"] and "stale info" in result["error"]
    assert len(calls) == 2
    assert pr_publish._tip("origin", record.pr.branch, str(wt)) == base
    assert tracking.load_record(path).pr.state == "creating"


def test_transport_uses_prepared_commit_after_source_moves(pr_repo, monkeypatch):
    config, wid, wt, remote, path, record, calls = uncertain_push(pr_repo, monkeypatch)
    push = git_ops.push
    expected = record.pr.creation["push_head"]

    def advance(*args, **kwargs):
        assert args[1].partition(":")[0] == expected
        (wt / "later.txt").write_text("later unpublished work\n")
        git_ops.git("add", "later.txt", cwd=wt)
        git_ops.git("commit", "-m", "Later work", cwd=wt)
        return push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", advance)
    result = pr_ops.create_pr(wid, config, title="Example contribution", open_pr=False)
    assert result["success"], result
    assert result["head_sha"] == tracking.load_record(path).pr.head_sha == expected
    assert pr_publish._tip("origin", record.pr.branch, str(wt)) == expected
    assert git_ops.git("rev-parse", "HEAD", cwd=wt).stdout.strip() != expected


def test_settled_prepared_attempt_becomes_active_after_interleaving(pr_repo, monkeypatch):
    config, wid, wt, remote, path, record, calls = uncertain_push(pr_repo, monkeypatch)
    selected = record.pr
    sibling = replace(
        selected, pr_id="other-attempt", branch="feature/other", creation={},
        opened_at=pr_publication_state.monotonic_opened_at(record),
    )
    record.prs.append(sibling)
    tracking.save_record(record)
    assert tracking.load_record(path).active_pr().pr_id == sibling.pr_id
    result = pr_ops.create_pr(
        wid, config, title="Example contribution", branch=selected.branch, open_pr=False,
    )
    assert result["success"], result
    current = tracking.load_record(path)
    assert current.active_pr().pr_id == selected.pr_id
    assert current.prs[1] == sibling
