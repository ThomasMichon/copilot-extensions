"""Injected Windows custody and unchanged ordinary POSIX routing."""

from __future__ import annotations

import subprocess
import argparse
import platform
import copy
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, Protocol, TypeVar
from collections.abc import Callable

import pytest

from agent_worktrees import __main__ as core, git_ops as go, push_timeout as pt
from agent_worktrees import config as cfg, tracking, sessions, list_cache, worktree_git_facts
from agent_worktrees import status_updater_cli as updater_api
from agent_worktrees.status_segment_cache import _StatusSegmentCache
from agent_worktrees.worktree_status_cache import WorktreeStatusCache
from agent_worktrees.worktrees_runtime import WorktreesRuntime
from work_coalescing_singleton import client as wcs_client
from zdd import lifecycle

_T = TypeVar("_T")


class _LeaseCall(Protocol):
    def __call__(
        self, session: str, compute: Callable[[], _T], *,
        still_needed: Callable[[], bool] | None = None,
    ) -> _T | int: ...


class _UpdaterLoop(Protocol):
    def __call__(
        self, path: str, interval: float, session_state: Callable[[], str],
        owns: Callable[[], bool], runtime_superseded: Callable[[], bool],
        set_option: Callable[[str, str], object],
    ) -> int: ...


with_updater_lease: _LeaseCall = updater_api._with_updater_lease
run_updater_loop: _UpdaterLoop = updater_api._run_status_updater_loop


class UnusedPolicy(core._ResidentHookPolicy):
    def __init__(self) -> None:
        pass

    def ready(self) -> NoReturn:
        pytest.fail("pending snapshot inspected hook policy")


class PublicationRuntime(WorktreesRuntime):
    def __init__(self, failed: Callable[[str, Exception], None]) -> None:
        self.failed = failed

    def publication_failed(self, project: str, error: Exception) -> None:
        self.failed(project, error)


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _StatusSegmentCache:
    from agent_worktrees import status_monitor_cutover

    monkeypatch.setattr(go, "_windows_observation", lambda: True, raising=False)
    monkeypatch.setattr(cfg, "project_name", lambda: "A")
    monkeypatch.setattr(status_monitor_cutover, "routing_dir", lambda: tmp_path / "routing")
    return _StatusSegmentCache(15)


def fail(error: BaseException) -> NoReturn:
    raise error


def pending(
    owner: _StatusSegmentCache, path: str = "/repo/wt",
    error: pt.ObservationCleanupError | None = None,
) -> pt.ObservationCleanupError:
    error = error or pt.ObservationCleanupError("pending")
    if not hasattr(error, "try_settle"):
        setattr(error, "try_settle", lambda: False)
    with pytest.raises(pt.ObservationCleanupError):
        owner.run_observation("A", "/repo", path, lambda: fail(error))
    return error


def test_carrier_gates_aliases_but_independent_anchor_progresses(owner: _StatusSegmentCache) -> None:
    error = pending(owner)
    assert owner.observation_counts() == (0, 1)
    with pytest.raises(pt.ObservationCleanupPending):
        owner.run_observation("alias", "/repo", "/repo/wt/subdir", lambda: pytest.fail("alias probed"))
    assert owner.run_observation("B", "/independent", "/independent/wt", lambda: "B") == "B"
    assert owner.observation_counts() == (0, 1)
    assert next(iter(owner._observations.values())).cleanup is error


def test_active_observation_is_not_reported_as_unsettled_cleanup(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = owner.begin_observation("A", "/repo", "/repo/wt")
    monkeypatch.setattr(core, "_find_record_for_path", lambda *a: pytest.fail("busy lookup"))
    assert owner.get("/repo/wt") == "UNKNOWN (Git observation busy)"
    assert owner.observation_counts() == (1, 0)
    owner.retain_cleanup(ticket, pt.ObservationCleanupError("retained"))
    assert owner.get("/repo/wt") == owner.pending_segment()
    assert owner.observation_counts() == (0, 1)


@pytest.mark.parametrize("entry", ["segment", "snapshot"])
def test_active_observation_keeps_fresh_completed_segment(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, entry: str,
) -> None:
    path = "/repo/wt"
    key = owner._key(path)
    original = (time.monotonic(), "last completed status")
    owner._entries[key] = original
    owner._entry_projects[key] = "A"
    owner._aliases[owner._key(path + "/nested")] = key
    ticket = owner.begin_observation("A", "/repo", "/repo/other")
    monkeypatch.setattr(core, "_find_record_for_path", lambda *a: pytest.fail("new Git lookup"))
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: pytest.fail("activation"))
    if entry == "segment":
        result = owner.get(path + "/nested")
    else:
        result = core._resident_hook_decision(
            "snapshot", {"cwd": path + "/nested"}, segment_cache=owner, policy=UnusedPolicy(),
        )["segment"]
    assert result == original[1]
    assert owner._entries[key] == original
    assert next(iter(owner._observations.values())) is ticket
    assert owner.observation_counts() == (1, 0)


