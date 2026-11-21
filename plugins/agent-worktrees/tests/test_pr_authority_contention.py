"""Routine publication contention must recover without weakening ownership."""

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import threading
from typing import Any

import pytest
from machine_transport import detect_platform

from agent_worktrees import (
    __main__ as cli, claim_history, config as cfg, finalize_open_pr_gate,
    pr_authority, pr_config, pr_ops, pr_state_cli, providers, publication_deadline,
    tracking,
)
from agent_worktrees.providers.base import PullResult
from agent_worktrees.tracking_lifecycle import create_new_record

pytestmark = pytest.mark.contract("agent_worktrees.pr_publish.authority_contention")


class _Provider:
    def __init__(self) -> None:
        self.pull = PullResult(state="merged", merged=True)

    def get_pull(self, *args: Any, **kwargs: Any) -> PullResult:
        return self.pull

    def head_contained_in_base(self, *args: Any, **kwargs: Any) -> bool:
        return False


Observation = tuple[cfg.Config, tracking.WorktreeRecord, _Provider, list[dict[str, Any]]]


def _selected(record: tracking.WorktreeRecord) -> tracking.PRRecord:
    pr = record.pr
    assert pr is not None
    return pr


@pytest.fixture
def observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Observation:
    ledger = tmp_path / "worktrees"
    ledger.mkdir()
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path)
    monkeypatch.setattr(cfg, "tracking_dir", lambda *a: ledger)
    worktree = tmp_path / "example"
    worktree.mkdir()
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform=detect_platform(), repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor=str(tmp_path), worktree_root=str(tmp_path),
            pr=cfg.PRConfig(enabled=True, provider="github", push_timeout_seconds=7),
        )},
    )
    rec = create_new_record(
        "example", "worktree/example", str(worktree), "ext", "test", detect_platform(), ledger,
    )
    rec.prs = [tracking.PRRecord(
        pr_id="example-pr", number=42, repo="org/repo", provider="github",
        state="open", branch="pr/example", head_sha="a" * 40,
    )]
    pr_ops._ensure_pr_claim(rec, _selected(rec))
    tracking.save_record(rec)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(claim_history, "record_pr_event", lambda *a, **k: events.append(k))

    provider = _Provider()
    monkeypatch.setattr(finalize_open_pr_gate, "trusted_target", lambda *a: (provider, "org/repo"))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: None)
    monkeypatch.setattr(pr_ops, "_pull_forward_recommendation", lambda *a, **k: None)
    monkeypatch.setattr(pr_ops, "_live_pr_state", lambda *a, **k: {"live": {"merge_state": "merged"}})
    return config, rec, provider, events


