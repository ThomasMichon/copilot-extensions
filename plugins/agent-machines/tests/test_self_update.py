from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_machines import __main__ as cli
from agent_machines import self_update, self_update_state, self_update_tasks
from agent_machines.manifest import ManifestError, load_package
from agent_machines.reconcile import plan
from agent_machines.resources import resolve_resources

from ._helpers import base_package, write_package


def _pkg(
    tmp_path: Path,
    name: str,
    resources: list[dict],
    *,
    authority: int | None = None,
) -> object:
    data = base_package(
        name=name,
        schema_version=4,
        gate=["box-1"],
        manage={},
        resources=resources,
    )
    if authority is not None:
        data["authority"] = authority
    path = write_package(tmp_path / name.replace("/", "_"), "pkg.yaml", data)
    return load_package(path, source_repo=name.split("/")[0])


class _FakeMutex:
    def __init__(self, outcome: str):
        self.outcome = outcome
        self.released = False
        self.closed = False

    def try_acquire(self) -> str:
        return self.outcome

    def release(self) -> None:
        self.released = True

    def close(self) -> None:
        self.closed = True


def _task_snapshot(
    tier: str,
    *,
    present: bool,
    matching: bool = True,
    enabled: bool | None = True,
):
    return self_update.ScheduledTaskSnapshot(
        task_name=self_update.TIER_SPECS[tier].task_name,
        present=present,
        enabled=enabled,
        logon_type="Interactive" if present else None,
        description=self_update.task_description(tier) if present else None,
        execute="conhost.exe" if present else None,
        arguments=self_update.task_action_arguments(tier),
        working_directory=self_update.task_working_directory(),
        matching=matching,
    )


def test_manifest_accepts_self_update_resource_and_rejects_bad_tier(tmp_path):
    package = _pkg(
        tmp_path,
        "acme/watchdog",
        [{"type": "self-update", "tier": "watchdog"}],
    )
    assert package.resources[0]["tier"] == "watchdog"
    with pytest.raises(ManifestError, match="self-update tier"):
        _pkg(
            tmp_path,
            "acme/invalid",
            [{"type": "self-update", "tier": "weekly"}],
        )


def test_self_update_resource_selects_highest_authority_and_equal_highest_conflicts(
    tmp_path,
):
    disabled = _pkg(
        tmp_path,
        "acme/disabled",
        [{"type": "self-update", "tier": "watchdog", "state": "absent"}],
        authority=0,
    )
    enabled = _pkg(
        tmp_path,
        "acme/enabled",
        [{"type": "self-update", "tier": "watchdog", "state": "present"}],
        authority=10,
    )
    resolved, findings = resolve_resources([disabled, enabled], "box-1", "windows")
    watchdog = next(resource for resource in resolved if resource.type == "self-update")
    assert watchdog.desired["state"] == "present"
    assert any(finding.code == "authority-supersession" for finding in findings)
    equal = _pkg(
        tmp_path,
        "acme/equal",
        [{"type": "self-update", "tier": "watchdog", "state": "absent"}],
        authority=10,
    )
    _, equal_findings = resolve_resources([equal, enabled], "box-1", "windows")
    assert any(finding.code == "resource-conflict" for finding in equal_findings)


def test_run_noops_when_tier_not_opted_in():
    result = self_update.run_tier("watchdog", opted_in=False)
    assert result.ok is True
    assert result.status == "noop"
    assert result.detail == "tier 'watchdog' is not opted in"


def test_lock_refuses_live_owner_without_double_drive(tmp_path, monkeypatch):
    started = datetime(2026, 1, 1, tzinfo=UTC)
    self_update.lock_path("watchdog", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    self_update.lock_path("watchdog", tmp_path).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tier": "watchdog",
                "pid": 4321,
                "started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("timeout"))
    monkeypatch.setattr(self_update_state, "_WindowsMutex", lambda _name: _FakeMutex("timeout"))
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update_tasks.sys, "platform", "win32")
    lock = self_update.TierLock(tier="watchdog", home=tmp_path)
    acquired, detail = lock.acquire()
    assert acquired is False
    assert "already active" in detail
    assert lock.snapshot is not None
    assert lock.snapshot.pid == 4321