@pytest.mark.parametrize("condition", ["expired", "invalidated", "cleanup", "retiring"])
def test_completed_segment_does_not_override_observation_fences(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    path = "/repo/wt"
    key = owner._key(path)
    observed = time.monotonic() - (owner.ttl + 1 if condition == "expired" else 0)
    owner._entries[key] = original = (observed, "last completed status")
    owner._entry_projects[key] = "A"
    ticket = owner.begin_observation("A", "/repo", "/repo/other")
    if condition == "invalidated":
        owner.invalidate(path)
    elif condition == "cleanup":
        owner.retain_cleanup(ticket, pt.ObservationCleanupError("retained"))
    elif condition == "retiring":
        owner.close_observation_admission()
    monkeypatch.setattr(core, "_find_record_for_path", lambda *a: pytest.fail("new Git lookup"))
    assert owner.get(path).startswith("UNKNOWN (")
    assert owner._entries[key] == original
    assert next(iter(owner._observations.values())) is ticket


def test_status_reason_follows_only_the_matching_observation_scope(owner: _StatusSegmentCache) -> None:
    ticket = owner.begin_observation("A", "/repo", "/repo/wt")
    other = owner.begin_observation("B", "/other", "/other/wt")
    owner.retain_cleanup(other, pt.ObservationCleanupError("unrelated"))
    assert owner.pending_segment_for_path("/repo/wt") == "UNKNOWN (Git observation busy)"
    assert owner.pending_segment_for_path("/other/wt") == owner.pending_segment()
    assert owner.pending_segment_for_path("/unknown/wt") == "UNKNOWN (Git observation unavailable)"
    owner.finish_observation(ticket)
    owner.close_observation_admission()
    assert owner.pending_segment_for_path("/repo/wt") == "UNKNOWN (observation retiring)"
    assert owner.observation_counts() == (0, 1)


@pytest.mark.parametrize("condition", [
    "expired", "invalidated", "busy", "project", "worktree", "path", "creation", "cleanup", "retiring",
])
def test_advisory_segment_preserves_subject_and_hard_observation_fences(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    from agent_worktrees import status_segment_cache

    path = "/repo/wt"
    monkeypatch.setattr(cfg, "active_project", lambda: "A")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor="/repo"),
    ))
    monkeypatch.setattr(core, "_find_record_for_path", lambda *a: SimpleNamespace(
        worktree_id="one", worktree_path=path, creation_nonce="first",
    ))
    monkeypatch.setattr(core, "_render_status_segment", lambda *a, **k: "DIRTY")
    assert owner.get(path) == "DIRTY"
    clock = time.monotonic() + owner.ttl + 1
    monkeypatch.setattr(status_segment_cache, "time", SimpleNamespace(monotonic=lambda: clock))
    if condition == "invalidated":
        owner.invalidate(path)
    elif condition == "busy":
        owner.begin_observation("A", "/repo", path)
    elif condition == "cleanup":
        pending(owner, path)
    elif condition == "retiring":
        owner.close_observation_admission()
    assert owner.get_cached(path) is None
    result = owner.get_advisory(
        "B" if condition == "project" else "A",
        "/different/wt" if condition == "path" else path,
        "two" if condition == "worktree" else "one",
        "second" if condition == "creation" else "first",
    )
    if condition in {"expired", "invalidated", "busy"}:
        assert result is not None and "DIRTY" in result and "(stale)" in result
    else:
        assert result is None


def test_pending_keeps_last_good_bytes_ages_and_survives_invalidation(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "/repo/wt"
    key = owner._key(path)
    owner._entries[key] = (1.0, "last-good")
    owner._aliases[owner._key(path + "/nested")] = key
    pending(owner, path)
    monkeypatch.setattr(core, "_find_record_for_path", lambda *a: pytest.fail("pending lookup"))
    owner.invalidate(path)
    owner.invalidate_all()
    assert owner.get(path + "/nested") == owner.pending_segment()
    assert owner._entries[key] == (1.0, "last-good")
    assert owner.observation_counts() == (0, 1)


def test_late_callback_transfers_custody_after_close(owner: _StatusSegmentCache) -> None:
    ticket = owner.begin_observation("A", "/repo", "/repo/wt")
    owner.close_observation_admission()
    error = pt.ObservationCleanupError("late")
    owner.retain_cleanup(ticket, error)
    owner.finish_observation(ticket)
    assert owner.observation_counts() == (0, 1)
    assert next(iter(owner._observations.values())).cleanup is error
    with pytest.raises(pt.ObservationCleanupPending):
        owner.begin_observation("B", "/B", "/B/wt")


def test_one_settlement_step_does_not_renew_D_or_start_work(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequence = iter([False, True])
    error = pt.ObservationCleanupError("retained")
    setattr(error, 'try_settle', lambda: next(sequence))
    pending(owner, error=error)
    monkeypatch.setattr(time, "monotonic", lambda: pytest.fail("renewed D"))
    monkeypatch.setattr(go, "git", lambda *a, **k: pytest.fail("cleanup Git"))
    assert owner.settle_pending_once() == 1
    assert owner.observation_counts() == (0, 1)
    assert owner.settle_pending_once() == 0
    assert owner.observation_counts() == (0, 0)


def test_native_empty_is_not_enough_until_root_and_readers_finish() -> None:
    calls = []

    class Job:
        def try_terminate_and_confirm_empty(self) -> bool:
            calls.append("job")
            return True

    class Process:
        stdin = stdout = stderr = None
        completed = False

        def communicate(self, *, timeout: float) -> tuple[str, str]:
            assert timeout == 0
            calls.append("readers")
            if not self.completed:
                self.completed = True
                raise subprocess.TimeoutExpired("reader", 0)
            return "", ""

        def wait(self, *, timeout: float) -> None:
            assert timeout == 0
            calls.append("root")

    error = pt.ObservationCleanupError("retained")
    setattr(error, "job", Job())
    setattr(error, "process", Process())
    assert error.try_settle() is False
    assert error.try_settle() is True
    assert calls == ["job", "readers", "readers", "root"]


def test_ordinary_posix_classification_keeps_baseline_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(go, "_windows_observation", lambda: False, raising=False)
    (tmp_path / ".git").mkdir()
    calls = []

    def git(*args: str, **options: Any) -> SimpleNamespace:
        calls.append((args, options))
        stdout = {"rev-parse": "tracked\n", "status": " M changed\n",
                  "rev-list": "1 0\n", "--no-pager": "subject\n"}[args[0]]
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(go, "git", git)
    info = go.classify_worktree(str(tmp_path), "tracked")
    assert info.state == go.WorktreeState.DIRTY
    assert len(calls) == 4
    assert "timeout" not in calls[0][1]
    assert [options["timeout"] for _, options in calls[1:]] == [15.0] * 3
    assert all("observation_deadline" not in options and "kill_tree" not in options
               for _, options in calls)


def test_explicit_windows_only_request_refuses_posix_before_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(go, "_windows_observation", lambda: False, raising=False)
    monkeypatch.setattr(go, "git", lambda *a, **k: pytest.fail("unsupported probe"))
    (tmp_path / ".git").mkdir()
    with pytest.raises(pt.ObservationUnavailable):
        go.classify_worktree(str(tmp_path), "tracked", observation_deadline=99999999)


def test_snapshot_pending_never_activates_or_renders(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending(owner)
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: pytest.fail("pending activation"))
    result = core._resident_hook_decision(
        "snapshot", {"cwd": "/repo/wt"}, segment_cache=owner, policy=UnusedPolicy(),
    )
    assert result == {"segment": owner.pending_segment()}
    assert owner.observation_counts() == (0, 1)


@pytest.mark.parametrize("field", ["cwd", "workingDirectory"])
def test_closed_snapshot_preserves_matching_cleanup_for_both_cwd_forms(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    pending(owner)
    owner.close_observation_admission()
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *args, **kwargs: pytest.fail(
        "Closed snapshot must not discover project state",
    ))
    result = core._resident_hook_decision(
        "snapshot", {field: "/repo/wt"}, segment_cache=owner, policy=UnusedPolicy(),
    )
    assert result == {"segment": owner.pending_segment()}
    assert owner.observation_counts() == (0, 1)


def test_segment_callback_itself_retains_failed_cleanup(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = pt.ObservationCleanupError("render")
    monkeypatch.setattr(core, "_find_record_for_path", lambda path: None)
    monkeypatch.setattr(core, "_render_status_segment", lambda *a, **k: fail(failure))
    monkeypatch.setattr(cfg, "load_config", lambda **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor="/repo"),
    ))
    assert owner.get("/repo/wt") == owner.pending_segment()
    assert next(iter(owner._observations.values())).cleanup is failure
    assert owner._entries == {}


def test_bundle_raw_callback_rethrows_before_later_facts(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import worktree_status_compute as compute

    failure = pt.ObservationCleanupError("bundle")
    record = SimpleNamespace(worktree_id="wt", worktree_path="/repo/wt", session_turns=0)
    monkeypatch.setattr(cfg, "project_dir", lambda project: Path("unused"))
    monkeypatch.setattr(tracking, "load_record_by_id", lambda *a, **k: record)
    monkeypatch.setattr(cfg, "load_config", lambda **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor="/repo"),
    ))
    monkeypatch.setattr(worktree_git_facts, "compute", lambda *a, **k: fail(failure))
    with pytest.raises(pt.ObservationCleanupError) as error:
        compute.compute("A", "wt", cleanup_owner=owner)
    assert error.value is failure
    assert owner.observation_counts() == (0, 1)