def test_real_authority_contention_waits_without_record_lock_and_recovers(
    observation: Observation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, rec, _provider, events = observation
    holding = threading.Event()
    waiting = threading.Event()
    original = pr_authority.guard
    budgets: list[float] = []

    @contextmanager
    def observed_guard(*, timeout: float | None = None) -> Iterator[None]:
        if timeout:
            budgets.append(timeout)
            waiting.set()
        with original(timeout=timeout):
            yield

    def owner() -> None:
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
    assert _selected(fresh).state == _selected(rec).state == "merged"
    assert fresh.resources[0].state == rec.resources[0].state == "released"
    assert [event["event"] for event in events] == ["released"]
    pr_ops._reconcile_active_pr(rec, config)
    assert len(events) == 1


@pytest.mark.parametrize("reassociate", [False, True])
def test_retry_revalidates_reassociation_and_preserves_newer_stamp(
    observation: Observation, monkeypatch: pytest.MonkeyPatch, reassociate: bool,
) -> None:
    config, rec, _provider, events = observation
    original = pr_authority.guard

    @contextmanager
    def reassigned(*, timeout: float | None = None) -> Iterator[None]:
        if timeout == 0:
            if not getattr(pr_authority._held, "paths", set()):
                raise pr_authority.PublicationAuthorityBusy()
            with original(timeout=0):
                yield
            return
        with original(timeout=timeout):
            fresh = tracking.load_record(rec.yaml_path)
            if reassociate:
                _selected(fresh).number = 43
            fresh.title = "new owner observation"
            tracking.save_record(fresh)
            yield

    monkeypatch.setattr(pr_authority, "guard", reassigned)
    pr_ops._reconcile_active_pr(rec, config)
    fresh = tracking.load_record(rec.yaml_path)
    assert fresh.title == "new owner observation"
    if reassociate:
        assert _selected(fresh).number == 43 and _selected(fresh).state == "open"
        assert fresh.resources[0].state == "active"
        assert events == []
    else:
        assert _selected(fresh).number == 42 and _selected(fresh).state == "merged"
        assert fresh.resources[0].state == "released"
        assert [event["event"] for event in events] == ["released"]


def test_best_effort_skips_authority_contention_without_waiting(
    observation: Observation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, rec, _provider, events = observation
    attempts: list[float | None] = []

    @contextmanager
    def busy(*, timeout: float | None = None) -> Iterator[None]:
        attempts.append(timeout)
        raise pr_authority.PublicationAuthorityBusy()
        yield

    monkeypatch.setattr(pr_authority, "guard", busy)
    before = rec.yaml_path.read_bytes()
    pr_ops._reconcile_active_pr(rec, config, best_effort=True)
    assert attempts == [0]
    assert rec.yaml_path.read_bytes() == before
    assert _selected(rec).state == "open" and rec.resources[0].state == "active"
    assert events == []


def test_unchanged_observation_needs_no_authority_wait(
    observation: Observation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, rec, provider, events = observation
    provider.pull = PullResult(state="open", head_sha=_selected(rec).head_sha)

    @contextmanager
    def forbidden(**kwargs: Any) -> Iterator[None]:
        pytest.fail("unchanged status attempted publication authority")
        yield

    monkeypatch.setattr(pr_authority, "guard", forbidden)
    pr_ops._reconcile_active_pr(rec, config)
    assert _selected(rec).state == "open"
    assert events == []


@pytest.mark.parametrize("json_mode", [False, True])
def test_persistent_contention_has_actionable_cli_result(
    observation: Observation, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], json_mode: bool,
) -> None:
    config, rec, _provider, events = observation
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    flow = pr_config._pr_flow_profile(config.default_repo)
    monkeypatch.setattr(pr_config, "resolve_actor_pr_flow", lambda *a: pr_config.ActorPRFlow(
        pr_config=config.default_repo.pr, configured_flow=flow, flow=flow,
    ))
    attempts: list[float | None] = []

    @contextmanager
    def busy(*, timeout: float | None = None) -> Iterator[None]:
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


def test_unrelated_record_timeout_is_not_classified_as_publication_contention(
    observation: Observation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, rec, _provider, _events = observation

    def failure(*a: Any, **k: Any) -> None:
        raise TimeoutError("synthetic unrelated record failure")

    monkeypatch.setattr(pr_ops, "_persist_pr_observation", failure)
    with pytest.raises(TimeoutError, match="unrelated record failure"):
        pr_ops.pr_status(rec.worktree_id, config=config)


def test_no_live_status_does_not_attempt_authority(
    observation: Observation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, rec, _provider, _events = observation

    @contextmanager
    def forbidden(**kwargs: Any) -> Iterator[None]:
        pytest.fail("metadata-only status attempted publication authority")
        yield

    monkeypatch.setattr(pr_authority, "guard", forbidden)
    result = pr_ops.pr_status(rec.worktree_id, config=config, live=False)
    assert result["state"] == "open"
    assert "error" not in result and "live" not in result


def test_authority_timeout_is_typed_with_specific_recovery(
    observation: Observation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import pr_publish

    def busy(*a: Any, **k: Any) -> None:
        raise pr_publish.PublishLockTimeout("generic shared lock message")

    monkeypatch.setattr(pr_publish, "_lock_publish_file", busy)
    with pytest.raises(pr_authority.PublicationAuthorityBusy) as raised:
        with pr_authority.guard(timeout=0.01):
            pytest.fail("entered occupied authority")
    result = raised.value.status_result()
    assert result["error_code"] == "pr_authority_busy" and result["retryable"]
    assert "installation-wide" in str(raised.value)
    assert "generic shared lock message" not in str(raised.value)


def test_fenced_finalize_skips_busy_observation_and_preserves_obligation(
    observation: Observation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import claim_handoffs, finalize

    config, rec, _provider, events = observation
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    monkeypatch.setattr(claim_handoffs, "active_bundle_ids_for_source", lambda *a: [])
    attempts: list[float | None] = []

    @contextmanager
    def busy(*, timeout: float | None = None) -> Iterator[None]:
        assert tracking._thread_sidecar_counts(), "finalization lost its outer record fence"
        assert timeout == 0, "fenced finalization must never wait for publication authority"
        attempts.append(timeout)
        raise pr_authority.PublicationAuthorityBusy()
        yield

    monkeypatch.setattr(pr_authority, "guard", busy)
    assert finalize.validate_and_finalize(rec.worktree_id, config) is False
    assert attempts == [0]
    fresh = tracking.load_record(rec.yaml_path)
    assert _selected(fresh).state == "open" and fresh.resources[0].state == "active"
    assert events == []


def test_foreground_reconcile_never_waits_with_an_outer_record_lock(
    observation: Observation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, rec, _provider, events = observation
    attempts: list[float | None] = []

    @contextmanager
    def busy(*, timeout: float | None = None) -> Iterator[None]:
        attempts.append(timeout)
        raise pr_authority.PublicationAuthorityBusy()
        yield

    monkeypatch.setattr(pr_authority, "guard", busy)
    with tracking._RecordLock(rec.yaml_path, require_sidecar=True):
        with pytest.raises(pr_authority.PublicationAuthorityBusy):
            pr_ops._reconcile_active_pr(rec, config)
    assert attempts == [0]
    assert _selected(tracking.load_record(rec.yaml_path)).state == "open"
    assert events == []
