"""Retained hook-worker and create/resume lifecycle regression coverage."""

import argparse
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import agent_worktrees.__main__ as m
from agent_worktrees import local_cache_refresh as lcr

class TestSessionstartDiagnostic:
    """``sessionstart_diagnostic`` is the backup refresh ``__main__.py``'s
    ``_run_session_lifecycle`` calls before registration on ``sessionStart``; see
    ``test_hook_ipc.py`` for coverage of its wiring into that lifecycle."""

    def test_queues_without_waiting_for_renderer(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "_queue_refresh",
            lambda repo_root: calls.append(repo_root) or lcr.RefreshResult("queued"),
        )
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda *a, **k: pytest.fail("hook must not render"),
        )

        diagnostic = lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 200.0)

        assert calls == [str(tmp_path)]
        assert "[local-guidance] queued" in diagnostic

    def test_short_hook_budget_still_queues_without_rendering(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "_queue_refresh",
            lambda repo_root: calls.append(repo_root) or lcr.RefreshResult("queued"),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 9.0)

        assert len(calls) == 1
        assert calls == [str(tmp_path)]

    def test_expired_deadline_never_spawns_worker(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import time

        calls = []
        monkeypatch.setattr(
            lcr,
            "_queue_refresh",
            lambda repo_root: calls.append(repo_root),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() - 1.0)

        assert calls == []

    def test_skips_when_budget_is_too_tight(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Too little deadline budget remaining skips the refresh
        entirely -- attempting it would only risk the shared lifecycle
        deadline for a call that could never complete in time anyway."""
        import time

        calls = []
        monkeypatch.setattr(
            lcr, "_queue_refresh", lambda repo_root: calls.append(repo_root)
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=time.time() + 0.5)

        assert calls == []

    def test_queues_with_no_deadline(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        calls = []
        monkeypatch.setattr(
            lcr,
            "_queue_refresh",
            lambda repo_root: calls.append(repo_root) or lcr.RefreshResult("queued"),
        )

        lcr.sessionstart_diagnostic(str(tmp_path), deadline=None)

        assert calls == [str(tmp_path)]

    def test_spawn_failure_is_not_reported_as_delivery(self, tmp_path, monkeypatch):
        def fail(cwd):
            raise OSError("spawn refused")

        monkeypatch.setattr(lcr, "_queue_refresh", fail)
        diagnostic = lcr.sessionstart_diagnostic(str(tmp_path), deadline=None)
        assert "[local-guidance] failed" in diagnostic
        assert "spawn refused" in diagnostic

    def test_worker_is_headless_survivable_and_reaped(self, tmp_path, monkeypatch):
        from agent_worktrees import status_monitor_runtime, local_cache_worker

        (tmp_path / ".git").mkdir()
        calls = []
        waits = []
        monkeypatch.setattr(lcr, "_worker_directory", lambda root: tmp_path / "worker")
        (tmp_path / "worker").mkdir()
        log_path = tmp_path / "worker" / "refresh.log"
        log_path.write_text("old result\n" * 100_000, encoding="utf-8")
        monkeypatch.setattr(status_monitor_runtime, "_daemon_cwd", lambda: str(tmp_path))
        monkeypatch.setattr(status_monitor_runtime, "_daemon_environment", lambda: {"HOME": str(tmp_path)})
        monkeypatch.setattr(
            local_cache_worker, "windowless_daemon_kwargs",
            lambda **kwargs: calls.append(kwargs) or {"start_new_session": True},
        )
        monkeypatch.setattr(
            lcr.subprocess, "Popen",
            lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(
                pid=123, wait=lambda: waits.append(True) or 0,
            ),
        )
        monkeypatch.setattr(local_cache_worker, "_owner", lambda pid: {"pid": pid, "start_time": "fixture"})
        result = lcr._queue_refresh(str(tmp_path))
        assert result.status == "queued"
        assert "not yet delivered" in result.detail
        assert calls[0] == {"breakaway": True}
        argv, kwargs = calls[1]
        assert argv[:5] == [
            sys.executable, "-m", "agent_worktrees.local_cache_worker",
            str(tmp_path.resolve()), str(tmp_path / "worker"),
        ]
        assert len(argv[5]) == 32
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["start_new_session"]
        assert log_path.stat().st_size < local_cache_worker._MAX_LOG_BYTES
        assert "old result" not in log_path.read_text()
        deadline = time.monotonic() + 1
        while not waits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert waits

    def test_active_worker_keeps_its_log_and_is_not_redispatched(self, tmp_path, monkeypatch):
        from single_instance_lease import SingleInstance

        monkeypatch.setattr(lcr, "_worker_directory", lambda root: tmp_path)
        log_path = tmp_path / "refresh.log"
        log_path.write_text("current worker result", encoding="utf-8")
        monkeypatch.setattr(
            lcr.subprocess, "Popen", lambda *a, **k: pytest.fail("worker already owns checkout"),
        )
        lease = SingleInstance(tmp_path, service="local-guidance")
        lease.acquire()
        try:
            result = lcr._queue_refresh(str(tmp_path))
        finally:
            lease.release()
        assert result.status == "queued"
        assert "dispatch already reserved" in result.detail
        assert log_path.read_text() == "current worker result"

    def test_git_worktree_removal_reclaims_worker_metadata(self, tmp_path):
        anchor = tmp_path / "anchor"
        checkout = tmp_path / "checkout"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        def git(*args):
            subprocess.run(
                ["git", *args], check=True, capture_output=True, creationflags=flags,
            )

        git("init", "--quiet", str(anchor))
        git(
            "-C", str(anchor), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "Fixture",
        )
        git("-C", str(anchor), "worktree", "add", "--quiet", "--detach", str(checkout), "HEAD")
        directory = lcr._worker_directory(checkout.resolve())
        assert directory.parent.parent == anchor / ".git" / "worktrees"
        directory.mkdir()
        (directory / "refresh.log").write_text("latest result", encoding="utf-8")
        git("-C", str(anchor), "worktree", "remove", str(checkout))
        assert not directory.exists()

    def test_only_one_worker_renders_a_repository(self, tmp_path, monkeypatch, capsys):
        from single_instance_lease import SingleInstance

        monkeypatch.setattr(lcr, "_worker_directory", lambda root: tmp_path)
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda *a, **k: pytest.fail("duplicate render"),
        )
        lease = SingleInstance(tmp_path, service="local-guidance")
        lease.acquire()
        try:
            assert lcr._run_refresh_worker(str(tmp_path)) == 0
        finally:
            lease.release()
        assert "another refresh worker" in capsys.readouterr().out

    @pytest.mark.parametrize("status,exit_code", [("ready", 0), ("timeout", 1), ("partial", 1)])
    def test_worker_records_its_real_outcome(self, tmp_path, monkeypatch, capsys, status, exit_code):
        monkeypatch.setattr(lcr, "_worker_directory", lambda root: tmp_path)
        monkeypatch.setattr(lcr, "refresh_local_cache", lambda root: lcr.RefreshResult(status))
        assert lcr._run_refresh_worker(str(tmp_path)) == exit_code
        assert f"[local-guidance] {status}" in capsys.readouterr().out


class TestCreateWiring:
    def _harness_config(self, tmp_path: Path):
        from agent_worktrees import config as cfg_mod

        anchor = tmp_path / "anchor"
        anchor.mkdir()
        return cfg_mod.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="linux",
            repo_name="demo-repo",
            repos={
                "demo-repo": cfg_mod.RepoConfig(
                    anchor=str(anchor),
                    worktree_root=str(tmp_path / "worktrees"),
                )
            },
        )

    @pytest.mark.parametrize("no_pair", [False, True])
    @pytest.mark.parametrize("deferred", [False, True])
    @pytest.mark.parametrize("recovery", [False, True])
    @pytest.mark.parametrize("pair_failure", [False, True])
    def test_create_refreshes_the_local_cache_for_the_new_worktree(
        self, tmp_path: Path, monkeypatch, no_pair, deferred, recovery, pair_failure,
    ) -> None:
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda name=None: tmp_path / "tracking")
        monkeypatch.setattr(m.permissions, "clone_permissions", lambda *a: False)
        monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda *a: False)
        monkeypatch.setattr(
            m.permissions, "ensure_extension_permission_approvals", lambda *a: False
        )
        monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)
        monkeypatch.setattr(m.git_ops, "create_worktree", lambda *a, **k: None)
        from agent_worktrees.codename_config import CodenameConfig
        monkeypatch.setattr(
            m.cfg, "load_config",
            lambda project=None: SimpleNamespace(
                default_repo=SimpleNamespace(
                    codename=CodenameConfig(),
                    pr=SimpleNamespace(
                        enabled=False, source_attribution_configured=False,
                    ),
                )
            ),
        )

        calls = []
        monkeypatch.setattr(m, "_paired_knowledge_allocation_preflight", lambda config: None)
        def carve(*a, **kw):
            calls.append("pair")
            if pair_failure:
                raise OSError("pair allocation failed")
            return {}

        monkeypatch.setattr(m, "_carve_paired_knowledge", carve)
        monkeypatch.setattr(lcr, "mark_pair_pending", lambda root: calls.append("pair-pending"))
        monkeypatch.setattr(
            m, "_stamp_and_compose_paired_knowledge",
            lambda *a, **kw: calls.append("compose") or {"action": "composed"},
        )
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        result = m._create_worktree_core(
            self._harness_config(tmp_path), no_pair=no_pair,
            defer_projections=deferred, recovery=recovery, creation_intent=lambda value: None,
        )

        assert calls == (
            ([] if no_pair else ["pair", "compose"])
            + (["pair-pending"] if pair_failure and not no_pair else [])
            + ([] if deferred or pair_failure and not no_pair else [result["worktree"]["path"]])
        )


class TestResumeWiring:
    def _config(self, tmp_path: Path):
        from agent_worktrees import config as cfg_mod

        anchor = tmp_path / "anchor"
        anchor.mkdir()
        return cfg_mod.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="windows",
            repo_name="demo-repo",
            auto_fast_forward=False,
            repos={
                "demo-repo": cfg_mod.RepoConfig(
                    anchor=str(anchor),
                    worktree_root=str(tmp_path / "worktrees"),
                )
            },
        )

    def _patched_resume(self, tmp_path: Path, monkeypatch, sample_record, *, dry_run: bool):

        config = self._config(tmp_path)
        worktree_path = tmp_path / "worktrees" / "wt-1"
        worktree_path.mkdir(parents=True)
        record = replace(
            sample_record,
            worktree_id="wt-1",
            repo=config.repo_name,
            machine=config.machine,
            platform=config.platform,
            worktree_path=str(worktree_path),
            branch="worktree/wt-1",
            sessions=[],
            resume_count=0,
            last_resumed_at=None,
            codename="test-record",
        )

        class _NullLock:
            def __enter__(self):
                return None

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(m.tracking, "_RecordLock", lambda *_a, **_k: _NullLock())
        monkeypatch.setattr(m.tracking, "load_record", lambda *_a, **_k: record)
        monkeypatch.setattr(m.tracking, "mark_resumed", lambda *_a, **_k: None)
        monkeypatch.setattr(m.tracking, "save_record", lambda *_a, **_k: None)
        monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
        monkeypatch.setattr(
            m, "_preflight_launch", lambda *_a, **_k: SimpleNamespace(error=None)
        )
        monkeypatch.setattr(
            m,
            "_launch_profile_selection",
            lambda *_a, **_k: SimpleNamespace(profile=None, assignment=None),
        )
        monkeypatch.setattr(m, "_reflect_assignment", lambda *_a, **_k: None)
        monkeypatch.setattr(m, "_build_launch_cmd", lambda *_a, **_k: ["copilot"])
        monkeypatch.setattr(m, "_repo_session_env", lambda *_a, **_k: {})
        monkeypatch.setattr(m, "_build_env", lambda *_a, **_k: {})
        monkeypatch.setattr(m, "_apply_assignment_env", lambda env, _s: env)
        monkeypatch.setattr(m, "_emit_parent_context_hint", lambda *_a, **_k: None)
        monkeypatch.setattr(m, "_emit_plan", lambda plan: None)
        from agent_worktrees import knowledge_plugins

        monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **kw: {"action": "no-op"})

        args = argparse.Namespace(
            worktree_id="wt-1", dry_run=dry_run, no_mux=False, no_resume=True,
            restore=False, json=True, bare_resume=False, no_fast_forward=True,
        )
        return record, config, args, worktree_path

    @pytest.mark.parametrize("deferred", [False, True])
    def test_resume_refreshes_the_local_cache(
        self, tmp_path: Path, monkeypatch, sample_record, deferred,
    ) -> None:
        record, config, args, worktree_path = self._patched_resume(
            tmp_path, monkeypatch, sample_record, dry_run=False
        )
        calls = []
        args.defer_projections = deferred
        from agent_worktrees import knowledge_plugins

        def compose(*, cwd, config):
            assert cwd == str(worktree_path)
            calls.append("compose")
            return {"action": "no-op"}

        monkeypatch.setattr(knowledge_plugins, "compose_from_pair", compose)
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        m._resolve_resume(record, config, args)

        assert calls == ([] if deferred else ["compose", str(worktree_path)])

    def test_dry_run_resume_never_refreshes(
        self, tmp_path: Path, monkeypatch, sample_record
    ) -> None:
        record, config, args, _worktree_path = self._patched_resume(
            tmp_path, monkeypatch, sample_record, dry_run=True
        )
        calls = []
        monkeypatch.setattr(
            lcr, "refresh_local_cache", lambda repo_root, **k: calls.append(repo_root)
        )

        m._resolve_resume(record, config, args)

        assert calls == []