def _cached_bundle(project: str = "A", worktree_id: str = "wt") -> dict[str, Any]:
    return {
        "project": project, "worktree_id": worktree_id, "machine": "test",
        "started_at": 1.0, "as_of": 1.0,
        "facts": {"git_state": {"value": {"state": "wip"}, "confirmed": True, "observed_at": 1.0}},
    }


def _status_cache(owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch) -> WorktreeStatusCache:
    from agent_worktrees.worktree_status_cache import WorktreeStatusCache

    monkeypatch.setattr(WorktreeStatusCache, "_warm_restore", lambda self: None)
    cache = WorktreeStatusCache(
        Path("not-opened.sqlite"), ttl_seconds=1, now=lambda: 10.0,
        cleanup_owner=owner,
    )
    monkeypatch.setattr(cache, "_persist", lambda *a: None)
    monkeypatch.setattr(cache, "_delete_row", lambda *a, **k: None)
    return cache


@pytest.mark.parametrize("condition,reason", [
    ("cleanup", "project cleanup pending"),
    ("busy", "Git observation busy"),
    ("retiring", "Git observation retiring"),
    ("unavailable", "Git observation unavailable"),
])
def test_degraded_bundle_explains_pending_scope_without_changing_cached_facts(
    owner: _StatusSegmentCache, condition: str, reason: str,
) -> None:
    if condition == "cleanup":
        pending(owner)
    elif condition == "busy":
        owner.begin_observation("A", "/repo", "/repo/wt")
    elif condition == "retiring":
        owner.close_observation_admission()
    original = _cached_bundle()
    before = copy.deepcopy(original)
    bundle = owner.degraded_bundle("A", "wt", original)
    assert bundle["availability"] == {"status": "pending", "reason": reason}
    assert bundle["facts"]["git_state"] == {
        **before["facts"]["git_state"], "confirmed": False,
    }
    assert bundle["as_of"] == before["as_of"]
    assert original == before


