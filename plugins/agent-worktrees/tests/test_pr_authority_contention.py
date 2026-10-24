"""Routine publication contention must recover without weakening ownership."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import threading

import pytest

from agent_worktrees import (
    __main__ as cli, claim_history, config as cfg, finalize_open_pr_gate,
    pr_authority, pr_config, pr_ops, pr_state_cli, providers, publication_deadline,
    tracking,
)
from agent_worktrees.providers.base import PullResult

pytestmark = pytest.mark.contract("agent_worktrees.pr_publish.authority_contention")


@pytest.fixture
def observation(tmp_path, monkeypatch):
    ledger = tmp_path / "worktrees"
    ledger.mkdir()
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(cfg, "tracking_dir", lambda *a: ledger)
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor=str(tmp_path), worktree_root=str(tmp_path),
            pr=cfg.PRConfig(enabled=True, provider="github", push_timeout_seconds=7),
        )},
    )
    rec = tracking.create_new_record(
        "example", "worktree/example", str(tmp_path), "ext", "test", "linux", ledger,
    )
    rec.prs = [tracking.PRRecord(
        pr_id="example-pr", number=42, repo="org/repo", provider="github",
        state="open", branch="pr/example", head_sha="a" * 40,
    )]
    pr_ops._ensure_pr_claim(rec, rec.pr)
    tracking.save_record(rec)
    events = []
    monkeypatch.setattr(claim_history, "record_pr_event", lambda *a, **k: events.append(k))

    class Provider:
        pull = PullResult(state="merged", merged=True)

        def get_pull(self, *args, **kwargs):
            return self.pull

        def head_contained_in_base(self, *args, **kwargs):
            return False

    provider = Provider()
    monkeypatch.setattr(finalize_open_pr_gate, "trusted_target", lambda *a: (provider, "org/repo"))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: None)
    monkeypatch.setattr(pr_ops, "_pull_forward_recommendation", lambda *a, **k: None)
    monkeypatch.setattr(pr_ops, "_live_pr_state", lambda *a, **k: {"live": {"merge_state": "merged"}})
    return config, rec, provider, events


def test_real_authority_contention_waits_without_record_lock_and_recovers(observation, monkeypatch):
    config, rec, _provider, events = observation
    holding = threading.Event()
    waiting = threading.Event()
    original = pr_authority.guard
    budgets = []

    @contextmanager
    def observed_guard(*, timeout=None):
        if timeout:
            budgets.append(timeout)
            waiting.set()
        with original(timeout=timeout):
            yield

    def owner():
        with original():
            holding.set()
            assert waiting.wait(5), "reconciliation never retried outside its record transaction"
            with tracking._RecordLock(rec.yaml_path, blocking=False) as lock:
                assert lock.acquired, "reconciliation waited while holding the record lock"

    monkeypatch.setattr(pr_authority, "guard", observed_guard)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(owner)
        assert holding.wait(5)
        pr_ops._reconcile_active_pr(rec, config)
        future.result(timeout=5)
    fresh = tracking.load_record(rec.yaml_path)
    assert budgets == [publication_deadline.lock_wait(7)]
    assert fresh.pr.state == rec.pr.state == "merged"
    assert fresh.resources[0].state == rec.resources[0].state == "released"
    assert [event["event"] for event in events] == ["released"]
    pr_ops._reconcile_active_pr(rec, config)
    assert len(events) == 1


@pytest.mark.parametrize("reassociate", [False, True])
def test_retry_revalidates_reassociation_and_preserves_newer_stamp(observation, monkeypatch, reassociate):
    config, rec, _provider, events = observation
    original = pr_authority.guard

    @contextmanager
    def reassigned(*, timeout=None):
        if timeout == 0:
            if not getattr(pr_authority._held, "paths", set()):
                raise pr_authority.PublicationAuthorityBusy()
            with original(timeout=0):
                yield
            return
        with original(timeout=timeout):
            fresh = tracking.load_record(rec.yaml_path)
            if reassociate:
                fresh.pr.number = 43
            fresh.title = "new owner observation"
            tracking.save_record(fresh)
            yield

    monkeypatch.setattr(pr_authority, "guard", reassigned)
    pr_ops._reconcile_active_pr(rec, config)
    fresh = tracking.load_record(rec.yaml_path)
    assert fresh.title == "new owner observation"
    if reassociate:
        assert fresh.pr.number == 43 and fresh.pr.state == "open"
        assert fresh.resources[0].state == "active"
        assert events == []
    else:
        assert fresh.pr.number == 42 and fresh.pr.state == "merged"
        assert fresh.resources[0].state == "released"
        assert [event["event"] for event in events] == ["released"]


def test_best_effort_skips_authority_contention_without_waiting(observation, monkeypatch):
    config, rec, _provider, events = observation
    attempts = []

    @contextmanager
    def busy(*, timeout=None):
        attempts.append(timeout)
        raise pr_authority.PublicationAuthorityBusy()
        yield

    monkeypatch.setattr(pr_authority, "guard", busy)
    before = rec.yaml_path.read_bytes()
    pr_ops._reconcile_active_pr(rec, config, best_effort=True)
    assert attempts == [0]
    assert rec.yaml_path.read_bytes() == before
    assert rec.pr.state == "open" and rec.resources[0].state == "active"
    assert events == []


def test_unchanged_observation_needs_no_authority_wait(observation, monkeypatch):
    config, rec, provider, events = observation
    provider.pull = PullResult(state="open", head_sha=rec.pr.head_sha)

    @contextmanager
    def forbidden(**kwargs):
        pytest.fail("unchanged status attempted publication authority")
        yield

    monkeypatch.setattr(pr_authority, "guard", forbidden)
    pr_ops._reconcile_active_pr(rec, config)
    assert rec.pr.state == "open"
    assert events == []


@pytest.mark.parametrize("json_mode", [False, True])
def test_persistent_contention_has_actionable_cli_result(observation, monkeypatch, capfd, json_mode):
    config, rec, _provider, events = observation
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    flow = pr_config._pr_flow_profile(config.default_repo)
    monkeypatch.setattr(pr_config, "resolve_actor_pr_flow", lambda *a: pr_config.ActorPRFlow(
        pr_config=config.default_repo.pr, configured_flow=flow, flow=flow,
    ))
    attempts = []

    @contextmanager
    def busy(*, timeout=None):
        attempts.append(timeout)
        raise pr_authority.PublicationAuthorityBusy()
        yield

    monkeypatch.setattr(pr_authority, "guard", busy)
    before = rec.yaml_path.read_bytes()
    args = cli.build_parser().parse_args(["pr-status", rec.worktree_id] + (["--json"] if json_mode else []))
    assert pr_state_cli.cmd_pr_status(args) == 1
    out, err = capfd.readouterr()
    assert "Traceback" not in out + err
    assert attempts == [0, publication_deadline.lock_wait(7)]
    assert rec.yaml_path.read_bytes() == before
    assert events == []
    if json_mode:
        result = json.loads(out)
        assert result["has_pr"] and result["state"] == "open"
        assert result["live"]["merge_state"] == "merged"
        assert result["error_code"] == "pr_authority_busy"
        assert result["retryable"] and result["reconciliation_pending"]
        assert result["retry_after_seconds"] == 2
        assert "pr-status --json" in result["next_action"]
        assert "reminder" not in result
    else:
        assert "installation-wide publication ownership lock" in out + err
        assert "without asking the operator" in out + err
        assert "never delete the lock" in out + err


def test_unrelated_record_timeout_is_not_classified_as_publication_contention(observation, monkeypatch):
    config, rec, _provider, _events = observation

    def failure(*a, **k):
        raise TimeoutError("synthetic unrelated record failure")

    monkeypatch.setattr(pr_ops, "_persist_pr_observation", failure)
    with pytest.raises(TimeoutError, match="unrelated record failure"):
        pr_ops.pr_status(rec.worktree_id, config=config)


def test_no_live_status_does_not_attempt_authority(observation, monkeypatch):
    config, rec, _provider, _events = observation

    @contextmanager
    def forbidden(**kwargs):
        pytest.fail("metadata-only status attempted publication authority")
        yield

    monkeypatch.setattr(pr_authority, "guard", forbidden)
    result = pr_ops.pr_status(rec.worktree_id, config=config, live=False)
    assert result["state"] == "open"
    assert "error" not in result and "live" not in result


def test_authority_timeout_is_typed_with_specific_recovery(observation, monkeypatch):
    from agent_worktrees import pr_publish

    def busy(*a, **k):
        raise pr_publish.PublishLockTimeout("generic shared lock message")

    monkeypatch.setattr(pr_publish, "_lock_publish_file", busy)
    with pytest.raises(pr_authority.PublicationAuthorityBusy) as raised:
        with pr_authority.guard(timeout=0.01):
            pytest.fail("entered occupied authority")
    result = raised.value.status_result()
    assert result["error_code"] == "pr_authority_busy" and result["retryable"]
    assert "installation-wide" in str(raised.value)
    assert "generic shared lock message" not in str(raised.value)


def test_fenced_finalize_skips_busy_observation_and_preserves_obligation(observation, monkeypatch):
    from agent_worktrees import claim_handoffs, finalize

    config, rec, _provider, events = observation
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    monkeypatch.setattr(claim_handoffs, "active_bundle_ids_for_source", lambda *a: [])
    attempts = []

    @contextmanager
    def busy(*, timeout=None):
        assert tracking._thread_sidecar_counts(), "finalization lost its outer record fence"
        assert timeout == 0, "fenced finalization must never wait for publication authority"
        attempts.append(timeout)
        raise pr_authority.PublicationAuthorityBusy()
        yield

    monkeypatch.setattr(pr_authority, "guard", busy)
    assert finalize.validate_and_finalize(rec.worktree_id, config) is False
    assert attempts == [0]
    fresh = tracking.load_record(rec.yaml_path)
    assert fresh.pr.state == "open" and fresh.resources[0].state == "active"
    assert events == []


def test_foreground_reconcile_never_waits_with_an_outer_record_lock(observation, monkeypatch):
    config, rec, _provider, events = observation
    attempts = []

    @contextmanager
    def busy(*, timeout=None):
        attempts.append(timeout)
        raise pr_authority.PublicationAuthorityBusy()
        yield

    monkeypatch.setattr(pr_authority, "guard", busy)
    with tracking._RecordLock(rec.yaml_path, require_sidecar=True):
        with pytest.raises(pr_authority.PublicationAuthorityBusy):
            pr_ops._reconcile_active_pr(rec, config)
    assert attempts == [0]
    assert tracking.load_record(rec.yaml_path).pr.state == "open"
    assert events == []