def test_lock_reclaims_dead_owner_only_after_stale_window(tmp_path, monkeypatch):
    started = datetime(2026, 1, 1, tzinfo=UTC)
    path = self_update.lock_path("watchdog", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tier": "watchdog",
                "pid": 4321,
                "started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update_tasks.sys, "platform", "win32")
    lock = self_update.TierLock(
        tier="watchdog",
        home=tmp_path,
        pid=9999,
        now=lambda: started + timedelta(minutes=11),
        pid_alive=lambda _pid: False,
    )
    acquired, detail = lock.acquire()
    assert acquired is True
    assert detail == "acquired"
    assert lock.reclaimed is True
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["pid"] == 9999
    lock.release()


def test_lock_refuses_recent_dead_owner(tmp_path, monkeypatch):
    started = datetime(2026, 1, 1, tzinfo=UTC)
    path = self_update.lock_path("watchdog", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tier": "watchdog",
                "pid": 4321,
                "started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update_tasks.sys, "platform", "win32")
    lock = self_update.TierLock(
        tier="watchdog",
        home=tmp_path,
        pid=9999,
        now=lambda: started + timedelta(minutes=9),
        pid_alive=lambda _pid: False,
    )
    acquired, detail = lock.acquire()
    assert acquired is False
    assert "stale window has not elapsed" in detail
    assert lock.snapshot is not None
    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == 4321


def test_watchdog_starts_launcher_when_missing(monkeypatch):
    config = self_update.DtsshConfig(
        config_path=Path("config.json"),
        install_root=Path("install"),
        launcher_path=Path(r"C:\agent-ssh-dtssh\dtssh-host-launcher.ps1"),
        alias="box-1",
        port=2222,
    )
    state = {"running": False}

    def process_lister():
        return (
            []
            if not state["running"]
            else [{"ProcessId": 321, "CommandLine": str(config.launcher_path)}]
        )

    def starter(_config):
        state["running"] = True
        return True

    monkeypatch.setattr(self_update, "load_dtssh_config", lambda local_app_data=None: config)
    steps = self_update.ensure_watchdog(
        process_lister=process_lister,
        launcher_starter=starter,
        sleeper=lambda _seconds: None,
        now=iter([0.0, 0.0]).__next__,
    )
    assert steps[0].status == "changed"
    assert "started dtssh host launcher" in steps[0].detail


def test_refresh_dtssh_mesh_skips_when_agent_ssh_missing(monkeypatch):
    monkeypatch.setattr(self_update.shutil, "which", lambda _name: None)
    step = self_update.refresh_dtssh_mesh(runner=lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not run without agent-ssh")
    ))
    assert step.status == "skipped"
    assert "agent-ssh is not installed" in step.detail


def test_refresh_dtssh_mesh_skips_when_no_machines_yaml(monkeypatch):
    monkeypatch.setattr(self_update.shutil, "which", lambda _name: "agent-ssh")

    def runner(argv, *, cwd=None, timeout=0):
        payload = json.dumps(
            {"ok": True, "machines_yaml": None, "detail": "no machines.yaml found", "aliases": []}
        )
        return self_update.CommandResult(list(argv), 0, payload, "")

    step = self_update.refresh_dtssh_mesh(runner=runner)
    assert step.status == "skipped"
    assert "no machines.yaml" in step.detail


def test_refresh_dtssh_mesh_ok_when_mesh_reachable(monkeypatch):
    monkeypatch.setattr(self_update.shutil, "which", lambda _name: "agent-ssh")

    def runner(argv, *, cwd=None, timeout=0):
        payload = json.dumps(
            {
                "ok": True,
                "machines_yaml": "machines.yaml",
                "detail": "refreshed the dtssh mesh; all 2 known alias(es) reachable",
                "aliases": [
                    {"alias": "host-a", "reachable": True, "detail": "reachable"},
                    {"alias": "host-b", "reachable": True, "detail": "reachable"},
                ],
            }
        )
        return self_update.CommandResult(list(argv), 0, payload, "")

    step = self_update.refresh_dtssh_mesh(runner=runner)
    assert step.status == "ok"
    assert "all 2 known alias(es) reachable" in step.detail


def test_refresh_dtssh_mesh_error_when_alias_unreachable(monkeypatch):
    monkeypatch.setattr(self_update.shutil, "which", lambda _name: "agent-ssh")

    def runner(argv, *, cwd=None, timeout=0):
        payload = json.dumps(
            {
                "ok": False,
                "machines_yaml": "machines.yaml",
                "detail": "refreshed the dtssh mesh; unreachable: host-b",
                "aliases": [
                    {"alias": "host-a", "reachable": True, "detail": "reachable"},
                    {"alias": "host-b", "reachable": False, "detail": "unreachable after refresh"},
                ],
            }
        )
        return self_update.CommandResult(list(argv), 1, payload, "")

    step = self_update.refresh_dtssh_mesh(runner=runner)
    assert step.status == "error"
    assert "host-b" in step.detail


def test_run_tier_watchdog_appends_mesh_refresh_step(monkeypatch, tmp_path):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update_tasks.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    config = self_update.DtsshConfig(
        config_path=tmp_path / "config.json",
        install_root=tmp_path,
        launcher_path=tmp_path / "dtssh-host-launcher.ps1",
        alias="box-1",
        port=2222,
    )
    monkeypatch.setattr(self_update, "load_dtssh_config", lambda local_app_data=None: config)
    monkeypatch.setattr(
        self_update,
        "watchdog_running",
        lambda _config, process_lister=None: True,
    )

    calls = []

    def mesh_refresher():
        calls.append(True)
        return self_update.StepResult("dtssh-mesh-refresh", "ok", "refreshed the dtssh mesh")

    result = self_update.run_tier(
        "watchdog",
        opted_in=True,
        mesh_refresher=mesh_refresher,
        home=tmp_path,
    )
    assert result.status == "ok"
    assert calls == [True]
    assert result.steps[-1].name == "dtssh-mesh-refresh"
    assert result.steps[-1].status == "ok"


def test_run_tier_watchdog_fails_when_mesh_refresh_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update_tasks.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    config = self_update.DtsshConfig(
        config_path=tmp_path / "config.json",
        install_root=tmp_path,
        launcher_path=tmp_path / "dtssh-host-launcher.ps1",
        alias="box-1",
        port=2222,
    )
    monkeypatch.setattr(self_update, "load_dtssh_config", lambda local_app_data=None: config)
    monkeypatch.setattr(
        self_update,
        "watchdog_running",
        lambda _config, process_lister=None: True,
    )

    def mesh_refresher():
        return self_update.StepResult(
            "dtssh-mesh-refresh", "error", "unreachable: host-b"
        )

    result = self_update.run_tier(
        "watchdog",
        opted_in=True,
        mesh_refresher=mesh_refresher,
        home=tmp_path,
    )
    assert result.status == "error"
    assert "unreachable: host-b" in result.detail
    assert result.steps[-1].status == "error"


def test_fast_forward_repo_skips_dirty_and_diverged(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def dirty_runner(argv, *, cwd=None, timeout=0):
        if argv[:3] == ["git", "rev-parse", "--is-bare-repository"]:
            return self_update.CommandResult(list(argv), 0, "false\n", "")
        if argv[:2] == ["git", "status"]:
            return self_update.CommandResult(list(argv), 0, " M tracked.txt\n", "")
        raise AssertionError(argv)

    dirty = self_update.fast_forward_repo(repo, runner=dirty_runner)
    assert dirty.status == "skipped"
    assert "dirty" in dirty.detail

    def diverged_runner(argv, *, cwd=None, timeout=0):
        mapping = {
            ("git", "rev-parse", "--is-bare-repository"): self_update.CommandResult(
                list(argv), 0, "false\n", ""
            ),
            ("git", "status"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-parse"): self_update.CommandResult(list(argv), 0, "origin/main\n", ""),
            ("git", "fetch"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-list"): self_update.CommandResult(list(argv), 0, "1\t2\n", ""),
        }
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        raise AssertionError(argv)

    diverged = self_update.fast_forward_repo(repo, runner=diverged_runner)
    assert diverged.status == "skipped"
    assert "diverged" in diverged.detail


SCRATCH_REF = "refs/agent-machines/self-update-fetch/main"


def test_fast_forward_repo_bare_anchor_fast_forwards_branch_ref(tmp_path):
    repo = tmp_path / "bare-repo"
    repo.mkdir()
    calls: list[list[str]] = []

    def bare_runner(argv, *, cwd=None, timeout=0):
        calls.append(list(argv))
        mapping = {
            ("git", "rev-parse", "--is-bare-repository"): self_update.CommandResult(
                list(argv), 0, "true\n", ""
            ),
            ("git", "symbolic-ref"): self_update.CommandResult(list(argv), 0, "main\n", ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                self_update.CommandResult(list(argv), 0, "origin/main\n", "")
            ),
            ("git", "rev-parse", "--verify", "HEAD"): self_update.CommandResult(
                list(argv), 0, "aaaa000\n", ""
            ),
            ("git", "fetch"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-parse", "--verify", SCRATCH_REF): self_update.CommandResult(
                list(argv), 0, "bbbb111\n", ""
            ),
            ("git", "rev-list"): self_update.CommandResult(list(argv), 0, "0\t1\n", ""),
            ("git", "merge-base", "--is-ancestor"): self_update.CommandResult(
                list(argv), 0, "", ""
            ),
            ("git", "update-ref", "-d"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "update-ref"): self_update.CommandResult(list(argv), 0, "", ""),
        }
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        raise AssertionError(argv)

    result = self_update.fast_forward_repo(repo, runner=bare_runner)
    assert result.status == "changed"
    assert "bare" in result.detail
    expected_fetch = [
        "git", "fetch", "--quiet", "--no-tags", "origin", f"+refs/heads/main:{SCRATCH_REF}",
    ]
    assert expected_fetch in calls
    assert ["git", "merge-base", "--is-ancestor", "aaaa000", "bbbb111"] in calls
    assert ["git", "update-ref", "refs/heads/main", "bbbb111", "aaaa000"] in calls
    assert ["git", "update-ref", "-d", SCRATCH_REF] in calls


def test_fast_forward_repo_bare_anchor_skips_detached_head(tmp_path):
    repo = tmp_path / "bare-repo"
    repo.mkdir()

    def bare_runner(argv, *, cwd=None, timeout=0):
        mapping = {
            ("git", "rev-parse", "--is-bare-repository"): self_update.CommandResult(
                list(argv), 0, "true\n", ""
            ),
            ("git", "symbolic-ref"): self_update.CommandResult(
                list(argv), 128, "", "not a symbolic ref"
            ),
        }
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        raise AssertionError(argv)

    result = self_update.fast_forward_repo(repo, runner=bare_runner)
    assert result.status == "skipped"
    assert "detached" in result.detail


def test_fast_forward_repo_bare_anchor_reports_error_when_ref_lock_fails(tmp_path):
    repo = tmp_path / "bare-repo"
    repo.mkdir()

    def bare_runner(argv, *, cwd=None, timeout=0):
        mapping = {
            ("git", "rev-parse", "--is-bare-repository"): self_update.CommandResult(
                list(argv), 0, "true\n", ""
            ),
            ("git", "symbolic-ref"): self_update.CommandResult(list(argv), 0, "main\n", ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                self_update.CommandResult(list(argv), 0, "origin/main\n", "")
            ),
            ("git", "rev-parse", "--verify", "HEAD"): self_update.CommandResult(
                list(argv), 0, "aaaa000\n", ""
            ),
            ("git", "fetch"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-parse", "--verify", SCRATCH_REF): self_update.CommandResult(
                list(argv), 0, "bbbb111\n", ""
            ),
            ("git", "rev-list"): self_update.CommandResult(list(argv), 0, "0\t1\n", ""),
            ("git", "merge-base", "--is-ancestor"): self_update.CommandResult(
                list(argv), 0, "", ""
            ),
            ("git", "update-ref", "-d"): self_update.CommandResult(list(argv), 0, "", ""),
            # Something else locked/moved the ref concurrently: the
            # compare-and-swap write itself fails.
            ("git", "update-ref"): self_update.CommandResult(
                list(argv), 128, "", "fatal: cannot lock ref: is at cccc222 but expected aaaa000"
            ),
        }
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        raise AssertionError(argv)

    result = self_update.fast_forward_repo(repo, runner=bare_runner)
    assert result.status == "error"
    assert "lock ref" in result.detail


def test_fast_forward_repo_bare_anchor_skips_when_upstream_diverges_during_update(tmp_path):
    repo = tmp_path / "bare-repo"
    repo.mkdir()

    def bare_runner(argv, *, cwd=None, timeout=0):
        mapping = {
            ("git", "rev-parse", "--is-bare-repository"): self_update.CommandResult(
                list(argv), 0, "true\n", ""
            ),
            ("git", "symbolic-ref"): self_update.CommandResult(list(argv), 0, "main\n", ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                self_update.CommandResult(list(argv), 0, "origin/main\n", "")
            ),
            ("git", "rev-parse", "--verify", "HEAD"): self_update.CommandResult(
                list(argv), 0, "aaaa000\n", ""
            ),
            ("git", "fetch"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-parse", "--verify", SCRATCH_REF): self_update.CommandResult(
                list(argv), 0, "cccc222\n", ""
            ),
            ("git", "rev-list"): self_update.CommandResult(list(argv), 0, "0\t1\n", ""),
            # A hostile/mirror fetch refspec somehow still produced a
            # divergent scratch ref: the local ancestry re-check catches it.
            ("git", "merge-base", "--is-ancestor"): self_update.CommandResult(
                list(argv), 1, "", ""
            ),
            ("git", "update-ref", "-d"): self_update.CommandResult(list(argv), 0, "", ""),
        }
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        raise AssertionError(argv)

    result = self_update.fast_forward_repo(repo, runner=bare_runner)
    assert result.status == "skipped"
    assert "non-fast-forward" in result.detail


def test_fast_forward_repo_bare_anchor_already_up_to_date(tmp_path):
    repo = tmp_path / "bare-repo"
    repo.mkdir()
    calls: list[list[str]] = []

    def bare_runner(argv, *, cwd=None, timeout=0):
        calls.append(list(argv))
        mapping = {
            ("git", "rev-parse", "--is-bare-repository"): self_update.CommandResult(
                list(argv), 0, "true\n", ""
            ),
            ("git", "symbolic-ref"): self_update.CommandResult(list(argv), 0, "main\n", ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                self_update.CommandResult(list(argv), 0, "origin/main\n", "")
            ),
            ("git", "rev-parse", "--verify", "HEAD"): self_update.CommandResult(
                list(argv), 0, "aaaa000\n", ""
            ),
            ("git", "fetch"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-parse", "--verify", SCRATCH_REF): self_update.CommandResult(
                list(argv), 0, "aaaa000\n", ""
            ),
            ("git", "rev-list"): self_update.CommandResult(list(argv), 0, "0\t0\n", ""),
            ("git", "update-ref", "-d"): self_update.CommandResult(list(argv), 0, "", ""),
        }
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        raise AssertionError(argv)

    result = self_update.fast_forward_repo(repo, runner=bare_runner)
    assert result.status == "ok"
    assert "up to date" in result.detail
    assert ["git", "update-ref", "-d", SCRATCH_REF] in calls



def test_live_session_deferral_reason_reports_busy_worktree():
    reason = self_update.live_session_deferral_reason(
        worktree_lister=lambda: [
            {
                "id": "wt-1",
                "title": "busy worktree",
                "live_rest": "busy",
                "reciprocal_relation": {"binding": {"state": "bound-here"}},
            }
        ]
    )
    assert reason == "live session is active in worktree busy worktree"


def test_sweep_continues_despite_unrelated_live_session_and_uses_maintenance_safe_restore(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update_tasks.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    repo = type("Repo", (), {"path": tmp_path / "repo"})
    repo.path.mkdir()

    def runner(argv, *, cwd=None, timeout=0):
        if argv[:2] == ["git", "status"]:
            if cwd == repo.path:
                return self_update.CommandResult(list(argv), 0, " M tracked.txt\n", "")
            return self_update.CommandResult(list(argv), 0, "", "")
        mapping = {
            ("git", "rev-parse"): self_update.CommandResult(list(argv), 0, "origin/main\n", ""),
            ("git", "fetch"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-list"): self_update.CommandResult(list(argv), 0, "0\t0\n", ""),
            ("agent-worktrees", "-p"): self_update.CommandResult(list(argv), 0, "", ""),
            (self_update.sys.executable, "-m"): self_update.CommandResult(list(argv), 0, "", ""),
        }
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        raise AssertionError(argv)

    result = self_update.run_tier(
        "sweep",
        opted_in=True,
        discovered_repos=[repo],
        runner=runner,
        worktree_lister=lambda: [
            {
                "id": "wt-1",
                "title": "busy worktree",
                "live_rest": "busy",
                "reciprocal_relation": {"binding": {"state": "bound-here"}},
            }
        ],
        home=tmp_path,
    )
    assert result.status == "ok"
    assert result.steps[0].name == "git-pull"
    assert result.steps[0].status == "skipped"
    assert "dirty" in result.steps[0].detail
    restore_step = next(step for step in result.steps if step.name == "restore")
    assert "--maintenance-safe" in (restore_step.command or [])


def test_plan_includes_self_update_observed_status(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    self_update.write_status(
        tmp_path,
        "watchdog",
        attempt="2026-09-14T09:00:00+00:00",
        success="2026-09-14T09:05:00+00:00",
    )
    package = _pkg(
        tmp_path,
        "acme/watchdog",
        [{"type": "self-update", "tier": "watchdog"}],
    )
    current = plan([package], "box-1", "windows")
    resource = next(item for item in current.resources if item["type"] == "self-update")
    assert resource["observed"] == {
        "last_attempt": "2026-09-14T09:00:00+00:00",
        "last_success": "2026-09-14T09:05:00+00:00",
    }
    assert "last-attempt=2026-09-14T09:00:00+00:00" in resource["summary"]


def test_cli_self_update_run_emits_json(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "_resolve_machine_identity",
        lambda args: type(
            "Identity",
            (),
            {"canonical": "box-1", "accepted": ("box-1",), "warnings": [], "raw": "box-1"},
        )(),
    )
    monkeypatch.setattr(cli, "_emit_identity_warnings", lambda identity: None)
    monkeypatch.setattr(cli, "_self_update_packages", lambda machine, accepted_machines=None: [])
    monkeypatch.setattr(
        cli._reconcile, "resolve_union", lambda packages, machine, accepted_machines=None: []
    )
    monkeypatch.setattr(cli._validator, "validate", lambda resolved, machine, plat=None: [])
    monkeypatch.setattr(
        cli._resources, "resolve_resources", lambda resolved, machine, plat: ([], [])
    )
    monkeypatch.setattr(cli._discover, "discover", lambda machine, accepted_machines=None: [])
    monkeypatch.setattr(
        cli._self_update,
        "run_tier",
        lambda tier, **kwargs: self_update.RunResult(
            tier=tier,
            status="noop",
            opted_in=False,
            detail="tier 'watchdog' is not opted in",
        ),
    )
    rc = cli.main(["self-update", "run", "--tier", "watchdog", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tier"] == "watchdog"
    assert payload["status"] == "noop"


def test_reconcile_task_registers_new_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update_tasks.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    state = {"present": False}

    def query(tier, *, machine=None, runner=None, resolve_binary=None, home=None):
        return _task_snapshot(tier, present=state["present"])

    def register(tier, *, machine=None, runner=None, resolve_binary=None, home=None):
        state["present"] = True
        return self_update.CommandResult(["pwsh"], 0, "", "")

    monkeypatch.setattr(self_update_tasks, "query_scheduled_task", query)
    monkeypatch.setattr(self_update_tasks, "register_scheduled_task", register)
    result = self_update.reconcile_scheduled_task("watchdog", desired_present=True, home=tmp_path)
    assert result.status == "changed"
    assert result.changed is True
    assert result.snapshot is not None and result.snapshot.present is True


def test_register_scheduled_task_hourly_uses_native_repetition_parameters():
    """Regression: mutating $trigger.Repetition.Interval post-hoc does not
    persist through Register-ScheduledTask (Get-ScheduledTask reads back an
    empty Repetition on the live CIM object), so the hourly watchdog task
    registers but never matches its own expected definition. Repetition must
    be supplied via New-ScheduledTaskTrigger's own -RepetitionInterval /
    -RepetitionDuration parameters instead."""
    captured: dict[str, list[str]] = {}

    def fake_runner(argv, *, timeout=300):
        captured["argv"] = argv
        return self_update.CommandResult(argv, 0, "", "")

    self_update_tasks.register_scheduled_task(
        "watchdog",
        runner=fake_runner,
        resolve_binary=lambda name: f"/usr/bin/{name}",
        home=Path("/home/tmichon"),
    )
    script = captured["argv"][-1]
    assert "-RepetitionInterval (New-TimeSpan -Hours 1)" in script
    assert "-RepetitionDuration (New-TimeSpan -Days 3650)" in script
    assert "$trigger.Repetition.Interval" not in script
    assert "$trigger.Repetition.Duration" not in script


def test_register_scheduled_task_daily_has_no_repetition_parameters():
    captured: dict[str, list[str]] = {}

    def fake_runner(argv, *, timeout=300):
        captured["argv"] = argv
        return self_update.CommandResult(argv, 0, "", "")

    self_update_tasks.register_scheduled_task(
        "sweep",
        runner=fake_runner,
        resolve_binary=lambda name: f"/usr/bin/{name}",
        home=Path("/home/tmichon"),
    )
    script = captured["argv"][-1]
    assert "-Daily -At '3:00AM' -DaysInterval 1" in script
    assert "-RepetitionInterval" not in script


def test_reconcile_task_defers_to_elevated_install_when_registration_is_denied(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update_tasks.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(
        self_update_tasks,
        "query_scheduled_task",
        lambda tier, **kwargs: _task_snapshot(tier, present=False, matching=False),
    )
    monkeypatch.setattr(
        self_update_tasks,
        "register_scheduled_task",
        lambda tier, **kwargs: self_update.CommandResult(["pwsh"], 1, "", "Access is denied."),
    )
    result = self_update.reconcile_scheduled_task("watchdog", desired_present=True, home=tmp_path)
    assert result.status == "deferred"
    assert result.ok is False
    assert result.commands == [["agent-machines", "self-update", "install", "--tier", "watchdog"]]
    assert "run once from an elevated PowerShell" in result.detail


def test_reconcile_task_removes_opted_out_task_without_retry_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update_tasks.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(
        self_update_tasks,
        "query_scheduled_task",
        lambda tier, **kwargs: _task_snapshot(tier, present=True, matching=True, enabled=True),
    )
    calls: list[str] = []

    def unregister(tier, *, runner=None, resolve_binary=None, machine=None):
        calls.append(tier)
        return self_update.CommandResult(["pwsh"], 0, "", "")

    monkeypatch.setattr(self_update_tasks, "unregister_scheduled_task", unregister)
    result = self_update.reconcile_scheduled_task("sweep", desired_present=False, home=tmp_path)
    assert result.status == "changed"
    assert result.changed is True
    assert calls == ["sweep"]
    assert "elevated PowerShell" not in result.detail


def test_default_command_runner_resolves_pathext_shim(monkeypatch):
    """Regression: on Windows, a bare command name that resolves to a
    `.cmd`/`.bat` shim (e.g. the `agent-worktrees` binstub) raises
    FileNotFoundError under subprocess.run(shell=False), because CreateProcess
    does not apply PATHEXT resolution the way cmd.exe does. Found while
    dogfooding the sweep tier's `agent-worktrees reconcile-plugins` call on a
    real Windows machine -- it failed with WinError 2 even though
    `agent-worktrees` was genuinely on PATH."""
    captured: dict[str, list[str]] = {}

    def fake_which(name):
        return f"C:\\Users\\tmichon\\.local\\bin\\{name}.cmd" if name == "agent-worktrees" else None

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    monkeypatch.setattr(self_update.shutil, "which", fake_which)
    monkeypatch.setattr(self_update.subprocess, "run", fake_run)
    result = self_update.default_command_runner(["agent-worktrees", "-p", "dotfiles", "list"])
    assert captured["argv"][0] == "C:\\Users\\tmichon\\.local\\bin\\agent-worktrees.cmd"
    # The reported CommandResult.argv still shows the original logical argv
    # (not the resolved absolute path) so status/log output stays readable.
    assert result.argv[0] == "agent-worktrees"


def test_default_command_runner_leaves_unresolvable_argv0_unchanged(monkeypatch):
    def fake_which(name):
        return None

    captured: dict[str, list[str]] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "not found"

        return _Proc()

    monkeypatch.setattr(self_update.shutil, "which", fake_which)
    monkeypatch.setattr(self_update.subprocess, "run", fake_run)
    self_update.default_command_runner(["totally-unknown-binary"])
    assert captured["argv"] == ["totally-unknown-binary"]



def test_cli_self_update_install_skips_non_opted_in_tier_without_registration(monkeypatch, capsys):
    resource = type("Resource", (), {"desired": {"state": "absent"}})()
    monkeypatch.setattr(
        cli,
        "_resolve_self_update_resources",
        lambda args: (None, "box-1", {"watchdog": resource}),
    )

    def fail(*args, **kwargs):
        raise AssertionError("install should not be attempted for opted-out tiers")

    monkeypatch.setattr(cli._self_update, "reconcile_scheduled_task", fail)
    rc = cli.main(["self-update", "install", "--tier", "watchdog", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tiers"][0]["status"] == "skipped"
    assert "not opted in" in payload["tiers"][0]["detail"]


def test_cli_self_update_status_emits_json(monkeypatch, capsys):
    resource = type("Resource", (), {"desired": {"state": "present"}})()
    monkeypatch.setattr(
        cli,
        "_resolve_self_update_resources",
        lambda args: (None, "box-1", {"watchdog": resource}),
    )
    monkeypatch.setattr(
        cli._self_update,
        "scheduled_task_status",
        lambda tier, opted_in, machine=None: self_update.ScheduledTaskStatus(
            tier=tier,
            opted_in=opted_in,
            task_name=self_update.TIER_SPECS[tier].task_name,
            registered=True,
            enabled=True,
            matching=True,
            state="Ready",
            detail="Scheduled Task is registered",
            last_attempt="2026-09-14T09:00:00+00:00",
            last_success="2026-09-14T09:05:00+00:00",
        ),
    )
    rc = cli.main(["self-update", "status", "--tier", "watchdog", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tiers"][0]["registered"] is True
    assert payload["tiers"][0]["opted_in"] is True