def test_bundle_request_and_background_preserve_bytes_and_allow_B(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _status_cache(owner, monkeypatch)
    old = _cached_bundle()
    cache._entries[("A", "wt")] = (copy.deepcopy(old), 1.0, 10.0)
    cache._entries[("B", "other")] = (_cached_bundle("B", "other"), 1.0, 10.0)
    pending(owner)
    value = cache.get_or_refresh("A", "wt", force=True, compute=lambda: pytest.fail("pending compute"))
    assert value["facts"]["git_state"]["confirmed"] is False
    assert value["as_of"] == 1.0
    assert cache._entries[("A", "wt")] == (old, 1.0, 10.0)
    calls = []

    def refresh(project: str, worktree_id: str) -> dict[str, Any]:
        assert project == "B"
        calls.append(project)
        return _cached_bundle(project, worktree_id)

    assert cache.sweep_due(refresh=refresh) == 1
    assert calls == ["B"]
    cache._demand_ttl = 0
    cache._entries[("A", "wt")] = (old, 1.0, 1.0)
    cache.sweep_due(refresh=lambda *a: pytest.fail("pending expiry refresh"))
    assert cache._entries[("A", "wt")] == (old, 1.0, 1.0)
    assert owner.observation_counts() == (0, 1)


@pytest.mark.parametrize("active,probes_ok", [(False, True), (True, True), (False, False)])
def test_forced_resident_liveness_progresses_without_releasing_git_custody(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, active: bool, probes_ok: bool,
) -> None:
    from agent_worktrees import worktree_status_daemon, sessions

    cache = _status_cache(owner, monkeypatch)
    pending(owner)
    record = SimpleNamespace(worktree_id="wt")
    monkeypatch.setattr(cfg, "project_dir", lambda project: Path("unused"))
    monkeypatch.setattr(
        tracking, "load_record_by_id",
        lambda worktree_id, **kwargs: record,
    )
    monkeypatch.setattr(
        sessions, "verify_worktree_active",
        lambda current: sessions.LiveVerdict(active=active, probes_ok=probes_ok),
    )
    compute = worktree_status_daemon.build_cached_compute(
        cache, lambda *args: pytest.fail("Retained Git cleanup must not be bypassed"),
    )
    result = compute("worktree_status", {"project": "A", "worktree_id": "wt", "force": True})
    assert result["facts"]["liveness"]["confirmed"] is probes_ok
    if probes_ok:
        assert result["facts"]["liveness"]["value"]["active"] is active
    else:
        assert result["facts"]["liveness"]["value"] is None
    assert result["facts"]["git_state"]["confirmed"] is False
    assert result["availability"] == {"status": "pending", "reason": "project cleanup pending"}
    assert owner.observation_counts() == (0, 1)
    assert cache._entries == {}


def test_classify_pending_returns_valid_unknown_without_probes(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending(owner)
    monkeypatch.setattr(tracking, "list_records", lambda *a, **k: [
        SimpleNamespace(worktree_id="wt", worktree_path="/repo/wt"),
    ])
    monkeypatch.setattr(cfg, "project_dir", lambda project: Path("unused"))
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda *a: pytest.fail("pending PID scan"))
    result = core._classify_daemon_compute(
        "classify", {"project": "A", "all": True}, cleanup_owner=owner,
    )
    assert result["wt"]["state"] == "unknown"


def test_warm_roster_gate_retains_publication_and_never_gathers(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import worktrees_producer

    pending(owner)
    errors = []
    runtime = PublicationRuntime(lambda project, error: errors.append((project, error)))
    monkeypatch.setattr(worktrees_producer, "_publish_active_project",
                        lambda *a: pytest.fail("pending roster gather"))
    with pytest.raises(pt.ObservationCleanupPending):
        worktrees_producer.publish_active_project(runtime, cleanup_owner=owner)
    assert errors[0][0] == "A"
    assert owner.observation_counts() == (0, 1)


def test_fallback_warm_list_never_renews_pending_cache(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import list_cli

    pending(owner)
    monkeypatch.setattr(list_cache, "recent_demands", lambda *a: pytest.fail("pending gather"))
    assert list_cli._warm_list_cache_for_active_project(cleanup_owner=owner) == 0


def test_status_client_pending_data_never_enters_direct_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import worktree_status_daemon as daemon

    data = _StatusSegmentCache().degraded_bundle("A", "wt", _cached_bundle())
    monkeypatch.setattr(wcs_client, "new_client_id", lambda: "client")
    monkeypatch.setattr(wcs_client, "request", lambda *a, **k: data)
    monkeypatch.setattr(wcs_client, "release", lambda *a, **k: None)
    result = daemon.status_via_daemon(
        {"worktree_status_endpoint": "127.0.0.1:1234", "worktree_status_token": "test"},
        key="A:wt", payload={"project": "A", "worktree_id": "wt"},
        fallback=lambda: pytest.fail("known pending direct fallback"),
    )
    assert result["facts"]["git_state"]["confirmed"] is False


def test_atomic_scope_race_and_independent_scope_progress(owner: _StatusSegmentCache) -> None:
    entered, release = threading.Event(), threading.Event()
    errors = []
    failure = pt.ObservationCleanupError("concurrent")

    def compute() -> NoReturn:
        entered.set()
        assert release.wait(15), "test-only barrier headroom exhausted"
        raise failure

    def worker() -> None:
        try:
            owner.run_observation("A", "/repo", "/repo/wt", compute)
        except pt.ObservationCleanupError as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(15), "test-only barrier headroom exhausted"
        with pytest.raises(pt.ObservationCleanupPending):
            owner.begin_observation("alias", "/repo", "/repo/wt")
        assert owner.run_observation("B", "/B", "/B/wt", lambda: "B") == "B"
    finally:
        release.set()
        thread.join(15)
    assert not thread.is_alive()
    assert errors == [failure] and owner.observation_counts() == (0, 1)


@pytest.mark.parametrize("retire", ["gone", "token", "superseded", "cancel"])
def test_updater_retains_native_custody_and_lease_through_retirement(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, retire: str,
) -> None:
    import single_instance_lease
    from agent_worktrees import status_updater_cli as updater, status_monitor_runtime

    events = []
    settled = False
    rendered = 0
    attempts = 0

    class Lease:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def acquire(self) -> None:
            events.append("lease")

        def release(self) -> None:
            assert settled
            events.append("released")

    error = pt.ObservationCleanupError("updater")
    if retire == "cancel":
        setattr(error, 'operation_error', KeyboardInterrupt())

    def step() -> bool:
        nonlocal attempts, settled
        attempts += 1
        events.append("settle")
        settled = attempts == 3
        return settled

    def render(*args: Any, **kwargs: Any) -> NoReturn:
        nonlocal rendered
        rendered += 1
        raise error

    def state() -> str:
        assert rendered == 0 or settled, "liveness probe while cleanup pending"
        return "gone" if settled and retire == "gone" else "alive"

    def owns() -> bool:
        assert rendered == 0 or settled, "ownership probe while cleanup pending"
        return not (settled and retire == "token")

    def superseded() -> bool:
        assert rendered == 0 or settled, "runtime probe while cleanup pending"
        return settled and retire == "superseded"

    setattr(error, 'try_settle', step)
    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: Path("unused"))
    monkeypatch.setattr(updater, "_render_status_segment", render)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    def run() -> int:
        return run_updater_loop(
            "/repo/wt", 15, state, owns, superseded,
            lambda key, value: events.append(value),
        )

    if retire == "cancel":
        with pytest.raises(KeyboardInterrupt):
            with_updater_lease("session", run)
    else:
        assert with_updater_lease("session", run) == 0
    assert rendered == 1 and attempts == 3
    assert events[0] == "lease" and events[-1] == "released"


def test_ordinary_posix_updater_does_not_acquire_new_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    import single_instance_lease

    monkeypatch.setattr(go, "_windows_observation", lambda: False)
    monkeypatch.setattr(single_instance_lease, "SingleInstance", lambda *a, **k: pytest.fail("new POSIX lease"))
    assert with_updater_lease("session", lambda: 37) == 37


@pytest.mark.parametrize("hint", ["known", "removed", "unknown"])
def test_served_snapshot_and_warm_roster_share_one_carrier(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, hint: str,
) -> None:
    from agent_worktrees import config_cache, worktrees_producer

    pending(owner)
    current = ["A"]
    rendered, published, stale = [], [], []
    paths = {"sA": "/repo/wt", "sB": "/B/wt"}
    monkeypatch.setattr(cfg, "project_name", lambda: current[0])
    monkeypatch.setattr(cfg, "active_project", lambda: current[0])
    inside = [False]

    class ProjectLock:
        def __enter__(self) -> None:
            inside[0] = True

        def __exit__(self, *args: object) -> None:
            inside[0] = False

    def select(project: str) -> None:
        assert inside[0], "project effect moved outside existing lock"
        current[0] = project

    monkeypatch.setattr(cfg, "set_active_project", select)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor="/repo" if current[0] == "A" else "/B"),
    ))
    monkeypatch.setattr(cfg, "project_dir", lambda project: Path("unused"))
    monkeypatch.setattr(config_cache, "project_scope", lambda *a, **k: nullcontext())
    monkeypatch.setattr(core, "_monitor_registry_dir", lambda: Path("unused"), raising=False)
    monkeypatch.setattr(core, "_read_monitor_registry", lambda *a: paths.copy(), raising=False)
    monkeypatch.setattr(core, "_monitor_managed_session_union", lambda *a: ({}, set(), list(paths.items())), raising=False)
    monkeypatch.setattr(core, "_manager_owned_mapping_for_session", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(core, "_publish_managed_session_status", lambda **k: None, raising=False)
    monkeypatch.setattr(core, "_monitor_mux_set", lambda *a: True, raising=False)
    monkeypatch.setattr(core, "_find_record_for_path", lambda path: SimpleNamespace(worktree_path=path))

    def render(path: str, **kwargs: Any) -> str:
        assert inside[0] and kwargs["persist_title"]
        assert path == "/B/wt"
        rendered.append(path)
        return "WIP B"

    def gather(
        runtime: object, project: str, *, observe_records: object = None,
        managed_mux_cache: object = None, on_progress: object = None, content_paths: object = None,
    ) -> int:
        assert inside[0] and project == "B"
        assert managed_mux_cache is None and on_progress is None and content_paths is None
        published.append(project)
        return 1

    monkeypatch.setattr(core, "_render_status_segment", render)
    monkeypatch.setattr(worktrees_producer, "_publish_active_project", gather)
    runtime = PublicationRuntime(lambda project, error: stale.append(project))
    sessions = {owner._key(path): project for project, path in zip(("A", "B"), paths.values())}
    if hint == "removed":
        sessions.pop(owner._key("/repo/wt"))
        owner.invalidate("/repo/wt")
    elif hint == "unknown":
        paths["sC"] = "/unknown/wt"
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: pytest.fail(
        "project discovery before unresolved custody gate",
    ))
    assert core._monitor_sweep(
        None, "token", "prefix", set(paths),
        segment_cache=owner, session_projects=sessions, project_lock=ProjectLock(),
        roster_publisher=lambda: worktrees_producer.publish_active_project(runtime, cleanup_owner=owner),
    ) == len(paths)
    assert rendered == ["/B/wt"] and published == ["B"] and stale == ["A"]
    assert owner.observation_counts() == (0, 1)


