from __future__ import annotations

import argparse
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from machine_transport import detect_platform

from agent_worktrees import __main__ as cli
from agent_worktrees import activity, claimant, finalize as fin, finalize_local, git_ops, reclaim, sessions, tracking, worktree_identity
from agent_worktrees import config as cfg

S = git_ops.WorktreeState
_REAL_BUILD_ACTIVE_PATHS = cli._build_active_paths


def iso_at(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def info(
    state: git_ops.WorktreeState,
    *,
    ahead: int = 0,
    behind: int = 0,
    dirty: int = 0,
) -> git_ops.WorktreeStateInfo:
    return git_ops.WorktreeStateInfo(
        state=state,
        ahead=ahead,
        behind=behind,
        dirty=dirty,
    )


def make_record(
    root: Path,
    *,
    wt_id: str = "wt1",
    status: tracking.WorktreeStatus = "finalized",
    exists: bool = True,
    branch: str | None = None,
    path_name: str | None = None,
    started_at: str | None = None,
    last_resumed_at: str | None = None,
    dispatch_attempt: tracking.DispatchAttempt | None = None,
) -> tracking.WorktreeRecord:
    worktree_path = root / (path_name or wt_id)
    if exists:
        worktree_path.mkdir(parents=True, exist_ok=True)
    return tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=branch or f"worktree/{wt_id}",
        worktree_path=str(worktree_path),
        repo="demo",
        machine="m",
        platform=detect_platform(),
        started_at=started_at or iso_at(0),
        last_resumed_at=last_resumed_at or iso_at(0),
        resume_count=0,
        title=None,
        status=status,
        completed_at=None,
        sessions=[],
        prs=[],
        kind="session",
        dispatch_attempt=dispatch_attempt,
    )


def save_record(tracking_dir: Path, rec: tracking.WorktreeRecord) -> Path:
    path = tracking_dir / f"{rec.worktree_id}.yaml"
    tracking.save_record(rec, path)
    return path


def load_record(path: Path) -> tracking.WorktreeRecord:
    return tracking.load_record(path)


def mutate_record(path: Path, mutate: Callable[[tracking.WorktreeRecord], None]) -> None:
    rec = tracking.load_record(path)
    mutate(rec)
    tracking.save_record(rec, path)


def session_ctx(
    *,
    active_paths: Sequence[str] = (),
    turn_count: dict[str, int] | None = None,
) -> sessions.SessionContext:
    return sessions.SessionContext(
        active_sessions={p: ["sess-1"] for p in active_paths},
        turn_count=turn_count or {},
    )


def hold_record_lock_worker(yaml_path_str: str, ready_file: str, release_file: str) -> None:
    from pathlib import Path as _Path

    from agent_worktrees.tracking import _RecordLock

    with _RecordLock(_Path(yaml_path_str)):
        _Path(ready_file).write_text("1", encoding="utf-8")
        deadline = time.monotonic() + 30
        while not _Path(release_file).exists():
            if time.monotonic() > deadline:
                break
            time.sleep(0.01)