@pytest.mark.parametrize("ignore_custody", [False, True])
def test_actual_monitor_shutdown_retains_custody_and_negative_control(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, ignore_custody: bool,
    operation: str = "shutdown", recovery_directory: Path | None = None,
    cancellation: BaseException | None = None,
) -> None:
    import shutil
    import single_instance_lease
    from agent_worktrees import (
        status_monitor_cli as monitor, status_monitor_runtime, status_monitor_cutover,
        worktrees_runtime, repository_map, worktree_status_daemon, mux_link,
        handoff_prompt_receipt_control as receipt_control, resident_push,
        registry_paths, loop_governance, session_catalog, pane_reaper, locks, tracking_write,
    )

    settled = [False]
    attempts = []
    error = pt.ObservationCleanupError("shutdown")
    if cancellation is not None:
        setattr(error, 'operation_error', cancellation)

    def step() -> bool:
        attempts.append(1)
        settled[0] = len(attempts) == 3
        return settled[0]

    setattr(error, 'try_settle', step)
    if cancellation is None:
        pending(owner, error=error)
    else:
        worker = threading.Thread(target=lambda: pending(owner, error=error))
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
    if ignore_custody:
        monkeypatch.setattr(owner, "observation_counts", lambda: (0, 0))

    class Lease:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def acquire(self) -> None:
            pass

    class Runtime:
        @classmethod
        def for_monitor(
            cls, wake: threading.Event, admitted: Callable[[], bool], context: object, *,
            interval: float,
        ) -> Runtime:
            return cls()

        server = None
        cache = None
        hook_facts = SimpleNamespace(generation="fixture-hook-facts")
        feed = SimpleNamespace(active_count=lambda: 0, wait_closed=lambda *a: None)
        agendas = SimpleNamespace(feed=SimpleNamespace(
            active_count=lambda: 0, wait_closed=lambda *a: None,
        ))
        agenda_counts = worktrees_runtime.WorktreesRuntime.agenda_counts
        agenda_busy = worktrees_runtime.WorktreesRuntime.agenda_busy

        def observation_counts(self, server: object) -> dict[str, int]:
            return {}

        def observation_busy(self, server: object) -> list[str]:
            return []

        def observation_wait_closed(self, timeout: float) -> None:
            pass

        def note_changed(self, path: str | None) -> None:
            pass

        def native_compute(self, kind: str, payload: dict[str, Any]) -> NoReturn:
            pytest.fail("shutdown fixture must not start native computation")

        def demand_projects(self) -> set[str]:
            return set()

        def has_active_demand(self) -> bool:
            return False

        def stream(self, *args: Any) -> NoReturn:
            pytest.fail("shutdown fixture must not start a stream")

        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def start(self, *a: Any, **k: Any) -> None:
            pass

        def close_admission(self, *a: Any) -> None:
            pass

        def activate(self) -> None:
            pass

        def lock_extra(self) -> dict[str, Any]:
            return {}

        def active_work_count(self) -> int:
            return 0

        def health(self) -> dict[str, Any]:
            return {
                "worktrees": {"status": "unready" if operation == "guarded" else "shutdown"},
                "repositories": {"admitting": False, "refreshing": False},
                "capabilities": [],
            }

        def shutdown(self) -> None:
            pass

        def shutdown_and_wait(self) -> None:
            self.shutdown()

    class Control:
        port = 1234

        def __init__(
            self, handle: Callable[[str, dict[str, Any]], dict[str, Any]], **kwargs: Any,
        ) -> None:
            self.handle = handle

        def start(self) -> None:
            if operation == "guarded":
                import os
                from zdd import routing
                from agent_worktrees import status_monitor_recovery_contract as recovery

                assert recovery_directory is not None
                owner.close_observation_admission()
                health = self.handle("health", {})
                assert recovery.CAPABILITY in health["capabilities"]
                route = routing.publish_active(
                    recovery_directory, bind="127.0.0.1", port=self.port, pid=os.getpid(),
                    process_start_time=locks.process_start_time(os.getpid()), version="fixture",
                )
                assert route.pid is not None and route.process_start_time is not None
                receiver = recovery.ReceiverIdentity(
                    route.pid, route.process_start_time, health["runtime_generation"],
                )
                payload: dict[str, Any] = {
                    "contractVersion": 1, "expectedReceiver": receiver.to_dict(),
                    "expectedRoute": route.to_dict(),
                }
                wrong = copy.deepcopy(payload)
                wrong["expectedReceiver"]["runtimeGeneration"] += "-wrong"
                assert self.handle(recovery.ACTION, wrong)["status"] == "refused"
                assert owner._closing_observations
                result = self.handle(recovery.ACTION, payload)
                assert result["status"] == "undrained" and result["receiver"] == receiver.to_dict()
                assert result["route"] == route.to_dict() and not owner._closing_observations
            if operation == "undrain":
                owner.close_observation_admission()
                assert self.handle("undrain", {}) == {"draining": False}
                assert not owner._closing_observations, "undrain left observation admission closed"
            if operation == "force-race":
                owner._observations.clear()
                enter = threading.Event()
                acquired = threading.Event()
                close = owner.close_observation_admission

                def acquire() -> None:
                    assert enter.wait(15)
                    pending(owner, error=error)
                    acquired.set()

                def close_with_barrier() -> None:
                    enter.set()
                    assert acquired.wait(15)
                    close()

                monkeypatch.setattr(owner, "close_observation_admission", close_with_barrier)
                worker = threading.Thread(target=acquire)
                worker.start()
                try:
                    result = self.handle("drain", {"force": True, "timeout": 0})
                    assert result["drained"] is False, "force accepted custody admitted before close"
                finally:
                    enter.set()
                    worker.join(15)
                assert not worker.is_alive()
            if operation != "cancellation":
                assert self.handle("shutdown", {})["shutdown"]

        def current_read_count(self, *a: Any) -> int:
            return 0

        def close(self) -> None:
            pass

    receipts = SimpleNamespace(
        close_admission=lambda: None, active_count=lambda: 0, wait_idle=lambda: None,
        open_admission=lambda: None,
        health=lambda: {"capabilities": [], "promptReceipt": {}},
    )
    if recovery_directory is not None:
        monkeypatch.setattr("agent_worktrees.runtime_identity.PROCESS_GENERATION", "fixture-runtime")
        monkeypatch.setattr("agent_worktrees.status_monitor_health.PROCESS_GENERATION", "fixture-runtime")
        monkeypatch.setattr(status_monitor_cutover, "routing_dir", lambda: recovery_directory)
    monkeypatch.setattr(shutil, "which", lambda name, *, path=None: None)
    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: Path("unused"))
    monkeypatch.setattr(core, "_monitor_lock_path", lambda: Path("unused.lock"), raising=False)
    monkeypatch.setattr(core, "_aw_runtime_home", lambda: Path("unused"), raising=False)
    monkeypatch.setattr(core, "_runtime_superseded", lambda **k: False, raising=False)
    monkeypatch.setattr(core, "_StatusSegmentCache", lambda ttl: owner)
    monkeypatch.setattr(core, "_load_hook_client_module", lambda: None)
    monkeypatch.setattr(core, "_ResidentHookPolicy", lambda client: SimpleNamespace(ready=lambda: False))
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    monkeypatch.setattr(loop_governance, "LoopGovernance", lambda: SimpleNamespace(
        recheck=lambda *a, **k: pytest.fail("monitor continued after stored cancellation"),
    ) if cancellation is not None else object())
    monkeypatch.setattr(session_catalog, "ResidentSessionReconciler", lambda **k: object())
    monkeypatch.setattr(pane_reaper, "ResidentPaneReconciler", lambda **k: object())
    monkeypatch.setattr(receipt_control, "for_runtime", lambda *a, **k: receipts)
    monkeypatch.setattr(worktrees_runtime, "WorktreesRuntime", Runtime)
    monkeypatch.setattr(repository_map, "RepositoryMapRuntime", Runtime)
    monkeypatch.setattr(worktree_status_daemon, "InProcessRuntime", Runtime)
    monkeypatch.setattr(mux_link, "InProcessRuntime", Runtime)
    monkeypatch.setattr(status_monitor_cutover, "ControlServer", Control)
    monkeypatch.setattr(
        status_monitor_cutover, "active_generation_for_pid",
        lambda pid: 1 if operation in {"undrain", "guarded"} else None,
    )
    monkeypatch.setattr(status_monitor_cutover, "publish_route", lambda *a, **k: None)
    monkeypatch.setattr("agent_worktrees.runtime_monitor_identity.publish_lock", lambda *a, **k: True)
    def clear_route(*args: Any) -> None:
        assert settled[0], "early native custody release"

    monkeypatch.setattr(status_monitor_cutover, "clear_route_if_owner", clear_route)
    monkeypatch.setattr(resident_push, "bind", lambda *a, **k: None)
    monkeypatch.setattr(resident_push, "reset", lambda: None)
    monkeypatch.setattr(locks, "read_lock", lambda *a: None)
    monkeypatch.setattr(locks, "lock_is_live", lambda *a: False)
    monkeypatch.setattr(locks, "write_lock", lambda *a, **k: True)
    monkeypatch.setattr(tracking_write, "has_inflight_write", lambda: False)
    monkeypatch.setattr(tracking_write, "_ensure_verb_modules_loaded", lambda: None)
    monkeypatch.setattr(tracking_write, "start_server", lambda *a: fail(OSError("injected")))
    monkeypatch.setattr(lifecycle, "record", lambda *a, **k: True)
    from agent_worktrees import classify_daemon
    monkeypatch.setattr(classify_daemon, "start_server", lambda *a: fail(OSError("injected")))
    monkeypatch.setattr(monitor, "_wake_interruptible_wait", lambda *a: None)
    monkeypatch.setattr(monitor, "_SELF_RETIRE_MAX_GRACE_S", -1)
    monkeypatch.setattr(go, "git", lambda *a, **k: pytest.fail("shutdown Git"))
    args = argparse.Namespace(interval=15, passive=False, control_port=None)
    if ignore_custody:
        with pytest.raises(AssertionError, match="early native"):
            monitor.cmd_status_monitor(args)
    else:
        if cancellation is not None:
            with pytest.raises(type(cancellation)) as caught:
                monitor.cmd_status_monitor(args)
            assert caught.value is cancellation
        else:
            assert monitor.cmd_status_monitor(args) == 0
        assert len(attempts) == 3 and owner.observation_counts() == (0, 0)


def test_cleanup_after_verified_job_close_retries_only_readers_and_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import owned_process

    calls = []

    class Job:
        def terminate_and_wait(self, timeout: float) -> None:
            calls.append("verified-job-close")

        def try_terminate_and_confirm_empty(self) -> bool:
            pytest.fail("closed handle was used as a new emptiness query")

    class Process:
        stdin = stdout = stderr = None
        returncode = 0
        attempts = 0

        def communicate(self, *, timeout: float) -> tuple[str, str]:
            self.attempts += 1
            if self.attempts <= 2:
                raise subprocess.TimeoutExpired("reader", timeout)
            assert timeout == 0
            calls.append("readers")
            return "", ""

        def wait(self, *, timeout: float) -> None:
            assert timeout == 0
            calls.append("root")

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)
    monkeypatch.setattr(pt, "_spawn_observation", lambda *a, **k: (Process(), Job()))
    with pytest.raises(pt.ObservationCleanupError) as error:
        pt.run_bounded(["git"], cwd="worktree", env={}, timeout=15,
                       observation_deadline=time.monotonic() + 15)
    deadline: float = getattr(error.value, "observation_deadline")
    assert error.value.try_settle() is True
    assert getattr(error.value, "observation_deadline") == deadline
    assert calls == ["verified-job-close", "readers", "root"]