class CleanupHarness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.monkeypatch = monkeypatch
        self.root = tmp_path
        self.tracking_dir = tmp_path / "project" / "worktrees"
        self.tracking_dir.mkdir(parents=True)
        self.anchor = tmp_path / "anchor"
        self.anchor.mkdir()
        self.worktree_root = tmp_path
        self.repo = cfg.RepoConfig(
            anchor=str(self.anchor),
            remote="origin",
            default_branch="main",
            worktree_root=str(self.worktree_root),
        )
        self.config = cfg.Config(
            srcroot=str(tmp_path), machine="m", platform=detect_platform(),
            repo_name="demo", repos={"demo": self.repo},
        )
        def receiving_config(project: str, **kwargs: Any) -> cfg.Config:
            return replace(
                self.config, repo_name=project, repos={**self.config.repos, project: self.repo},
            )
        self.reaped: list[tuple[str, str]] = []
        self.finalize_held = False
        self.finalize_events: list[str] = []
        self.classify_calls = 0
        self.scan_calls = 0
        self.merge_calls = 0
        self.has_mux_calls = 0
        self.claimant_calls = 0
        self.on_reap: Callable[[tracking.WorktreeRecord, git_ops.WorktreeStateInfo], tuple[int, list[str]]] | None = None
        self.monkeypatch.setattr(cfg, "load_config", lambda *a, **k: self.config)
        self.monkeypatch.setattr(cfg, "load_project_config", receiving_config)
        self.monkeypatch.setattr(cfg, "tracking_dir", lambda project=None: self.tracking_dir)
        self.monkeypatch.setattr(cfg, "project_dir", lambda project=None: tmp_path / "project")
        self.monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: {})
        self.monkeypatch.setattr(git_ops, "has_remote", lambda *_a, **_k: False)
        self.monkeypatch.setattr(git_ops, "fetch", lambda *_a, **_k: None)
        self.monkeypatch.setattr(
            finalize_local, "resolve_upstream", lambda *_a, **_k: ("origin/main", False),
        )
        self.monkeypatch.setattr(git_ops, "prune_worktrees", lambda *_a, **_k: None)
        self.monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
        self.monkeypatch.setattr(cli, "_hosted_session_blocks_cleanup", lambda rec: False)
        self.monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {})
        self.monkeypatch.setattr(sessions, "_mux_session_activity", lambda: {})
        self.monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
        self.monkeypatch.setattr(activity, "log_event", lambda *_a, **_k: None)
        self.monkeypatch.setattr(
            cli,
            "_reap_worktree",
            lambda rec, wt_info, _repo, _tracking_dir: self._reap(rec, wt_info),
        )
        self.set_finalize_lock()
        self.set_session_contexts([session_ctx(), session_ctx()])
        self.set_build_active_paths_sequence([set(), set()])

    def record_path(self, wt_id: str = "wt1") -> Path:
        return self.tracking_dir / f"{wt_id}.yaml"

    def norm(self, path: str) -> str:
        return sessions._normalize_path(path)

    def seed(self, rec: tracking.WorktreeRecord) -> Path:
        return save_record(self.tracking_dir, rec)

    def _reap(
        self, rec: tracking.WorktreeRecord, wt_info: git_ops.WorktreeStateInfo
    ) -> tuple[int, list[str]]:
        self.reaped.append((rec.worktree_id, wt_info.state.value))
        if self.on_reap is not None:
            return self.on_reap(rec, wt_info)
        return 0, []

    def set_reap_callback(
        self,
        cb: Callable[[tracking.WorktreeRecord, git_ops.WorktreeStateInfo], tuple[int, list[str]]] | None,
    ) -> None:
        self.on_reap = cb

    def set_finalize_lock(
        self,
        on_acquire: Callable[[], None] | None = None,
        *,
        order: list[str] | None = None,
    ) -> None:
        harness = self

        class _Lock:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def acquire(self) -> None:
                if order is not None:
                    order.append("finalize-acquire")
                harness.finalize_events.append("finalize-acquire")
                harness.finalize_held = True
                if on_acquire is not None:
                    on_acquire()

            def release(self) -> None:
                if order is not None:
                    order.append("finalize-release")
                harness.finalize_events.append("finalize-release")
                harness.finalize_held = False

        self.monkeypatch.setattr(fin, "FinalizeLock", _Lock)

    def set_session_contexts(
        self,
        values: Sequence[sessions.SessionContext],
        *,
        assert_revalidation_under_finalize: bool = False,
    ) -> None:
        seq = list(values)
        if not seq:
            seq = [session_ctx()]

        def _scan(_records: Any) -> sessions.SessionContext:
            idx = min(self.scan_calls, len(seq) - 1)
            self.scan_calls += 1
            if assert_revalidation_under_finalize and self.scan_calls >= 2:
                assert self.finalize_held is True
            return seq[idx]

        self.monkeypatch.setattr(sessions, "scan_sessions_fast", _scan)

    def set_build_active_paths_sequence(self, values: Sequence[set[str]]) -> None:
        seq = list(values)
        if not seq:
            seq = [set()]
        calls = {"count": 0}

        def _build(_records: Any, _ctx: Any = None) -> set[str]:
            idx = min(calls["count"], len(seq) - 1)
            calls["count"] += 1
            return set(seq[idx])

        self.monkeypatch.setattr(cli, "_build_active_paths", _build)

    def set_classifier(
        self,
        initial: git_ops.WorktreeStateInfo,
        fresh: git_ops.WorktreeStateInfo | None = None,
        *,
        assert_revalidation_under_finalize: bool = False,
    ) -> None:
        fresh_info = initial if fresh is None else fresh

        def _classify(worktree_path: str, _branch: str, *, active_paths: set[str], **_kwargs: Any) -> git_ops.WorktreeStateInfo:
            self.classify_calls += 1
            if assert_revalidation_under_finalize and self.classify_calls >= 2:
                assert self.finalize_held is True
            if self.norm(worktree_path) in {self.norm(p) for p in active_paths}:
                return info(S.ACTIVE)
            return initial if self.classify_calls == 1 else fresh_info

        self.monkeypatch.setattr(git_ops, "classify_worktree", _classify)

    def set_branch_merged_sequence(self, values: Sequence[bool]) -> None:
        seq = list(values)
        if not seq:
            seq = [True]

        def _merged(*_args: Any, **_kwargs: Any) -> bool:
            idx = min(self.merge_calls, len(seq) - 1)
            self.merge_calls += 1
            return seq[idx]

        self.monkeypatch.setattr(git_ops, "is_branch_merged", _merged)

    def set_claimant_alive_sequence(self, values: Sequence[bool | None]) -> None:
        seq = list(values)
        if not seq:
            seq = [False]

        def _claimant(_owner_ref: str) -> bool | None:
            idx = min(self.claimant_calls, len(seq) - 1)
            self.claimant_calls += 1
            return seq[idx]

        self.monkeypatch.setattr(claimant, "resolve_claimant_alive", _claimant)

    def use_real_build_active_paths_with_mux_sequence(
        self,
        *,
        has_mux_values: Sequence[bool],
    ) -> None:
        self.has_mux_calls = 0
        seq = list(has_mux_values)
        if not seq:
            seq = [False]
        self.monkeypatch.setattr(cli, "_build_active_paths", _REAL_BUILD_ACTIVE_PATHS)
        self.monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: None)

        def _has_mux(_wt_id: str) -> bool:
            idx = min(self.has_mux_calls, len(seq) - 1)
            self.has_mux_calls += 1
            return seq[idx]

        self.monkeypatch.setattr(sessions, "has_mux_session", _has_mux)

    def use_real_build_active_paths_with_mux_catalogs(self, catalogs: Sequence[dict[str, int] | None]) -> None:
        self.mux_list_calls = 0
        self.monkeypatch.setattr(cli, "_build_active_paths", _REAL_BUILD_ACTIVE_PATHS)

        def _list_mux() -> dict[str, int] | None:
            index = min(self.mux_list_calls, len(catalogs) - 1)
            self.mux_list_calls += 1
            return catalogs[index]

        self.monkeypatch.setattr(sessions, "_list_mux_sessions", _list_mux)
        self.monkeypatch.setattr(
            sessions, "has_mux_session",
            lambda *a, **kw: pytest.fail("complete catalog must not fall back to a probe"),
        )

    def install_record_lock_wrapper(
        self,
        order: list[str],
        *,
        assert_finalize_held: bool = True,
    ) -> None:
        real_lock = tracking._RecordLock
        harness = self

        class _WrappedRecordLock:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self._inner = real_lock(*args, **kwargs)

            def __enter__(self) -> tracking._RecordLock:
                order.append("record-enter")
                if assert_finalize_held:
                    assert harness.finalize_held is True
                return self._inner.__enter__()

            def __exit__(self, *exc: Any) -> None:
                return self._inner.__exit__(*exc)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

        self.monkeypatch.setattr(tracking, "_RecordLock", _WrappedRecordLock)

    def assert_record_lock_blocks_peer_thread(self, yaml_path: Path) -> None:
        outcome: dict[str, bool] = {}

        def _peer() -> None:
            with tracking._RecordLock(yaml_path, blocking=False) as lk:
                outcome["acquired"] = lk.acquired

        thread = threading.Thread(target=_peer, daemon=True)
        thread.start()
        thread.join(timeout=5)
        assert thread.is_alive() is False
        assert outcome["acquired"] is False

    def run_batch(
        self,
        *,
        clean: bool = True,
        include_unused: bool = False,
        include_conversations: bool = False,
        reconcile_prs: bool = False,
    ) -> int:
        args = argparse.Namespace(
            worktree_id=None,
            clean=clean,
            include_unused=include_unused,
            include_conversations=include_conversations,
            reconcile_prs=reconcile_prs,
        )
        return cli.cmd_cleanup(args)

    def run_reap_one(
        self,
        wt_id: str = "wt1",
        *,
        force: bool = False,
        include_unused: bool = False,
        include_conversations: bool = False,
        reconcile_prs: bool = False,
    ) -> dict[str, Any]:
        return cli.reap_one(
            wt_id,
            force=force,
            include_unused=include_unused,
            include_conversations=include_conversations,
            reconcile_prs=reconcile_prs,
        )

    def run_sweep(
        self,
        *,
        dry_run: bool = False,
        min_idle_secs: float | None = 0,
        now: float | None = None,
    ) -> dict[str, Any]:
        return cli.sweep_finished_session_worktrees(
            dry_run=dry_run,
            min_idle_secs=min_idle_secs,
            now=now,
        )