def test_late_additional_custody_during_settlement_is_not_dropped(owner: _StatusSegmentCache) -> None:
    first, second = pt.ObservationCleanupError("first"), pt.ObservationCleanupError("late")
    late_steps = iter([False, True])
    setattr(second, 'try_settle', lambda: next(late_steps))

    def first_step() -> bool:
        owner.retain_unclaimed_cleanup("A", "/repo/wt", second)
        return True

    setattr(first, 'try_settle', first_step)
    pending(owner, error=first)
    owner.close_observation_admission()
    assert owner.settle_pending_once() == 1
    assert owner.settle_pending_once() == 1
    assert owner.settle_pending_once() == 0


def test_classify_pending_protocol_data_never_runs_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import classify_daemon

    data = {"wt": {"state": "unknown", "fetch_requested": False, "fetch_failed": False}}
    monkeypatch.setattr(wcs_client, "request", lambda *a, **k: data)
    result = classify_daemon.classify_via_daemon(
        {"classify_endpoint": "127.0.0.1:1234", "classify_token": "test"},
        key="A", payload={"project": "A"},
        fallback=lambda: pytest.fail("known pending classification fallback"),
    )
    assert result is data


@pytest.mark.parametrize("timeout_network", [False, True])
def test_ordinary_posix_default_resolution_keeps_baseline_options(
    monkeypatch: pytest.MonkeyPatch, timeout_network: bool,
) -> None:
    from agent_worktrees import status_bar_cli

    monkeypatch.setattr(go, "_windows_observation", lambda: False)
    monkeypatch.setattr(status_bar_cli, "_core_helper", lambda name, local: local)
    calls = []

    def git(*args: str, **options: Any) -> SimpleNamespace:
        calls.append((args, options))
        if timeout_network and args[0] == "ls-remote":
            raise subprocess.TimeoutExpired(args, 10)
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(go, "git", git)
    assert status_bar_cli._resolve_remote_default_branch(
        ".", "origin", config_default="main", allow_remote=True,
    ) is None
    assert [args[0] for args, _ in calls] == [
        "rev-parse", "symbolic-ref", "ls-remote", "rev-parse", "rev-parse",
    ]
    assert [options.get("timeout") for _, options in calls] == [None, None, 10, None, None]
    assert all("observation_deadline" not in options and "kill_tree" not in options
               for _, options in calls)


def test_isolated_consumer_import_has_the_actual_shared_settlement_api() -> None:
    import agent_procutil

    assert callable(agent_procutil.JobHandle.try_terminate_and_confirm_empty)


def test_explicit_windows_deadline_refuses_posix_before_detect_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import status_bar_cli

    monkeypatch.setattr(go, "_windows_observation", lambda: False)
    monkeypatch.setattr(status_bar_cli, "_core_helper", lambda *a: pytest.fail(
        "unsupported observation reached override",
    ))
    with pytest.raises(pt.ObservationUnavailable):
        status_bar_cli._detect_upstream_branch(
            ".", "origin", "main", observation_deadline=123,
        )


def test_updater_negative_control_detects_swallowed_custody(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import status_updater_cli as updater

    original = pt.ObservationCleanupError("retained")
    rendered: list[int] = []

    class WrongCatch(subprocess.SubprocessError):
        pass

    def state() -> str:
        assert not rendered, "continued observing after discarded cleanup custody"
        return "alive"

    def render(*args: Any, **kwargs: Any) -> NoReturn:
        rendered.append(1)
        raise original

    monkeypatch.setattr(pt, "ObservationCleanupError", WrongCatch)
    monkeypatch.setattr(updater, "_render_status_segment", render)
    monkeypatch.setattr(time, "sleep", lambda *a: None)
    with pytest.raises(AssertionError, match="discarded cleanup custody"):
        run_updater_loop(
            "/repo/wt", 15, state, lambda: True, lambda: False, lambda *a: None,
        )


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_settlement_preserves_every_ticket(
    owner: _StatusSegmentCache, interruption: type[BaseException],
) -> None:
    first = pt.ObservationCleanupError("A")
    second = pt.ObservationCleanupError("B")
    setattr(first, 'try_settle', lambda: fail(interruption()))
    setattr(second, 'try_settle', lambda: True)
    pending(owner, error=first)
    with pytest.raises(pt.ObservationCleanupError):
        owner.run_observation("B", "/B", "/B/wt", lambda: fail(second))
    with pytest.raises(interruption):
        owner.settle_pending_once()
    assert owner.observation_counts() == (0, 2)
    assert all(not ticket.settling for ticket in owner._observations.values())
    setattr(first, 'try_settle', lambda: True)
    assert owner.settle_pending_once() == 0


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/repo/wt2", "UNKNOWN (cleanup pending)"),
     ("/unresolved/wt", "UNKNOWN (Git observation unavailable)")],
)
def test_snapshot_gates_sibling_and_unknown_path_before_discovery(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, path: str, expected: str,
) -> None:
    pending(owner)
    monkeypatch.setattr(core, "_hook_payload_cwd", lambda payload: path)
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: pytest.fail(
        "snapshot discovered Git before retained project hint",
    ))
    assert core._resident_hook_decision(
        "snapshot", {}, segment_cache=owner, policy=UnusedPolicy(),
    ) == {"segment": expected}
    assert owner.observation_counts() == (0, 1)


def test_blocked_project_cannot_fill_refresh_sample_cap(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _status_cache(owner, monkeypatch)
    pending(owner)
    for index in range(cache._max_refresh_per_sweep):
        key = ("A", f"blocked-{index}")
        cache._entries[key] = (_cached_bundle(*key), 1.0, 10.0)
    cache._entries[("B", "healthy")] = (_cached_bundle("B", "healthy"), 2.0, 10.0)
    calls = []

    def refresh(project: str, worktree_id: str) -> dict[str, Any]:
        calls.append(project)
        return _cached_bundle(project, worktree_id)

    assert cache.sweep_due(refresh=refresh) == 1
    assert calls == ["B"]
    assert owner.observation_counts() == (0, 1)


def test_updater_replacement_waits_for_previous_generation_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import single_instance_lease
    from agent_worktrees import status_monitor_runtime

    calls = []
    released = [False]

    class Lease:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def acquire(self) -> None:
            calls.append("acquire")
            if not released[0]:
                raise single_instance_lease.AlreadyRunningError(Path("predecessor"), 101)

        def release(self) -> None:
            calls.append("replacement-release")

    def wait(seconds: float) -> None:
        calls.append("predecessor-release")
        released[0] = True

    monkeypatch.setattr(go, "_windows_observation", lambda: True)
    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: Path("unused"))
    monkeypatch.setattr(time, "sleep", wait)
    assert with_updater_lease("session", lambda: calls.append("replacement-run")) is None
    assert calls == [
        "acquire", "predecessor-release", "acquire", "replacement-run", "replacement-release",
    ]


def test_actual_monitor_undrain_reopens_without_dropping_pending(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_actual_monitor_shutdown_retains_custody_and_negative_control(
        owner, monkeypatch, False, operation="undrain",
    )


def test_actual_monitor_force_sample_follows_admission_close(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_actual_monitor_shutdown_retains_custody_and_negative_control(
        owner, monkeypatch, False, operation="force-race",
    )


def test_two_updater_generations_do_not_lose_replacement_during_supersession(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import single_instance_lease
    from agent_worktrees import status_monitor_runtime

    held = threading.Lock()
    entered, retire, retired = threading.Event(), threading.Event(), threading.Event()
    events, failures = [], []

    class Lease:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def acquire(self) -> None:
            if not held.acquire(blocking=False):
                raise single_instance_lease.AlreadyRunningError(Path("previous"), 101)

        def release(self) -> None:
            events.append("release")
            held.release()

    def previous_work() -> None:
        entered.set()
        assert retire.wait(15)
        events.append("previous-cleanup-confirmed")

    def previous() -> None:
        try:
            with_updater_lease("same-session", previous_work)
        except BaseException as error:
            failures.append(error)
        finally:
            retired.set()

    def wait(seconds: float) -> None:
        retire.set()
        assert retired.wait(15)

    monkeypatch.setattr(go, "_windows_observation", lambda: True)
    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: Path("unused"))
    monkeypatch.setattr(time, "sleep", wait)
    worker = threading.Thread(target=previous)
    worker.start()
    try:
        assert entered.wait(15)
        with_updater_lease("same-session", lambda: events.append("replacement"))
    finally:
        retire.set()
        worker.join(15)
    assert not worker.is_alive() and not failures
    assert events == ["previous-cleanup-confirmed", "release", "replacement", "release"]


def test_actual_monitor_guarded_dispatch_uses_existing_undrain_and_native_route(
    owner: _StatusSegmentCache, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    test_actual_monitor_shutdown_retains_custody_and_negative_control(
        owner, monkeypatch, False, operation="guarded", recovery_directory=tmp_path,
    )
